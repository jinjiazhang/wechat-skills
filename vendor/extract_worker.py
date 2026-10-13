"""Private worker: expose only the read-only scanner, never debugger capture."""
import sys
from .wcdb_key_tool_windows import _scan_memory_raw_key
if __name__ == "__main__":
    _scan_memory_raw_key(sys.argv[1], sys.argv[2])
