from __future__ import annotations

import json
import time

from wxdb import load_config, list_contacts, list_session_messages, list_sessions
from .chat_state import ChatError, account_id, database, identity, json_text


def validate_text(text):
    if not text.strip() or len(text) > 2000 or any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ChatError('INVALID_TEXT', 'Text must contain 1–2000 characters and no control keys')
    return text.replace('\r\n', '\n')


def target(session, ui_name=None):
    sessions = list_sessions(include_hidden=True)
    selected = [r for r in sessions if r['username'] == session]
    if len(selected) != 1:
        raise ChatError('UNKNOWN_SESSION', 'Choose an exact username from records sessions')
    name = ui_name or selected[0]['display_name']
    # Search is UI name based: ambiguity must be rejected before the first input.
    candidates = {r['username'] for r in sessions if r.get('display_name') == name}
    candidates.update(r['username'] for r in list_contacts(include_deleted=True)
                      if name in (r.get('display_name'), r.get('nick_name'), r.get('remark'), r.get('alias')))
    if session == 'filehelper' and name == '文件传输助手':
        candidates.add(session)
    if candidates != {session}:
        raise ChatError('AMBIGUOUS_TARGET', 'UI display name must uniquely match the selected session')
    return name


def rows_for(session):
    # Keep all local IDs for positive verification, not just a timestamp / status heuristic.
    return list_session_messages(session, limit=None, ascending=True)


def confirmed(rows, before, sender, text, started):
    return [r for r in rows if identity(r) not in before
            and r.get('sender_username') == sender and r.get('message_type') == 'text'
            and r.get('message_content', '').removeprefix(sender + ':\n') == text
            and int(r.get('create_time', 0)) >= int(started) - 2]


def send(config_path, state, session, sender, text, token, execute=False, ui_name=None,
         backend=None, timeout=12, clock=time.time, sleep=time.sleep):
    cfg = load_config(config_path)
    text = validate_text(text)
    if not token or len(token) > 200:
        raise ChatError('INVALID_TOKEN', 'A nonempty idempotency token up to 200 characters is required')
    name = target(session, ui_name)
    snapshot = rows_for(session)
    if not (cfg.wxid == sender or cfg.wxid.startswith(sender + '_')):
        raise ChatError('UNKNOWN_SELF', 'Self username must match the configured account folder identity')
    if not execute:
        return {'status': 'dry_run', 'session': session, 'ui_name': name, 'text': text,
                'token': token, 'side_effects': False}
    if backend is None:
        from .windows_sender import WindowsSender
        backend = WindowsSender()
    account = account_id(cfg)
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM outbox WHERE token=?', (token,)).fetchone()
        if existing:
            if (existing['account'], existing['session'], existing['sender'], existing['text']) != (account, session, sender, text):
                raise ChatError('TOKEN_CONFLICT', 'Token is already bound to different content or target')
            if existing['status'] == 'confirmed':
                return {'status': 'confirmed', 'deduplicated': True, 'message_id': existing['message_id'], 'token': token}
            if existing['status'] != 'not_sent':
                raise ChatError('SEND_UNCERTAIN', 'Previous send is pending or uncertain; reconcile instead of retrying')
            db.execute('DELETE FROM outbox WHERE token=?', (token,))
        unresolved = db.execute("SELECT token FROM outbox WHERE account=? AND status IN ('sending','uncertain')", (account,)).fetchone()
        if unresolved:
            raise ChatError('SEND_UNCERTAIN', 'Resolve the previous uncertain send before sending another message')
        before = [identity(r) for r in snapshot]
        started = clock()
        db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (token, account, session, sender, text, 'sending', json_text(before), started, None, None))
        db.commit()  # durable BEFORE any input: a crash must never cause automatic resend
        try:
            backend.send(name, text)
            deadline = clock() + timeout
            while clock() < deadline:
                matches = confirmed(rows_for(session), set(before), sender, text, started)
                if len(matches) == 1:
                    message_id = identity(matches[0])
                    db.execute("UPDATE outbox SET status='confirmed',message_id=? WHERE token=?", (message_id, token))
                    db.commit()
                    return {'status': 'confirmed', 'token': token, 'session': session,
                            'message_id': message_id, 'delivery': 'observed_in_local_database'}
                if len(matches) > 1:
                    raise ChatError('AMBIGUOUS_CONFIRMATION', 'More than one new identical self message was observed')
                sleep(0.5)
            raise ChatError('SEND_UNCERTAIN', 'No unique new self message observed; do not resend automatically')
        except Exception as exc:
            status = 'not_sent' if getattr(backend, 'submitted', None) is False else 'uncertain'
            db.execute('UPDATE outbox SET status=?,error=? WHERE token=?', (status, type(exc).__name__, token))
            db.commit()
            if isinstance(exc, ChatError):
                raise
            raise ChatError('SEND_UNCERTAIN', 'UI action or verification failed; reconcile before retrying') from None


def reconcile(config_path, state, token, confirm_not_sent=False):
    cfg = load_config(config_path)
    with database(state) as db:
        row = db.execute('SELECT * FROM outbox WHERE token=?', (token,)).fetchone()
        if row is None or row['account'] != account_id(cfg):
            raise ChatError('UNKNOWN_TOKEN', 'No outbox entry for this token and account')
        if row['status'] == 'confirmed':
            return {'status': 'confirmed', 'token': token, 'message_id': row['message_id']}
        matches = confirmed(rows_for(row['session']), set(json.loads(row['before_ids'])),
                            row['sender'], row['text'], row['started'])
        if len(matches) != 1:
            if not matches and confirm_not_sent:
                db.execute("UPDATE outbox SET status='abandoned' WHERE token=?", (token,))
                db.commit()
                return {'status': 'abandoned', 'token': token, 'operator_confirmed_not_sent': True}
            raise ChatError('SEND_UNCERTAIN', 'Result remains uncertain; inspect WeChat manually, no resend performed')
        key = identity(matches[0])
        db.execute("UPDATE outbox SET status='confirmed',message_id=?,error=NULL WHERE token=?", (key, token))
        db.commit()
        return {'status': 'confirmed', 'token': token, 'message_id': key}
