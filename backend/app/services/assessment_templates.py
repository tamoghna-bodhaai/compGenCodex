"""Canonical contracts for supported computer-based assessment templates."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from app.schemas.assessment import NumericRule, ResponseSpec


_TEMPLATES: dict[str, dict] = {
    "jee_main_2026_paper1": {
        "id": "jee_main_2026_paper1",
        "name": "JEE Main 2026 Paper 1",
        "exam": "JEE Main",
        "year": 2026,
        "paper": "Paper 1",
        "duration_minutes": 180,
        "total_questions": 75,
        "total_marks": 300,
        "subjects": [
            {
                "name": subject,
                "question_count": 25,
                "sections": [
                    {"id": "section_a", "title": "Section A", "question_type": "single_correct_mcq", "response_mode": "select", "count": 20, "option_count": 4},
                    {"id": "section_b", "title": "Section B", "question_type": "numerical", "response_mode": "numeric", "count": 5, "integer_only": True},
                ],
            }
            for subject in ("Mathematics", "Physics", "Chemistry")
        ],
        "marking": {"full": 4, "negative": -1, "zero": 0},
    }
}


def assessment_templates() -> list[dict]:
    return deepcopy(list(_TEMPLATES.values()))


def assessment_template(template_id: str) -> dict:
    try:
        return deepcopy(_TEMPLATES[template_id])
    except KeyError as error:
        raise ValueError("Unknown assessment template") from error


def apply_template_rules(template_id: str, question: dict, default_subject: str) -> tuple[str, str]:
    """Validate and normalize one question against its template slot."""
    template = assessment_template(template_id)
    metadata = question.get("generation_metadata") or {}
    exam_format = metadata.get("exam_format") or {}
    subject = str(exam_format.get("subject") or default_subject)
    kind = question["question_type"]
    subject_contract = next((item for item in template["subjects"] if item["name"] == subject), None)
    section = next((item for item in subject_contract["sections"] if item["question_type"] == kind), None) if subject_contract else None
    if not section:
        raise ValueError(f"{template['name']} does not allow {kind.replace('_', ' ')} questions in {subject}")
    spec = ResponseSpec.model_validate(metadata.get("response_spec") or {
        "mode": section["response_mode"],
        "min_selections": 1 if section["response_mode"] == "select" else None,
        "max_selections": 1 if section["response_mode"] == "select" else None,
    })
    if section["response_mode"] == "select":
        if spec.mode != "select" or len(question["question_json"].get("options") or []) != section["option_count"] or spec.max_selections not in {None, 1}:
            raise ValueError(f"{template['name']} {section['title']} questions need {section['option_count']} options and one answer")
        spec.min_selections = spec.max_selections = 1
    elif section["response_mode"] == "numeric":
        if spec.mode != "numeric":
            raise ValueError(f"{template['name']} {section['title']} questions need a numeric response")
        spec.numeric = NumericRule(integer_only=bool(section.get("integer_only")))
    spec.marking.full = template["marking"]["full"]
    spec.marking.negative = template["marking"]["negative"]
    spec.marking.zero = template["marking"]["zero"]
    spec.marking.partial = {}
    metadata["response_spec"] = spec.model_dump()
    metadata["exam_format"] = {**exam_format, "exam":template["exam"], "year":template["year"], "paper":template["paper"], "subject":subject}
    question["generation_metadata"] = metadata
    return subject, kind


def validate_template_counts(template_id: str, counts: Counter[tuple[str, str]]) -> None:
    template = assessment_template(template_id)
    missing = []
    for subject in template["subjects"]:
        for section in subject["sections"]:
            actual = counts[(subject["name"], section["question_type"])]
            if actual != section["count"]:
                missing.append(f"{subject['name']} {section['title']}: {actual}/{section['count']}")
    if missing:
        raise ValueError(f"{template['name']} question counts are incomplete ({'; '.join(missing)}).")
