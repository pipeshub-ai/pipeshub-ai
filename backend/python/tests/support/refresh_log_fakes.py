"""Fakes and checks shared by the credential refresh logging tests (connectors, toolsets).

The real OAuthProvider sends each refresh to :class:`FakeTokenEndpoint` (it replaces
aiohttp.ClientSession in oauth_service), so the lines are the ones production writes.
Fixtures stay in each test file: an autouse one here would need a conftest.py.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields

if TYPE_CHECKING:
    import logging

    import pytest

SEEDED_RT = "rt-SEEDED-0123456789abcdef"
SEEDED_SECRET = "cs-SEEDED-fedcba9876543210"
TOKEN_URL = "https://idp.example/token"
REDIRECT_URI = "http://localhost/cb"
# Providers echo the refresh token in prose: no redactor pattern matches it.
INVALID_GRANT = (400, f'{{"error": "invalid_grant", "error_description": "refresh token {SEEDED_RT} is not active"}}')
LEAK_MARKERS = (SEEDED_RT, SEEDED_SECRET, "idp.example", "localhost/cb", "is not active")


class FakeTokenEndpoint:
    """Answers each token request with the next scripted answer: "ok", (status, body) or an exception."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.sent: list[dict[str, str]] = []
        self.issued = 0

    def client_session(self, *_: object, **__: object) -> _Session:
        return _Session(self)

    def answer(self, form: dict[str, str]) -> tuple[int, str]:
        self.sent.append(form)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if answer == "ok":
            self.issued += 1
            return 200, json.dumps({"access_token": f"at-new-{self.issued}", "refresh_token": f"rt-new-{self.issued}",
                                    "expires_in": 3600})
        return answer  # type: ignore[return-value]


class _Session:
    closed = False

    def __init__(self, endpoint: FakeTokenEndpoint) -> None:
        self._endpoint = endpoint

    def post(self, _url: str, *, data: dict | None = None, json: dict | None = None, **_: object) -> _Response:
        return _Response(self._endpoint, {k: str(v) for k, v in (data or json or {}).items()})

    async def close(self) -> None:
        self.closed = True


class _Response:
    headers = {"Content-Type": "application/json"}

    def __init__(self, endpoint: FakeTokenEndpoint, form: dict[str, str]) -> None:
        self._endpoint, self._form = endpoint, form
        self.status, self._text = 0, ""

    async def __aenter__(self) -> _Response:
        self.status, self._text = self._endpoint.answer(self._form)
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return self._text

    async def json(self) -> dict:
        return json.loads(self._text)


class FakeConfigStore:
    """ConfigurationService's read, write and list, over a dict."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.save_result: bool | None = True
        self.read_error: BaseException | None = None

    async def get_config(self, key: str, default: object = None, *_: object, **__: object) -> object:
        if self.read_error is not None:
            raise self.read_error
        return copy.deepcopy(self.store[key]) if key in self.store else default

    async def set_config(self, key: str, value: object) -> bool | None:
        if self.save_result is not False:
            self.store[key] = copy.deepcopy(value)
        return self.save_result

    async def list_keys_in_directory(self, prefix: str) -> list[str]:
        return [key for key in self.store if key.startswith(prefix)]


def events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    """Every captured record with this event name, from any logger."""
    return [r for r in caplog.records if r.getMessage() == event]


def fields(record: logging.LogRecord) -> dict[str, object]:
    return log_record_fields(record)


def assert_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """No captured record, from any logger, rendered as either sink renders it, holds a seeded secret or the endpoint's text."""
    for record in caplog.records:
        rendered = TextFormatter().format(record) + JsonFormatter().format(record) + repr(log_record_fields(record))
        for seeded in LEAK_MARKERS:
            assert seeded not in rendered, (seeded, record.getMessage())
