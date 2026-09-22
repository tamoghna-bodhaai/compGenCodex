from __future__ import annotations


SYSTEM_PROMPT = """You are an academic assessment planner. Return only JSON matching the requested schema."""


def build_prompt(*, seeds: list[dict], requested_concepts: list[str], target_type: str, difficulty: int, variation_strength: str, custom_instruction: str | None = None) -> str:
    return f"""Plan a genuinely new JEE question blueprint using the seeds as conceptual grounding only.

Requested concepts: {requested_concepts or 'infer the shared primary concept from the seeds'}
Target question type: {target_type}
Target difficulty (1-5): {difficulty}
Originality strength: {variation_strength}
Custom regeneration instruction: {custom_instruction or "None"}

The source marked primary should guide this variation; use every supporting source as conceptual context. Choose an archetype, setup, unknown, and reasoning path different from the dominant seed archetype. Do not reuse the seed's surface setup or unknown.

Seeds:
{seeds}
"""
