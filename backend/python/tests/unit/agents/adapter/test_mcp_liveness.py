"""MCPFIX F4: an MCP server uninstalled, or its credential removed, while a run holds it is not called again.

``mcp_liveness`` re-reads the run's credential reference before a call unless a confirmation younger than the time
to live, untouched by a change notification for the instance, says it is still there."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.mcp_liveness import CredentialLiveness, MCPServerRevoked
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp import instance_scope
from app.agents.mcp.models import MCPToolInfo
from harness_core.credentials.errors import (
    CredentialStoreUnavailable,
    CredentialUnavailable,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_mcp_session import (
    _FakeClientManagerFactory,
    _server,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer

REF = {"kind": "mcp", "name": "inst-1/user-1"}


class _Credentials:
    """Counts reads of the run's MCP credential; ``refuse`` makes the next ones fail."""

    def __init__(self) -> None:
        self.reads = 0
        self.refuse: Exception | None = None

    async def mcp_auth(self, reference: dict[str, Any]) -> dict[str, Any]:
        self.reads += 1
        if self.refuse is not None:
            raise self.refuse
        return {}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def liveness(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> CredentialLiveness:
    held = CredentialLiveness(ttl_s=30.0, clock=clock)
    monkeypatch.setattr(mcp_session_module, "mcp_liveness", held)
    return held


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> _Credentials:
    held = _Credentials()
    monkeypatch.setattr(AgentContext, "tool_credentials", lambda self: held)
    return held


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    instances: dict[str, Any] = {"inst-1": {"_id": "inst-1"}}

    async def lookup(instance_id: str, config_service: object, org_id: str) -> dict[str, Any] | None:
        return instances.get(instance_id)

    monkeypatch.setattr(instance_scope, "get_instance_for_org", lookup)
    return instances


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> _FakeClientManagerFactory:
    factory = _FakeClientManagerFactory()
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", factory)
    return factory


def _referenced(**overrides: Any) -> ResolvedMCPServer:  # noqa: ANN401 - _server's fields
    return _server(credential_ref=REF, **overrides)


async def test_a_confirmed_server_is_called_with_no_credential_read(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, installed: dict[str, Any],
) -> None:
    context, server = make_context(), _referenced()
    liveness.confirm(("org-1", "inst-1", "user-1"), liveness.version("inst-1"))
    assert await MCPSessionManager(context).call(server, "search", {}) == "ok"
    assert await MCPSessionManager(context).call(server, "search", {}) == "ok"
    assert credentials.reads == 0


async def test_an_uninstalled_server_is_not_called_after_its_change_is_seen(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, installed: dict[str, Any],
) -> None:
    context, server = make_context(), _referenced()
    sessions = MCPSessionManager(context)
    assert await sessions.call(server, "search", {}) == "ok"
    assert credentials.reads == 1
    del installed["inst-1"]
    credentials.refuse = CredentialUnavailable("the secret behind the handle is gone")
    liveness.on_change("/services/mcp/instances/inst-1")

    with pytest.raises(MCPServerRevoked) as refused:
        await sessions.call(server, "search", {})

    assert refused.value.reason_code == "credential_unavailable"
    assert refused.value.tool_error_code == "unavailable"
    assert "removed by an administrator" in str(refused.value)
    assert clients.built[0].calls == [("search", {})]
    assert clients.built[0].closed


async def test_a_removed_credential_is_refused_as_auth_and_another_server_is_unaffected(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, installed: dict[str, Any],
) -> None:
    context = make_context()
    mine, other = _referenced(), _referenced(instance_id="inst-2")
    liveness.confirm(("org-1", "inst-2", "user-1"), liveness.version("inst-2"))
    credentials.refuse = CredentialUnavailable("gone")
    liveness.on_change("/services/mcp/credentials/inst-1/user-1")

    with pytest.raises(MCPServerRevoked) as refused:
        await MCPSessionManager(context).call(mine, "search", {})
    assert refused.value.tool_error_code == "auth"
    assert await MCPSessionManager(context).call(other, "search", {}) == "ok"


async def test_an_unreachable_store_refuses_rather_than_guesses(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, installed: dict[str, Any],
) -> None:
    credentials.refuse = CredentialStoreUnavailable("down")
    with pytest.raises(MCPServerRevoked) as refused:
        await MCPSessionManager(make_context()).call(_referenced(), "search", {})
    assert refused.value.reason_code == "credential_store_unavailable"
    assert clients.built == []


async def test_a_confirmation_expires_after_the_time_to_live(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, clock: _Clock,
) -> None:
    context, server = make_context(), _referenced()
    sessions = MCPSessionManager(context)
    _ = await sessions.call(server, "search", {})
    clock.now += 29
    _ = await sessions.call(server, "search", {})
    assert credentials.reads == 1
    clock.now += 2
    _ = await sessions.call(server, "search", {})
    assert credentials.reads == 2


def test_a_confirmation_a_change_overtook_is_not_recorded(liveness: CredentialLiveness) -> None:
    key = ("org-1", "inst-1", "user-1")
    version = liveness.version("inst-1")
    liveness.on_change("/services/mcp/credentials/inst-1/user-1/oauth-tokens")
    liveness.confirm(key, version)
    assert not liveness.fresh(key)
    liveness.confirm(key, liveness.version("inst-1"))
    assert liveness.fresh(key)
    liveness.on_change("__CLEAR_ALL__")
    assert not liveness.fresh(key)
    liveness.on_change("/services/toolsets/inst-1/user-1")
    liveness.confirm(key, liveness.version("inst-1"))
    liveness.on_change("/services/mcp/instances/other")
    assert liveness.fresh(key)


async def test_the_adapter_turns_a_revocation_into_a_typed_tool_failure(
    liveness: CredentialLiveness, credentials: _Credentials, clients: _FakeClientManagerFactory, installed: dict[str, Any],
) -> None:
    context, server = make_context(), _referenced()
    credentials.refuse = CredentialUnavailable("gone")
    info = MCPToolInfo(name="search", namespaced_name="jira_search", description="Search.", input_schema={})
    output = await MCPToolAdapter(server, info, MCPSessionManager(context)).execute()
    assert not output.success and output.error_code == "auth"
    assert "credential_unavailable" in (output.error or "")


def test_the_configuration_service_tells_its_listeners_what_changed() -> None:
    import threading

    from app.config.configuration_service import ConfigurationService

    service = ConfigurationService.__new__(ConfigurationService)
    service.cache = {"/services/mcp/instances/inst-1": {}}
    service._cache_lock = threading.Lock()  # main's ConfigurationService guards its LRU cache with a lock
    service._stopping = True
    service._change_listeners = []
    seen: list[str] = []
    service.add_change_listener(seen.append)
    service._invalidation_callback("/services/mcp/instances/inst-1")
    assert seen == ["/services/mcp/instances/inst-1"] and not service.cache
