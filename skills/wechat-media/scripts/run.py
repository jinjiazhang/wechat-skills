"""Repository launcher; installed CLI is preferred when moving the skill folder."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[3]
if (root / 'wechat_cli').is_dir():
    sys.path.insert(0, str(root))
from wechat_cli.cli import run
if __name__ == '__main__':
    sys.exit(run('media'))
