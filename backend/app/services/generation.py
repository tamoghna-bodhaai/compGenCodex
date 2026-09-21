from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.error_safety import safe_error_message
from app.core.settings import Settings, get_settings
from app.db.database import get_connection
from app.prompts import concept_blueprint, concept_variation, solution, structural_variation, validator
from app.schemas.generation import (
    ConceptBlueprint,
    GeneratedQuestion,
    GeneratedSolution,
    GeneratedSlotResult,
    GenerationMode,
    GenerationRequest,
    GenerationResponse,
    GenerationSlot,
    ValidationResult,
)
from app.services.openrouter import ModelConfigurationError, OpenRouterClient, OpenRouterError
from app.services.retrieval import MetadataFirstRetriever, RetrievalCandidate


class GenerationFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class _Candidate:
    slot: GenerationSlot
    question: GeneratedQuestion
    seeds: list[RetrievalCandidate]
    similarity: float
    attempt: int
    generation_model: str | None = None
    reference_question_id: str | None = None
    reference_question_index: int | None = None
    reference_question_number: int | None = None
    reference_reused: bool = False


def _deterministic_failure(question: GeneratedQuestion, slot: GenerationSlot) -> str | None:
    """Reject malformed drafts locally; successful checks still need an LLM."""
    if question.question_type != slot.question_type:
        return "Generated question did not match the requested question type."
    if question.difficulty != slot.difficulty:
        return "Generated question did not match the requested difficulty."
    if not question.stem.strip() or not question.solution.strip() or not question.correct_answer.strip():
        return "Generated question is missing its stem, answer, or solution."
    if slot.question_type.value in {"single_correct_mcq", "multiple_correct_mcq"}:
        answer = question.correct_answer.strip()
        labels = {chr(ord("A") + index) for index in range(len(question.options))}
        if answer.upper() not in labels and answer not in question.options:
            return "MCQ answer does not identify one of the supplied options."
    return None


def _schema(model: type) -> dict:
    return model.model_json_schema()


def _text_terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", value.lower()))


def max_seed_similarity(question: GeneratedQuestion, seeds: list[RetrievalCandidate]) -> float:
    question_terms = _text_terms(question.stem)
    if not question_terms:
        return 0.0
    similarities = []
    for seed in seeds:
        seed_terms = _text_terms(seed.question_json["stem"])
        similarities.append(len(question_terms & seed_terms) / len(question_terms | seed_terms))
    return round(max(similarities, default=0.0), 4)


class GenerationService:
    def __init__(self, *, settings: Settings | None = None, client: OpenRouterClient | None = None, retriever: MetadataFirstRetriever | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or OpenRouterClient(self.settings)
        self.retriever = retriever or MetadataFirstRetriever()

    async def generate(
        self,
        request: GenerationRequest,
        on_slot_complete: Callable[[GeneratedSlotResult], Awaitable[None] | None] | None = None,
        slots: list[GenerationSlot] | None = None,
        before_slot: Callable[[GenerationSlot], Awaitable[None] | None] | None = None,
        custom_instruction: str | None = None,
    ) -> GenerationResponse:
        if not self.settings.generation_ready:
            raise ModelConfigurationError(
                "Generation is not configured. Set OPENROUTER_API_KEY, GENERATION_MODEL, and VALIDATION_MODEL before generating questions."
            )
        pending = list(slots if slots is not None else request.build_slots())
        results: list[GeneratedSlotResult] = []

        # A round is deliberately staged: burst generation first, inexpensive
        # deterministic checks second, and independent LLM validation last.
        # Only failed slots re-enter a later round, so one bad draft never
        # cancels the rest of the paper.
        attempt = 0
        for generation_model, validation_model in self._model_phases():
            for _ in range(self.settings.max_generation_attempts):
                if not pending:
                    break
                attempt += 1
                candidates, failures = await self._generate_candidates(request, pending, attempt, before_slot, custom_instruction, generation_model)
                candidates, local_failures = await self._deterministically_validate(request, candidates)
                failures.update(local_failures)
                validated, validation_failures = await self._llm_validate(request, candidates, validation_model)
                failures.update(validation_failures)

                for result in validated:
                    results.append(result)
                    if on_slot_complete:
                        notification = on_slot_complete(result)
                        if notification is not None:
                            await notification
                pending = [slot for slot in pending if slot.slot in failures]
            if not pending:
                break
        if pending:
            failed_slot = pending[0]
            raise GenerationFailure(
                f"Generation failed for question slot {failed_slot.slot} after {attempt} attempts across primary and fallback models: {failures[failed_slot.slot]}"
            )
        results.sort(key=lambda result: result.slot.slot)
        return GenerationResponse(title=request.title, generation_mode=request.generation_mode, slots=results)

    async def generate_slot(self, request: GenerationRequest, slot: GenerationSlot, custom_instruction: str | None = None) -> GeneratedSlotResult:
        response = await self.generate(request, slots=[slot], custom_instruction=custom_instruction)
        return response.slots[0]

    async def generate_from_reference(
        self,
        request: GenerationRequest,
        reference_questions: list[dict],
        reference_images: list[str] | None = None,
        custom_instruction: str | None = None,
        on_slot_complete: Callable[[GeneratedSlotResult], Awaitable[None] | None] | None = None,
        slots: list[GenerationSlot] | None = None,
        before_slot: Callable[[GenerationSlot], Awaitable[None] | None] | None = None,
    ) -> GenerationResponse:
        """Generate variations directly from a reference image or paper without DB retrieval.

        reference_questions: list of dicts with stem/options/question_type/difficulty/primary_concept etc.
        reference_images: base64 data URLs for visual grounding (forwarded to LLM).
        Uses the request's variation mode, just like question-bank generation.
        """
        if not self.settings.generation_ready:
            raise ModelConfigurationError(
                "Generation is not configured. Set OPENROUTER_API_KEY, GENERATION_MODEL, and VALIDATION_MODEL before generating questions."
            )
        if not reference_questions:
            raise GenerationFailure("No reference questions extracted from the uploaded file.")
        pending = list(slots if slots is not None else request.build_slots())
        results: list[GeneratedSlotResult] = []

        # Build synthetic RetrievalCandidate-like dicts for logging/similarity
        # We bypass real retrieval and inject reference seeds per slot
        attempt = 0
        for generation_model, validation_model in self._model_phases():
            for _ in range(self.settings.max_generation_attempts):
                if not pending:
                    break
                attempt += 1
                candidates, failures = await self._generate_candidates_from_reference(
                    request, pending, attempt, before_slot, custom_instruction, reference_questions, reference_images, generation_model
                )
                candidates, local_failures = await self._deterministically_validate(request, candidates)
                failures.update(local_failures)
                validated, validation_failures = await self._llm_validate(request, candidates, validation_model)
                failures.update(validation_failures)

                for result in validated:
                    results.append(result)
                    if on_slot_complete:
                        notification = on_slot_complete(result)
                        if notification is not None:
                            await notification
                pending = [slot for slot in pending if slot.slot in failures]
            if not pending:
                break
        if pending:
            failed_slot = pending[0]
            raise GenerationFailure(
                f"Reference generation failed for slot {failed_slot.slot} after {attempt} attempts across primary and fallback models: {failures[failed_slot.slot]}"
            )
        results.sort(key=lambda result: result.slot.slot)
        return GenerationResponse(title=request.title, generation_mode=request.generation_mode, slots=results)

    async def _generate_candidates_from_reference(
        self,
        request: GenerationRequest,
        slots: list[GenerationSlot],
        attempt: int,
        before_slot: Callable[[GenerationSlot], Awaitable[None] | None] | None,
        custom_instruction: str | None,
        reference_questions: list[dict],
        reference_images: list[str] | None,
        generation_model: str,
    ) -> tuple[list[_Candidate], dict[int, str]]:
        semaphore = asyncio.Semaphore(self.settings.generation_burst_concurrency)

        def _to_candidate_dict(ref: dict) -> dict:
            return {
                "source_key": ref.get("source_key") or f"ref-{ref.get('source_question_number', 1)}",
                "primary_concept": ref.get("primary_concept") or ref.get("topic") or "reference",
                "question_archetype": ref.get("question_archetype") or "reference",
                "difficulty": ref.get("difficulty") or 3,
                "stem": ref.get("stem") or "",
                "options": ref.get("options") or [],
            }

        async def produce(slot: GenerationSlot) -> _Candidate:
            try:
                if before_slot:
                    notification = before_slot(slot)
                    if notification is not None:
                        await notification
                async with semaphore:
                    if before_slot:
                        notification = before_slot(slot)
                        if notification is not None:
                            await notification
                    # Preserve the selected reference order. Repeat only after
                    # the selected set is exhausted.
                    reference_index = (slot.slot - 1) % len(reference_questions)
                    ref = reference_questions[reference_index]
                    reference_id = ref.get("reference_question_id") or f"reference-{reference_index + 1}"
                    source_number = ref.get("source_question_number")
                    try:
                        source_number = int(source_number) if source_number is not None else None
                    except (TypeError, ValueError):
                        source_number = None
                    # Build synthetic RetrievalCandidate for downstream prompt/validation
                    # Create minimal mock with prompt_payload
                    @dataclass
                    class _RefSeed:
                        id: str
                        question_json: dict
                        primary_concept: str
                        question_archetype: str
                        difficulty: int
                        stem: str
                        options: list[str]

                        def prompt_payload(self):
                            return {
                                "source_key": self.id,
                                "primary_concept": self.primary_concept,
                                "question_archetype": self.question_archetype,
                                "difficulty": self.difficulty,
                                "stem": self.stem,
                                "options": self.options,
                            }

                    synthetic = _RefSeed(
                        id=reference_id,
                        question_json={"stem": ref.get("stem"), "options": ref.get("options")},
                        primary_concept=ref.get("primary_concept") or "reference",
                        question_archetype=ref.get("question_archetype") or "reference",
                        difficulty=ref.get("difficulty") or slot.difficulty,
                        stem=ref.get("stem") or "",
                        options=ref.get("options") or [],
                    )
                    seeds = [synthetic]  # type: ignore
                    # Merge custom instruction with reference hint
                    effective_instruction = custom_instruction
                    if not effective_instruction:
                        effective_instruction = "Take this paper as reference & generate a structural variation around this based on the paper"
                    # Tag for prompt image hint
                    if reference_images:
                        effective_instruction = f"[reference image attached] {effective_instruction}"
                    question = await self._generate_question(request, slot, seeds, effective_instruction, reference_images, generation_model)
                    similarity = max_seed_similarity(question, seeds)  # against synthetic
                    # For reference mode we relax similarity check (allow close)
                    return _Candidate(
                        slot, question, seeds, similarity, attempt,
                        reference_question_id=reference_id,
                        reference_question_index=reference_index,
                        reference_question_number=source_number,
                        reference_reused=slot.slot > len(reference_questions), generation_model=generation_model,
                    )  # type: ignore
            except (OpenRouterError, ValidationError, GenerationFailure) as error:
                self._log(request, slot=slot, status="retrying", failure_reason=safe_error_message(error), model=generation_model)
                raise

        return await self._collect(slots, produce)

    async def _generate_candidates(
        self,
        request: GenerationRequest,
        slots: list[GenerationSlot],
        attempt: int,
        before_slot: Callable[[GenerationSlot], Awaitable[None] | None] | None,
        custom_instruction: str | None,
        generation_model: str,
    ) -> tuple[list[_Candidate], dict[int, str]]:
        semaphore = asyncio.Semaphore(self.settings.generation_burst_concurrency)

        async def produce(slot: GenerationSlot) -> _Candidate:
            try:
                if before_slot:
                    notification = before_slot(slot)
                    if notification is not None:
                        await notification
                async with semaphore:
                    if before_slot:
                        notification = before_slot(slot)
                        if notification is not None:
                            await notification
                    seeds = self.retriever.retrieve(request, slot)
                    question = await self._generate_question(request, slot, seeds, custom_instruction, generation_model=generation_model)
                    similarity = max_seed_similarity(question, seeds)
                    if request.generation_mode == GenerationMode.CONCEPT and similarity > self.settings.max_seed_similarity:
                        raise GenerationFailure(f"Generated question is too similar to its seed pool ({similarity:.2f}).")
                    return _Candidate(slot, question, seeds, similarity, attempt, generation_model)
            except (OpenRouterError, ValidationError, GenerationFailure) as error:
                self._log(request, slot=slot, status="retrying", failure_reason=safe_error_message(error), model=generation_model)
                raise

        return await self._collect(slots, produce)

    async def _deterministically_validate(
        self, request: GenerationRequest, candidates: list[_Candidate]
    ) -> tuple[list[_Candidate], dict[int, str]]:
        semaphore = asyncio.Semaphore(self.settings.deterministic_validation_concurrency)

        async def check(candidate: _Candidate) -> _Candidate:
            async with semaphore:
                failure = _deterministic_failure(candidate.question, candidate.slot)
                if failure:
                    self._log(request, slot=candidate.slot, seeds=candidate.seeds, status="retrying", failure_reason=failure, model=candidate.generation_model)
                    raise GenerationFailure(failure)
                return candidate

        return await self._collect(candidates, check, key=lambda candidate: candidate.slot)

    async def _llm_validate(
        self, request: GenerationRequest, candidates: list[_Candidate], validation_model: str
    ) -> tuple[list[GeneratedSlotResult], dict[int, str]]:
        semaphore = asyncio.Semaphore(self.settings.validation_burst_concurrency)

        async def validate(candidate: _Candidate) -> GeneratedSlotResult:
            try:
                async with semaphore:
                    validation = await self._validate(candidate.question, request, candidate.slot, validation_model)
                if not validation.valid or not self._validation_matches_requirements(validation):
                    raise GenerationFailure(validation.comments or "Independent validation failed.")
                result = GeneratedSlotResult(
                    slot=candidate.slot,
                    question=candidate.question,
                    seed_question_ids=[seed.id for seed in candidate.seeds],
                    similarity_score=candidate.similarity,
                    validation=validation,
                    generation_attempt=candidate.attempt,
                    reference_question_id=candidate.reference_question_id,
                    reference_question_index=candidate.reference_question_index,
                    reference_question_number=candidate.reference_question_number,
                    reference_reused=candidate.reference_reused,
                )
                self._log(request, result=result, status="validated", model=candidate.generation_model)
                return result
            except (OpenRouterError, ValidationError, GenerationFailure) as error:
                self._log(request, slot=candidate.slot, seeds=candidate.seeds, status="retrying", failure_reason=safe_error_message(error), model=validation_model)
                raise

        return await self._collect(candidates, validate, key=lambda candidate: candidate.slot)

    @staticmethod
    async def _collect(items: list, worker: Callable, *, key: Callable | None = None) -> tuple[list, dict[int, str]]:
        key = key or (lambda item: item)
        outcomes = await asyncio.gather(*(worker(item) for item in items), return_exceptions=True)
        successes, failures = [], {}
        for item, outcome in zip(items, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                slot = key(item)
                failures[slot.slot] = safe_error_message(outcome)
            else:
                successes.append(outcome)
        return successes, failures

    def _model_phases(self) -> list[tuple[str, str]]:
        """Return the primary phase and, when configured, one fallback phase."""
        primary = (self.settings.generation_model, self.settings.validation_model)
        assert primary[0] and primary[1]  # guarded by generation_ready
        fallback = (
            self.settings.generation_fallback_model or primary[0],
            self.settings.validation_fallback_model or primary[1],
        )
        return [primary] if fallback == primary else [primary, fallback]

    async def generate_solution(self, question: dict, *, exam: str, subject: str) -> GeneratedSolution:
        if not self.settings.generation_ready:
            raise ModelConfigurationError(
                "Generation is not configured. Set OPENROUTER_API_KEY, GENERATION_MODEL, and VALIDATION_MODEL before generating solutions."
            )
        last_error: Exception | None = None
        for _, validation_model in self._model_phases():
            try:
                raw = await self.client.call_llm(
                    model=validation_model,
                    system_prompt=solution.SYSTEM_PROMPT,
                    user_prompt=solution.build_prompt(question=question, exam=exam, subject=subject),
                    response_schema=_schema(GeneratedSolution),
                    temperature=0.1,
                    max_tokens=1800,
                )
                return GeneratedSolution.model_validate(raw)
            except (OpenRouterError, ValidationError) as error:
                last_error = error
        assert last_error is not None
        raise last_error

    async def _generate_question(self, request: GenerationRequest, slot: GenerationSlot, seeds: list[RetrievalCandidate], custom_instruction: str | None = None, reference_images: list[str] | None = None, generation_model: str | None = None) -> GeneratedQuestion:
        seed_payload = [seed.prompt_payload() for seed in seeds]
        mode = slot.generation_mode or request.generation_mode
        strength = slot.variation_strength or request.variation_strength
        if mode == GenerationMode.STRUCTURAL:
            raw = await self.client.call_llm(
                model=generation_model or self.settings.generation_model,
                system_prompt=structural_variation.SYSTEM_PROMPT,
                user_prompt=structural_variation.build_prompt(
                    seeds=seed_payload, target_type=slot.question_type.value, difficulty=slot.difficulty,
                    variation_strength=strength.value, custom_instruction=custom_instruction,
                ),
                response_schema=_schema(GeneratedQuestion),
                images=reference_images,
            )
        else:
            blueprint_raw = await self.client.call_llm(
                model=generation_model or self.settings.generation_model,
                system_prompt=concept_blueprint.SYSTEM_PROMPT,
                user_prompt=concept_blueprint.build_prompt(
                    seeds=seed_payload, requested_concepts=request.concepts, target_type=slot.question_type.value,
                    difficulty=slot.difficulty, variation_strength=strength.value, custom_instruction=custom_instruction,
                ),
                response_schema=_schema(ConceptBlueprint),
                temperature=0.3,
                max_tokens=1200,
            )
            blueprint = ConceptBlueprint.model_validate(blueprint_raw)
            if blueprint.question_type != slot.question_type or blueprint.difficulty != slot.difficulty:
                raise GenerationFailure("Concept blueprint did not match the requested question type or difficulty.")
            raw = await self.client.call_llm(
                model=generation_model or self.settings.generation_model,
                system_prompt=concept_variation.SYSTEM_PROMPT,
                user_prompt=concept_variation.build_prompt(seeds=seed_payload, blueprint=blueprint.model_dump(), custom_instruction=custom_instruction),
                response_schema=_schema(GeneratedQuestion),
            )
        question = GeneratedQuestion.model_validate(raw)
        if question.question_type != slot.question_type or question.difficulty != slot.difficulty:
            raise GenerationFailure("Generated question did not match the requested type or difficulty.")
        return question

    async def _validate(self, question: GeneratedQuestion, request: GenerationRequest, slot: GenerationSlot, validation_model: str | None = None) -> ValidationResult:
        raw = await self.client.call_llm(
            model=validation_model or self.settings.validation_model,
            system_prompt=validator.SYSTEM_PROMPT,
            user_prompt=validator.build_prompt(
                question=question.model_dump(), expected_type=slot.question_type.value,
                expected_difficulty=slot.difficulty, expected_concepts=request.concepts,
            ),
            response_schema=_schema(ValidationResult),
            temperature=0.0,
        )
        return ValidationResult.model_validate(raw)

    @staticmethod
    def _validation_matches_requirements(validation: ValidationResult) -> bool:
        return all((
            validation.matches_generated_answer,
            not validation.ambiguous,
            not validation.multiple_answers_possible,
            validation.sufficient_information,
            validation.concept_match,
            validation.difficulty_match,
        ))

    def _log(
        self,
        request: GenerationRequest,
        *,
        status: str,
        result: GeneratedSlotResult | None = None,
        slot: GenerationSlot | None = None,
        seeds: list[RetrievalCandidate] | None = None,
        failure_reason: str | None = None,
        model: str | None = None,
    ) -> None:
        if result:
            slot, seeds = result.slot, []
            validation_json = result.validation.model_dump_json()
            seed_ids = result.seed_question_ids
            similarity = result.similarity_score
        else:
            validation_json = None
            seed_ids = [seed.id for seed in seeds or []]
            similarity = None
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO generation_logs (id, request_json, generation_mode, model, status, failure_reason, seed_question_ids, validation_result_json, similarity_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), request.model_dump_json(), request.generation_mode.value,
                    model or self.settings.generation_model, status,
                    safe_error_message(failure_reason) if failure_reason else None,
                    json.dumps(seed_ids), validation_json,
                    similarity, datetime.now(UTC).isoformat(),
                ),
            )
