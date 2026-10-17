"""Versioned assessment rules independent of the legacy display classification."""
from __future__ import annotations

from typing import Literal
import math

from pydantic import BaseModel, Field, model_validator


class NumericRule(BaseModel):
    integer_only: bool = False
    permitted_precision: int | None = Field(default=None, ge=0, le=12)
    rounding_rule: Literal["nearest", "floor", "ceil", "truncate"] | None = None
    tolerance: float | None = Field(default=None, ge=0)
    accepted_min: float | None = None
    accepted_max: float | None = None


class MarkingRule(BaseModel):
    full: float | None = None
    negative: float | None = None
    zero: float = 0
    partial: dict[str, float] = Field(default_factory=dict)
    source_instructions: str | None = None


class ExamFormat(BaseModel):
    version: int = 1
    exam: str
    year: int | None = None
    paper: str | None = None
    subject: str | None = None
    section: str | None = None
    section_instructions: str | None = None


class QuestionGroup(BaseModel):
    id: str
    type: Literal["comprehension", "data", "common_stem", "other"]
    shared_stem: str


class ResponseSpec(BaseModel):
    version: int = 1
    mode: Literal["select", "numeric", "matching", "subjective", "unknown"]
    min_selections: int | None = Field(default=None, ge=0)
    max_selections: int | None = Field(default=None, ge=1)
    option_groups: list[list[str]] = Field(default_factory=list)
    numeric: NumericRule | None = None
    list_i: list[str] = Field(default_factory=list)
    list_ii: list[str] = Field(default_factory=list)
    answer_combinations: list[str] = Field(default_factory=list)
    marking: MarkingRule = Field(default_factory=MarkingRule)
    instructions: str | None = None
    supported_for_generation: bool = False

    @model_validator(mode="after")
    def check_rules(self) -> "ResponseSpec":
        if self.min_selections is not None and self.max_selections is not None and self.min_selections > self.max_selections:
            raise ValueError("minimum selections cannot exceed maximum")
        if self.mode == "numeric" and self.numeric and self.numeric.accepted_min is not None and self.numeric.accepted_max is not None and self.numeric.accepted_min > self.numeric.accepted_max:
            raise ValueError("numeric accepted range is reversed")
        return self


def legacy_response_spec(question_type: str, option_count: int = 0) -> ResponseSpec:
    if question_type == "single_correct_mcq":
        return ResponseSpec(mode="select", min_selections=1, max_selections=1, supported_for_generation=True)
    if question_type == "multiple_correct_mcq":
        return ResponseSpec(mode="select", min_selections=1, max_selections=max(2, option_count), supported_for_generation=True)
    if question_type == "numerical":
        return ResponseSpec(mode="numeric", numeric=NumericRule(), supported_for_generation=True)
    return ResponseSpec(mode="subjective")


def validate_answer(spec: ResponseSpec, answer: object, option_count: int = 0, expected: object = None) -> bool:
    if answer is None:
        return True
    if spec.mode == "select":
        choices = answer if isinstance(answer, list) else [answer]
        if not choices or not all(isinstance(choice, str) for choice in choices):
            return False
        if len(set(choices)) != len(choices):
            return False
        if spec.min_selections is not None and len(choices) < spec.min_selections:
            return False
        if spec.max_selections is not None and len(choices) > spec.max_selections:
            return False
        return all(choice.upper() in [chr(65 + i) for i in range(option_count)] for choice in choices)
    if spec.mode == "numeric":
        try:
            value = float(answer)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        rule = spec.numeric or NumericRule()
        if rule.integer_only and not value.is_integer():
            return False
        if rule.permitted_precision is not None and not rule.rounding_rule and round(value, rule.permitted_precision) != value:
            return False
        if not ((rule.accepted_min is None or value >= rule.accepted_min) and (rule.accepted_max is None or value <= rule.accepted_max)):
            return False
        if expected is not None and rule.tolerance is not None:
            try:
                return abs(value - float(expected)) <= rule.tolerance
            except (TypeError, ValueError):
                return False
        return True
    if spec.mode == "matching":
        if spec.answer_combinations and isinstance(answer, str):
            return answer.upper() in [chr(65 + i) for i in range(len(spec.answer_combinations))]
        return isinstance(answer, dict) and set(answer) == set(spec.list_i) and all(value in spec.list_ii for value in answer.values())
    return spec.mode == "subjective" and isinstance(answer, str)


def rendered_stem(stem: str, spec: ResponseSpec, group: QuestionGroup | None = None) -> str:
    parts = [group.shared_stem, stem] if group else [stem]
    if spec.mode == "matching":
        if spec.list_i:
            parts.append("List I: " + "; ".join(spec.list_i))
        if spec.list_ii:
            parts.append("List II: " + "; ".join(spec.list_ii))
    return "\n\n".join(part for part in parts if part)


def generator_compatible(spec: ResponseSpec, question_type: str, option_count: int) -> bool:
    if not spec.supported_for_generation:
        return False
    if spec.mode == "numeric":
        return question_type == "numerical"
    if spec.mode == "select":
        if question_type == "single_correct_mcq":
            return option_count == 4 and spec.min_selections == spec.max_selections == 1
        if question_type == "multiple_correct_mcq":
            return option_count >= 2 and spec.max_selections is not None and spec.max_selections <= option_count
    return False
