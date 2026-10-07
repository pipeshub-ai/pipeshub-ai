"""xssSanitizationMiddleware refuses markup in any query value or body string, on every route.

It is mounted app-wide after the body parsers and before every router, so the refusal
comes before authentication and before the path is matched; dummy ids are enough. It
skips POST/PUT under /agents/, /conversations/ and /connectors, and POST/PUT/PATCH
under /skills; those reach their routers with the markup intact.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import requests
from global_audit_support import (
    JSON,
    TRIM_MARKER,
    XSS_DESCRIPTION_MARKER,
    XSS_MESSAGE,
    XSS_QUERY_PARAMETER,
    XSS_VALUE,
    Operation,
    call,
    error_of,
    operations,
    params_for,
    spec,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

CHECKED = [op for op in operations() if not op.xss_exempt]
EXEMPT = [op for op in operations() if op.xss_exempt]
CHECKED_WITH_JSON_BODY = [op for op in CHECKED if JSON in op.media_types]
TEAMS_ROUTE = "/api/v1/teams"

REFUSED_VALUES = [
    pytest.param(XSS_VALUE, id="html-tag"),
    pytest.param("javascript:alert(1)", id="javascript-url"),
    pytest.param("data:text/html,spec-audit", id="data-html-url"),
    pytest.param("&lt;script", id="encoded-script"),
    # The inline-event-handler pattern is any word starting with "on" followed by "=".
    pytest.param("one=1", id="plain-text-on-equals"),
]


def _assert_refused(resp: requests.Response, op: Operation) -> None:
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert error["message"] == XSS_MESSAGE, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    described = (op.response("400") or {}).get("description", "")
    assert XSS_DESCRIPTION_MARKER in described, f"the 400 of {op.id} does not describe the markup refusal"


@pytest.mark.parametrize("value", REFUSED_VALUES)
@pytest.mark.parametrize("op", params_for(CHECKED))
def test_markup_in_a_query_value_is_refused_before_authentication(
    pipeshub_client: PipeshubClient, op: Operation, value: str
) -> None:
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        params={XSS_QUERY_PARAMETER: value},
    )
    _assert_refused(resp, op)


@pytest.mark.parametrize(
    "value",
    [pytest.param(XSS_VALUE, id="html-tag"), pytest.param("a" * 100_001, id="over-100000-characters")],
)
@pytest.mark.parametrize("op", params_for(CHECKED_WITH_JSON_BODY))
def test_a_json_body_string_the_filter_dislikes_is_refused_before_authentication(
    pipeshub_client: PipeshubClient, op: Operation, value: str
) -> None:
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        json={"specAudit": {"nested": [value]}},
    )
    _assert_refused(resp, op)


@pytest.mark.parametrize("where", ["query", "json-body"])
@pytest.mark.parametrize("op", params_for(EXEMPT))
def test_exempt_routes_let_markup_through_to_authentication(
    pipeshub_client: PipeshubClient, op: Operation, where: str
) -> None:
    markup = {"params": {XSS_QUERY_PARAMETER: XSS_VALUE}} if where == "query" else {"json": {"specAudit": XSS_VALUE}}
    resp = call(pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds, **markup)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    described = (op.response("400") or {}).get("description", "")
    assert XSS_DESCRIPTION_MARKER not in described, f"{op.id} is exempt but its 400 claims the markup refusal"


def test_the_spec_describes_the_filter_once_for_every_route() -> None:
    description = spec()["info"]["description"]
    assert XSS_DESCRIPTION_MARKER in description
    assert TRIM_MARKER in description
    assert "`a < b` arrives as `a`" in description
    assert "`&gt;`" in description


@pytest.fixture
def created_team_ids(teams_client: TeamsClient) -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    for team_id in ids:
        teams_client.delete_team(team_id)


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        pytest.param("  padded \t", "padded", id="trimmed"),
        pytest.param("a < b & c", "a", id="lone-less-than-drops-the-rest"),
        pytest.param("1 > 0", "1 &gt; 0", id="greater-than-escaped"),
        pytest.param("a & b \"q\" 'q'", "a & b \"q\" 'q'", id="other-characters-kept"),
    ],
)
def test_strings_the_filter_lets_through_are_rewritten(
    teams_client: TeamsClient, created_team_ids: list[str], sent: str, stored: str
) -> None:
    name = f"spec-audit-global-{uuid.uuid4().hex[:10]}"
    resp = teams_client.create_team(f"  {name} ", description=sent)
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    team = resp.json()["data"]
    created_team_ids.append(team["id"])
    assert team["name"] == name
    assert team["description"] == stored
