"""Export a schema-only SQLite reconstruction and aggregate storage statistics.

python -m wxdb.diagnose --output data/runtime/rea-schema
No message bodies, account names or keys are written to the output.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .message import message_db_paths
from .sqlcipher import run_query


def export_schema(output: Path, sample_tables: int = 3) -> dict:
    if sample_tables < 1:
        raise ValueError("sample_tables must be positive")
    output.mkdir(parents=True, exist_ok=False)
    report = {"kind": "schema_reconstruction_not_database_backup", "databases": []}
    with closing(sqlite3.connect(output / "schema.sqlite")) as db, db:
        for shard_index, path in enumerate(message_db_paths()):
            tables = run_query(path, "SELECT name, sql FROM sqlite_master "
                               "WHERE type='table' AND name GLOB 'Msg_*' ORDER BY name")
            shard = {"source_db": path, "message_table_count": len(tables), "samples": []}
            for index, table in enumerate(tables[:sample_tables]):
                original = table["name"]
                alias = f"Msg_sample_{shard_index}_{index}"
                # These are ordinary message tables; export their declarations only.
                ddl = table["sql"].replace(original, alias)
                db.execute(ddl)
                quoted = '"' + original.replace('"', '""') + '"'
                stats = run_query(path, f"SELECT local_type, typeof(message_content) AS storage_type, "
                                  f"count(*) AS row_count, max(length(message_content)) AS max_length "
                                  f"FROM {quoted} GROUP BY local_type, typeof(message_content)")
                shard["samples"].append({"schema_table": alias, "storage_stats": stats})
            report["databases"].append(shard)
    (output / "storage-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--sample-tables", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(export_schema(args.output, args.sample_tables), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
