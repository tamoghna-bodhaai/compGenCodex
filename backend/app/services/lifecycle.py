"""Safe, structured operational events for long-running application work.

Event metadata is deliberately a small allow-list.  Do not add prompts,
question content, uploaded-source data, provider responses, or exception text.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.database import get_connection

logger = logging.getLogger("app.lifecycle")
if not logger.handlers:
    _stdout_handler = logging.StreamHandler(sys.stdout)
    _stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_stdout_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
SAFE_FAILURE_CODES = {
    "provider": "provider_failure", "timeout": "provider_timeout", "json": "invalid_schema",
    "schema": "invalid_schema", "similar": "similarity_limit", "ambiguous": "validation_ambiguous",
    "answer": "validation_answer_mismatch", "cancel": "cancelled", "config": "configuration_error",
}


def failure_code(error: object | None) -> str | None:
    if error is None:
        return None
    text = str(error).lower()
    return next((code for key, code in SAFE_FAILURE_CODES.items() if key in text), "operation_failed")


def emit_event(*, job_id: str | None, paper_id: str | None, operation: str, phase: str,
               outcome: str, slot: int | None = None, attempt: int | None = None,
               model: str | None = None, model_role: str | None = None,
               failure: object | None = None, duration_ms: int | None = None,
               details: dict[str, bool | int | str | None] | None = None) -> dict[str, Any]:
    """Write a redacted event to SQLite and Railway-compatible JSON stdout."""
    event = {
        "id": str(uuid.uuid4()), "job_id": job_id, "paper_id": paper_id,
        "operation": operation, "phase": phase, "outcome": outcome, "slot": slot,
        "attempt": attempt, "model": model, "model_role": model_role,
        "failure_code": failure_code(failure), "duration_ms": duration_ms,
        "details_json": json.dumps(details or {}, sort_keys=True), "created_at": datetime.now(UTC).isoformat(),
    }
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO lifecycle_events (id, job_id, paper_id, operation, phase, outcome, slot, attempt, model, model_role, failure_code, duration_ms, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(event.values()),
        )
    logger.info(json.dumps({"event": "lifecycle"} | {k: v for k, v in event.items() if k != "details_json"} | {"details": details or {}}, sort_keys=True))
    return event


def events_for_job(job_id: str, limit: int = 500) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM lifecycle_events WHERE job_id = ? ORDER BY created_at, id LIMIT ?", (job_id, limit)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json") or "{}")
        result.append(item)
    return result


def cleanup_events(days: int = 30) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with get_connection() as connection:
        connection.execute("DELETE FROM lifecycle_events WHERE created_at < ?", (cutoff,))
