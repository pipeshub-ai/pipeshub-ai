"""express.urlencoded({ extended: true }) is mounted app-wide, so every route also reads form bodies.

An operation documented with a JSON body therefore also takes the same fields form-encoded.
The parser keeps its defaults (100 KB, 1000 fields); past either it fails, before
authentication, with the same 500 INTERNAL_ERROR as the JSON parser.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from global_audit_support import (
    FORM,
    FORM_HEADERS,
    FORM_PARAMETER_LIMIT,
    INTERNAL_ERROR,
    JSON,
    XSS_MESSAGE,
    XSS_VALUE,
    Operation,
    call,
    error_of,
    operations,
    params_for,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

WITH_JSON_BODY = [op for op in operations() if JSON in op.media_types]
TOO_MANY_FIELDS = "&".join(f"f{i}=1" for i in range(FORM_PARAMETER_LIMIT + 1))
OVER_100_KB = "f=" + "x" * (100 * 1024)
TEAMS_ROUTE = "/api/v1/teams"


@pytest.mark.parametrize("op", params_for(WITH_JSON_BODY))
def test_an_operation_with_a_json_body_documents_the_same_body_form_encoded(op: Operation) -> None:
    content = op.request_body()["content"]
    assert FORM in content, f"{op.id} documents {sorted(content)}"
    assert content[FORM].get("schema") == content[JSON].get("schema")


@pytest.mark.parametrize("op", params_for([op for op in WITH_JSON_BODY if not op.xss_exempt]))
def test_a_form_body_reaches_the_request_body(pipeshub_client: PipeshubClient, op: Operation) -> None:
    # The markup filter only sees what a body parser put on req.body, so its refusal shows the form was parsed.
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        headers=FORM_HEADERS, data={"specAudit": XSS_VALUE},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["message"] == XSS_MESSAGE, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)


@pytest.mark.parametrize(
    ("body", "marker"),
    [pytest.param(TOO_MANY_FIELDS, "1000 fields", id="over-1000-fields"), pytest.param(OVER_100_KB, "100 KB", id="over-100-kb")],
)
@pytest.mark.parametrize("op", params_for(operations()))
def test_a_form_body_over_the_parser_limits_is_an_internal_error(
    pipeshub_client: PipeshubClient, op: Operation, body: str, marker: str
) -> None:
    resp = call(pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds, headers=FORM_HEADERS, data=body)
    assert resp.status_code == 500, resp.text[:500]
    assert error_of(resp)["code"] == INTERNAL_ERROR, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    described = (op.response("500") or {}).get("description", "")
    assert marker in described, f"the 500 of {op.id} does not mention the form parser limit"


@pytest.fixture
def created_team_ids(teams_client: TeamsClient) -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    for team_id in ids:
        teams_client.delete_team(team_id)


def test_a_route_documented_with_json_accepts_the_same_fields_form_encoded(
    teams_client: TeamsClient, created_team_ids: list[str]
) -> None:
    name = f"spec-audit-global-form-{uuid.uuid4().hex[:10]}"
    resp = teams_client.post("", headers=FORM_HEADERS, data={"name": name, "description": "sent as a form"})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    team = resp.json()["data"]
    created_team_ids.append(team["id"])
    assert (team["name"], team["description"]) == (name, "sent as a form")
