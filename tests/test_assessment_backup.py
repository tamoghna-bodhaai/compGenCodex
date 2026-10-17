from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.backup_assessment_data import backup


class AssessmentBackupTests(unittest.TestCase):
    def test_archive_contains_database_and_media_with_checksums_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "database.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('present')")
            media = root / "media"
            media.mkdir()
            (media / "diagram.png").write_bytes(b"diagram")
            output = root / "backup.zip"
            backup(database, output, media)
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(archive.read("media/diagram.png"), b"diagram")
                self.assertEqual(manifest["sha256"]["media/diagram.png"], hashlib.sha256(b"diagram").hexdigest())
                snapshot = root / "restored.sqlite3"
                snapshot.write_bytes(archive.read("database.sqlite3"))
            with sqlite3.connect(snapshot) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "present")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            with self.assertRaises(ValueError): backup(database, output, media)
            self.assertEqual(output.read_bytes(), before)
