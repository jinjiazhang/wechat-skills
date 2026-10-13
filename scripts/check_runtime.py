"""Verify bundled tools and imports without reading account secrets."""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if sys.platform != 'win32':
        raise RuntimeError('Bundled runtime supports Windows x64')
    manifest = json.loads((ROOT / 'tools/manifest.json').read_text(encoding='utf-8'))
    for filename, digest in manifest['sha256'].items():
        actual = hashlib.sha256((ROOT / 'tools' / filename).read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError('Runtime checksum mismatch: ' + filename)
    import zstandard
    from PIL import Image
    import imageio_ffmpeg
    import wxdb
    import wechat_cli
    assert Path(wxdb.__file__).resolve().is_relative_to(ROOT)
    assert Path(wechat_cli.__file__).resolve().is_relative_to(ROOT)
    process = subprocess.run([str(ROOT / 'tools/sqlcipher.exe'), '-version'],
                             check=True, capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    print(json.dumps({'ok': True, 'sqlcipher': process.stdout.strip(),
                      'python': sys.executable, 'repository': str(ROOT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
