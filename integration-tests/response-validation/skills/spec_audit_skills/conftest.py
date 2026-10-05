"""Shared fixtures for the strict OpenAPI audit of /api/v1/skills."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture

from skills_audit_support import (  # noqa: E402
    SeedSkill,
    SkillsClient,
    request_as,
    skill_payload,
    unique_skill_name,
)


@pytest.fixture(scope="session")
def skills_client(pipeshub_client: PipeshubClient) -> SkillsClient:
    return SkillsClient(pipeshub_client)


@pytest.fixture
def seed_skill(skills_client: SkillsClient) -> Iterator[SeedSkill]:
    """Factory: create one custom skill and return its metadata; all are deleted on teardown.

    ``seed_skill(owner=None, **payload_overrides)``. The admin owns it by default;
    pass ``owner=second_user`` for a skill only the member can see.
    """
    created: list[tuple[str, SecondUser | None]] = []

    def _seed(owner: SecondUser | None = None, **overrides: Any) -> dict[str, Any]:
        payload = skill_payload(overrides.pop("name", None) or unique_skill_name(), **overrides)
        if owner is None:
            resp = skills_client.create(payload)
        else:
            resp = request_as(owner, "POST", "/", json=payload)
        if resp.status_code == 403:
            pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
        assert resp.status_code == 201, f"seeding a skill failed: {resp.status_code} {resp.text[:500]}"
        created.append((payload["name"], owner))
        return resp.json()

    try:
        yield _seed
    finally:
        for name, owner in created:
            # Skills are creator-scoped, so only the owner's token can delete one.
            if owner is None:
                skills_client.remove(name, detach="true")
            else:
                request_as(owner, "DELETE", f"/{name}", params={"detach": "true"})


@pytest.fixture(scope="session")
def builtin_skill_name(skills_client: SkillsClient) -> str:
    """Name of an org-wide built-in skill; GET / is the route that seeds them."""
    resp = skills_client.list(source="builtin")
    skills = resp.json().get("skills") if resp.status_code == 200 else None
    if not skills:
        pytest.skip(f"no built-in skill on this stack: {resp.status_code} {resp.text[:200]}")
    return str(skills[0]["name"])
