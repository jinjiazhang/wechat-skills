from __future__ import annotations

from typing import Iterable, Optional

from .sqlcipher import run_query
from .sqlcipher import sql_quote


CONTACT_DB_PATH = "contact/contact.db"


def build_contact_query(
    limit: Optional[int] = None,
    include_deleted: bool = False,
    usernames: Optional[Iterable[str]] = None,
) -> str:
    conditions = []
    if not include_deleted:
        conditions.append("delete_flag = 0")

    if usernames is not None:
        username_list = list(dict.fromkeys(usernames))
        if not username_list:
            return "SELECT * FROM contact WHERE 1 = 0;"
        quoted = ", ".join(sql_quote(username) for username in username_list)
        conditions.append(f"username IN ({quoted})")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    limit_clause = "" if limit is None else f"LIMIT {limit}"
    return f"""
SELECT
    username,
    nick_name,
    remark,
    alias,
    verify_flag,
    local_type,
    flag,
    delete_flag,
    CASE
        WHEN remark IS NOT NULL AND remark <> '' THEN remark
        WHEN nick_name IS NOT NULL AND nick_name <> '' THEN nick_name
        ELSE username
    END AS display_name
FROM contact
{where_clause}
ORDER BY display_name COLLATE NOCASE, username
{limit_clause};
""".strip()


def list_contacts(
    limit: Optional[int] = None,
    include_deleted: bool = False,
    usernames: Optional[Iterable[str]] = None,
) -> list[dict]:
    sql = build_contact_query(
        limit=limit,
        include_deleted=include_deleted,
        usernames=usernames,
    )
    return run_query(CONTACT_DB_PATH, sql)
