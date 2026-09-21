"""Register already-uploaded legacy files from an inventory manifest.

Run inside the API service after copying the manifest-matched archive into
EXPORT_ROOT. Re-running is safe because relative paths are unique.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.db.database import initialize_database
from app.services.exports import register_export


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy Railway export metadata.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--export-root", type=Path, default=Path(os.getenv("EXPORT_ROOT", "/data/exports")))
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    os.environ["EXPORT_ROOT"] = str(args.export_root.resolve())
    initialize_database()
    imported = 0
    for item in payload.get("exports", []):
        path = args.export_root / item["relative_path"]
        if not path.is_file():
            raise SystemExit(f"Missing archive file: {path}")
        record = register_export(path=path, media_type=item["media_type"], paper_id=None, kind="legacy", created_at=item["created_at"])
        if record["sha256"] != item["sha256"]:
            raise SystemExit(f"Checksum mismatch: {path}")
        imported += 1
    print(f"Registered {imported} legacy exports.")


if __name__ == "__main__":
    main()
