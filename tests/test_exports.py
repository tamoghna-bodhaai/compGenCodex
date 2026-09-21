from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.api.exports import download_export, get_exports
from app.services.exports import ExportNotFoundError, list_exports, register_export, resolve_export


class ExportArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous_env = dict(os.environ)
        root = Path(self.tmp.name)
        os.environ["DATABASE_URL"] = f"sqlite:///{root / 'data' / 'archive.db'}"
        os.environ["EXPORT_ROOT"] = str(root / "exports")
        Path(os.environ["EXPORT_ROOT"]).mkdir()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.previous_env)

    def test_registers_legacy_export_idempotently_and_lists_it(self) -> None:
        path = Path(os.environ["EXPORT_ROOT"]) / "legacy" / "paper.pdf"
        path.parent.mkdir()
        path.write_bytes(b"test pdf")
        first = register_export(path=path, media_type="application/pdf", paper_id=None, kind="legacy")
        second = register_export(path=path, media_type="application/pdf", paper_id=None, kind="legacy")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual([item["filename"] for item in list_exports(kind="legacy")], ["paper.pdf"])
        self.assertEqual(get_exports(kind="legacy")["items"][0]["relative_path"], "legacy/paper.pdf")

    def test_resolve_rejects_missing_or_escaped_archive_paths(self) -> None:
        root = Path(os.environ["EXPORT_ROOT"])
        path = root / "paper.docx"
        path.write_bytes(b"document")
        record = register_export(path=path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", paper_id=None)
        path.unlink()
        with self.assertRaises(ExportNotFoundError):
            resolve_export(record["id"])
        with self.assertRaises(HTTPException) as raised:
            download_export("unknown")
        self.assertEqual(raised.exception.status_code, 404)

    def test_database_reopens_with_persisted_export_metadata(self) -> None:
        path = Path(os.environ["EXPORT_ROOT"]) / "paper.tex"
        path.write_text("\\documentclass{article}", encoding="utf-8")
        record = register_export(path=path, media_type="text/x-tex", paper_id=None)
        self.assertEqual(resolve_export(record["id"])[1], path.resolve())
        self.assertEqual(list_exports()[0]["sha256"], record["sha256"])
