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


@pytest.mark.parametrize(
    "nodeid",
    [
        "unit/test_request_id.py::test_short",
        _LONG_NODEID,
        f"{_LONG_NODEID}@serial",
    ],
)
def test_id_survives_backend_sanitizer(nodeid: str, _current_test) -> None:
    """The backend must log the id we sent, not a stripped or truncated one."""
    _current_test(nodeid)
    request_id = generate_request_id()

    assert len(request_id) <= 64
    assert sanitize_root_id(request_id) == request_id
    assert request_id.startswith(request_id_prefix(nodeid))


def test_prefix_is_shared_but_each_id_is_unique(_current_test) -> None:
    _current_test(_LONG_NODEID)
    first, second = generate_request_id(), generate_request_id()

    assert first != second
    assert first.startswith(request_id_prefix(_LONG_NODEID))
    assert second.startswith(request_id_prefix(_LONG_NODEID))


def test_xdist_group_suffix_does_not_change_the_prefix() -> None:
    """The report recomputes the prefix from the controller's stripped node id."""
    assert request_id_prefix(f"{_LONG_NODEID}@serial") == request_id_prefix(_LONG_NODEID)


def _prepare(headers: dict[str, str] | None = None) -> requests.PreparedRequest:
    install_requests_hook()
    return requests.Session().prepare_request(
        requests.Request("GET", "http://localhost:3001/api/v1/health", headers=headers)
    )


def test_hook_stamps_the_header(request: pytest.FixtureRequest) -> None:
    prepared = _prepare()

    assert prepared.headers[HEADER_REQUEST_ID].startswith(
        request_id_prefix(request.node.nodeid)
    )


def test_hook_does_not_override_an_explicit_header() -> None:
    prepared = _prepare({"X-Request-ID": "caller-supplied-id"})

    assert prepared.headers[HEADER_REQUEST_ID] == "caller-supplied-id"
