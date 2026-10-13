from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import get_config

# 模块级缓存：避免每次 run_query 都重复查找 sqlcipher 路径
_sqlcipher_path: str | None = None


def _find_sqlcipher() -> str:
    """查找 sqlcipher 可执行文件（结果会被缓存）。

    优先级：
    1. 项目 tools/ 目录下的 sqlcipher.exe（Windows）
    2. 系统 PATH 中的 sqlcipher
    """
    global _sqlcipher_path

    override = os.environ.get("WECHAT_SQLCIPHER")
    if override:
        if not Path(override).is_file():
            raise RuntimeError("WECHAT_SQLCIPHER does not name a file")
        return override

    # 命中缓存直接返回
    if _sqlcipher_path is not None:
        return _sqlcipher_path

    # 优先从项目 tools/ 目录查找
    project_root = Path(__file__).resolve().parent.parent
    tools_dir = project_root / "tools"

    # Windows 下优先找 sqlcipher.exe
    if sys.platform == "win32":
        exe_path = tools_dir / "sqlcipher.exe"
        if exe_path.is_file():
            _sqlcipher_path = str(exe_path)
            return _sqlcipher_path

    # 其他平台或 fallback：找 tools/ 下的 sqlcipher（无扩展名）
    generic_path = tools_dir / "sqlcipher"
    if generic_path.is_file():
        _sqlcipher_path = str(generic_path)
        return _sqlcipher_path

    # 最后尝试系统 PATH
    system_sqlcipher = shutil.which("sqlcipher")
    if system_sqlcipher:
        _sqlcipher_path = system_sqlcipher
        return _sqlcipher_path

    raise RuntimeError(
        "未找到 sqlcipher 可执行文件。\n"
        f"已尝试路径:\n  - {tools_dir / ('sqlcipher.exe' if sys.platform == 'win32' else 'sqlcipher')}\n"
        "  - 系统 PATH\n\n"
        "请将 sqlcipher.exe 放入项目 tools/ 目录，或安装 sqlcipher 并加入 PATH。"
    )


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _decode_json_stream(stdout: str) -> list[dict]:
    if not stdout:
        return []

    decoder = json.JSONDecoder()
    payloads = []
    index = 0
    while index < len(stdout):
        while index < len(stdout) and stdout[index].isspace():
            index += 1
        if index >= len(stdout):
            break
        payload, index = decoder.raw_decode(stdout, index)
        payloads.append(payload)

    if not payloads:
        return []

    if payloads[0] == [{"ok": "ok"}]:
        payloads = payloads[1:]
    if not payloads:
        return []

    return list(payloads[-1])


def run_query(relative_db_path: str, sql: str) -> list[dict]:
    config = get_config()
    db_path = config.resolve_db_path(relative_db_path)
    key_hex = config.get_db_key(relative_db_path)
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", key_hex):
        raise ValueError("SQLCipher key must contain an even number of hex digits")

    sqlcipher = _find_sqlcipher()

    pragma = f'PRAGMA key = "x\'{key_hex}\'";'
    command = [
        sqlcipher,
        str(db_path),
        "-readonly",
        "-json",
        "-bail",
    ]

    result = subprocess.run(
        command,
        input=pragma + "\n" + sql + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"sqlcipher 执行失败:\n{stderr}")

    stdout = result.stdout.strip()
    try:
        return _decode_json_stream(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("无法解析 sqlcipher JSON 输出") from exc
    except (TypeError, ValueError) as exc:
        raise RuntimeError("无法解析 sqlcipher JSON 输出") from exc
