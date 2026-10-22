"""search_answers only reports content when search answers with status "success"."""

from __future__ import annotations

import pytest
import requests

from sdk_tests import provision_sdk_test_env

pytestmark = pytest.mark.unit


def _answer(status_code: int, body: dict) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = requests.compat.json.dumps(body).encode()
    return response


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (404, {}, False),
        (200, {"searchResponse": {"status": "empty_response"}}, False),
        (200, {}, False),
        (200, {"searchResponse": {"status": "success"}}, True),
    ],
)
def test_search_answers(monkeypatch, status_code, body, expected):
    monkeypatch.setattr(
        provision_sdk_test_env.requests, "post", lambda *a, **kw: _answer(status_code, body)
    )
    assert provision_sdk_test_env.search_answers("http://app.test", {}) is expected
