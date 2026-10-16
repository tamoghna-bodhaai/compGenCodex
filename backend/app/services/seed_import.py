from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.db.database import get_connection
from app.schemas.generation import QuestionType


def normalize_options(options: object) -> object:
    """Accept common labelled-option JSON while persisting the engine's string list.

    LLM and OCR conversion tools often emit ``{"id": "a", "text": "..."}``
    option objects. The bank does not need the display id because option order is
    authoritative, but accepting this shape avoids a lossy manual rewrite.
    Invalid structures remain unchanged so ``validate_question`` can reject
    them with a clear error.
    """
    if not isinstance(options, list):
        return options
    if all(isinstance(option, dict) and isinstance(option.get("text"), str) for option in options):
        return [option["text"] for option in options]
    return options


def mark_unreadable_ocr_options(question: dict) -> None:
    """Keep scanned MCQs structurally valid without inventing missing text."""
    options = question["question_json"].get("options")
    if question.get("verification_status") != "transcription_pending" or not isinstance(options, list):
        return
    unreadable_count = sum(isinstance(option, str) and not option.strip() for option in options)
    if not unreadable_count:
        return
    question["question_json"]["options"] = [
        "[Option unreadable in source scan]" if isinstance(option, str) and not option.strip() else option
        for option in options
    ]
    note = "One or more options were unreadable in the source scan and require review."
    question["transcription_note"] = " ".join(filter(None, [question.get("transcription_note"), note]))


def load_seed_questions(seed_file: Path) -> list[dict]:
    payload = json.loads(seed_file.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported seed-data schema version")
    raw_questions = payload.get("questions")
    defaults = payload.get("defaults", {})
    questions = []
    for raw_question in raw_questions or []:
        question = {**defaults, **raw_question}
        source_question_number = str(question["source_question_number"]).strip()
        if not source_question_number:
            raise ValueError("Every seed question must include a source_question_number")
        source_number_key = (
            f"{int(source_question_number):02d}"
            if source_question_number.isdigit()
            else source_question_number
        )
        question["source_key"] = question.get(
            "source_key", f"{payload['collection_id']}-q{source_number_key}"
        )
        question["source_reference"] = question.get(
            "source_reference",
            f"{payload['source_document']} - page {question['source_page']}, question {question['source_question_number']}",
        )
        if "question_json" not in question:
            question["question_json"] = {"stem": question.pop("stem"), "options": question.pop("options")}
        else:
            question["question_json"] = dict(question["question_json"])
        question["question_json"]["options"] = normalize_options(question["question_json"].get("options", []))
        mark_unreadable_ocr_options(question)
        questions.append(question)
    if not isinstance(questions, list) or not questions:
        raise ValueError("Seed file must contain at least one question")
    return questions


def validate_question(question: dict) -> None:
    required = {
        "source_key", "exam", "subject", "chapter", "topic", "primary_concept",
        "question_archetype", "question_type", "difficulty", "question_json",
        "source", "source_reference", "verification_status",
    }
    missing = sorted(key for key in required if not question.get(key))
    if missing:
        raise ValueError(f"{question.get('source_key', '<unknown>')}: missing {', '.join(missing)}")
    question_json = question["question_json"]
    if not isinstance(question_json, dict) or not str(question_json.get("stem", "")).strip():
        raise ValueError(f"{question['source_key']}: a non-empty question stem is required")
    options = question_json.get("options", [])
    if not isinstance(options, list) or not all(isinstance(option, str) and option.strip() for option in options):
        raise ValueError(f"{question['source_key']}: options must be a list of non-empty strings")
    try:
        question_type = QuestionType(question["question_type"])
    except ValueError as error:
        raise ValueError(f"{question['source_key']}: unsupported question type") from error
    structured_mode = (question.get("response_spec") or {}).get("mode")
    if question_type == QuestionType.SINGLE_CORRECT and len(options) != 4 and not (structured_mode == "select" and len(options) >= 2) and structured_mode not in {"matching", "unknown"}:
        raise ValueError(f"{question['source_key']}: single-correct MCQs require four options")
    if question_type == QuestionType.MULTIPLE_CORRECT and len(options) < 2:
        raise ValueError(f"{question['source_key']}: multiple-correct MCQs require at least two options")
    if question_type in {QuestionType.NUMERICAL, QuestionType.SUBJECTIVE} and options and structured_mode not in {"matching", "unknown"}:
        raise ValueError(f"{question['source_key']}: {question_type.value} questions cannot include options")
    if not 1 <= question["difficulty"] <= 5:
        raise ValueError(f"{question['source_key']}: difficulty must be 1-5")


def upsert_questions(questions: list[dict], database_path: Path | None = None) -> tuple[int, int]:
    """Validate and upsert normalized questions from any import source."""
    now = datetime.now(UTC).isoformat()
    inserted = updated = 0
    with get_connection(database_path) as connection:
        for question in questions:
            validate_question(question)
            exists = connection.execute(
                "SELECT id FROM questions WHERE source_key = ?", (question["source_key"],)
            ).fetchone()
            record_id = exists["id"] if exists else str(uuid.uuid4())
            values = {
                "id": record_id,
                "source_key": question["source_key"],
                "exam": question["exam"],
                "class_level": question.get("class_level"),
                "subject": question["subject"],
                "chapter": question["chapter"],
                "topic": question["topic"],
                "subtopic": question.get("subtopic"),
                "primary_concept": question["primary_concept"],
                "secondary_concepts": json.dumps(question.get("secondary_concepts", [])),
                "question_archetype": question["question_archetype"],
                "question_type": question["question_type"],
                "difficulty": question["difficulty"],
                "question_json": json.dumps(question["question_json"], ensure_ascii=False),
                "answer_json": json.dumps(question["answer_json"]) if question.get("answer_json") else None,
                "solution": question.get("solution"),
                "expected_time_minutes": question.get("expected_time_minutes"),
                "marks": question.get("marks"),
                "source": question["source"],
                "source_reference": question["source_reference"],
                "verification_status": question["verification_status"],
                "source_job_id": question.get("source_job_id"),
                "source_question_number": question.get("source_question_number"),
                "exam_format_json": json.dumps(question.get("exam_format")) if question.get("exam_format") else None,
                "response_spec_json": json.dumps(question.get("response_spec")) if question.get("response_spec") else None,
                "question_group_json": json.dumps(question.get("question_group")) if question.get("question_group") else None,
                "answer_provenance": question.get("answer_provenance"),
                "solution_provenance": question.get("solution_provenance"),
                "created_at": now,
                "updated_at": now,
            }
            columns = ", ".join(values)
            placeholders = ", ".join(f":{column}" for column in values)
            assignments = ", ".join(
                f"{column} = excluded.{column}" for column in values if column not in {"id", "source_key", "created_at"}
            )
            connection.execute(
                f"INSERT INTO questions ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(source_key) DO UPDATE SET {assignments}",
                values,
            )
            if exists:
                updated += 1
            else:
                inserted += 1
    return inserted, updated


def upsert_seed_questions(seed_file: Path, database_path: Path | None = None) -> tuple[int, int]:
    return upsert_questions(load_seed_questions(seed_file), database_path)
