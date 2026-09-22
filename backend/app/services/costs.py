"""Provider-reported LLM usage ledger. Never stores request or response content."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.db.database import get_connection


def record_llm_cost(*, context: dict[str, Any] | None, response: dict[str, Any], requested_model: str | None) -> None:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    raw_cost = usage.get("cost", response.get("cost"))
    try:
        cost = str(Decimal(str(raw_cost))) if raw_cost is not None else None
    except (InvalidOperation, ValueError):
        cost = None
    context = context or {}
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO llm_cost_ledger (id, job_id, paper_id, operation, phase, slot, attempt, model, provider_generation_id, input_tokens, output_tokens, total_tokens, cost_usd, usage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), context.get("job_id"), context.get("paper_id"), context.get("operation", "unattributed"), context.get("phase", "llm"), context.get("slot"), context.get("attempt"), response.get("model") or requested_model, response.get("id"), usage.get("prompt_tokens", usage.get("input_tokens")), usage.get("completion_tokens", usage.get("output_tokens")), usage.get("total_tokens"), cost, json.dumps({k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens") if usage.get(k) is not None}), datetime.now(UTC).isoformat()),
        )


def cost_summary(*, paper_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    clauses, params = [], []
    if paper_id: clauses.append("paper_id = ?"); params.append(paper_id)
    if job_id: clauses.append("job_id = ?"); params.append(job_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        row = connection.execute(f"SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens, COALESCE(SUM(total_tokens),0) total_tokens, SUM(cost_usd) cost_usd, SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) calls_without_cost FROM llm_cost_ledger {where}", params).fetchone()
    return dict(row)
