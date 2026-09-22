from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.core.settings import Settings
from app.db.database import get_connection
from app.schemas.generation import GeneratedQuestion, GeneratedSolution, GenerationRequest
from app.services.generation import GenerationService
from app.services.openrouter import parse_model_json, repair_decoded_latex_escapes, strict_json_schema
from app.services.seed_import import upsert_seed_questions


class FakeOpenRouterClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.models: list[str | None] = []
        self.prompts: list[str] = []

    async def call_llm(self, *, system_prompt: str, user_prompt: str, model: str | None = None, **_: object) -> dict:
        self.calls.append(system_prompt)
        self.models.append(model)
        self.prompts.append(user_prompt)
        if "assessment planner" in system_prompt:
            return {
                "primary_concept": "symmetry of definite integrals",
                "archetype": "functional_symmetry_integral",
                "setup": "continuous functions over a symmetric interval",
                "unknown": "a parameter in the integrand",
                "reasoning_steps": ["apply symmetry", "evaluate the reduced integral"],
                "difficulty": 3,
                "question_type": "single_correct_mcq",
            }
        if "independent JEE examination validator" in system_prompt:
            return {
                "valid": True,
                "independent_answer": "B",
                "matches_generated_answer": True,
                "ambiguous": False,
                "multiple_answers_possible": False,
                "sufficient_information": True,
                "concept_match": True,
                "difficulty_match": True,
                "comments": "",
            }
        return {
            "question_type": "single_correct_mcq",
            "stem": "Evaluate $\\int_{-1}^{1}(x^2+3)\\,dx$.",
            "options": ["$4$", "$20/3$", "$8$", "$10/3$"],
            "correct_answer": "B",
            "solution": "$\\int_{-1}^{1}x^2dx=2/3$ and $\\int_{-1}^{1}3dx=6$, so the answer is $20/3$.",
            "primary_concept": "symmetry of definite integrals",
            "secondary_concepts": [],
            "difficulty": 3,
            "estimated_time_minutes": 3,
            "marks": 3,
        }


def request_for(mode: str) -> GenerationRequest:
    return GenerationRequest.model_validate(
        {
            "title": "Definite Integral Practice",
            "exam": "JEE",
            "subject": "Mathematics",
            "chapters": ["Calculus"],
            "topics": ["Definite Integrals"],
            "concepts": ["symmetry of definite integrals"],
            "question_types": [{"type": "single_correct_mcq", "count": 1}],
            "difficulty_distribution": [{"difficulty": 3, "count": 1}],
            "generation_mode": mode,
        }
    )


class GenerationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "questions.db"
        self.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite:///{self.database_path}"
        upsert_seed_questions(ROOT_DIR / "sample_data" / "jee_definite_integrals_questions.json", self.database_path)
        self.settings = Settings("test", "generator", "validator", None, None, 3, 3, 0.90)

    def tearDown(self) -> None:
        if self.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.previous_database_url
        self.temporary_directory.cleanup()

    def test_structural_pipeline_generates_then_validates(self) -> None:
        client = FakeOpenRouterClient()
        result = asyncio.run(GenerationService(settings=self.settings, client=client).generate(request_for("structural_variation")))
        self.assertEqual(len(result.slots), 1)
        self.assertTrue(result.slots[0].validation.valid)
        self.assertEqual(len(client.calls), 2)
        self.assertFalse(any("assessment planner" in call for call in client.calls))

    def test_exhausted_primary_retries_use_generation_and_validation_fallbacks(self) -> None:
        class PrimaryValidatorRejects(FakeOpenRouterClient):
            async def call_llm(self, *, model: str | None = None, system_prompt: str, user_prompt: str, **kwargs: object) -> dict:
                if model == "validator-primary":
                    self.calls.append(system_prompt)
                    self.models.append(model)
                    self.prompts.append(user_prompt)
                    return {
                        "valid": False, "independent_answer": "B", "matches_generated_answer": False,
                        "ambiguous": False, "multiple_answers_possible": False, "sufficient_information": True,
                        "concept_match": True, "difficulty_match": True, "comments": "Primary validator rejected it.",
                    }
                return await super().call_llm(model=model, system_prompt=system_prompt, user_prompt=user_prompt, **kwargs)

        settings = Settings(
            "test", "generator-primary", "validator-primary", None, None, 3, 1, 0.90,
            generation_fallback_model="generator-fallback", validation_fallback_model="validator-fallback",
        )
        client = PrimaryValidatorRejects()
        result = asyncio.run(GenerationService(settings=settings, client=client).generate(request_for("structural_variation")))

        self.assertEqual(len(result.slots), 1)
        self.assertEqual(result.slots[0].generation_attempt, 2)
        self.assertEqual(client.models, ["generator-primary", "validator-primary", "generator-fallback", "validator-fallback"])

    def test_lifecycle_events_identify_primary_rejection_and_fallback_acceptance(self) -> None:
        class PrimaryValidatorRejects(FakeOpenRouterClient):
            async def call_llm(self, *, model: str | None = None, system_prompt: str, user_prompt: str, **kwargs: object) -> dict:
                if model == "validator-primary":
                    return {"valid": False, "independent_answer": "B", "matches_generated_answer": False,
                            "ambiguous": False, "multiple_answers_possible": False, "sufficient_information": True,
                            "concept_match": True, "difficulty_match": True, "comments": "answer mismatch"}
                return await super().call_llm(model=model, system_prompt=system_prompt, user_prompt=user_prompt, **kwargs)

        events: list[dict] = []
        settings = Settings("test", "generator-primary", "validator-primary", None, None, 3, 1, .90,
                            generation_fallback_model="generator-fallback", validation_fallback_model="validator-fallback")
        result = asyncio.run(GenerationService(settings=settings, client=PrimaryValidatorRejects(), on_event=events.append).generate(request_for("structural_variation")))
        self.assertEqual(len(result.slots), 1)
        self.assertTrue(any(item["phase"] == "llm_validation" and item["outcome"] == "rejected" for item in events))
        self.assertTrue(any(item["phase"] == "model_phase" and item["model_role"] == "fallback" for item in events))
        self.assertTrue(any(item["phase"] == "llm_validation" and item["outcome"] == "accepted" and item["model"] == "validator-fallback" for item in events))

    def test_reference_generation_keeps_ordered_mapping_and_marks_reuse(self) -> None:
        request = GenerationRequest.model_validate(
            {
                "title": "Reference variations",
                "exam": "JEE",
                "subject": "Mathematics",
                "question_types": [{"type": "single_correct_mcq", "count": 3}],
                "difficulty_distribution": [{"difficulty": 3, "count": 3}],
                "generation_mode": "structural_variation",
            }
        )
        references = [
            {"reference_question_id": "ref-q10", "source_question_number": 10, "stem": "Original ten", "options": ["A", "B", "C", "D"], "question_type": "single_correct_mcq", "difficulty": 3},
            {"reference_question_id": "ref-q11", "source_question_number": 11, "stem": "Original eleven", "options": ["A", "B", "C", "D"], "question_type": "single_correct_mcq", "difficulty": 3},
        ]
        client = FakeOpenRouterClient()
        result = asyncio.run(GenerationService(settings=self.settings, client=client).generate_from_reference(request, references))
        self.assertEqual([slot.reference_question_id for slot in result.slots], ["ref-q10", "ref-q11", "ref-q10"])
        self.assertEqual([slot.reference_question_number for slot in result.slots], [10, 11, 10])
        self.assertEqual([slot.reference_reused for slot in result.slots], [False, False, True])

    def test_reference_generation_uses_the_requested_concept_variation_mode(self) -> None:
        request = request_for("concept_variation")
        references = [
            {"reference_question_id": "ref-q1", "source_question_number": 1, "stem": "Original", "options": ["A", "B", "C", "D"], "question_type": "single_correct_mcq", "difficulty": 3},
        ]
        client = FakeOpenRouterClient()
        result = asyncio.run(GenerationService(settings=self.settings, client=client).generate_from_reference(request, references))

        self.assertEqual(result.generation_mode.value, "concept_variation")
        self.assertTrue(any("assessment planner" in call for call in client.calls))

    def test_concept_pipeline_builds_blueprint_before_generation(self) -> None:
        client = FakeOpenRouterClient()
        result = asyncio.run(GenerationService(settings=self.settings, client=client).generate(request_for("concept_variation")))
        self.assertEqual(result.slots[0].question.correct_answer, "B")
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(any("assessment planner" in call for call in client.calls))

    def test_custom_regeneration_instruction_reaches_generation_prompts(self) -> None:
        for mode in ("structural_variation", "concept_variation"):
            client = FakeOpenRouterClient()
            asyncio.run(GenerationService(settings=self.settings, client=client).generate(
                request_for(mode), custom_instruction="Use a kinematics-style setup."
            ))
            self.assertTrue(any("Use a kinematics-style setup." in prompt for prompt in client.prompts))

    def test_subtopic_plans_build_sectioned_slots(self) -> None:
        request = GenerationRequest.model_validate(
            {
                "title": "Mixed",
                "exam": "JEE",
                "subject": "Mathematics",
                "chapters": ["Calculus"],
                "topics": ["Definite Integrals"],
                "subtopics": ["Properties", "Area"],
                "question_types": [{"type": "single_correct_mcq", "count": 3}],
                "difficulty_distribution": [{"difficulty": 3, "count": 2}, {"difficulty": 1, "count": 1}],
                "generation_mode": "structural_variation",
                "subtopic_plans": [
                    {
                        "topic": "Definite Integrals", "subtopic": "Properties",
                        "question_types": [{"type": "single_correct_mcq", "count": 2}],
                        "difficulty_distribution": [{"difficulty": 3, "count": 2}],
                        "generation_mode": "structural_variation", "variation_strength": "close",
                    },
                    {
                        "topic": "Definite Integrals", "subtopic": "Area",
                        "question_types": [{"type": "single_correct_mcq", "count": 1}],
                        "difficulty_distribution": [{"difficulty": 1, "count": 1}],
                        "generation_mode": "concept_variation", "variation_strength": "high",
                    },
                ],
            }
        )
        slots = request.build_slots()
        self.assertEqual(len(slots), 3)
        self.assertEqual(request.requested_total(), 3)
        self.assertEqual([slot.subtopic for slot in slots], ["Properties", "Properties", "Area"])
        self.assertEqual(slots[0].section_title, "Definite Integrals › Properties")
        self.assertEqual(slots[2].generation_mode, "concept_variation")

    def test_selected_seed_pool_rotates_without_a_question_count_cap(self) -> None:
        request = GenerationRequest.model_validate(
            {
                "title": "Selected seed variations", "exam": "JEE", "subject": "Mathematics",
                "generation_mode": "structural_variation",
                "subtopic_plans": [{
                    "topic": "Definite Integrals", "question_types": [{"type": "single_correct_mcq", "count": 15}],
                    "difficulty_distribution": [{"difficulty": 3, "count": 15}],
                    "seed_question_ids": ["seed-1", "seed-2"],
                }],
            }
        )
        slots = request.build_slots()
        self.assertEqual(len(slots), 15)
        self.assertEqual([slot.selected_seed_question_id for slot in slots[:5]], ["seed-1", "seed-2", "seed-1", "seed-2", "seed-1"])
        self.assertTrue(all(slot.selected_seed_question_ids == ["seed-1", "seed-2"] for slot in slots))

    def test_selected_seed_pool_grounds_every_variation_and_rotates_primary(self) -> None:
        with get_connection() as connection:
            seed_ids = [row["id"] for row in connection.execute("SELECT id FROM questions ORDER BY source_key LIMIT 2").fetchall()]
        request = GenerationRequest.model_validate(
            {
                "title": "Selected pool variations", "exam": "JEE", "subject": "Mathematics",
                "generation_mode": "structural_variation",
                "subtopic_plans": [{
                    "topic": "Definite Integrals", "question_types": [{"type": "single_correct_mcq", "count": 3}],
                    "difficulty_distribution": [{"difficulty": 3, "count": 3}], "seed_question_ids": seed_ids,
                }],
            }
        )
        client = FakeOpenRouterClient()
        result = asyncio.run(GenerationService(settings=self.settings, client=client).generate(request))
        self.assertEqual([slot.seed_question_ids for slot in result.slots], [seed_ids, seed_ids, seed_ids])
        self.assertEqual([slot.primary_seed_question_id for slot in result.slots], [seed_ids[0], seed_ids[1], seed_ids[0]])
        generation_prompts = [prompt for prompt in client.prompts if "Seed questions:" in prompt]
        self.assertEqual(len(generation_prompts), 3)
        self.assertTrue(all("'source_role': 'primary'" in prompt and "'source_role': 'supporting'" in prompt for prompt in generation_prompts))

    def test_automatic_seed_pool_rotates_primary_sources(self) -> None:
        request = GenerationRequest.model_validate(
            {
                "title": "Automatic pool variations", "exam": "JEE", "subject": "Mathematics",
                "chapters": ["Calculus"], "topics": ["Definite Integrals"],
                "question_types": [{"type": "single_correct_mcq", "count": 2}],
                "difficulty_distribution": [{"difficulty": 3, "count": 2}], "generation_mode": "structural_variation",
            }
        )
        result = asyncio.run(GenerationService(settings=self.settings, client=FakeOpenRouterClient()).generate(request))
        self.assertGreaterEqual(len(result.slots[0].seed_question_ids), 2)
        self.assertEqual(result.slots[0].seed_question_ids, result.slots[1].seed_question_ids)
        self.assertNotEqual(result.slots[0].primary_seed_question_id, result.slots[1].primary_seed_question_id)

    def test_topic_level_plan_is_valid_without_a_subtopic(self) -> None:
        request = GenerationRequest.model_validate(
            {
                "title": "Topic-only paper", "exam": "JEE", "subject": "Mathematics",
                "topics": ["Definite Integrals"],
                "question_types": [{"type": "single_correct_mcq", "count": 1}],
                "difficulty_distribution": [{"difficulty": 3, "count": 1}],
                "generation_mode": "concept_variation",
                "subtopic_plans": [{
                    "topic": "Definite Integrals",
                    "question_types": [{"type": "single_correct_mcq", "count": 1}],
                    "difficulty_distribution": [{"difficulty": 3, "count": 1}],
                }],
            }
        )
        slot = request.build_slots()[0]
        self.assertIsNone(slot.subtopic)
        self.assertEqual(slot.section_title, "Definite Integrals")

    def test_strict_schema_requires_defaulted_fields(self) -> None:
        schema = strict_json_schema(GeneratedQuestion.model_json_schema())
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])

    def test_solution_schema_requires_answer_and_working(self) -> None:
        schema = strict_json_schema(GeneratedSolution.model_json_schema())
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_repairs_latex_commands_decoded_as_json_control_characters(self) -> None:
        repaired = repair_decoded_latex_escapes(
            {"solution": "Use \frac{1}{2}, \tan x, \begin{aligned}x\right, \binom{8}{4}, and \asqrt{2}."}
        )
        self.assertEqual(
            repaired["solution"],
            r"Use \frac{1}{2}, \tan x, \begin{aligned}x\right, \binom{8}{4}, and \sqrt{2}.",
        )

    def test_removes_actual_and_literal_nul_latex_sentinels(self) -> None:
        repaired = repair_decoded_latex_escapes({"stem": "\x00\\(S\\u0000\\text{ value}\\)"})
        self.assertEqual(repaired["stem"], r"\(S\text{ value}\)")

    def test_repairs_other_control_prefixed_latex_commands(self) -> None:
        repaired = repair_decoded_latex_escapes({"stem": "\x0bcdots \x01alpha \x1cpi"})
        self.assertEqual(repaired["stem"], r"\cdots \alpha \pi")

    def test_parses_json_wrapped_in_provider_preamble_and_code_fence(self) -> None:
        response = parse_model_json("I have formatted the result." + "\n" + "```JSON" + "\n" + '{"questions": []}' + "\n```")
        self.assertEqual(response, {"questions": []})

    def test_repairs_unescaped_latex_before_json_parsing(self) -> None:
        response = parse_model_json(r'{"stem": "Evaluate \left(x \cdot y\right)"}'.replace('\\"', '"'))
        self.assertEqual(response, {"stem": r"Evaluate \left(x \cdot y\right)"})


if __name__ == "__main__":
    unittest.main()
