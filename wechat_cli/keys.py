from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def write_config(path: Path, payload: dict, force: bool = False) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create by default; explicit --force permits atomic replacement.
    if not force:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.wxdb-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def discover(base: Path) -> list[dict]:
    return [{'account': p.parent.name, 'db_dir': str(p.resolve()),
             'database_count': sum(1 for _ in p.rglob('*.db'))}
            for p in sorted(base.glob('*/db_storage')) if p.is_dir()]


def extract(db_dir: Path, output: Path, force: bool, timeout: int) -> dict:
    if sys.platform != 'win32':
        raise ValueError('key extraction requires Windows')
    db_dir = db_dir.resolve()
    if db_dir.name != 'db_storage' or not db_dir.is_dir():
        raise ValueError('--db-dir must name an existing account/db_storage directory')
    if output.exists() and not force:
        raise FileExistsError('output exists; use a new path or --force')
    with tempfile.TemporaryDirectory(prefix='wechat-keys-') as temporary:
        raw = Path(temporary) / 'keys.json'
        result = subprocess.run([sys.executable, '-m', 'vendor.extract_worker', str(db_dir), str(raw)],
                                capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Worker diagnostics may contain candidate bytes. Never forward them to agent stdout/stderr.
        if result.returncode or not raw.exists():
            raise RuntimeError('key extraction failed; keep WeChat logged in and verify process read access')
        payload = json.loads(raw.read_text(encoding='utf-8'))
        keys = {k.replace('\\', '/'): {'enc_key': v['enc_key']} for k, v in payload.items()
                if isinstance(v, dict) and re.fullmatch('[0-9a-fA-F]{64}', v.get('enc_key', ''))}
    if not any(re.fullmatch(r'message/message_\d+\.db', k) for k in keys):
        raise RuntimeError('no verified message database key found; configuration not written')
    account = db_dir.parent
    write_config(output, {'wxid': account.name, 'base_dir': str(account.parent), 'db_keys': keys}, force)
    missing = sorted(str(p.relative_to(db_dir)).replace('\\', '/') for p in db_dir.rglob('*.db')
                     if str(p.relative_to(db_dir)).replace('\\', '/') not in keys)
    return {'config': str(output.resolve()), 'account': account.name, 'verified_databases': len(keys),
            'missing_databases': missing, 'complete': not missing}


def image_key(config_path: Path, output: Path, force: bool, statistic_dir: Path) -> dict:
    from PIL import Image
    from wxdb import config as cfg
    from wxdb.image import V2_MAGIC_FULL, _aes_ecb_decrypt, _detect_image_format, decrypt_dat_file
    from wxdb.message import message_db_paths
    from wxdb.sqlcipher import run_query

    original = cfg.load_config(config_path)
    if output.exists() and not force:
        raise FileExistsError('output exists; use a new path or --force')
    identities = {original.wxid, original.wxid.rsplit('_', 1)[0]}
    for database in message_db_paths():
        identities.update(r['user_name'] for r in run_query(database, 'SELECT user_name FROM Name2Id'))
    candidates = set()
    for file in statistic_dir.glob('key_*_*.statistic'):
        match = re.match(r'key_(\d+)_', file.name)
        if match:
            code = match.group(1)
            candidates.update((hashlib.md5((code + identity).encode()).hexdigest()[:16], int(code) & 255)
                              for identity in identities)
    samples = sorted(original.attach_dir.rglob('*_t.dat'), key=lambda p: p.stat().st_mtime, reverse=True)[:16]
    verified = set()
    try:
        with tempfile.TemporaryDirectory(prefix='wechat-image-key-') as temporary:
            for sample in samples:
                with sample.open('rb') as stream:
                    head = stream.read(31)
                if not head.startswith(V2_MAGIC_FULL) or len(head) < 31:
                    continue
                for key, xor in candidates:
                    if _detect_image_format(_aes_ecb_decrypt(head[15:31], key.encode())) == 'bin':
                        continue
                    cfg._global_config = replace(original, image_aes_key=key, image_xor_key=xor)
                    try:
                        decoded, _ = decrypt_dat_file(sample, Path(temporary) / 'test.img')
                        with Image.open(decoded) as image:
                            image.load()
                        verified.add((key, xor))
                    except (ValueError, OSError, RuntimeError):
                        continue
    finally:
        cfg._global_config = original
    if len(verified) != 1:
        raise RuntimeError('expected one verified image key; open a recent image in WeChat and retry')
    key, xor = verified.pop()
    payload = json.loads(config_path.read_text(encoding='utf-8'))
    payload.update(image_aes_key=key, image_xor_key=xor)
    write_config(output, payload, force)
    return {'config': str(output.resolve()), 'image_key_verified': True, 'samples_checked': len(samples)}
