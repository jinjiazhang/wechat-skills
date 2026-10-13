from __future__ import annotations

from typing import Optional

from .contact import list_contacts
from .sqlcipher import run_query


SESSION_DB_PATH = "session/session.db"


def build_session_query(
    limit: Optional[int] = None,
    include_hidden: bool = False,
    unread_only: bool = False,
) -> str:
    conditions = []
    if not include_hidden:
        conditions.append("is_hidden = 0")
    if unread_only:
        conditions.append("unread_count > 0")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    limit_clause = "" if limit is None else f"LIMIT {limit}"
    return f"""
SELECT
    username,
    type,
    unread_count,
    is_hidden,
    summary,
    draft,
    status,
    last_timestamp,
    sort_timestamp,
    last_clear_unread_timestamp,
    last_msg_locald_id,
    last_msg_type,
    last_msg_sub_type,
    last_msg_sender,
    last_sender_display_name,
    last_msg_ext_type
FROM SessionTable
{where_clause}
ORDER BY sort_timestamp DESC, username
{limit_clause};
""".strip()


def list_sessions(
    limit: Optional[int] = None,
    include_hidden: bool = False,
    with_contact_names: bool = True,
    unread_only: bool = False,
) -> list[dict]:
    sql = build_session_query(
        limit=limit,
        include_hidden=include_hidden,
        unread_only=unread_only,
    )
    sessions = run_query(SESSION_DB_PATH, sql)

    if not with_contact_names or not sessions:
        return sessions

    usernames = [session["username"] for session in sessions if session.get("username")]
    contacts = list_contacts(
        usernames=usernames,
        include_deleted=True,
    )
    contact_map = {contact["username"]: contact for contact in contacts}

    for session in sessions:
        contact = contact_map.get(session["username"])
        if contact:
            session["display_name"] = contact["display_name"]
            session["nick_name"] = contact["nick_name"]
            session["remark"] = contact["remark"]
            session["alias"] = contact["alias"]
            session["verify_flag"] = contact["verify_flag"]
        else:
            session["display_name"] = session["username"]

    return sessions
