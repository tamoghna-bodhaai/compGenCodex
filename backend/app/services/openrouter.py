from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.error_safety import PROVIDER_FAILURE_MESSAGE
from app.core.settings import Settings
from app.services.costs import record_llm_cost

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make Pydantic's JSON Schema valid for strict OpenAI-compatible output.

    OpenRouter may route a request to providers that enforce the OpenAI strict
    schema subset. In that subset each object must reject unknown fields and
    every declared property must be in ``required``—including fields that are
    optional in the application model. Nullable fields still use their existing
    ``anyOf`` shape, so callers can return ``null`` where appropriate.
    """
    normalized = copy.deepcopy(schema)

    def normalize(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for child in value.values():
                normalize(child)
        elif isinstance(value, list):
            for child in value:
                normalize(child)

    normalize(normalized)
    return normalized


class ModelConfigurationError(RuntimeError):
    """Raised when a generation operation is attempted without model settings."""


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter rejects or cannot complete a request."""


def repair_decoded_latex_escapes(value: Any) -> Any:
    r"""Repair unescaped LaTeX commands that JSON has decoded as controls.

    A model can incorrectly emit ``\\frac`` as ``\frac`` in its JSON text.
    JSON then decodes ``\f`` as a form-feed before schema validation. These
    replacements are intentionally limited to recognizable LaTeX command
    suffixes, leaving legitimate whitespace untouched.
    """
    if isinstance(value, str):
        return _repair_latex_controls(value)
    if isinstance(value, list):
        return [repair_decoded_latex_escapes(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_decoded_latex_escapes(item) for key, item in value.items()}
    return value


def _repair_latex_controls(value: str) -> str:
    # Some providers use a NUL sentinel around TeX commands.  It can arrive as
    # an actual control character after JSON decoding or as the literal text
    # ``\\u0000`` after the fallback JSON repair below.  It has no semantic
    # meaning in the question content, so remove it before rendering or saving.
    repaired = (
        value.replace("\\u0000", "")
        .replace("\x00", "")
        .replace("\f" + "rac", "\\frac")
        .replace("\t" + "an", "\\tan")
        .replace("\t" + "ext", "\\text")
        .replace("\b" + "egin", "\\begin")
        .replace("\b" + "ox", "\\box")
        .replace("\b" + "inom", "\\binom")
        .replace("\r" + "ight", "\\right")
        .replace("\a" + "sqrt", "\\sqrt")
    )
    # A few providers substitute other C0 controls for the leading backslash
    # (for example, ``\\alpha`` becomes ``\\x01alpha``).  Newlines, tabs,
    # and carriage returns are intentionally excluded so prose formatting is
    # never changed.
    return re.sub(r"[\x01-\x08\x0b-\x1f](?=[A-Za-z])", r"\\", repaired)


def parse_model_json(content: str) -> dict[str, Any]:
    """Parse JSON even when a provider adds a fence or a short preamble.

    Some OpenRouter providers return otherwise-valid structured output wrapped
    in Markdown or reasoning text despite ``response_format``.  We only accept
    a complete JSON object and never attempt to interpret prose as data.
    """
    candidates = [content.strip()]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\\s*(.*?)```", content, re.IGNORECASE | re.DOTALL))
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            if start < 0:
                continue
            try:
                value, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                # Models often write LaTeX as ``\left`` or ``\cdot`` instead
                # of JSON-safe ``\\left``/``\\cdot``.  Escape those command
                # prefixes only after normal JSON parsing has failed.
                repaired_candidate = re.sub(r"\\(?=[A-Za-z])", r"\\\\", candidate[start:])
                try:
                    value, _ = decoder.raw_decode(repaired_candidate)
                except json.JSONDecodeError:
                    continue
        if isinstance(value, dict):
            return repair_decoded_latex_escapes(value)
    raise OpenRouterError("OpenRouter returned invalid JSON; the classification model did not produce a complete JSON object.")


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def call_llm(
        self,
        *,
        model: str | None,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        temperature: float = 0.4,
        max_tokens: int = 2400,
        images: list[str] | None = None,
        cost_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.openrouter_api_key or not model:
            raise ModelConfigurationError(
                "OpenRouter is not configured. Set OPENROUTER_API_KEY and an OpenRouter model for this operation."
            )
        if images:
            user_content: Any = [{"type": "text", "text": user_prompt}]
            for b64 in images:
                # Accept raw base64 (default PNG) or full data URL with explicit mime
                if b64.startswith("data:"):
                    url = b64
                elif b64.startswith("http"):
                    url = b64
                else:
                    # Heuristic: JPEG magic: /9j/ prefix indicates JPEG
                    mime = "image/png"
                    if b64.startswith("/9j/"):
                        mime = "image/jpeg"
                    elif b64.startswith("UklGR"):
                        mime = "image/webp"
                    url = f"data:{mime};base64,{b64}"
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_prompt
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "question_generator_response",
                    "strict": True,
                    "schema": strict_json_schema(response_schema),
                },
            },
        }
        response = await asyncio.to_thread(self._post, payload)
        record_llm_cost(context=cost_context, response=response, requested_model=model)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise OpenRouterError("OpenRouter response did not include a message.") from error
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise OpenRouterError("OpenRouter returned a non-text structured response.")
        return parse_model_json(content)

    async def call_image(self, *, model: str | None, prompt: str, images: list[str] | None = None) -> str:
        """Call OpenRouter image-capable models and normalize common image responses.

        Providers differ: some return an ``images`` array, others return an
        image content part. Keeping that variation out of question services
        makes the selected model entirely configuration-driven.
        """
        if not self.settings.openrouter_api_key or not model:
            raise ModelConfigurationError("Diagram generation is not configured. Set OPENROUTER_API_KEY and DIAGRAM_GENERATION_MODEL.")
        content: Any = [{"type": "text", "text": prompt}]
        for image in images or []:
            content.append({"type": "image_url", "image_url": {"url": image}})
        payload = {"model": model, "messages": [{"role": "user", "content": content}], "modalities": ["image", "text"], "max_tokens": 800}
        response = await asyncio.to_thread(self._post, payload)
        record_llm_cost(context={"operation": "diagram_generation", "phase": "image"}, response=response, requested_model=model)
        candidates: list[Any] = []
        candidates.extend(response.get("images") or [])
        try:
            message = response["choices"][0]["message"]
            candidates.extend(message.get("images") or [])
            if isinstance(message.get("content"), list):
                candidates.extend(message["content"])
        except (KeyError, IndexError, TypeError):
            pass
        for item in candidates:
            if isinstance(item, str) and item.startswith("data:image/"):
                return item
            if isinstance(item, dict):
                url = item.get("image_url", {}).get("url") if isinstance(item.get("image_url"), dict) else item.get("url")
                if isinstance(url, str) and url.startswith("data:image/"):
                    return url
                b64 = item.get("b64_json") or item.get("data")
                if isinstance(b64, str):
                    return f"data:image/png;base64,{b64}"
        raise OpenRouterError("The selected diagram model did not return an image asset.")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            OPENROUTER_CHAT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "AI Question Variation Paper Generator",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed OpenRouter endpoint
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # Do not include the response body. Providers sometimes echo the
            # rejected Authorization header verbatim, which would leak a key
            # into a user-visible generation job or API error.
            raise OpenRouterError(f"{PROVIDER_FAILURE_MESSAGE} (HTTP {error.code}.)") from error
        except URLError as error:
            raise OpenRouterError("Could not reach OpenRouter.") from error
