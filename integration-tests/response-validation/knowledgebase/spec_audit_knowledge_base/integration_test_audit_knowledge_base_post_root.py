"""Strict OpenAPI audit of POST /api/v1/knowledgeBase.

body parser -> XSS check (refuses HTML, trims every string) -> authenticate ->
requireScopes(kb:write) -> zod body (kbName: 1..255) -> createKnowledgeBase, which
sends only the name to the connector service (POST /api/v1/kb/).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import requests
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    HTML_REFUSED_MESSAGE,
    request_as,
    unique_name,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase"
CREATED_FIELDS = {"id", "name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"}
MAX_NAME_LENGTH = 255


@pytest.fixture
def created(kb_client: KBClient) -> Iterator[list[str]]:
    """Ids of the knowledge bases a test created; all are deleted afterwards."""
    kb_ids: list[str] = []
    try:
        yield kb_ids
    finally:
        for kb_id in kb_ids:
            kb_client.delete(f"/{kb_id}")


def _created(resp: requests.Response, created: list[str]) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    body: dict[str, Any] = resp.json()
    created.append(body["id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(body) == CREATED_FIELDS, body
    assert body["userRole"] == "OWNER"
    return body


def test_create_returns_the_knowledge_base_with_the_caller_as_owner(
    kb_client: KBClient, created: list[str]
) -> None:
    name = unique_name("spec-audit-create")

    body = _created(kb_client.post("", json={"kbName": name}), created)

    assert body["name"] == name
    assert body["createdAtTimestamp"] == body["updatedAtTimestamp"]
    fetched = kb_client.get(f"/{body['id']}")
    assert fetched.status_code == 200, fetched.text[:500]
    assert fetched.json()["name"] == name


def test_create_as_member_is_allowed(second_user: SecondUser) -> None:
    # No admin gate: any signed-in member owns the knowledge bases they create.
    name = unique_name("spec-audit-member")
    resp = request_as(second_user, "POST", json={"kbName": name})
    assert resp.status_code == 200, resp.text[:500]
    kb_id = resp.json()["id"]
    try:
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["userRole"] == "OWNER"
    finally:
        request_as(second_user, "DELETE", f"/{kb_id}")


def test_create_trims_the_name(kb_client: KBClient, created: list[str]) -> None:
    name = unique_name("spec-audit-padded")
    body = _created(kb_client.post("", json={"kbName": f"  {name}\t"}), created)
    assert body["name"] == name


def test_create_accepts_a_name_at_the_length_limit(kb_client: KBClient, created: list[str]) -> None:
    name = unique_name("k").ljust(MAX_NAME_LENGTH, "k")
    body = _created(kb_client.post("", json={"kbName": name}), created)
    assert body["name"] == name


def test_create_allows_a_name_that_is_already_taken(kb_client: KBClient, created: list[str]) -> None:
    name = unique_name("spec-audit-twin")
    first = _created(kb_client.post("", json={"kbName": name}), created)
    second = _created(kb_client.post("", json={"kbName": name}), created)
    assert first["id"] != second["id"]
    assert first["name"] == second["name"] == name


def test_create_ignores_fields_other_than_the_name(kb_client: KBClient, created: list[str]) -> None:
    name = unique_name("spec-audit-extra")
    with outside_request_contract("the validator drops body fields it does not know"):
        resp = kb_client.post(
            "", json={"kbName": name, "isHidden": True, "userRole": "READER", "orgId": "another-org"}
        )
        body = _created(resp, created)
    assert body["name"] == name
    # isHidden never reaches the connector service: the knowledge base is listed as usual.
    listed = kb_client.get("", params={"search": name})
    assert listed.status_code == 200, listed.text[:500]
    assert [kb["id"] for kb in listed.json()["knowledgeBases"]] == [body["id"]]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="name-missing"),
        pytest.param({"kbName": None}, id="name-null"),
        pytest.param({"kbName": ""}, id="name-empty"),
        pytest.param({"kbName": " \t "}, id="name-only-whitespace"),
        pytest.param({"kbName": "k" * (MAX_NAME_LENGTH + 1)}, id="name-too-long"),
        pytest.param({"kbName": 20261006}, id="name-not-a-string"),
        pytest.param({"name": "the connector service's own field name"}, id="python-field-name"),
        pytest.param(["spec-audit"], id="body-is-a-list"),
    ],
)
def test_create_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.post("", json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_a_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.post("")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("<b>spec-audit</b>", id="html-tag"),
        pytest.param("spec-audit javascript:alert(1)", id="javascript-url"),
        pytest.param("spec-audit onclick=alert(1)", id="event-handler"),
    ],
)
def test_create_refuses_html_in_the_name_before_the_token_check(kb_client: KBClient, name: str) -> None:
    # The global XSS check runs ahead of the router, so no token is needed to get this answer.
    resp = kb_client.post("", auth=False, json={"kbName": name})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert HTML_REFUSED_MESSAGE in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_create_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post("", auth=False, headers=headers, json={"kbName": unique_name()})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post("", auth=False, headers=unscoped_headers, json={"kbName": unique_name()})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_checks_the_scope_before_the_body(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post("", auth=False, headers=unscoped_headers, json={})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

