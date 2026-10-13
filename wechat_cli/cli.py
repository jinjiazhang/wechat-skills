from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be non-negative')
    return number


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def common_config(parser):
    parser.add_argument('--config', type=Path, required=True, help='Account wxdb.json; explicit account selection')


def parser_for(mode):
    parser = Parser(prog='wechat-' + mode)
    sub = parser.add_subparsers(dest='command', required=True)
    if mode == 'keys':
        p = sub.add_parser('discover')
        p.add_argument('--base-dir', type=Path, default=Path.home() / 'Documents/xwechat_files')
        p = sub.add_parser('extract')
        p.add_argument('--db-dir', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--force', action='store_true')
        p.add_argument('--timeout', type=nonnegative, default=180)
        p = sub.add_parser('images')
        common_config(p)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--force', action='store_true')
        p.add_argument('--statistic-dir', type=Path, default=Path(os.environ.get('APPDATA', str(Path.home()))) / 'Tencent/xwechat/net/kvcomm')
    elif mode == 'records':
        for name in ('sessions', 'contacts', 'messages', 'unread'):
            p = sub.add_parser(name)
            common_config(p)
            p.add_argument('--limit', type=nonnegative, default=50)
            if name in ('messages',):
                p.add_argument('--session', required=True, help='Exact username, not a nickname')
                p.add_argument('--since', type=nonnegative, help='Inclusive Unix timestamp in seconds')
                p.add_argument('--ascending', action='store_true')
                p.add_argument('--raw', action='store_true', help='Include original XML and raw decode fields')
            if name in ('sessions', 'unread'):
                p.add_argument('--include-hidden', action='store_true')
    elif mode == 'send':
        p = sub.add_parser('text')
        common_config(p)
        p.add_argument('--state', type=Path, default=Path('state/chat.sqlite'))
        p.add_argument('--session', required=True)
        p.add_argument('--self', dest='sender', required=True)
        p.add_argument('--ui-name')
        p.add_argument('--token', required=True)
        p.add_argument('--text-file', type=Path, required=True, help='UTF-8 text file; use - for stdin')
        p.add_argument('--execute', action='store_true')
        p = sub.add_parser('reconcile')
        common_config(p)
        p.add_argument('--state', type=Path, default=Path('state/chat.sqlite'))
        p.add_argument('--token', required=True)
        p.add_argument('--confirm-not-sent', action='store_true', help='Operator confirmed no send; abandon uncertain token')
        sub.add_parser('inspect')
    elif mode == 'agent':
        p = sub.add_parser('init')
        common_config(p)
        p.add_argument('--state', type=Path, default=Path('state/chat.sqlite'))
        p.add_argument('--name', required=True)
        p.add_argument('--session', required=True)
        p.add_argument('--self', dest='sender', required=True)
        p.add_argument('--style', default='简洁、自然地回复，不编造未读取的信息。')
        p.add_argument('--max-sends', type=nonnegative, default=10)
        p.add_argument('--cooldown', type=nonnegative, default=3)
        p.add_argument('--replay-last', type=nonnegative, default=0, help='Explicitly replay N existing messages for testing')
        for command in ('next', 'reply', 'skip', 'status', 'stop', 'start'):
            p = sub.add_parser(command)
            p.add_argument('--state', type=Path, default=Path('state/chat.sqlite'))
            p.add_argument('--name', required=True)
            if command == 'next':
                p.add_argument('--wait', type=nonnegative, default=0, help='Bounded wait, at most 60 seconds')
            if command in ('reply', 'skip'):
                p.add_argument('--ticket', required=True)
            if command == 'reply':
                p.add_argument('--text-file', type=Path, required=True)
                p.add_argument('--execute', action='store_true')
    else:
        p = sub.add_parser('images')
        common_config(p)
        p.add_argument('--session', required=True)
        p.add_argument('--limit', type=nonnegative, default=20)
        p.add_argument('--output', type=Path, required=True)
        p = sub.add_parser('voice')
        common_config(p)
        p.add_argument('--session', required=True)
        p.add_argument('--local-id', type=nonnegative, required=True)
        p.add_argument('--server-id', type=nonnegative, required=True)
        p.add_argument('--create-time', type=nonnegative, required=True)
        p.add_argument('--output', type=Path, required=True)
        p = sub.add_parser('validate')
        common_config(p)
        p.add_argument('--output', type=Path, required=True)
    return parser


def normalize(value):
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: str(v) if k in ('server_id', 'svr_id', 'sort_seq', 'local_type') and isinstance(v, int)
                else normalize(v) for k, v in value.items()}
    return value


def readable(row, raw=False):
    row = dict(row)
    text = row.get('message_text', '')
    sender = row.get('sender_username', '')
    # Only remove a verified sender prefix from group TEXT messages.
    if row.get('message_type') == 'text' and row.get('session_username', '').endswith('@chatroom'):
        for prefix in (sender + ':\n', sender + ':\r\n'):
            if sender and text.startswith(prefix):
                row['message_text'] = text[len(prefix):]
                break
    if 'create_time' in row:
        row['time'] = datetime.fromtimestamp(row['create_time'], timezone(timedelta(hours=8))).isoformat()
    if not raw:
        row.pop('message_content', None)
        row.pop('message_content_hex', None)
    return row


def dispatch(mode, args):
    def read_text():
        return sys.stdin.read() if str(args.text_file) == '-' else args.text_file.read_text(encoding='utf-8-sig')
    if mode == 'send':
        from .sending import send, reconcile
        if args.command == 'inspect':
            from .windows_sender import WindowsSender
            return WindowsSender().inspect()
        if args.command == 'reconcile':
            return reconcile(args.config, args.state, args.token, args.confirm_not_sent)
        return send(args.config, args.state, args.session, args.sender, read_text(), args.token,
                    args.execute, args.ui_name)
    if mode == 'agent':
        from . import agent
        if args.command == 'init':
            return agent.initialize(args.config, args.state, args.name, args.session, args.sender, args.style,
                                    args.max_sends, args.cooldown, args.replay_last)
        if args.command == 'next':
            import time
            if args.wait > 60:
                raise ValueError('wait must be at most 60')
            deadline = time.monotonic() + args.wait
            while True:
                result = agent.next_batch(args.state, args.name)
                if result['status'] != 'idle' or time.monotonic() >= deadline:
                    return result
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        if args.command == 'reply':
            return agent.reply(args.state, args.name, args.ticket, read_text(), args.execute)
        if args.command == 'skip':
            return agent.skip(args.state, args.name, args.ticket)
        if args.command in ('stop', 'start'):
            return agent.set_active(args.state, args.name, args.command == 'start')
        return agent.status(args.state, args.name)
    if mode == 'keys':
        from .keys import discover, extract, image_key
        if args.command == 'discover':
            return discover(args.base_dir)
        if args.command == 'extract':
            return extract(args.db_dir, args.output, args.force, args.timeout)
        return image_key(args.config, args.output, args.force, args.statistic_dir)
    from wxdb import load_config, list_sessions, list_contacts, list_session_messages, list_unread_messages
    load_config(args.config)
    if mode == 'records':
        if args.command == 'sessions':
            return list_sessions(limit=args.limit, include_hidden=args.include_hidden)
        if args.command == 'contacts':
            return list_contacts(limit=args.limit)
        if args.command == 'messages':
            return [readable(r, args.raw) for r in list_session_messages(args.session, args.limit,
                    ascending=args.ascending, since_timestamp=args.since)]
        return [readable(r) for r in list_unread_messages(include_hidden=args.include_hidden)[:args.limit]]
    if args.command == 'images':
        from wxdb import list_session_images
        return list_session_images(args.session, args.limit, args.output.resolve())
    if args.command == 'voice':
        from wxdb import resolve_voice
        return resolve_voice(args.session, args.local_id, args.server_id, args.create_time, args.output.resolve())
    from wxdb.validate_media import validate
    return validate(str(args.config), args.output.resolve())


def run(mode, argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        args = parser_for(mode).parse_args(argv)
        data = dispatch(mode, args)
        resources = data if isinstance(data, list) else [data]
        partial = any(isinstance(r, dict) and r.get('success') is False for r in resources)
        if mode == 'media' and args.command == 'validate':
            partial = any(s.get('success') is False or s.get('pixel_decode') == 'failed'
                          or s.get('decode_status') == 'error'
                          or s.get('parse_status') != 'ok'
                          or s.get('length_matches') is False for s in data['samples'])
        result = {'schema_version': 1, 'ok': not partial, 'command': mode + '.' + args.command,
                  'data': normalize(data)}
        if partial:
            result['error'] = {'code': 'RESOURCE_UNAVAILABLE', 'message': 'See per-resource errors in data'}
        print(json.dumps(result, ensure_ascii=False))
        return 1 if partial else 0
    except Exception as exc:
        from .chat_state import ChatError
        if isinstance(exc, ChatError):
            print(json.dumps({'schema_version': 1, 'ok': False, 'error': {'code': exc.code, 'message': str(exc)}}, ensure_ascii=False))
            return 1
        # No exception payloads: SQL errors can contain keys or private message bodies.
        code = {FileNotFoundError: 'NOT_FOUND', FileExistsError: 'OUTPUT_EXISTS',
                ValueError: 'INVALID_ARGUMENT', ModuleNotFoundError: 'DEPENDENCY_MISSING'}.get(type(exc), 'OPERATION_FAILED')
        messages = {'NOT_FOUND': 'Check config, database and tool paths',
                    'OUTPUT_EXISTS': 'Choose a new output path or use --force',
                    'INVALID_ARGUMENT': 'Check command arguments with --help',
                    'DEPENDENCY_MISSING': 'Install the package and optional media dependencies',
                    'OPERATION_FAILED': 'Check login, account config, database keys and tool availability'}
        print(json.dumps({'schema_version': 1, 'ok': False, 'error': {'code': code, 'message': messages[code],
                          'exception_type': type(exc).__name__}}, ensure_ascii=False))
        return 1


def keys_main():
    return run('keys')


def records_main():
    return run('records')


def media_main():
    return run('media')


def send_main():
    return run('send')


def agent_main():
    return run('agent')
