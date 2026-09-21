from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.db.database import get_connection


class ExportNotFoundError(RuntimeError):
    pass


def export_root() -> Path:
    """Return the durable export location when Railway's API volume is mounted."""
    configured = os.getenv("EXPORT_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "output" / "exports"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_relative(path: Path) -> str:
    root = export_root().resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError as error:
        raise ValueError("Export file must be located under EXPORT_ROOT.") from error


def register_export(*, path: Path, media_type: str, paper_id: str | None, kind: str = "paper", created_at: str | None = None) -> dict:
    if kind not in {"paper", "legacy"}:
        raise ValueError("Unsupported export kind.")
    if not path.is_file():
        raise ValueError("Export file does not exist.")
    relative_path = _as_relative(path)
    item = {
        "id": str(uuid.uuid4()), "paper_id": paper_id, "kind": kind,
        "filename": path.name, "relative_path": relative_path, "media_type": media_type,
        "byte_size": path.stat().st_size, "sha256": _sha256(path), "created_at": created_at or _now(),
    }
    with get_connection() as connection:
        existing = connection.execute("SELECT * FROM paper_exports WHERE relative_path = ?", (relative_path,)).fetchone()
        if existing is not None:
            if existing["sha256"] != item["sha256"]:
                raise ValueError("An archived file changed after it was registered.")
            return dict(existing)
        connection.execute(
            "INSERT INTO paper_exports (id, paper_id, kind, filename, relative_path, media_type, byte_size, sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(item.values()),
        )
    return item


def list_exports(*, paper_id: str | None = None, kind: str | None = None) -> list[dict]:
    clauses, params = [], []
    if paper_id is not None:
        clauses.append("paper_id = ?")
        params.append(paper_id)
    if kind is not None:
        if kind not in {"paper", "legacy"}:
            raise ValueError("Unsupported export kind.")
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(
            f"SELECT * FROM paper_exports {where} ORDER BY created_at DESC", params
        ).fetchall()]


def resolve_export(export_id: str) -> tuple[dict, Path]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM paper_exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise ExportNotFoundError("Export not found")
    item = dict(row)
    root = export_root().resolve()
    path = (root / item["relative_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise ExportNotFoundError("Export file is unavailable")
    return item, path
