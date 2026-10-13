from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_global_config: "WeChatDBConfig | None" = None
CONFIG_NAME = "wxdb.json"
DEFAULT_CONFIG = Path(os.environ.get("WECHAT_CONFIG", Path(__file__).resolve().parent.parent / "state" / CONFIG_NAME))


@dataclass(frozen=True)
class WeChatDBConfig:
    wxid: str
    base_dir: Path
    db_keys: dict[str, dict[str, str]]
    image_aes_key: str | None = None
    image_xor_key: int | None = None

    @property
    def account_root(self) -> Path:
        return self.base_dir / self.wxid

    @property
    def attach_dir(self) -> Path:
        return self.account_root / "msg" / "attach"

    @property
    def images_dir(self) -> Path:
        return Path(os.environ.get("WECHAT_STATE", Path(__file__).resolve().parent.parent / "state")) / "images"

    def resolve_db_path(self, relative_db_path: str) -> Path:
        account_root = self.base_dir / self.wxid
        candidates = [
            account_root / relative_db_path,
            account_root / "db_storage" / relative_db_path,
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise FileNotFoundError(
            "未找到数据库文件，已尝试路径:\n"
            + "\n".join(f"- {candidate}" for candidate in candidates)
        )

    def get_db_key(self, relative_db_path: str) -> str:
        try:
            return self.db_keys[relative_db_path]["enc_key"]
        except KeyError as exc:
            raise KeyError(f"{CONFIG_NAME} 缺少 {relative_db_path} 的 enc_key 配置") from exc


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> WeChatDBConfig:
    global _global_config
    _global_config = _read_config(config_path)
    return _global_config


def get_config() -> WeChatDBConfig:
    if _global_config is None:
        return load_config(DEFAULT_CONFIG)
    return _global_config


def _read_config(config_path: str | Path) -> WeChatDBConfig:
    path = _resolve_config_path(config_path)
    payload = _load_payload(path)
    wxid = _require_str(payload, "wxid", path)
    base_dir = Path(_require_str(payload, "base_dir", path)).expanduser()
    db_keys = _require_object(payload, "db_keys", path)
    image_aes_key = _optional_str(payload.get("image_aes_key"))
    image_xor_key = _optional_int(payload.get("image_xor_key"))
    return WeChatDBConfig(
        wxid=wxid,
        base_dir=base_dir,
        db_keys=_normalize_db_keys(db_keys),
        image_aes_key=image_aes_key,
        image_xor_key=image_xor_key,
    )


def _load_payload(merged_path: Path) -> dict[str, Any]:
    return _load_json_object(merged_path)


def _resolve_config_path(config_path: str | Path) -> Path:
    path = Path(config_path)
    if path.is_dir():
        return path / CONFIG_NAME
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"配置文件必须是 JSON 对象: {path}")
    return payload


def _require_str(payload: dict[str, Any], key: str, path: Path) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise KeyError(f"{path.name} 缺少必要字段: {key}")
    return value


def _require_object(payload: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise KeyError(f"{path.name} 缺少必要字段: {key}")
    return value


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{CONFIG_NAME} 中的 image_xor_key 必须是整数。") from exc


def _normalize_db_keys(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}
    for relative_path, item in payload.items():
        if not isinstance(item, dict):
            raise ValueError(f"{CONFIG_NAME} 中 {relative_path} 的配置必须是对象。")
        enc_key = str(item.get("enc_key") or "").strip()
        if not enc_key:
            raise ValueError(f"{CONFIG_NAME} 中 {relative_path} 缺少 enc_key。")
        normalized[str(relative_path).strip()] = {"enc_key": enc_key}
    return normalized
