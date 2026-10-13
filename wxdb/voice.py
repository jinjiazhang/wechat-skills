"""Read local voice payloads using the observed WeChat 4.x VoiceInfo schema.

No network downloads, transcription, or SILK decoding is performed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .config import get_config
from .sqlcipher import run_query, sql_quote


def voice_format(data: bytes) -> str:
    if data.startswith(b"\x02#!SILK_V3"):
        return "wechat_silk"
    if data.startswith(b"#!SILK_V3"):
        return "silk"
    if data.startswith(b"#!AMR\n"):
        return "amr"
    return "unknown"


def resolve_voice(session_username: str, local_id: int, server_id: int, create_time: int,
                  output_dir: str | Path | None = None) -> dict:
    config = get_config()
    paths = sorted(p for p in config.db_keys if re.fullmatch(r"message/media_\d+\.db", p))
    if not paths:
        return {"success": False, "error": "media_database_not_configured"}
    matches = []
    required = {"chat_name_id", "create_time", "local_id", "svr_id", "voice_data"}
    unsupported = []
    for path in paths:
        columns = {r["name"] for r in run_query(path, "PRAGMA table_info(VoiceInfo);")}
        if not required.issubset(columns):
            unsupported.append(path)
            continue
        sql = f"""SELECT hex(voice_data) AS payload_hex FROM VoiceInfo
WHERE chat_name_id = (SELECT rowid FROM Name2Id WHERE user_name = {sql_quote(session_username)})
AND local_id = {int(local_id)} AND svr_id = {int(server_id)} AND create_time = {int(create_time)}
LIMIT 2;"""
        matches.extend((path, r) for r in run_query(path, sql))
    if unsupported:
        return {"success": False, "error": "unsupported_media_schema", "source_dbs": unsupported}
    if len(matches) != 1:
        return {"success": False, "error": "ambiguous_voice" if matches else "voice_not_found"}
    path, row = matches[0]
    try:
        data = bytes.fromhex(row.get("payload_hex") or "")
    except ValueError:
        return {"success": False, "error": "invalid_voice_hex", "source_db": path}
    if not data:
        return {"success": False, "error": "voice_not_downloaded", "source_db": path}
    kind = voice_format(data)
    if kind == "unknown":
        return {"success": False, "error": "unknown_voice_format", "source_db": path, "size": len(data)}
    digest = hashlib.sha256(data).hexdigest()
    result = {"success": True, "source_db": path, "format": kind, "size": len(data),
              "sha256": digest, "decoded": False}
    if output_dir is not None:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (digest + (".amr" if kind == "amr" else ".silk"))
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                return {"success": False, "error": "output_conflict"}
        else:
            with target.open("xb") as stream:
                stream.write(data)
        # Keep the WeChat prefix intact so that export remains byte-for-byte exact.
        result["path"] = str(target.resolve())
    return result
