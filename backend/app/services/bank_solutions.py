"""Bulk and individual solution generation for supported seed formats."""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from app.db.database import decode_question_row, get_connection
from app.schemas.assessment import ResponseSpec, validate_answer
from app.services.generation import GenerationService
from app.core.error_safety import safe_error_message


def _now() -> str:
    return datetime.now(UTC).isoformat()


def queue_solution_job(*, source_job_id: str | None = None, question_ids: list[str] | None = None,
                       subject: str | None = None, chapter: str | None = None, topic: str | None = None,
                       subtopic: str | None = None, replace_existing: bool = False) -> dict:
    if replace_existing and not question_ids:
        raise ValueError("Retry requires explicit question IDs.")
    clauses = ["(response_spec_json IS NULL OR json_extract(response_spec_json, '$.supported_for_generation') = 1)"]
    if replace_existing:
        clauses.append("COALESCE(solution_provenance, '') != 'source'")
    else:
        clauses.append("(solution IS NULL OR solution = '')")
    params: list = []
    for column, value in (("source_job_id", source_job_id), ("subject", subject), ("chapter", chapter), ("topic", topic), ("subtopic", subtopic)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if question_ids:
        clauses.append(f"id IN ({','.join('?' for _ in question_ids)})")
        params.extend(question_ids)
    with get_connection() as connection:
        if source_job_id:
            active = connection.execute(
                "SELECT id FROM bank_solution_jobs WHERE source_job_id = ? AND state IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (source_job_id,),
            ).fetchone()
            if active:
                return {**get_solution_job(active["id"]), "reused": True}
        rows = connection.execute(
            f"SELECT id FROM questions WHERE {' AND '.join(clauses)} ORDER BY source_question_number, id",
            params,
        ).fetchall()
        ids = [row["id"] for row in rows]
        identifier, now = str(uuid.uuid4()), _now()
        connection.execute("INSERT INTO bank_solution_jobs (id, source_job_id, scope_json, state, total_questions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (identifier, source_job_id, json.dumps({"question_ids": ids}), "queued" if ids else "succeeded", len(ids), now, now))
        connection.executemany(
            "INSERT INTO bank_solution_job_items (job_id, question_id, position, state, updated_at) VALUES (?, ?, ?, 'queued', ?)",
            ((identifier, question_id, position, now) for position, question_id in enumerate(ids)),
        )
    return get_solution_job(identifier)


def get_solution_job(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM bank_solution_jobs WHERE id = ?", (job_id,)).fetchone()
        items = connection.execute(
            "SELECT question_id, state, error_message FROM bank_solution_job_items WHERE job_id = ? ORDER BY position",
            (job_id,),
        ).fetchall()
    if row is None:
        raise LookupError("Solution job not found.")
    result = dict(row)
    result["scope"] = json.loads(result.pop("scope_json"))
    result["items"] = [dict(item) for item in items]
    return result


def latest_source_solution_job(source_job_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id FROM bank_solution_jobs WHERE source_job_id = ? ORDER BY created_at DESC LIMIT 1",
            (source_job_id,),
        ).fetchone()
    return get_solution_job(row["id"]) if row else None


async def run_solution_job(job_id: str) -> None:
    job = get_solution_job(job_id)
    with get_connection() as connection:
        connection.execute("UPDATE bank_solution_jobs SET state = 'running', updated_at = ? WHERE id = ?", (_now(), job_id))
    service = GenerationService()
    failures = []
    for identifier in job["scope"]["question_ids"]:
        with get_connection() as connection:
            connection.execute(
                "UPDATE bank_solution_job_items SET state = 'running', updated_at = ? WHERE job_id = ? AND question_id = ?",
                (_now(), job_id, identifier),
            )
        item_error = None
        try:
            with get_connection() as connection:
                row = connection.execute("SELECT * FROM questions WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                raise LookupError("Question was deleted before its solution could be generated.")
            question = decode_question_row(row)
            spec = ResponseSpec.model_validate(question["response_spec"])
            if not spec.supported_for_generation:
                raise ValueError("This question is not eligible for solution generation.")
            result = await service.generate_solution({**question["question_json"], "response_spec": spec.model_dump()}, exam=question["exam"], subject=question["subject"])
            answer = result.correct_answer
            if spec.mode == "select" and spec.max_selections != 1 and isinstance(answer, str):
                answer = [part.strip().upper() for part in re.split(r"[,/&+]", answer)]
            expected = (question.get("answer_json") or {}).get("correct_answer") if question.get("answer_provenance") == "source" else None
            if not validate_answer(spec, answer, len(question["question_json"].get("options", [])), expected=expected):
                raise ValueError("Generated answer violates the stored response rule.")
            with get_connection() as connection:
                if question.get("answer_provenance") == "source":
                    connection.execute("UPDATE questions SET solution = ?, solution_provenance = 'ai', verification_status = 'pending_review', updated_at = ? WHERE id = ?",
                                       (result.solution, _now(), identifier))
                else:
                    connection.execute("UPDATE questions SET answer_json = ?, solution = ?, answer_provenance = 'ai', solution_provenance = 'ai', verification_status = 'pending_review', updated_at = ? WHERE id = ?",
                                       (json.dumps({"correct_answer": answer}) if answer is not None else None, result.solution, _now(), identifier))
        except Exception as error:  # Continue the batch while reporting failures.
            item_error = safe_error_message(error)
            failures.append(f"{identifier}: {item_error}")
        finally:
            with get_connection() as connection:
                connection.execute(
                    "UPDATE bank_solution_job_items SET state = ?, error_message = ?, updated_at = ? WHERE job_id = ? AND question_id = ?",
                    ("failed" if item_error else "succeeded", item_error, _now(), job_id, identifier),
                )
                connection.execute("UPDATE bank_solution_jobs SET completed_questions = completed_questions + 1, updated_at = ? WHERE id = ?", (_now(), job_id))
    with get_connection() as connection:
        connection.execute("UPDATE bank_solution_jobs SET state = ?, error_message = ?, updated_at = ? WHERE id = ?", ("failed" if failures else "succeeded", "; ".join(failures)[:1000] if failures else None, _now(), job_id))
