from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class QuestionType(StrEnum):
    SINGLE_CORRECT = "single_correct_mcq"
    MULTIPLE_CORRECT = "multiple_correct_mcq"
    NUMERICAL = "numerical"
    SUBJECTIVE = "subjective"


class GenerationMode(StrEnum):
    STRUCTURAL = "structural_variation"
    CONCEPT = "concept_variation"


class VariationStrength(StrEnum):
    CLOSE = "close"
    BALANCED = "balanced"
    HIGH = "high"


class QuestionTypeCount(BaseModel):
    type: QuestionType
    count: int = Field(ge=1)


class DifficultyCount(BaseModel):
    difficulty: int = Field(ge=1, le=5)
    count: int = Field(ge=1)


class SubtopicPlanItem(BaseModel):
    topic: str = Field(min_length=1)
    # Some imported collections are organized only to topic level.  Treat an
    # omitted subtopic as a valid topic-level plan instead of rejecting a
    # usable seed bank at request validation time.
    subtopic: str | None = Field(default=None, min_length=1)
    chapters: list[str] = Field(default_factory=list)
    section_title: str | None = Field(default=None, max_length=120)
    question_types: list[QuestionTypeCount]
    difficulty_distribution: list[DifficultyCount]
    generation_mode: GenerationMode | None = None
    variation_strength: VariationStrength | None = None
    seed_question_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def plan_distributions_have_equal_totals(self) -> "SubtopicPlanItem":
        type_total = sum(item.count for item in self.question_types)
        difficulty_total = sum(item.count for item in self.difficulty_distribution)
        if not type_total:
            raise ValueError("each subtopic plan needs at least one question")
        if type_total != difficulty_total:
            raise ValueError("each subtopic plan needs equal question-type and difficulty totals")
        if len({item.type for item in self.question_types}) != len(self.question_types):
            raise ValueError("question_types cannot repeat a type within a subtopic plan")
        if len({item.difficulty for item in self.difficulty_distribution}) != len(self.difficulty_distribution):
            raise ValueError("difficulty_distribution cannot repeat a difficulty within a subtopic plan")
        return self

    def resolved_section_title(self) -> str:
        if self.section_title and self.section_title.strip():
            return self.section_title.strip()[:120]
        return (f"{self.topic} › {self.subtopic}" if self.subtopic else self.topic)[:120]


class GenerationSlot(BaseModel):
    slot: int = Field(ge=1)
    question_type: QuestionType
    difficulty: int = Field(ge=1, le=5)
    topic: str | None = None
    subtopic: str | None = None
    chapters: list[str] = Field(default_factory=list)
    section_title: str | None = None
    generation_mode: GenerationMode | None = None
    variation_strength: VariationStrength | None = None
    # All teacher-selected sources ground every variation in the section.
    # The singular field remains the per-slot, rotating primary source for
    # backwards-compatible consumers and clear provenance.
    selected_seed_question_ids: list[str] = Field(default_factory=list)
    selected_seed_question_id: str | None = None


class GenerationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    exam: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    chapters: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    subtopics: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    question_types: list[QuestionTypeCount] = Field(default_factory=list)
    difficulty_distribution: list[DifficultyCount] = Field(default_factory=list)
    generation_mode: GenerationMode
    variation_strength: VariationStrength = VariationStrength.BALANCED
    subtopic_plans: list[SubtopicPlanItem] | None = None

    @model_validator(mode="after")
    def distributions_have_equal_totals(self) -> "GenerationRequest":
        if self.subtopic_plans:
            if len({(item.topic, item.subtopic) for item in self.subtopic_plans}) != len(self.subtopic_plans):
                raise ValueError("subtopic_plans cannot repeat a topic/subtopic pair")
            return self
        type_total = sum(item.count for item in self.question_types)
        difficulty_total = sum(item.count for item in self.difficulty_distribution)
        if not type_total:
            raise ValueError("question_types needs at least one question")
        if type_total != difficulty_total:
            raise ValueError("question_types and difficulty_distribution must have the same total count")
        if len({item.type for item in self.question_types}) != len(self.question_types):
            raise ValueError("question_types cannot repeat a type")
        if len({item.difficulty for item in self.difficulty_distribution}) != len(self.difficulty_distribution):
            raise ValueError("difficulty_distribution cannot repeat a difficulty")
        return self

    def requested_total(self) -> int:
        if self.subtopic_plans:
            return sum(sum(item.count for item in plan.question_types) for plan in self.subtopic_plans)
        return sum(item.count for item in self.question_types)

    def build_slots(self) -> list[GenerationSlot]:
        # Pair deterministic, repeated sequences. This preserves both requested
        # marginals without inventing a question-type/difficulty matrix.
        if self.subtopic_plans:
            slots: list[GenerationSlot] = []
            for plan in self.subtopic_plans:
                types = [item.type for item in plan.question_types for _ in range(item.count)]
                difficulties = [item.difficulty for item in plan.difficulty_distribution for _ in range(item.count)]
                for plan_slot, (question_type, difficulty) in enumerate(zip(types, difficulties, strict=True)):
                    slots.append(
                        GenerationSlot(
                            slot=len(slots) + 1, question_type=question_type, difficulty=difficulty,
                            topic=plan.topic, subtopic=plan.subtopic, chapters=list(plan.chapters or self.chapters),
                            section_title=plan.resolved_section_title(),
                            generation_mode=plan.generation_mode, variation_strength=plan.variation_strength,
                            selected_seed_question_ids=list(plan.seed_question_ids),
                            selected_seed_question_id=(plan.seed_question_ids[plan_slot % len(plan.seed_question_ids)] if plan.seed_question_ids else None),
                        )
                    )
            return slots
        types = [item.type for item in self.question_types for _ in range(item.count)]
        difficulties = [item.difficulty for item in self.difficulty_distribution for _ in range(item.count)]
        return [
            GenerationSlot(slot=index + 1, question_type=question_type, difficulty=difficulty)
            for index, (question_type, difficulty) in enumerate(zip(types, difficulties, strict=True))
        ]


class GeneratedQuestion(BaseModel):
    question_type: QuestionType
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    correct_answer: str = Field(min_length=1)
    solution: str = Field(min_length=1)
    primary_concept: str = Field(min_length=1)
    secondary_concepts: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=5)
    estimated_time_minutes: int = Field(ge=1, le=60)
    marks: int = Field(ge=1, le=100)
    machine_check: "MachineCheckSpec | None" = None

    @model_validator(mode="after")
    def question_shape_matches_type(self) -> "GeneratedQuestion":
        if self.question_type in {QuestionType.SINGLE_CORRECT, QuestionType.MULTIPLE_CORRECT} and len(self.options) < 2:
            raise ValueError("MCQ questions require options")
        if self.question_type == QuestionType.SINGLE_CORRECT and len(self.options) != 4:
            raise ValueError("single-correct MCQs require exactly four options")
        return self


class PolynomialTerm(BaseModel):
    coefficient: str
    power: int = Field(ge=0, le=12)


class MachineCheckSpec(BaseModel):
    """Restricted, declarative data for questions the backend can verify exactly.

    It intentionally describes only supported question families.  It is not a
    general-purpose expression language and is never executed as model code.
    """

    family: Literal[
        "quadratic_roots",
        "polynomial_definite_integral",
        "constant_acceleration_final_velocity",
        "work_done_by_constant_force",
    ]
    coefficients: list[str] = Field(default_factory=list, max_length=3)
    terms: list[PolynomialTerm] = Field(default_factory=list, max_length=20)
    lower_bound: str | None = None
    upper_bound: str | None = None
    quantities: dict[str, float] = Field(default_factory=dict)
    display_values: list[str] = Field(default_factory=list, max_length=20)
    option_values: list[str] = Field(default_factory=list, max_length=4)
    numeric_tolerance: float = Field(default=1e-6, gt=0, le=0.01)


class SymbolicVerification(BaseModel):
    status: Literal["verified", "fallback_required"]
    family: str | None = None
    engine: str = "sympy"
    computed_answer: str | None = None
    checks: list[str] = Field(default_factory=list)
    reason: str | None = None
    audited: bool = False


class GeneratedSolution(BaseModel):
    correct_answer: str | None = None
    solution: str = Field(min_length=1)


class ConceptBlueprint(BaseModel):
    primary_concept: str
    archetype: str
    setup: str
    unknown: str
    reasoning_steps: list[str] = Field(min_length=2)
    difficulty: int = Field(ge=1, le=5)
    question_type: QuestionType


class ValidationResult(BaseModel):
    valid: bool
    independent_answer: str | None = None
    matches_generated_answer: bool
    ambiguous: bool
    multiple_answers_possible: bool
    sufficient_information: bool
    concept_match: bool
    difficulty_match: bool
    comments: str = ""


class GeneratedSlotResult(BaseModel):
    slot: GenerationSlot
    question: GeneratedQuestion
    seed_question_ids: list[str]
    primary_seed_question_id: str | None = None
    selected_seed_question_id: str | None = None
    similarity_score: float
    validation: ValidationResult
    symbolic_verification: SymbolicVerification | None = None
    generation_attempt: int
    reference_question_id: str | None = None
    reference_question_index: int | None = None
    reference_question_number: int | None = None
    reference_reused: bool = False


class GenerationResponse(BaseModel):
    title: str
    generation_mode: GenerationMode
    slots: list[GeneratedSlotResult]
