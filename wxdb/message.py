from __future__ import annotations

import hashlib
import re
from typing import Optional

import zstandard as zstd

from .image import resolve_image
from .config import get_config
from .content import parse_message_content
from .session import list_sessions
from .sqlcipher import run_query
from .sqlcipher import sql_quote


MESSAGE_DB_PATH = "message/message_0.db"
_ZSTD_MAGIC = bytes.fromhex("28B52FFD")
MAX_CONTENT_BYTES = 16 * 1024 * 1024


def message_db_paths() -> list[str]:
    """Only numbered message shards; FTS and resource databases are not messages."""
    paths = [p for p in get_config().db_keys if re.fullmatch(r"message/message_\d+\.db", p)]
    if not paths:
        raise ValueError("wxdb.json has no message/message_<number>.db keys")
    return sorted(paths, key=lambda p: int(p.rsplit("_", 1)[1][:-3]))


def _message_table_name(session_username: str) -> str:
    table_suffix = hashlib.md5(session_username.encode("utf-8")).hexdigest()
    return f"Msg_{table_suffix}"


def _message_table_exists(table_name: str, db_path: str = MESSAGE_DB_PATH) -> bool:
    sql = f"""
SELECT name
FROM sqlite_master
WHERE type = 'table' AND name = {sql_quote(table_name)}
LIMIT 1;
""".strip()
    rows = run_query(db_path, sql)
    return bool(rows)


def _load_name_map(db_path: str = MESSAGE_DB_PATH) -> dict[int, str]:
    sql = """
SELECT
    rowid AS sender_id,
    user_name
FROM Name2Id
ORDER BY rowid;
""".strip()
    rows = run_query(db_path, sql)
    return {int(row["sender_id"]): row["user_name"] for row in rows if row.get("sender_id")}


def _message_type_name(local_type: int) -> str:
    local_type = local_type & 0xFFFFFFFF
    mapping = {
        1: "text",
        3: "image",
    }
    return mapping.get(local_type, f"type_{local_type}")


def _decode_message_content(row: dict) -> str:
    """Decode bytes transported as hex; retain failures explicitly for inspection."""
    content_type = str(row.get("message_content_type") or "").lower()
    row["content_decode_status"] = "ok"
    row.pop("content_decode_error", None)
    if content_type == "null":
        row["content_decode_status"] = "null"
        return ""
    if content_type != "blob" and "message_content_hex" not in row:
        return str(row.get("message_content") or "")

    hex_content = str(row.get("message_content_hex") or "").strip()
    if not hex_content:
        return ""

    try:
        raw = bytes.fromhex(hex_content)
    except ValueError:
        row["content_decode_status"] = "error"
        row["content_decode_error"] = "invalid_hex"
        return ""
    try:
        if raw.startswith(_ZSTD_MAGIC):
            size = zstd.frame_content_size(raw)
            if size > MAX_CONTENT_BYTES:
                raise ValueError("content_too_large")
            # max_output_size also permits frames without a declared size.
            # Reject truncated frames/trailing data rather than returning partial text.
            raw = zstd.ZstdDecompressor().decompress(
                raw, max_output_size=MAX_CONTENT_BYTES, allow_extra_data=False,
            )
        if len(raw) > MAX_CONTENT_BYTES:
            raise ValueError("content_too_large")
        return raw.decode("utf-8")
    except (zstd.ZstdError, UnicodeDecodeError, ValueError) as exc:
        row["content_decode_status"] = "error"
        row["content_decode_error"] = type(exc).__name__
        return ""


def _enrich_messages(rows: list[dict], db_path: str = MESSAGE_DB_PATH) -> list[dict]:
    if not rows:
        return rows

    name_map = _load_name_map(db_path)
    for row in rows:
        local_type = int(row.get("local_type") or 0)
        sender_id = int(row.get("real_sender_id") or 0)
        row["message_content"] = _decode_message_content(row)
        row["message_type"] = _message_type_name(local_type)
        row.update(parse_message_content(row["message_content"], local_type))
        row["sender_username"] = name_map.get(sender_id, "")
        row["source_db"] = db_path
        row.pop("message_content_type", None)
        if row["content_decode_status"] != "error":
            row.pop("message_content_hex", None)
    return rows


def _attach_images(rows: list[dict], output_dir: str | None = None) -> list[dict]:
    for row in rows:
        if row.get("message_type") != "image":
            continue
        result = resolve_image(
            int(row.get("local_id") or 0),
            session_username=str(row.get("session_username") or "").strip() or None,
            output_dir=output_dir,
            server_id=int(row.get("server_id") or 0),
            create_time=int(row.get("create_time") or 0),
            content_md5=row.get("image_metadata", {}).get("md5"),
        )
        row["image"] = result
        if result.get("success"):
            row["image_path"] = result.get("preview_path", result["path"])
            row["image_format"] = "png" if result.get("preview_path") else result["format"]
    return rows


def build_session_messages_query(
    session_username: str,
    limit: Optional[int] = None,
    ascending: bool = False,
    since_timestamp: Optional[int] = None,
) -> str:
    table_name = _message_table_name(session_username)
    order = "ASC" if ascending else "DESC"
    where_clause = ""
    if since_timestamp is not None:
        where_clause = f"WHERE create_time >= {int(since_timestamp)}"
    limit_clause = _limit_clause(limit)
    return f"""
SELECT
    { sql_quote(session_username) } AS session_username,
    local_id,
    server_id,
    local_type,
    sort_seq,
    real_sender_id,
    create_time,
    status,
    upload_status,
    download_status,
    server_seq,
    origin_source,
    typeof(message_content) AS message_content_type,
    hex(message_content) AS message_content_hex
FROM {table_name}
{where_clause}
ORDER BY sort_seq {order}, local_id {order}
{limit_clause};
""".strip()


def list_session_messages(
    session_username: str,
    limit: Optional[int] = 50,
    ascending: bool = False,
    since_timestamp: Optional[int] = None,
    with_image_paths: bool = False,
    output_dir: str | None = None,
    with_voice_paths: bool = False,
) -> list[dict]:
    sql = build_session_messages_query(
        session_username=session_username,
        limit=limit,
        ascending=ascending,
        since_timestamp=since_timestamp,
    )
    rows = _query_shards(session_username, sql, limit, ascending)
    if with_image_paths:
        rows = _attach_images(rows, output_dir=output_dir)
    if with_voice_paths:
        from .voice import resolve_voice
        from pathlib import Path
        voice_dir = output_dir or str(Path(__file__).resolve().parent.parent / "state/audio")
        for row in rows:
            if row["message_type"] == "voice":
                row["voice_resource"] = resolve_voice(
                    session_username, int(row["local_id"]), int(row.get("server_id") or 0),
                    int(row["create_time"]), voice_dir,
                )
    return rows


def _limit_clause(limit: int | None) -> str:
    if limit is None:
        return ""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer or None")
    return f"LIMIT {limit}"


def _query_shards(username: str, sql: str, limit: int | None, ascending: bool) -> list[dict]:
    if limit == 0:
        return []
    rows = []
    table = _message_table_name(username)
    for path in message_db_paths():
        if _message_table_exists(table, path):
            rows.extend(_enrich_messages(run_query(path, sql), path))
    rows.sort(key=lambda r: (int(r.get("sort_seq") or 0), int(r.get("local_id") or 0),
                             r["source_db"]), reverse=not ascending)
    return rows if limit is None else rows[:limit]


def build_unread_messages_query(
    session_username: str,
    unread_count: int,
    last_clear_unread_timestamp: int,
) -> str:
    table_name = _message_table_name(session_username)
    where_clause = ""
    if last_clear_unread_timestamp > 0:
        where_clause = f"WHERE sort_seq > {int(last_clear_unread_timestamp) * 1000}"

    limit_clause = "" if unread_count <= 0 else f"LIMIT {int(unread_count)}"
    return f"""
SELECT
    {sql_quote(session_username)} AS session_username,
    local_id,
    server_id,
    local_type,
    sort_seq,
    real_sender_id,
    create_time,
    status,
    upload_status,
    download_status,
    server_seq,
    origin_source,
    typeof(message_content) AS message_content_type,
    hex(message_content) AS message_content_hex
FROM {table_name}
{where_clause}
ORDER BY sort_seq DESC, local_id DESC
{limit_clause};
""".strip()


def list_unread_messages(
    include_hidden: bool = False,
    with_contact_names: bool = True,
) -> list[dict]:
    unread_sessions = list_sessions(
        include_hidden=include_hidden,
        with_contact_names=with_contact_names,
        unread_only=True,
    )
    unread_messages = []

    for session in unread_sessions:
        session_username = session["username"]
        unread_count = int(session.get("unread_count") or 0)
        last_clear_unread_timestamp = int(session.get("last_clear_unread_timestamp") or 0)
        if unread_count <= 0:
            continue

        sql = build_unread_messages_query(
            session_username=session_username,
            unread_count=unread_count,
            last_clear_unread_timestamp=last_clear_unread_timestamp,
        )
        session_messages = _query_shards(session_username, sql, unread_count, False)

        for message in session_messages:
            message["session_unread_count"] = unread_count
            if with_contact_names:
                message["display_name"] = session.get("display_name", session_username)
                if "nick_name" in session:
                    message["nick_name"] = session["nick_name"]
                if "remark" in session:
                    message["remark"] = session["remark"]

        unread_messages.extend(session_messages)

    unread_messages.sort(
        key=lambda item: (item.get("sort_seq", 0), item.get("local_id", 0)),
        reverse=True,
    )
    return unread_messages
