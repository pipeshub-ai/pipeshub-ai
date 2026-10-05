"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/skills."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

SKILLS_BASE = "/api/v1/skills"

# Well-formed kebab-case name that no test ever creates.
MISSING_SKILL_NAME = "spec-audit-missing-skill"
# Passes Node's path guard but fails the Python name rule ^[a-z0-9]+(-[a-z0-9]+)*$.
MALFORMED_SKILL_NAME = "Spec_Audit_Bad_Name"
# Decodes to "a/b": guardPathParams answers 400 before the request reaches Python.
UNSAFE_PATH_SEGMENT = "a%2Fb"
MISSING_CANDIDATE_ID = "spec-audit-missing-candidate"
MISSING_VERSION = "999.0.0"
MISSING_RESOURCE_PATH = "references/spec-audit-missing.md"
RESOURCE_PATH = "references/spec-audit.md"

SKILL_BODY_MARKER = "spec-audit-marker"

SeedSkill = Callable[..., dict[str, Any]]


def unique_skill_name() -> str:
    return f"spec-audit-{uuid.uuid4().hex[:10]}"


def skill_payload(name: str | None = None, **overrides: Any) -> dict[str, Any]:
    """A valid POST / and PUT /:name body (Python SkillWriteRequest)."""
    payload: dict[str, Any] = {
        "description": "Seeded by the skills spec audit",
        "body": f"# Spec audit\n\nUse this skill for nothing. {SKILL_BODY_MARKER}\n",
    }
    if name is not None:
        payload["name"] = name
    payload.update(overrides)
    return payload


def skill_md(name: str) -> str:
    """A SKILL.md document, as POST /import/finalize expects in ``content``."""
    return (
        f"---\nname: {name}\ndescription: Seeded by the skills spec audit\n---\n"
        f"# Spec audit\n\n{SKILL_BODY_MARKER}\n"
    )


class SkillsClient(APIClient):
    """Client for /api/v1/skills, acting as the shared org admin."""

    BASE = SKILLS_BASE

    def list(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("/", auth=auth, params=params)

    def create(self, payload: dict[str, Any], *, auth: bool = True) -> requests.Response:
        return self.post("/", auth=auth, json=payload)

    def fetch(self, name: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{name}", auth=auth)

    def remove(self, name: str, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.delete(f"/{name}", auth=auth, params=params)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a skills route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = {"Authorization": f"Bearer {user.token}", **kwargs.pop("headers", {})}
    return requests.request(
        method, f"{user.base_url}{SKILLS_BASE}{path}", headers=headers, **kwargs
    )
