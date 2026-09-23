from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.generation import QuestionType


class ClassifiedSeedQuestion(BaseModel):
    """A transcribed and classified question returned by the ingestion model."""

    source_question_number: int = Field(ge=1)
    source_page: int | None = Field(default=None, ge=1)
    exam: Literal["JEE", "NEET"]
    class_level: str | None = None
    subject: Literal["Mathematics", "Physics", "Chemistry"]
    chapter: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    subtopic: str | None = None
    primary_concept: str = Field(min_length=1)
    secondary_concepts: list[str] = Field(default_factory=list)
    question_archetype: str = Field(min_length=1)
    question_type: QuestionType
    difficulty: int = Field(ge=1, le=5)
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    correct_answer: str | None = None
    solution: str | None = None
    expected_time_minutes: int | None = Field(default=None, ge=1, le=60)
    marks: int | None = Field(default=None, ge=1, le=100)
    diagram_required: bool = False
    diagram_bbox: list[float] | None = None
    diagram_description: str | None = None
    diagram_render_spec: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "ClassifiedSeedQuestion":
        if any(not option.strip() for option in self.options):
            raise ValueError("options cannot contain empty values")
        if self.question_type == QuestionType.SINGLE_CORRECT and len(self.options) != 4:
            raise ValueError("single-correct MCQs require exactly four options")
        if self.question_type == QuestionType.MULTIPLE_CORRECT and len(self.options) < 2:
            raise ValueError("multiple-correct MCQs require at least two options")
        if self.question_type in {QuestionType.NUMERICAL, QuestionType.SUBJECTIVE} and self.options:
            raise ValueError("numerical and subjective questions cannot include options")
        if self.diagram_bbox is not None and (len(self.diagram_bbox) != 4 or any(value < 0 or value > 1 for value in self.diagram_bbox)):
            raise ValueError("diagram_bbox must contain normalized x, y, width, height values between 0 and 1")
        if self.diagram_required and (not self.diagram_bbox or not self.diagram_description):
            raise ValueError("diagram questions require a bounding box and description")
        return self


class ClassificationResponse(BaseModel):
    questions: list[ClassifiedSeedQuestion] = Field(min_length=1)
