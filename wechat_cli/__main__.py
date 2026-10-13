import sys
from .cli import run

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('keys', 'records', 'media', 'send', 'agent'):
        print('Usage: python -m wechat_cli {keys,records,media,send,agent} --help', file=sys.stderr)
        sys.exit(2)
    sys.exit(run(sys.argv[1], sys.argv[2:]))
