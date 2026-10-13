"""Read-only local sample verification. Reports omit bodies, usernames and keys."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .config import load_config
from .message import message_db_paths, list_session_messages
from .sqlcipher import run_query


def validate(config_path: str, output: Path) -> dict:
    from PIL import Image  # Optional validation dependency, not required for database reading.

    load_config(config_path)
    output.mkdir(parents=True, exist_ok=True)
    users = set()
    for database in message_db_paths():
        users.update(r["user_name"] for r in run_query(database, "SELECT user_name FROM Name2Id"))
    counts = Counter()
    samples = []
    for user in sorted(users):
        rows = list_session_messages(user, limit=None, with_image_paths=True, with_voice_paths=True,
                                     output_dir=str(output / "media"))
        for row in rows:
            counts[row["message_type"]] += 1
            if row["message_type"] not in ("image", "voice", "quote"):
                continue
            sample = {"type": row["message_type"], "source_db": row["source_db"],
                      "decode_status": row["content_decode_status"], "parse_status": row["content_parse_status"]}
            if row["message_type"] == "image":
                resource = row["image"]
                sample.update({k: resource.get(k) for k in ("success", "format", "size", "thumbnail_only", "preview_status")})
                path = resource.get("preview_path") or resource.get("path")
                if path:
                    try:
                        with Image.open(path) as image:
                            image.load()
                            sample.update(pixel_decode="ok", dimensions=list(image.size))
                    except (OSError, ValueError):
                        sample["pixel_decode"] = "failed"
                if resource.get("path"):
                    sample["sha256"] = hashlib.sha256(Path(resource["path"]).read_bytes()).hexdigest()
            elif row["message_type"] == "voice":
                sample["duration_ms"] = row["voice"]["duration_ms"]
                sample["declared_bytes"] = row["voice"]["byte_length"]
                resource = row["voice_resource"]
                sample.update({k: resource.get(k) for k in ("success", "format", "size", "sha256", "decoded")})
                sample["length_matches"] = sample["declared_bytes"] == sample["size"]
            else:
                reference = row.get("reference", {})
                sample.update(reply_chars=len(row.get("reply_text", "")),
                              reference_type=reference.get("type"),
                              reference_chars=len(reference.get("content", "")),
                              has_reference_server_id=bool(reference.get("server_id")))
            samples.append(sample)
    report = {"counts": dict(counts), "samples": samples,
              "limitations": ["Live databases are not an atomic snapshot", "WXGF preview is first frame only",
                              "SILK exported unchanged; no audio decoding or transcription"]}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.config, args.output), ensure_ascii=False, indent=2))
