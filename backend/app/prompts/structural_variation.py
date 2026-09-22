from __future__ import annotations


SYSTEM_PROMPT = """You create rigorous JEE-level examination questions. Return only JSON that matches the requested schema. Mathematical content uses LaTeX inside strings."""


def build_prompt(*, seeds: list[dict], target_type: str, difficulty: int, variation_strength: str, custom_instruction: str | None = None) -> str:
    has_image_note = " The reference paper image is attached — use it as visual grounding alongside the seed data; treat the image as the primary source for structure and concept if seeds are synthetic." if custom_instruction and "reference" in custom_instruction.lower() else ""
    return f"""Create one structurally varied question.

Target output type: {target_type}
Target difficulty (1-5): {difficulty}
Variation strength: {variation_strength}
Custom regeneration instruction: {custom_instruction or "None"}{has_image_note}

Use the supplied seed questions only as grounding. The source marked primary should guide this variation; use every supporting source to preserve coverage and avoid repeating a single source's surface setup. Preserve the core concept, question archetype, approximate solution strategy, and reasoning depth. Change values, parameters, wording, notation, and setup enough that the result is not a paraphrase. The question must be complete, solvable, and have an answer consistent with its solution.
If a reference image is attached, base the variation on the visible questions in that image and the custom instruction.

Seed questions:
{seeds}
"""
