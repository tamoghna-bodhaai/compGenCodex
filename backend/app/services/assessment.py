"""Immutable CBT snapshots and deterministic scoring for CompGen questions."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from app.schemas.assessment import ResponseSpec, legacy_response_spec
from app.services.diagrams import asset_path


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC))


def _answer_for(question: dict, spec: ResponseSpec) -> Any:
    raw = (question.get("answer_json") or {}).get("correct_answer")
    if raw is None or raw == "":
        raise ValueError("A reviewed answer is required")
    if spec.mode == "select":
        value = raw if isinstance(raw, list) else [part.strip().upper() for part in str(raw).replace(";", ",").split(",")]
        value = [str(part).upper() for part in value if part]
        option_count = len(question["question_json"].get("options") or [])
        if not value or len(set(value)) != len(value) or any(part not in [chr(65 + i) for i in range(option_count)] for part in value):
            raise ValueError("Answer choices do not match the question options")
        if spec.min_selections is not None and len(value) < spec.min_selections or spec.max_selections is not None and len(value) > spec.max_selections:
            raise ValueError("Answer choices conflict with the response rules")
        return value
    if spec.mode == "numeric":
        try:
            result = float(raw)
        except (ValueError, TypeError) as error:
            raise ValueError("Numeric answer is invalid") from error
        if not math.isfinite(result):
            raise ValueError("Numeric answer is invalid")
        if _normalized_numeric(result, spec) is None:
            raise ValueError("Numeric answer conflicts with the response rules")
        return result
    if spec.mode == "matching":
        if isinstance(raw, dict):
            if set(raw) != set(spec.list_i) or any(value not in spec.list_ii for value in raw.values()):
                raise ValueError("Matching answer conflicts with the response lists")
            return raw
        if spec.answer_combinations and str(raw).strip().upper() in [chr(65+i) for i in range(len(spec.answer_combinations))]:
            return str(raw).strip().upper()
        raise ValueError("Matching answer cannot be scored")
    raise ValueError("This response format needs manual grading")


def snapshot_item(question: dict, section_title: str | None, exam: str = "") -> dict:
    metadata = question.get("generation_metadata") or {}
    raw_spec = metadata.get("response_spec") or question["question_json"].get("response_spec")
    if not raw_spec:
        source_id = metadata.get("seed_question_id") or (metadata.get("seed_question_ids") or [None])[0]
        if source_id:
            from app.db.database import get_connection
            with get_connection() as connection:
                source = connection.execute("SELECT response_spec_json FROM questions WHERE id=?", (source_id,)).fetchone()
            raw_spec = json.loads(source["response_spec_json"]) if source and source["response_spec_json"] else None
    spec = ResponseSpec.model_validate(raw_spec) if raw_spec else legacy_response_spec(question["question_type"], len(question["question_json"].get("options") or []))
    if spec.mode == "select" and question["question_type"] == "single_correct_mcq" and (spec.min_selections or 1) != 1:
        raise ValueError("Single-correct question conflicts with the response rules")
    if spec.mode == "select" and question["question_type"] == "single_correct_mcq" and spec.max_selections not in {None, 1}:
        raise ValueError("Single-correct question conflicts with the response rules")
    exam_format = metadata.get("exam_format") or question["question_json"].get("exam_format") or {}
    effective_exam = str(exam_format.get("exam") or exam)
    if "advanced" in effective_exam.lower() and (not spec.marking.source_instructions or spec.marking.full is None or spec.marking.negative is None):
        raise ValueError("JEE Advanced questions need reviewed full and negative marks from their section instructions")
    if spec.mode in {"numeric", "matching"} and (spec.marking.full is None or spec.marking.negative is None):
        raise ValueError("Numeric and matching questions need explicit full and negative marks")
    if "partial" in (spec.marking.source_instructions or "").lower() and not spec.marking.partial:
        raise ValueError("Partial-credit instructions need a structured partial-credit rule")
    answer = _answer_for(question, spec)
    marking = spec.marking
    partial = marking.partial or {}
    if any(_partial_count(str(key)) is None and _partial_choices(str(key)) is None for key in partial):
        raise ValueError("Partial-credit rule is not supported")
    media = []
    for diagram in question.get("diagrams") or []:
        if diagram.get("validation_status") != "accepted" or asset_path(diagram) is None:
            raise ValueError("A required diagram is missing or unapproved")
        media.append({"id": diagram["id"], "url": diagram["url"], "option_index": diagram.get("option_index"), "description": diagram.get("description")})
    return {
        "id": question["id"], "section": section_title, "kind": question["question_type"],
        "stem": question["question_json"]["stem"], "options": question["question_json"].get("options") or [],
        "answer": answer, "solution": question.get("solution"), "response_spec": spec.model_dump(),
        "scoring": {"full": marking.full if marking.full is not None else question["question_json"].get("marks", 4),
                    "negative": marking.negative if marking.negative is not None else -1,
                    "zero": marking.zero, "partial": partial},
        "diagrams": media, "source": metadata.get("seed_question_id") or metadata.get("seed_question_ids"),
    }


def _choices(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("options", value.get("answer"))
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    return [str(part).strip().upper() for part in value if str(part).strip()]


def _partial_count(key: str) -> int | None:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    normalized = key.strip().lower()
    match = re.fullmatch(r"(\d+|one|two|three|four|five)(?:\s+correct)?", normalized)
    return (int(match[1]) if match[1].isdigit() else words[match[1]]) if match else None


def _partial_choices(key: str) -> set[str] | None:
    parts = [part.strip().upper() for part in key.split(",")]
    return set(parts) if parts and all(re.fullmatch(r"[A-Z]", part) for part in parts) and len(parts) == len(set(parts)) else None


def _normalized_numeric(value: Any, spec: ResponseSpec) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    rule = spec.numeric
    if rule:
        if rule.integer_only and number != number.to_integral_value():
            return None
        if rule.accepted_min is not None and number < Decimal(str(rule.accepted_min)):
            return None
        if rule.accepted_max is not None and number > Decimal(str(rule.accepted_max)):
            return None
        if rule.permitted_precision is not None:
            quantum = Decimal(1).scaleb(-rule.permitted_precision)
            if rule.rounding_rule:
                mode = {"nearest": ROUND_HALF_UP, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING, "truncate": ROUND_DOWN}[rule.rounding_rule]
                number = number.quantize(quantum, rounding=mode)
            elif number != number.quantize(quantum):
                return None
    return number


def score_item(item: dict, response: Any) -> tuple[float, bool | None]:
    policy = item["scoring"]
    if response is None or response == "" or response == [] or response == {}:
        return float(policy["zero"]), None
    spec = ResponseSpec.model_validate(item["response_spec"])
    correct = False
    if spec.mode == "select":
        given = _choices(response)
        expected = item["answer"]
        if not given or len(given) != len(set(given)) or len(given) < (spec.min_selections or 1) or len(given) > (spec.max_selections or len(item["options"])):
            return float(policy["negative"]), False
        correct = set(given) == set(expected)
        if not correct and set(given) < set(expected):
            partial = policy.get("partial") or {}
            for key, points in partial.items():
                choices = _partial_choices(key)
                if choices == set(given) or choices is None and _partial_count(key) == len(given):
                    return float(points), False
    elif spec.mode == "numeric":
        given = _normalized_numeric(response, spec)
        expected = _normalized_numeric(item["answer"], spec)
        tolerance = Decimal(str(spec.numeric.tolerance if spec.numeric and spec.numeric.tolerance is not None else 0))
        correct = given is not None and expected is not None and abs(given - expected) <= tolerance
    elif spec.mode == "matching":
        given = response.get("matches", response.get("answer", response)) if isinstance(response, dict) else response
        correct = given == item["answer"]
    return float(policy["full"] if correct else policy["negative"]), correct


def score_version(content: dict, responses: dict[str, Any]) -> tuple[float, list[dict]]:
    total = 0.0
    rows = []
    for item in content["questions"]:
        points, correct = score_item(item, responses.get(item["id"]))
        total += points
        rows.append({**item, "response": responses.get(item["id"]), "points": points, "correct": correct})
    return total, rows


def finalize_expired(connection: Any, attempt: Any, now: datetime | None = None) -> dict:
    row = dict(attempt)
    if row["submitted_at"] or (now or utc_now()) < parse_time(row["ends_at"]):
        return row
    version = connection.execute("SELECT content FROM test_versions WHERE id=?", (row["version_id"],)).fetchone()
    responses = {answer["question_id"]: json.loads(answer["answer"]) if answer["answer"] else None for answer in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (row["id"],))}
    total, _ = score_version(json.loads(version["content"]), responses)
    connection.execute("UPDATE test_attempts SET submitted_at=?,score=? WHERE id=? AND submitted_at IS NULL", (row["ends_at"], total, row["id"]))
    row["submitted_at"], row["score"] = row["ends_at"], total
    return row
