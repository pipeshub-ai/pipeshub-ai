"""Shared fixtures for the strict OpenAPI audit of /api/v1/toolsets."""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from toolsets_audit_support import (  # noqa: E402
    AGENTS_BASE,
    DeleteInstanceLater,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    forget_access_token,
    instance_body,
    mint_narrow_scope_token,
    oauth_instance_body,
    toolset_store_lock,
)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

logger = logging.getLogger("toolsets-audit")


@pytest.fixture(scope="session")
def toolsets_client(pipeshub_client: PipeshubClient) -> ToolsetsClient:
    return ToolsetsClient(pipeshub_client)


@pytest.fixture(scope="session")
def narrow_scope_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Headers of an OAuth token without any ``connector:*`` or ``agent:*`` scope.

    The suite's own token is an OAuth client-credentials token too, so Python checks
    scopes for it; this one is the same client asking for less.
    """
    token = mint_narrow_scope_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds)
    try:
        yield {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    finally:
        forget_access_token(token)


def _delete_instances(toolsets_client: ToolsetsClient, records: list[JsonObject]) -> None:
    """Delete instances (with the credentials saved against them), then their OAuth configs.

    Instances go first: a config still linked to one of them cannot be deleted.
    """
    with toolset_store_lock():
        for record in records:
            deleted = toolsets_client.delete_instance(record["_id"])
            if deleted.status_code >= 300 and deleted.status_code != 404:
                logger.warning("could not delete toolset instance %s: %s", record["_id"], deleted.text[:300])
        for toolset_type, oauth_config_id in sorted(
            {(r["toolsetType"], r["oauthConfigId"]) for r in records if r.get("oauthConfigId")}
        ):
            removed = toolsets_client.delete_oauth_config(toolset_type, oauth_config_id)
            if removed.status_code >= 300 and removed.status_code != 404:
                logger.warning("could not delete OAuth config %s: %s", oauth_config_id, removed.text[:300])


@pytest.fixture
def delete_instance_later(toolsets_client: ToolsetsClient) -> Iterator[DeleteInstanceLater]:
    """Register an instance record a test created itself; it is deleted on teardown."""
    created: list[JsonObject] = []
    try:
        yield created.append
    finally:
        _delete_instances(toolsets_client, created)


@pytest.fixture
def seed_toolset_instance(
    toolsets_client: ToolsetsClient, delete_instance_later: DeleteInstanceLater
) -> SeedToolsetInstance:
    """Factory: create one toolset instance and return its stored record (``_id`` is the id).

    ``seed_toolset_instance(oauth=False, **overrides)`` merges camelCase overrides into
    ``instance_body()``, or ``oauth_instance_body()`` when ``oauth=True``; that record also
    carries ``oauthConfigId``. On teardown every instance is deleted, which removes the
    user and agent credentials saved against it, and then its OAuth config.
    """

    def _seed(oauth: bool = False, **overrides: Any) -> JsonObject:
        body = oauth_instance_body(**overrides) if oauth else instance_body(**overrides)
        with toolset_store_lock():
            resp = toolsets_client.create_instance(body)
        if resp.status_code not in (200, 201):
            pytest.fail(f"seeding a toolset instance: {resp.status_code} {resp.text[:500]}")
        record: JsonObject = resp.json().get("instance") or {}
        if not record.get("_id"):
            pytest.fail(f"instance create returned no instance._id: {resp.text[:500]}")
        delete_instance_later(record)
        if oauth and not record.get("oauthConfigId"):
            pytest.fail(f"OAuth instance was created without an OAuth config: {resp.text[:500]}")
        return record

    return _seed


@pytest.fixture
def seed_agent(pipeshub_client: PipeshubClient) -> Iterator[SeedAgent]:
    """Factory: create one agent owned by the admin and return its key; deleted on teardown.

    ``seed_agent(is_service_account=True)``. The agent-scoped write routes only accept a
    service-account agent; pass ``False`` for the agent they refuse.
    """
    created: list[str] = []

    def _seed(is_service_account: bool = True) -> str:
        # No models: creation then needs no configured LLM, and nothing here ever chats.
        resp = pipeshub_client.request(
            "POST",
            f"{AGENTS_BASE}/create",
            json={
                "name": f"spec-audit-toolsets-{uuid.uuid4().hex[:8]}",
                "isServiceAccount": is_service_account,
            },
        )
        if resp.status_code not in (200, 201):
            pytest.fail(f"seeding an agent: {resp.status_code} {resp.text[:500]}")
        agent_key = (resp.json().get("agent") or {}).get("_key")
        if not agent_key:
            pytest.fail(f"agent create returned no agent._key: {resp.text[:500]}")
        created.append(agent_key)
        return agent_key

    try:
        yield _seed
    finally:
        for agent_key in created:
            deleted = pipeshub_client.request("DELETE", f"{AGENTS_BASE}/{agent_key}")
            if deleted.status_code >= 300 and deleted.status_code != 404:
                logger.warning("could not delete agent %s: %s", agent_key, deleted.text[:300])
