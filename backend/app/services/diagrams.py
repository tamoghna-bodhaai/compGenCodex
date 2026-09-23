"""Durable diagram assets and the small, failure-tolerant diagram pipeline."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.settings import Settings, get_settings
from app.db.database import ROOT_DIR, get_connection
from app.services.openrouter import ModelConfigurationError, OpenRouterClient, OpenRouterError

MEDIA_DIR = ROOT_DIR / "backend" / "data" / "media" / "diagrams"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _image_bytes(data_url: str) -> tuple[bytes, str]:
    header, encoded = data_url.split(",", 1) if "," in data_url else ("", data_url)
    mime = header.split(";")[0].removeprefix("data:") or "image/png"
    return base64.b64decode(encoded), mime


def _asset_info(data: bytes) -> tuple[bytes, str, int, int]:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "image/png", image.width, image.height


def crop_normalized_page(page_base64: str, bbox: list[float]) -> bytes:
    """Crop a normalized source-page box, rejecting invalid/tiny regions."""
    raw = base64.b64decode(page_base64)
    with Image.open(io.BytesIO(raw)) as image:
        x, y, width, height = bbox
        left, top = int(x * image.width), int(y * image.height)
        right, bottom = int((x + width) * image.width), int((y + height) * image.height)
        if right <= left or bottom <= top or left < 0 or top < 0 or right > image.width or bottom > image.height:
            raise ValueError("Diagram crop lies outside the source page.")
        if right - left < 24 or bottom - top < 24:
            raise ValueError("Diagram crop is too small to retain.")
        output = io.BytesIO()
        image.crop((left, top, right, bottom)).save(output, format="PNG")
        return output.getvalue()


def store_diagram(*, owner_column: str, owner_id: str, provenance: str, data: bytes | None, description: str,
                  render_spec: str = "", source_page: int | None = None, crop: dict | None = None,
                  generation_prompt: str | None = None, model: str | None = None,
                  validation_status: str = "accepted", validation_notes: str = "") -> dict:
    if owner_column not in {"seed_question_id", "paper_question_id"}:
        raise ValueError("Unsupported diagram owner.")
    diagram_id, now = str(uuid.uuid4()), _now()
    path: str | None = None
    mime: str | None = None
    width = height = None
    digest = None
    if data:
        normalized, mime, width, height = _asset_info(data)
        digest = hashlib.sha256(normalized).hexdigest()
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        target = MEDIA_DIR / f"{diagram_id}.png"
        target.write_bytes(normalized)
        path = str(target.relative_to(ROOT_DIR))
    with get_connection() as connection:
        connection.execute(
            f"INSERT INTO question_diagrams (id, {owner_column}, storage_path, mime_type, width, height, sha256, provenance, source_page, crop_json, description, render_spec, generation_prompt, model, validation_status, validation_notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (diagram_id, owner_id, path, mime, width, height, digest, provenance, source_page, json.dumps(crop) if crop else None,
             description, render_spec, generation_prompt, model, validation_status, validation_notes, now, now),
        )
    return get_diagram(diagram_id) or {}


def get_diagram(diagram_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM question_diagrams WHERE id = ?", (diagram_id,)).fetchone()
    return _decode(row) if row else None


def diagrams_for(*, owner_column: str, owner_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(f"SELECT * FROM question_diagrams WHERE {owner_column} = ? ORDER BY created_at", (owner_id,)).fetchall()
    return [_decode(row) for row in rows]


def asset_path(diagram: dict) -> Path | None:
    raw = diagram.get("storage_path")
    if not raw:
        return None
    target = (ROOT_DIR / raw).resolve()
    try:
        target.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def asset_data_url(diagram: dict) -> str | None:
    path = asset_path(diagram)
    return _data_url(path.read_bytes(), diagram.get("mime_type") or "image/png") if path else None


def _decode(row: Any) -> dict:
    item = dict(row)
    item["crop"] = json.loads(item.pop("crop_json") or "null")
    item["url"] = f"/api/diagrams/{item['id']}/asset" if item.get("storage_path") else None
    return item


async def generate_diagram(*, question: dict, render_spec: str, source_diagram: dict | None = None,
                           settings: Settings | None = None) -> dict:
    """Return an image or a safe fallback record. Image-model differences stay here."""
    settings = settings or get_settings()
    if not settings.diagram_generation_model:
        return {"status": "unavailable", "notes": "DIAGRAM_GENERATION_MODEL is not configured."}
    prompt = (
        "Create a clean, black-and-white examination diagram only. No answer, solution, decorations, watermark, or prose. "
        "All labels must exactly match this specification. Use clear vector-like lines and readable typography.\n"
        f"Question: {question.get('stem', '')}\nDiagram specification: {render_spec}"
    )
    images = [asset_data_url(source_diagram)] if source_diagram and asset_data_url(source_diagram) else None
    client = OpenRouterClient(settings)
    last_error = ""
    for _ in range(settings.diagram_max_attempts):
        try:
            data_url = await client.call_image(model=settings.diagram_generation_model, prompt=prompt, images=images)
            data, _ = _image_bytes(data_url)
            # A real decode is an inexpensive but useful first validation. Optional
            _asset_info(data)
            if settings.diagram_analysis_model:
                verdict = await client.call_llm(
                    model=settings.diagram_analysis_model,
                    system_prompt="You validate examination diagrams. Return only the schema. Never solve the question.",
                    user_prompt=f"Does this diagram faithfully and legibly satisfy this answer-independent specification? {render_spec}",
                    response_schema={"type": "object", "properties": {"valid": {"type": "boolean"}, "notes": {"type": "string"}}, "required": ["valid", "notes"], "additionalProperties": False},
                    temperature=0.0, max_tokens=180, images=[data_url], cost_context={"operation": "diagram_generation", "phase": "validation"},
                )
                if not verdict.get("valid"):
                    last_error = str(verdict.get("notes") or "Diagram validation rejected the image.")
                    continue
            return {"status": "accepted", "data": data, "prompt": prompt, "model": settings.diagram_generation_model}
        except (OpenRouterError, ModelConfigurationError, ValueError) as error:
            last_error = str(error)
    return {"status": "failed", "notes": last_error or "Image model did not return a usable diagram.", "prompt": prompt, "model": settings.diagram_generation_model}
