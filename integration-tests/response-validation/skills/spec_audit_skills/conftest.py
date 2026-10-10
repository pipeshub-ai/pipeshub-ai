"""Shared fixtures for the strict OpenAPI audit of /api/v1/skills."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Awaitable, Iterator

import pytest
import pytest_asyncio

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import (  # noqa: E402
    SecondUser,
    create_second_user,
    delete_second_user,
    second_user,  # noqa: F401 - fixture
)

from skills_audit_support import (  # noqa: E402
    UNRELATED_SCOPE,
    SeedSkill,
    SkillsClient,
    forget_access_token,
    mint_scoped_token,
    remove_candidate,
    request_as,
    seed_candidate,
    skill_payload,
    unique_skill_name,
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol


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
        assert resp.status_code == 201, f"seeding a skill failed: {resp.status_code} {resp.text[:500]}"
        created.append((payload["name"], owner))
        return resp.json()

    try:
        yield _seed
    finally:
        # Newest first: a skill another seeded skill `requires` cannot be deleted before it.
        for name, owner in reversed(created):
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
        pytest.fail(f"GET / seeds the built-in skills, yet none is listed: {resp.status_code} {resp.text[:200]}")
    return str(skills[0]["name"])


@pytest.fixture(scope="session")
def unscoped_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Authorization of an OAuth token of the suite's own client with neither skill scope.

    The suite's own token carries every scope and a session JWT is never scope-checked,
    so this is the only caller the gateway refuses for a missing scope.
    """
    token = mint_scoped_token(pipeshub_client.base_url, UNRELATED_SCOPE, pipeshub_client.timeout_seconds)
    try:
        yield {"Authorization": f"Bearer {token}"}
    finally:
        forget_access_token(token)


@pytest.fixture(scope="module")
def import_user(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    """A disposable member for one module of import-route calls.

    The import routes share a 10 calls/minute limiter per user, so each module that calls
    them gets its own budget instead of draining the admin's or the shared member's.
    """
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_second_user(pipeshub_client, user)


SeedCandidate = Callable[..., Awaitable[dict[str, Any]]]


@pytest_asyncio.fixture(loop_scope="session")
async def seed_skill_candidate(
    graph_provider: "GraphProviderProtocol", pipeshub_client: PipeshubClient
) -> AsyncGenerator[SeedCandidate, None]:
    """Factory: queue a pending candidate in the admin's org; every one is removed on teardown."""
    created: list[str] = []

    async def _seed(**fields: Any) -> dict[str, Any]:
        doc = await seed_candidate(graph_provider, pipeshub_client.org_id, **fields)
        created.append(doc["candidateId"])
        return doc

    try:
        yield _seed
    finally:
        for candidate_id in created:
            await remove_candidate(graph_provider, candidate_id)
