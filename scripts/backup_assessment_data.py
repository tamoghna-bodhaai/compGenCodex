#!/usr/bin/env python3
"""Create an offline SQLite and assessment-media backup archive.

Stop writers before running so the database, immutable test media, source
media, and exports are captured at a consistent point in time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def backup(database: Path, destination: Path, media: Path, legacy_media: Path | None = None, exports: Path | None = None) -> None:
    database = database.resolve()
    if not database.is_file():
        raise ValueError("Database does not exist")
    if destination.exists():
        raise ValueError("Destination already exists")
    roots = [("media", media), ("legacy-diagrams", legacy_media), ("exports", exports)]
    roots = [(name, root.resolve()) for name, root in roots if root is not None and root.exists()]
    destination = destination.resolve()
    if any(destination.is_relative_to(root) for _, root in roots):
        raise ValueError("Destination must be outside the directories being backed up")
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / "database.sqlite3"
        source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity check failed")
        finally:
            target.close()
            source.close()
        manifest = {"created_at": datetime.now(UTC).isoformat(), "database_path": str(database), "roots": {name: str(root) for name, root in roots}, "sha256": {}}
        created = False
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as output, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                def add(path: Path, name: str) -> None:
                    digest = hashlib.sha256()
                    with path.open("rb") as source_file, archive.open(name, "w") as target_file:
                        while chunk := source_file.read(1024 * 1024):
                            target_file.write(chunk)
                            digest.update(chunk)
                    manifest["sha256"][name] = digest.hexdigest()
                add(snapshot, "database.sqlite3")
                for name, root in roots:
                    for path in sorted(root.rglob("*")):
                        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                            add(path, f"{name}/{path.relative_to(root).as_posix()}")
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-media", type=Path)
    parser.add_argument("--exports", type=Path)
    args = parser.parse_args()
    backup(args.database, args.output, args.database.resolve().parent / "media", args.legacy_media, args.exports)


if __name__ == "__main__":
    main()
