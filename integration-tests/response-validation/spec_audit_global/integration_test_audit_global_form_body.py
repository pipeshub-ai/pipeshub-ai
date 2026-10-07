"""express.urlencoded({ extended: true }) is mounted app-wide, so every route also reads form bodies.

Form values arrive as strings and the request validators do not coerce them, so a form body can
only satisfy an operation whose body is all text. The spec lists the form media type only there.
The parser keeps its defaults (100 KB, 1000 fields); past either it fails, before
authentication, with the same 500 INTERNAL_ERROR as the JSON parser.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from global_audit_support import (
    DUMMY_OBJECT_ID,
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
    find,
    operations,
    params_for,
    text_only_problem,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

WITH_JSON_BODY = [op for op in operations() if JSON in op.media_types]
WITH_FORM_BODY = [op for op in operations() if FORM in op.media_types]
TOO_MANY_FIELDS = "&".join(f"f{i}=1" for i in range(FORM_PARAMETER_LIMIT + 1))
OVER_100_KB = "f=" + "x" * (100 * 1024)
TEAMS_ROUTE = "/api/v1/teams"
SMTP_ROUTE = "/api/v1/configurationManager/smtpConfig"
KB_ROUTE = "/api/v1/knowledgeBase"


@pytest.mark.parametrize("op", params_for(WITH_FORM_BODY))
def test_a_documented_form_body_is_all_text(op: Operation) -> None:
    schema = op.request_body()["content"][FORM].get("schema")
    assert schema is not None, f"{op.id} documents a form body without a schema"
    problem = text_only_problem(schema)
    assert problem is None, f"{op.id} documents a form body, but a form cannot carry {problem}"


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


def test_a_route_with_an_all_text_body_accepts_it_form_encoded(pipeshub_client: PipeshubClient) -> None:
    assert FORM in find("post", "/knowledgeBase").media_types
    name = f"spec-audit-global-form-{uuid.uuid4().hex[:10]}"
    resp = pipeshub_client.request("POST", KB_ROUTE, headers=FORM_HEADERS, data={"kbName": name})
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, KB_ROUTE)
        assert resp.json()["name"] == name, resp.text[:500]
    finally:
        if resp.status_code < 300:
            pipeshub_client.request("DELETE", f"{KB_ROUTE}/{resp.json()['id']}")


def test_a_number_field_sent_form_encoded_is_refused_and_the_spec_lists_no_form_body(
    pipeshub_client: PipeshubClient,
) -> None:
    # fromEmail is empty, so even a validator that coerced the port would refuse the body and save nothing.
    resp = pipeshub_client.request(
        "POST", SMTP_ROUTE, headers=FORM_HEADERS,
        data={"host": "smtp.invalid", "port": "587", "fromEmail": ""},
    )
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert "body.port" in {e.get("field") for e in error["metadata"]["errors"]}, resp.text[:500]
    assert FORM not in find("post", "/configurationManager/smtpConfig").media_types
    assert_strict_openapi_exchange(resp, SMTP_ROUTE)


def test_a_list_field_sent_once_form_encoded_is_refused_and_the_spec_lists_no_form_body(
    pipeshub_client: PipeshubClient,
) -> None:
    # qs turns a key sent once into a string, never a one-item list.
    resp = pipeshub_client.request(
        "POST", "/api/v1/users/by-ids", headers=FORM_HEADERS, data={"userIds": DUMMY_OBJECT_ID}
    )
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert "body.userIds" in {e.get("field") for e in error["metadata"]["errors"]}, resp.text[:500]
    assert FORM not in find("post", "/users/by-ids").media_types
    assert_strict_openapi_exchange(resp, "/api/v1/users/by-ids")
