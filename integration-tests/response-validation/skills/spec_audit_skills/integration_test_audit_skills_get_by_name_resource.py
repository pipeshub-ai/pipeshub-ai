"""Strict OpenAPI audit of GET /api/v1/skills/:name/resource."""

from __future__ import annotations

import json
from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_RESOURCE_PATH,
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/resource"

RESOURCE_TEXT = "# Spec audit reference\n\nplain text, not json\n"


def _write(skills_client: SkillsClient, name: str, content: str) -> None:
    written = skills_client.put(f"/{name}/resource", json={"path": RESOURCE_PATH, "content": content})
    assert written.status_code == 200, f"writing the resource failed: {written.text[:500]}"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(RESOURCE_TEXT, RESOURCE_TEXT, id="prose-as-json-string"),
        pytest.param("", "", id="empty-file-as-empty-string"),
        pytest.param('{"a": 1}', {"a": 1}, id="json-object-text-as-object"),
        pytest.param("[1, 2]", [1, 2], id="json-array-text-as-array"),
        pytest.param("123", 123, id="json-number-text-as-number"),
        pytest.param("true", True, id="json-boolean-text-as-boolean"),
        pytest.param('"quoted"', "quoted", id="json-string-text-unquoted"),
    ],
)
def test_get_resource_returns_file_content_as_json(
    skills_client: SkillsClient, seed_skill: SeedSkill, content: str, expected: Any
) -> None:
    # API bug: the skills service answers text/plain, but the gateway's axios parses that text
    # as JSON when it can and re-sends the value through res.json, so the raw file never arrives.
    name = seed_skill()["name"]
    _write(skills_client, name, content)

    resp = skills_client.get(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.json() == expected
    if content == RESOURCE_TEXT:
        assert resp.text == json.dumps(RESOURCE_TEXT, separators=(",", ":"))


def test_get_resource_json_null_text_arrives_as_null(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    _write(skills_client, name, "null")

    resp = skills_client.get(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.text == "null"


def test_get_builtin_skill_resource_is_not_found_for_unknown_path(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    resp = skills_client.get(f"/{builtin_skill_name}/resource", params={"path": MISSING_RESOURCE_PATH})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_query_parameter_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    _write(skills_client, name, RESOURCE_TEXT)
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.get(
            f"/{name}/resource", params={"path": RESOURCE_PATH, "spec_audit_unknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_get_resource_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(
        f"/{MISSING_SKILL_NAME}/resource", auth=False, params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "error_type"),
    [
        pytest.param({}, "missing", id="path_missing"),
        pytest.param({"path": ""}, "string_too_short", id="path_empty"),
        # The gateway forwards a repeated parameter as path[]=..., which the service does not read.
        pytest.param([("path", RESOURCE_PATH), ("path", MISSING_RESOURCE_PATH)], "missing", id="path_repeated"),
    ],
)
def test_get_resource_invalid_path_query_is_unprocessable(
    skills_client: SkillsClient, params: Any, error_type: str
) -> None:
    # FastAPI validates the query before the skill is looked up, so no seed is needed.
    with outside_request_contract("a path query the spec does not allow"):
        resp = skills_client.get(f"/{MISSING_SKILL_NAME}/resource", params=params)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["detail"][0]
    assert (error["loc"], error["type"]) == (["query", "path"], error_type)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("path", [MISSING_RESOURCE_PATH, "../spec-audit-escape.md"], ids=["unknown", "traversal"])
def test_get_missing_resource_of_existing_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill, path: str
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/resource", params={"path": path})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Resource {path!r} not found for skill {name!r}"}


@pytest.mark.parametrize("whose", ["missing", "another-user"])
def test_get_resource_of_invisible_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser, whose: str
) -> None:
    # The lookup is reported as a missing resource, not a missing skill.
    if whose == "missing":
        name = MISSING_SKILL_NAME
    else:
        name = seed_skill(owner=second_user)["name"]
    resp = skills_client.get(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Resource {RESOURCE_PATH!r} not found for skill {name!r}"}


def test_get_resource_unsafe_name_is_rejected_by_path_guard(
    skills_client: SkillsClient,
) -> None:
    resp = skills_client.get(
        f"/{UNSAFE_PATH_SEGMENT}/resource", params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
