"""Shared fixtures for the strict OpenAPI audit of /api/v1/mcp-servers."""

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

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture
from helper.xdist_shared import shared_session_resource  # noqa: E402

from mcp_servers_audit_support import (  # noqa: E402
    AGENTS_BASE,
    MCP_FEATURE_FLAG,
    PLATFORM_SETTINGS,
    JsonObject,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    instance_body,
)

logger = logging.getLogger("mcp-servers-audit")


@pytest.fixture(scope="session", autouse=True)
def mcp_feature_enabled(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    pipeshub_client: PipeshubClient,
) -> Iterator[None]:
    """Python answers every route with 403 until the ENABLE_MCP platform flag is on.

    The flag is org-wide, so one worker flips it for the run; a serial run puts the
    previous settings back.
    """

    def _post(flags: dict[str, bool], max_bytes: int) -> int:
        # The POST replaces the whole settings object, so everything else goes back unchanged.
        return pipeshub_client.request(
            "POST",
            PLATFORM_SETTINGS,
            json={"fileUploadMaxSizeBytes": max_bytes, "featureFlags": flags},
        ).status_code

    def _enable() -> JsonObject:
        resp = pipeshub_client.request("GET", PLATFORM_SETTINGS)
        if resp.status_code != 200:
            pytest.fail(f"reading platform settings: {resp.status_code} {resp.text[:300]}")
        settings = resp.json()
        flags = dict(settings.get("featureFlags") or {})
        max_bytes = settings.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024
        if flags.get(MCP_FEATURE_FLAG) is True:
            return {"changed": False, "flags": flags, "max_bytes": max_bytes}
        status = _post({**flags, MCP_FEATURE_FLAG: True}, max_bytes)
        if status != 200:
            pytest.fail(f"enabling MCP servers failed with HTTP {status}")
        return {"changed": True, "flags": flags, "max_bytes": max_bytes}

    def _restore(state: JsonObject) -> None:
        if state["changed"] and _post(state["flags"], state["max_bytes"]) != 200:
            logger.warning("could not restore platform settings after the MCP servers audit")

    with shared_session_resource(
        "spec_audit_mcp_feature_enabled",
        config=request.config,
        tmp_path_factory=tmp_path_factory,
        create=_enable,
        destroy=_restore,
        dump=lambda state: state,
        load=lambda raw: raw,
    ):
        yield


@pytest.fixture(scope="session")
def mcp_servers_client(pipeshub_client: PipeshubClient) -> McpServersClient:
    return McpServersClient(pipeshub_client)


@pytest.fixture
def seed_mcp_instance(mcp_servers_client: McpServersClient) -> Iterator[SeedMcpInstance]:
    """Factory: create one MCP server instance and return its stored record (``_id`` is the id).

    ``seed_mcp_instance(**overrides)`` merges camelCase overrides into ``instance_body()``;
    every instance, with its credentials and OAuth client config, is deleted on teardown.
    """
    created: list[str] = []

    def _seed(**overrides: Any) -> JsonObject:
        resp = mcp_servers_client.create_instance(instance_body(**overrides))
        if resp.status_code != 201:
            pytest.fail(f"seeding an MCP instance: {resp.status_code} {resp.text[:500]}")
        record: JsonObject = resp.json()
        created.append(record["_id"])
        return record

    try:
        yield _seed
    finally:
        for instance_id in created:
            deleted = mcp_servers_client.delete_instance(instance_id)
            if deleted.status_code >= 300 and deleted.status_code != 404:
                logger.warning("could not delete MCP instance %s: %s", instance_id, deleted.text[:300])


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
                "name": f"spec-audit-mcp-{uuid.uuid4().hex[:8]}",
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
