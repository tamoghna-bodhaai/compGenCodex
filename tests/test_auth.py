from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException, Request, Response
from app.api.auth import login, logout, session
from app.core.auth import create_session, credentials_are_valid, get_auth_settings, verify_session
from app.db.database import initialize_database
from app.schemas.auth import LoginRequest


def request_with_cookie(cookie: str | None) -> Request:
    headers = [(b"host", b"testserver")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "GET", "path": "/api/auth/session", "headers": headers})


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = dict(os.environ)
        self.database_directory = tempfile.TemporaryDirectory()
        os.environ.update({
            "AUTH_ALLOWED_EMAIL": "utils@bodhaai.tech",
            "AUTH_ACCESS_CODES": "12345,67890",
            "AUTH_SESSION_SECRET": "test-session-secret-with-more-than-thirty-two-characters",
            "AUTH_SESSION_TTL_HOURS": "1",
            "AUTH_COOKIE_SECURE": "false",
            "DATABASE_URL": f"sqlite:///{Path(self.database_directory.name) / 'test.db'}",
        })
        initialize_database()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.environ)
        self.database_directory.cleanup()

    def test_allowlisted_email_and_reusable_codes_are_required(self) -> None:
        settings = get_auth_settings()
        self.assertTrue(credentials_are_valid("utils@bodhaai.tech", "12345", settings))
        self.assertTrue(credentials_are_valid("utils@bodhaai.tech", "67890", settings))
        self.assertFalse(credentials_are_valid("other@bodhaai.tech", "12345", settings))
        self.assertFalse(credentials_are_valid("utils@bodhaai.tech", "00000", settings))

    def test_sessions_expire_and_tampered_sessions_fail(self) -> None:
        settings = get_auth_settings()
        token = create_session(settings.allowed_email, settings, now=100)
        self.assertEqual(verify_session(token, settings, now=101), settings.allowed_email)
        self.assertIsNone(verify_session(token, settings, now=3700))
        self.assertIsNone(verify_session(f"{token}x", settings, now=101))

    def test_login_sets_a_cookie_and_session_reads_it(self) -> None:
        response = Response()
        result = login(LoginRequest(email="utils@bodhaai.tech", access_code="12345"), response)
        self.assertTrue(result.authenticated)
        cookie = next(value.decode() for key, value in response.raw_headers if key == b"set-cookie").split(";", 1)[0]
        session_result = session(request_with_cookie(cookie), Response())
        self.assertTrue(session_result.authenticated)

    def test_invalid_session_cookie_is_expired(self) -> None:
        response = Response()
        result = session(
            request_with_cookie("paper_studio_session=invalid"),
            response,
        )

        self.assertFalse(result.authenticated)
        cookie = next(value.decode() for key, value in response.raw_headers if key == b"set-cookie")
        self.assertIn("paper_studio_session=\"\"", cookie)

    def test_login_rejects_invalid_credentials(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            login(LoginRequest(email="other@bodhaai.tech", access_code="12345"), Response())
        self.assertEqual(raised.exception.status_code, 401)

    def test_parallel_invalid_sign_ins_respect_the_account_limit(self) -> None:
        login(LoginRequest(email="utils@bodhaai.tech", access_code="12345"), Response())

        def attempt(_: int) -> int:
            try:
                login(LoginRequest(email="utils@bodhaai.tech", access_code="00000"), Response())
                return 200
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(attempt, range(15)))
        self.assertEqual(outcomes.count(401), 10)
        self.assertEqual(outcomes.count(429), 5)

    def test_logout_clears_the_cookie(self) -> None:
        response = logout()
        self.assertEqual(response.status_code, 204)
        cookie = next(value.decode() for key, value in response.raw_headers if key == b"set-cookie")
        self.assertIn("paper_studio_session=\"\"", cookie)
