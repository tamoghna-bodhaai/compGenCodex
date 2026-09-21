"""Keep provider credentials out of API responses, logs, and persisted job state."""

from __future__ import annotations

import re


PROVIDER_FAILURE_MESSAGE = (
    "The AI provider rejected the request. Check the server-side OpenRouter configuration and try again."
)

# OpenRouter keys currently begin with ``sk-or-``.  The broader alternatives
# also protect against common OpenAI-style keys and a complete Bearer value
# echoed by an upstream HTTP error.
_SECRET_PATTERN = re.compile(
    r"(?:bearer\s+)?(?:sk-or-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{16,}|[A-Za-z_]*api[_-]?key\s*[=:]\s*[^\s,;'}\]]+)",
    re.IGNORECASE,
)


def contains_secret(value: object) -> bool:
    return bool(_SECRET_PATTERN.search(str(value)))


def safe_error_message(error: object, *, fallback: str = PROVIDER_FAILURE_MESSAGE) -> str:
    """Return a display-safe error without ever echoing a credential."""
    message = str(error).strip()
    if not message or contains_secret(message):
        return fallback
    return message
