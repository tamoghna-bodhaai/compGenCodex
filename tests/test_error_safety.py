from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.core.error_safety import PROVIDER_FAILURE_MESSAGE, safe_error_message


class ErrorSafetyTests(unittest.TestCase):
    def test_openrouter_key_is_replaced_with_a_generic_message(self) -> None:
        raw = "Invalid header value b'Bearer sk-or-v1-this-must-never-reach-the-browser'"
        self.assertEqual(safe_error_message(raw), PROVIDER_FAILURE_MESSAGE)

    def test_normal_user_safe_message_is_preserved(self) -> None:
        self.assertEqual(safe_error_message("OpenRouter returned invalid JSON."), "OpenRouter returned invalid JSON.")
