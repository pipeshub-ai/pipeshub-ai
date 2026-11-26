"""SEC5-1, SEC5-2, SEC5-9..SEC5-11: the org-scoped MCP instance path and the non-route callers that use it.

The store is the SEC5 world: org A's instances, org B's own, and org C, a child of org A that inherits org A's
instances only under ``InheritingEdition``.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.agents.constants.mcp_server_constants import get_mcp_instance_path
from app.agents.mcp import service as mcp_service
from app.agents.mcp.instance_scope import get_instance_for_org, list_instances_for_org
from app.api.routes.agent import (
    _resolve_mcp_server_configs,  # pyright: ignore[reportPrivateUsage]
    get_assistant_agent,  # pyright: ignore[reportUnknownVariableType] - its return type names an untyped dict
)
from app.harness.credentials.sources import MCP_KIND, McpSecretSource
from harness_core.credentials import CredentialScope
from tests.unit.api.routes.sec5_mcp_world import (
    A_HTTP,
    A_OAUTH,
    A_SHARED,
    A_STDIO,
    B_HTTP,
    ORG_A,
    ORG_B,
    ORG_C,
    InheritingEdition,
    seed,
)

LOG = logging.getLogger("sec5")
ORG_A_INSTANCES = {A_HTTP, A_OAUTH, A_SHARED, A_STDIO}


@pytest.mark.parametrize(
    ("instance_id", "org_id", "resolves"),
    [
        (A_HTTP, ORG_A, True),
        (A_HTTP, ORG_B, False),
        (A_HTTP, ORG_C, False),
        (B_HTTP, ORG_B, True),
        (B_HTTP, ORG_A, False),
        ("no-org", ORG_A, False),
        ("absent", ORG_A, False),
        (A_HTTP, "", False),
    ],
)
async def test_sec5_1_community_seam_resolves_only_the_callers_own_instance(
    instance_id: str, org_id: str, resolves: bool
) -> None:
    config = seed()
    config.values[get_mcp_instance_path("no-org")] = {"_id": "no-org", "name": "legacy", "authMode": "none"}

    instance = await get_instance_for_org(instance_id, config, org_id)  # type: ignore[arg-type]

    assert (instance is not None) is resolves
    if instance is not None:
        assert instance["_id"] == instance_id and instance["orgId"] == org_id


@pytest.mark.parametrize(
    ("org_id", "inheriting", "visible"),
    [
        (ORG_A, False, ORG_A_INSTANCES),
        (ORG_B, False, {B_HTTP}),
        (ORG_C, False, set[str]()),
        (ORG_C, True, ORG_A_INSTANCES),
        (ORG_B, True, {B_HTTP}),
        (ORG_A, True, ORG_A_INSTANCES),
    ],
)
async def test_sec5_2_list_through_the_scoped_path(
    monkeypatch: pytest.MonkeyPatch, org_id: str, inheriting: bool, visible: set[str]
) -> None:
    if inheriting:
        InheritingEdition().install(monkeypatch)

    listed = await list_instances_for_org(seed(), org_id)  # type: ignore[arg-type]

    assert {i["_id"] for i in listed} == visible


async def _chat_path(org_id: str, user_id: str) -> tuple[dict[str, Any], list[str]]:
    configs, _configured, missing, _unauthenticated = await _resolve_mcp_server_configs(
        seed(),  # type: ignore[arg-type]
        [{"instanceId": A_HTTP, "name": "Org A server"}],
        org_id=org_id,
        credential_lookup_id=user_id,
        executing_user_id=user_id,
        agent_id=f"agent-{org_id}",
        is_service_account=False,
        logger=LOG,  # type: ignore[arg-type]
    )
    return configs, missing


async def test_sec5_9_a_run_of_another_org_naming_an_instance_finds_nothing() -> None:
    configs, missing = await _chat_path(ORG_B, "user-b")

    assert configs == {} and missing == ["Org A server"]


async def test_sec5_9_positive_control_the_owning_orgs_run_resolves_it() -> None:
    configs, _ = await _chat_path(ORG_A, "user-a")

    assert A_HTTP in configs


@pytest.mark.parametrize(
    ("tenant", "owner", "inheriting", "resolves"),
    [
        (ORG_A, "user-a", False, True),
        (ORG_B, "user-a", False, False),
        (ORG_C, "user-c", False, False),
        (ORG_C, "user-c", True, True),
        (ORG_B, "user-a", True, False),
    ],
)
async def test_sec5_10_mcp_secret_source_reads_only_what_the_tenant_may_see(
    monkeypatch: pytest.MonkeyPatch, tenant: str, owner: str, inheriting: bool, resolves: bool
) -> None:
    if inheriting:
        InheritingEdition().install(monkeypatch)

    secret = await McpSecretSource(seed()).read(tenant, CredentialScope(MCP_KIND, f"{A_HTTP}/{owner}"))

    assert (secret is not None) is resolves


class _NoUserGraph:
    async def get_user_by_user_id(self, user_id: str) -> None:
        del user_id


@pytest.mark.parametrize(("org_id", "considered"), [(ORG_A, ORG_A_INSTANCES), (ORG_B, {B_HTTP})])
async def test_sec5_11_assistant_auto_attach_considers_only_the_callers_org(
    monkeypatch: pytest.MonkeyPatch, org_id: str, considered: set[str]
) -> None:
    seen: list[str] = []
    real = mcp_service.get_authenticated_mcp_servers

    async def spy(
        owner_id: str, config_service: Any, instances: list[dict[str, Any]], **kwargs: Any  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        seen.extend(i["_id"] for i in instances)
        return await real(owner_id, config_service, instances, **kwargs)

    monkeypatch.setattr(mcp_service, "get_authenticated_mcp_servers", spy)

    await get_assistant_agent(
        "user-a", org_id, seed(), _NoUserGraph(), None, LOG,  # type: ignore[arg-type]
        actions_enabled=False, mcp_enabled=True,
    )

    assert set(seen) == considered
