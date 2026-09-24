"""Unit tests for the x-request-id the suite sends (no network, no services)."""

from __future__ import annotations

import pytest
import requests

from app.utils.request_context import HEADER_REQUEST_ID, sanitize_root_id
from helper.http.request_id import (
    generate_request_id,
    install_requests_hook,
    request_id_prefix,
    set_current_test,
)

_LONG_NODEID = (
    "response-validation/knowledgebase/integration_test_knowledgebase_crud.py"
    "::TestKnowledgeBaseCRUD::test_create_knowledge_base_with_a_very_long_name[case-with-params]"
)


@pytest.fixture
def _current_test(request: pytest.FixtureRequest):
    """Bind a node id for the test, then restore this test's own."""

    def _bind(nodeid: str) -> None:
        set_current_test(nodeid)

    yield _bind
    set_current_test(request.node.nodeid)


def test_generated_id_survives_sanitizer_and_stays_unique(_current_test) -> None:
    for nodeid in (
        "unit/test_request_id.py::test_short",
        _LONG_NODEID,
        f"{_LONG_NODEID}@serial",
    ):
        _current_test(nodeid)
        request_id = generate_request_id()
        assert len(request_id) <= 64
        assert sanitize_root_id(request_id) == request_id
        assert request_id.startswith(request_id_prefix(nodeid))

    _current_test(_LONG_NODEID)
    first, second = generate_request_id(), generate_request_id()
    assert first != second
    assert first.startswith(request_id_prefix(_LONG_NODEID))
    assert second.startswith(request_id_prefix(_LONG_NODEID))
    assert request_id_prefix(f"{_LONG_NODEID}@serial") == request_id_prefix(_LONG_NODEID)


def _prepare(headers: dict[str, str] | None = None) -> requests.PreparedRequest:
    install_requests_hook()
    return requests.Session().prepare_request(
        requests.Request("GET", "http://localhost:3001/api/v1/health", headers=headers)
    )


def test_hook_stamps_header_but_keeps_an_explicit_one(
    request: pytest.FixtureRequest,
) -> None:
    stamped = _prepare()
    assert stamped.headers[HEADER_REQUEST_ID].startswith(
        request_id_prefix(request.node.nodeid)
    )

    explicit = _prepare({"X-Request-ID": "caller-supplied-id"})
    assert explicit.headers[HEADER_REQUEST_ID] == "caller-supplied-id"
