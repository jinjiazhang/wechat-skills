from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import re
import struct
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import get_config
from .sqlcipher import run_query
from .sqlcipher import sql_quote


MESSAGE_RESOURCE_DB_PATH = "message/message_resource.db"
V2_MAGIC_FULL = b"\x07\x08V2\x08\x07"
V1_MAGIC_FULL = b"\x07\x08V1\x08\x07"
DEFAULT_V1_AES_KEY = b"cfcd208495d565ef"
DEFAULT_V2_XOR_KEY = 0x88
IMAGE_MAGIC = {
    "png": bytes([0x89, 0x50, 0x4E, 0x47]),
    "gif": b"GIF",
    "bmp": b"BM",
    "webp": b"RIFF",
    "jpg": bytes([0xFF, 0xD8, 0xFF]),
    "tif": bytes([0x49, 0x49, 0x2A, 0x00]),
    "wxgf": b"wxgf",
}

_BCRYPT_AES_ALGORITHM = "AES"
_BCRYPT_CHAINING_MODE = "ChainingMode"
_BCRYPT_CHAIN_MODE_ECB = "ChainingModeECB"
_BCRYPT_OBJECT_LENGTH = "ObjectLength"


if ctypes.sizeof(ctypes.c_void_p) and hasattr(ctypes, "windll"):
    _bcrypt = ctypes.windll.bcrypt
else:
    _bcrypt = None


def extract_md5_from_packed_info(blob: bytes) -> str | None:
    if not blob:
        return None

    marker = b"\x12\x22\x0a\x20"
    idx = blob.find(marker)
    if idx >= 0 and idx + len(marker) + 32 <= len(blob):
        candidate = blob[idx + len(marker): idx + len(marker) + 32]
        try:
            text = candidate.decode("ascii")
            int(text, 16)
            return text.lower()
        except (UnicodeDecodeError, ValueError):
            pass

    hex_chars = set(b"0123456789abcdef")
    for i in range(0, max(0, len(blob) - 31)):
        candidate = blob[i:i + 32]
        if len(candidate) == 32 and all(b in hex_chars for b in candidate):
            return candidate.decode("ascii").lower()
    return None


def _message_resource_md5(local_id: int, session_username: str | None = None,
                          server_id: int | None = None, create_time: int | None = None) -> str | None:
    filters = [f"message_local_id = {int(local_id)}", "(message_local_type & 4294967295) = 3"]
    if session_username:
        filters.append("chat_id = (SELECT rowid FROM ChatName2Id WHERE user_name = "
                       + sql_quote(session_username) + ")")
    if server_id is not None:
        filters.append(f"message_svr_id = {int(server_id)}")
    if create_time is not None:
        filters.append(f"message_create_time = {int(create_time)}")
    sql = f"""
SELECT hex(packed_info) AS packed_info_hex
FROM MessageResourceInfo
WHERE {' AND '.join(filters)}
LIMIT 2;
""".strip()
    rows = run_query(MESSAGE_RESOURCE_DB_PATH, sql)
    if len(rows) != 1:
        return None
    hex_blob = str(rows[0].get("packed_info_hex") or "").strip()
    if not hex_blob:
        return None
    try:
        return extract_md5_from_packed_info(bytes.fromhex(hex_blob))
    except ValueError:
        return None


def _username_hash(session_username: str) -> str:
    return hashlib.md5(session_username.encode("utf-8")).hexdigest()


def _find_dat_files(file_md5: str, session_username: str | None = None) -> list[Path]:
    config = get_config()
    attach_dir = config.attach_dir
    if not attach_dir.exists():
        return []

    patterns: list[Path] = []
    if session_username:
        root = attach_dir / _username_hash(session_username)
        patterns.extend(root.glob(f"*/Img/{file_md5}*.dat"))
    else:
        patterns.extend(attach_dir.rglob(f"{file_md5}*.dat"))

    return sorted(
        [path for path in patterns if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _select_dat_file(candidates: list[Path], file_md5: str) -> Path | None:
    if not candidates:
        return None
    for path in candidates:
        if path.name.endswith("_h.dat"):
            return path
    for path in candidates:
        if path.stem == file_md5:
            return path
    for path in candidates:
        if path.name.endswith("_t.dat"):
            return path
    return candidates[0]


def _detect_xor_key(dat_path: Path) -> int | None:
    header = dat_path.read_bytes()[:16]
    if len(header) < 4 or header[:4] == V2_MAGIC_FULL[:4]:
        return None

    for magic in (IMAGE_MAGIC["png"], IMAGE_MAGIC["jpg"], IMAGE_MAGIC["gif"], IMAGE_MAGIC["tif"]):
        key = header[0] ^ magic[0]
        if all((header[i] ^ key) == magic[i] for i in range(min(len(magic), len(header)))):
            return key

    bmp_magic = IMAGE_MAGIC["bmp"]
    if len(header) >= 2:
        key = header[0] ^ bmp_magic[0]
        if (header[1] ^ key) == bmp_magic[1]:
            return key
    return None


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty data")
    pad = data[-1]
    if pad <= 0 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("invalid PKCS7 padding")
    return data[:-pad]


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    if len(key) != 16 or len(data) % 16 != 0:
        raise ValueError("invalid AES-ECB input")
    if _bcrypt is None:
        raise RuntimeError("AES-ECB decryption is only implemented for Windows in this environment")

    alg = wt.HANDLE()
    key_handle = wt.HANDLE()
    obj_len = wt.DWORD()
    cb_result = wt.DWORD()

    status = _bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), _BCRYPT_AES_ALGORITHM, None, 0)
    if status != 0:
        raise RuntimeError(f"BCryptOpenAlgorithmProvider failed: 0x{status:08x}")

    try:
        mode = ctypes.create_unicode_buffer(_BCRYPT_CHAIN_MODE_ECB)
        status = _bcrypt.BCryptSetProperty(
            alg,
            _BCRYPT_CHAINING_MODE,
            ctypes.cast(mode, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(mode),
            0,
        )
        if status != 0:
            raise RuntimeError(f"BCryptSetProperty failed: 0x{status:08x}")

        status = _bcrypt.BCryptGetProperty(
            alg,
            _BCRYPT_OBJECT_LENGTH,
            ctypes.byref(obj_len),
            ctypes.sizeof(obj_len),
            ctypes.byref(cb_result),
            0,
        )
        if status != 0:
            raise RuntimeError(f"BCryptGetProperty failed: 0x{status:08x}")

        key_object = (ctypes.c_ubyte * obj_len.value)()
        key_buffer = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
        status = _bcrypt.BCryptGenerateSymmetricKey(
            alg,
            ctypes.byref(key_handle),
            key_object,
            obj_len.value,
            key_buffer,
            len(key),
            0,
        )
        if status != 0:
            raise RuntimeError(f"BCryptGenerateSymmetricKey failed: 0x{status:08x}")

        try:
            input_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            output_buffer = (ctypes.c_ubyte * len(data))()
            written = wt.DWORD()
            status = _bcrypt.BCryptDecrypt(
                key_handle,
                input_buffer,
                len(data),
                None,
                None,
                0,
                output_buffer,
                len(data),
                ctypes.byref(written),
                0,
            )
            if status != 0:
                raise RuntimeError(f"BCryptDecrypt failed: 0x{status:08x}")
            return bytes(output_buffer[:written.value])
        finally:
            _bcrypt.BCryptDestroyKey(key_handle)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def _detect_image_format(header: bytes) -> str:
    if header.startswith(IMAGE_MAGIC["jpg"]):
        return "jpg"
    if header.startswith(IMAGE_MAGIC["png"]):
        return "png"
    if header.startswith(IMAGE_MAGIC["gif"]):
        return "gif"
    if header.startswith(IMAGE_MAGIC["bmp"]):
        return "bmp"
    if header.startswith(IMAGE_MAGIC["webp"]) and len(header) >= 12 and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(IMAGE_MAGIC["tif"]):
        return "tif"
    if header.startswith(IMAGE_MAGIC["wxgf"]):
        return "wxgf"
    return "bin"


def decrypt_dat_file(dat_path: str | Path, out_path: str | Path | None = None) -> tuple[Path, str]:
    config = get_config()
    path = Path(dat_path)
    data = path.read_bytes()
    if len(data) < 6:
        raise ValueError(f"图片文件过小: {path}")

    if data.startswith(V2_MAGIC_FULL) or data.startswith(V1_MAGIC_FULL):
        if len(data) < 15:
            raise ValueError("truncated V1/V2 header")
        aes_key_text = config.image_aes_key
        if data.startswith(V1_MAGIC_FULL):
            aes_key = DEFAULT_V1_AES_KEY
        else:
            if not aes_key_text:
                raise RuntimeError("wxdb.json 缺少 image_aes_key，无法解密 V2 图片")
            aes_key = aes_key_text.encode("ascii")
            if len(aes_key) != 16:
                raise ValueError("image_aes_key must be exactly 16 ASCII bytes")

        xor_key = int(config.image_xor_key if config.image_xor_key is not None else DEFAULT_V2_XOR_KEY)
        aes_size, xor_size = struct.unpack_from("<LL", data, 6)
        aligned_aes_size = (aes_size // 16 + 1) * 16 if aes_size else 0
        if not 0 <= xor_key <= 255 or 15 + aligned_aes_size + xor_size > len(data):
            raise ValueError("invalid V1/V2 segment sizes or XOR key")
        offset = 15
        aes_data = data[offset:offset + aligned_aes_size]
        offset += aligned_aes_size
        raw_end = len(data) - xor_size
        raw_data = data[offset:raw_end] if offset < raw_end else b""
        xor_data = data[raw_end:]
        decrypted_aes = _pkcs7_unpad(_aes_ecb_decrypt(aes_data, aes_key)) if aes_size else b""
        if len(decrypted_aes) != aes_size:
            raise ValueError("V1/V2 AES plaintext length mismatch")
        decrypted_xor = bytes(byte ^ xor_key for byte in xor_data)
        decrypted = decrypted_aes + raw_data + decrypted_xor
    else:
        xor_key = _detect_xor_key(path)
        if xor_key is None:
            raise RuntimeError(f"无法检测 XOR key: {path}")
        decrypted = bytes(byte ^ xor_key for byte in data)

    fmt = _detect_image_format(decrypted[:16])
    if fmt == "bin":
        raise ValueError("decrypted bytes have no recognized image signature")
    final_path = Path(out_path) if out_path is not None else path.with_suffix(f".{fmt}")
    if final_path.resolve() == path.resolve():
        raise ValueError("image output must not overwrite the encrypted source")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(decrypted)
    return final_path, fmt


def wxgf_first_frame(source: str | Path) -> Path:
    """Decode a WXGF embedded HEVC stream; animation/alpha reconstruction is unsupported."""
    source = Path(source)
    data = source.read_bytes()
    if not data.startswith(b"wxgf"):
        raise ValueError("not a WXGF container")
    offsets = [data.find(m) for m in (b"\x00\x00\x00\x01\x40", b"\x00\x00\x01\x40")]
    offsets = [n for n in offsets if n >= 4]
    if not offsets:
        raise ValueError("WXGF contains no supported HEVC VPS start code")
    executable = shutil.which("ffmpeg")
    if executable is None:
        try:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as exc:
            raise RuntimeError("WXGF preview requires ffmpeg or imageio-ffmpeg") from exc
    with tempfile.TemporaryDirectory(prefix="wxgf-") as directory:
        stream = Path(directory) / "stream.hevc"
        preview = Path(directory) / "frame.png"
        stream.write_bytes(data[min(offsets):])
        result = subprocess.run([executable, "-v", "error", "-nostdin", "-f", "hevc", "-i", str(stream),
                                 "-frames:v", "1", "-y", str(preview)], capture_output=True,
                                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not preview.exists() or not preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("WXGF first-frame decoding failed")
        target = source.with_suffix(".preview.png")
        target.write_bytes(preview.read_bytes())
    return target


def resolve_image(local_id: int, session_username: str | None = None, output_dir: str | Path | None = None,
                  *, server_id: int | None = None, create_time: int | None = None,
                  content_md5: str | None = None) -> dict:
    file_md5 = None
    if MESSAGE_RESOURCE_DB_PATH in get_config().db_keys:
        file_md5 = _message_resource_md5(int(local_id), session_username, server_id, create_time)
    if not file_md5 and content_md5 and re.fullmatch(r"[a-fA-F0-9]{32}", content_md5):
        file_md5 = content_md5.lower()
    if not file_md5:
        return {"success": False, "error": f"找不到 local_id={local_id} 对应的图片资源"}

    candidates = _find_dat_files(file_md5, session_username=session_username)
    selected = _select_dat_file(candidates, file_md5)
    if selected is None:
        return {"success": False, "error": f"找不到 MD5={file_md5} 对应的 .dat 文件", "md5": file_md5}

    config = get_config()
    target_dir = Path(output_dir) if output_dir is not None else config.images_dir / _username_hash(session_username or "unknown")
    target_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{local_id}_{file_md5}"

    try:
        image_path, fmt = decrypt_dat_file(selected, target_dir / f"{base_name}.tmp")
        final_path = image_path.with_suffix(f".{fmt}")
        if image_path != final_path:
            if final_path.exists():
                final_path.unlink()
            image_path.replace(final_path)
        else:
            final_path = image_path

        for sibling in target_dir.glob(f"{base_name}.*"):
            if sibling == final_path:
                continue
            if sibling.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".hevc", ".bin", ".tmp"}:
                sibling.unlink(missing_ok=True)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "md5": file_md5,
            "source": str(selected),
        }

    result = {
        "success": True,
        "local_id": int(local_id),
        "md5": file_md5,
        "source": str(selected),
        "path": str(final_path),
        "format": fmt,
        "size": final_path.stat().st_size,
        "thumbnail_only": selected.name.endswith("_t.dat"),
    }
    if fmt == "wxgf":
        try:
            result["preview_path"] = str(wxgf_first_frame(final_path))
            result["preview_status"] = "first_frame_only"
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            result["preview_status"] = "unavailable"
            result["preview_error"] = str(exc)
    return result


def list_session_images(session_username: str, limit: int = 20, output_dir: str | Path | None = None) -> list[dict]:
    from .message import _message_table_exists, _message_table_name, _limit_clause, message_db_paths

    _limit_clause(limit)
    if limit == 0:
        return []
    table_name = _message_table_name(session_username)

    sql = f"""
SELECT
    local_id,
    server_id,
    create_time,
    local_type
FROM {table_name}
WHERE (local_type = 3 OR local_type % 4294967296 = 3)
ORDER BY create_time DESC, local_id DESC
LIMIT {int(limit)};
""".strip()
    rows = []
    for path in message_db_paths():
        if _message_table_exists(table_name, path):
            rows.extend(dict(r, source_db=path) for r in run_query(path, sql))
    rows.sort(key=lambda r: (int(r["create_time"]), int(r["local_id"]), r["source_db"]), reverse=True)
    results = []
    for row in rows[:limit]:
        image = resolve_image(int(row["local_id"]), session_username=session_username, output_dir=output_dir,
                              server_id=int(row["server_id"]), create_time=int(row["create_time"]))
        image["source_db"] = row["source_db"]
        image["create_time"] = row.get("create_time")
        image["local_type"] = row.get("local_type")
        results.append(image)
    return results
