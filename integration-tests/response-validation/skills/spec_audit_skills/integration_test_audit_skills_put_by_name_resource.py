"""Strict OpenAPI audit of PUT /api/v1/skills/:name/resource."""

from __future__ import annotations

from typing import Any

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/resource"
RESOURCE_CONTENT = "# Spec audit resource\n\nWritten by the skills spec audit.\n"


def _listed_paths(skill: dict[str, Any]) -> list[str]:
    # GET /:name lists resources as {kind: [paths]}, never their content.
    return [path for paths in skill["resources"].values() for path in paths]


def test_put_writes_then_overwrites_resource(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.put(
        f"/{name}/resource", json={"path": RESOURCE_PATH, "content": RESOURCE_CONTENT}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    # Same path again is an upsert, not a conflict; empty content is a valid file.
    again = skills_client.put(
        f"/{name}/resource", json={"path": RESOURCE_PATH, "content": ""}
    )
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert _listed_paths(stored.json()) == [RESOURCE_PATH]


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        pytest.param(
            {"path": "../spec-audit-escape.md", "content": RESOURCE_CONTENT},
            400,
            id="path_traversal",
        ),
        pytest.param({"path": RESOURCE_PATH}, 422, id="content_missing"),
    ],
)
def test_put_refused_on_existing_skill(
    skills_client: SkillsClient,
    seed_skill: SeedSkill,
    payload: dict[str, Any],
    expected_status: int,
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.put(f"/{name}/resource", json=payload)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "detail" in resp.json()

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert _listed_paths(stored.json()) == []


def test_put_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.put(
        f"/{MISSING_SKILL_NAME}/resource",
        json={"path": RESOURCE_PATH, "content": RESOURCE_CONTENT},
    )
    if resp.status_code == 403:
        assert_strict_openapi_response(resp, ROUTE)
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "detail" in resp.json()


def test_put_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.put(
        f"/{MISSING_SKILL_NAME}/resource",
        auth=False,
        json={"path": RESOURCE_PATH, "content": RESOURCE_CONTENT},
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
