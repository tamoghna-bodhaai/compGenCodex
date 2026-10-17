from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.generation import GenerationRequest, QuestionType


class ExportFormat(StrEnum):
    DOCX = "docx"
    PDF = "pdf"


class ExportVariant(StrEnum):
    QUESTION_PAPER = "question_paper"
    ANSWER_KEY = "answer_key"


class PaperCreateRequest(GenerationRequest):
    pass


class PaperUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    branding_config: dict[str, Any] | None = None
    branding_template_id: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|generated|final)$")


class BrandingProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    branding_config: dict[str, Any]


class BrandingProfileUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    branding_config: dict[str, Any]


class SectionCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SectionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=1)


class PaperQuestionInput(BaseModel):
    question_type: QuestionType
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    correct_answer: str | None = None
    solution: str | None = None
    difficulty: int = Field(ge=1, le=5)
    marks: int = Field(default=4, ge=1, le=100)
    primary_concept: str | None = None
    secondary_concepts: list[str] = Field(default_factory=list)
    estimated_time_minutes: int | None = Field(default=None, ge=1, le=60)

    @model_validator(mode="after")
    def validate_mcq_options(self) -> "PaperQuestionInput":
        if self.question_type == QuestionType.SINGLE_CORRECT and len(self.options) != 4:
            raise ValueError("single-correct MCQs require exactly four options")
        if self.question_type == QuestionType.MULTIPLE_CORRECT and len(self.options) < 2:
            raise ValueError("multiple-correct MCQs require at least two options")
        return self

    def question_json(self) -> dict[str, Any]:
        return {
            "stem": self.stem,
            "options": self.options,
            "primary_concept": self.primary_concept,
            "secondary_concepts": self.secondary_concepts,
            "estimated_time_minutes": self.estimated_time_minutes,
            "marks": self.marks,
        }


class AddManualQuestionRequest(PaperQuestionInput):
    section_id: str | None = None


class AddBankQuestionRequest(BaseModel):
    seed_question_id: str
    section_id: str | None = None


class QuestionEditRequest(BaseModel):
    stem: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    correct_answer: Any | None = None
    response_spec: dict[str, Any] | None = None
    solution: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    marks: int | None = Field(default=None, ge=1, le=100)
    section_id: str | None = None
    position: int | None = Field(default=None, ge=1)


class LockRequest(BaseModel):
    locked: bool


class RegenerateSelectedRequest(BaseModel):
    question_ids: list[str] = Field(min_length=1, max_length=100)
    custom_instruction: str | None = Field(default=None, max_length=1200)


class PaperExportRequest(BaseModel):
    format: ExportFormat = ExportFormat.DOCX
    variant: ExportVariant = ExportVariant.QUESTION_PAPER
    branding_template_id: str | None = None
    branding_overrides: dict[str, Any] | None = None
