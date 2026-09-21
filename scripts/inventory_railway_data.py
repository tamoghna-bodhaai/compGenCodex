"""Create a transfer manifest without modifying the source database or files.

Example:
  PYTHONPATH=backend python scripts/inventory_railway_data.py \
    --database data/question_generator.db --exports output/exports --manifest railway-import.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".tex"}
TABLES = ("questions", "papers", "paper_questions", "ingestion_jobs", "generation_logs", "branding_profiles")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory existing data before moving it to a Railway Volume.")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        raise SystemExit(f"Database not found: {args.database}")
    with sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True) as connection:
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in table_names else 0 for table in TABLES}
    root = args.exports.resolve()
    files = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                files.append({
                    "relative_path": str(path.relative_to(root)), "filename": path.name,
                    "media_type": mimetypes.guess_type(path.name)[0] or "text/plain",
                    "byte_size": path.stat().st_size, "sha256": sha256(path),
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                })
    payload = {"version": 1, "created_at": datetime.now(UTC).isoformat(), "database": {"source": str(args.database), "sha256": sha256(args.database), "table_counts": counts}, "exports": files}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.manifest}: {len(files)} supported exports; {counts}")


if __name__ == "__main__":
    main()
