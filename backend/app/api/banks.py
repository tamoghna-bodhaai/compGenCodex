from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.database import get_connection

router = APIRouter(prefix="/api/banks", tags=["question banks"])


class BankCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class BankPublish(BaseModel):
    question_ids: list[str] = Field(default_factory=list)
    source_job_id: str | None = None
    allow_missing_answers: bool = False


def admin(request: Request) -> None:
    if request.state.user["role"] != "admin":
        raise HTTPException(403, "Only the super admin can publish question banks")


def has_answer(answer_json: str | None) -> bool:
    try:
        value = json.loads(answer_json) if answer_json else None
    except (TypeError, ValueError):
        return False
    answer = value.get("correct_answer") if isinstance(value, dict) else None
    if isinstance(answer, str):
        return bool(answer.strip())
    return answer is not None and answer != [] and answer != {}


@router.get("")
def list_banks(request: Request) -> dict:
    with get_connection() as connection:
        condition = "" if request.state.user["role"] == "admin" else "WHERE b.status='published'"
        rows = connection.execute(f"SELECT b.*, COUNT(q.id) AS question_count FROM question_banks b LEFT JOIN questions q ON q.bank_id=b.id AND q.published=1 {condition} GROUP BY b.id ORDER BY b.name").fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("", status_code=201)
def create_bank(payload: BankCreate, request: Request) -> dict:
    admin(request)
    identifier = str(uuid.uuid4())
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM question_banks WHERE name=?", (payload.name.strip(),)).fetchone():
            raise HTTPException(409, "Bank name already exists")
        connection.execute("INSERT INTO question_banks(id,name,status,created_at) VALUES (?,?,?,?)", (identifier,payload.name.strip(),"draft",datetime.now(UTC).isoformat()))
    return {"id": identifier, "name": payload.name.strip(), "status": "draft"}


@router.post("/{bank_id}/publish")
def publish_bank(bank_id: str, payload: BankPublish, request: Request) -> dict:
    admin(request)
    with get_connection() as connection:
        bank = connection.execute("SELECT * FROM question_banks WHERE id=?", (bank_id,)).fetchone()
        if not bank:
            raise HTTPException(404, "Bank not found")
        ids = list(dict.fromkeys(payload.question_ids))
        if payload.source_job_id:
            ids += [row["id"] for row in connection.execute("SELECT id FROM questions WHERE source_job_id=?", (payload.source_job_id,)) if row["id"] not in ids]
        if not ids:
            raise HTTPException(422, "Select questions or an ingestion source to publish")
        rows = connection.execute(f"SELECT id, answer_json, source_question_number FROM questions WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(422, "Some questions no longer exist")
        missing = [row for row in rows if not has_answer(row["answer_json"])]
        if missing and not payload.allow_missing_answers:
            labels = [f"Q{row['source_question_number']}" if row["source_question_number"] is not None else row["id"] for row in missing]
            shown = ", ".join(labels[:20])
            if len(labels) > 20:
                shown += f" and {len(labels) - 20} more"
            raise HTTPException(422, {"message": f"Missing recorded answers for {shown}. Add answers in Ingestion or choose to publish without them.", "question_ids": [row["id"] for row in missing]})
        missing_ids = {row["id"] for row in missing}
        connection.executemany("UPDATE questions SET bank_id=?, published=1, verification_status='approved' WHERE id=?", [(bank_id, identifier) for identifier in ids if identifier not in missing_ids])
        connection.executemany("UPDATE questions SET bank_id=?, published=1 WHERE id=?", [(bank_id, identifier) for identifier in ids if identifier in missing_ids])
        connection.execute("UPDATE question_banks SET status='published' WHERE id=?", (bank_id,))
    return {"id": bank_id, "published_count": len(ids), "missing_answer_count": len(missing)}
