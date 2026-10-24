"""`OpenAPIToolProvider` and `OpenAPIToolAdapter`: an agent's attached API toolset operations,
loaded into one request's tool registry and called against the Acme reference server."""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.openapi_tool_adapter import OpenAPIToolAdapter
from app.agents.agent_loop.openapi_tool_loader import OpenAPIToolProvider
from app.agents.agent_loop.tool_approvals import TOOLSET_TOOLS_BY_PATH
from app.agents.openapi import executor as executor_module
from app.agents.openapi import service
from app.agents.openapi.auth import ApiToolsetAuthError
from app.agents.openapi.models import ApiToolsetCredentials
from app.agents.openapi.store import ApiToolsetStore
from tests.support.in_memory_config import InMemoryConfigService
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agents.agent_loop.context import AgentContext
    from app.agents.openapi.models import ApiToolsetInstance

ACME_DIR = Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture"
SETTINGS = "/services/platform/settings"


def _context(config: InMemoryConfigService, tools: list[str], **extra: Any) -> AgentContext:  # noqa: ANN401
    return make_context(config_service=config, api_toolset_tools=tools, **extra)


async def _add_toolset(
    config: InMemoryConfigService, *, name: str = "Acme Helpdesk", base_url: str = "https://helpdesk.acme.test",
    scheme: str = "apiKeyAuth", shared: bool = False, instance_id: str = "a" * 32,
    operations: tuple[str, ...] = ("list_tickets", "get_ticket", "update_ticket"),
) -> ApiToolsetInstance:
    instance = service.build_instance(
        service.InstanceInput(name=name, base_url=base_url, operations=list(operations), spec=(ACME_DIR / "openapi.yaml").read_bytes(),
                              auth_scheme_name=scheme, use_shared_credential=shared),
        instance_id=instance_id, org_id="org-1", user_id="admin-1",
    )
    await ApiToolsetStore(config).save_instance(instance)
    return instance


@pytest.fixture
def config() -> InMemoryConfigService:
    store = InMemoryConfigService()
    store.values[SETTINGS] = {"featureFlags": {"ENABLE_API_TOOLSETS": True}}
    return store


def _tools(registry: ToolRegistry) -> list:
    return [registry.resolve_by_name(name) for name in registry.names()]


async def _load(context: AgentContext) -> ToolRegistry:
    registry = ToolRegistry()
    await OpenAPIToolProvider().load_into(registry, context)
    return registry


class TestLoading:
    async def test_only_the_attached_operations_load_under_one_group(self, config: InMemoryConfigService) -> None:
        instance = await _add_toolset(config)
        await ApiToolsetStore(config).save_credentials(instance.id, "user-1", ApiToolsetCredentials(api_key="k"))
        context = _context(config, [f"{instance.id}:list_tickets", f"{instance.id}:update_ticket"])

        registry = await _load(context)

        names = sorted(t.name for t in _tools(registry))
        assert names == ["api_acme_helpdesk_list_tickets", "api_acme_helpdesk_update_ticket"]
        group = next(g for g in registry.toolsets() if g.name == "api_acme_helpdesk")
        assert sorted(group.tool_names) == names and group.parent is None
        approvals = context.tool_state[TOOLSET_TOOLS_BY_PATH]
        kinds = {target.tool_name: target.kind for target in approvals.values()}
        assert kinds == {"list_tickets": "read", "update_ticket": "write"}
        assert {t.source for t in approvals.values()} == {"toolset"}
        # Reads run, writes ask first and deletes are refused, as for an MCP tool.
        assert {t.defaults for t in approvals.values()} == {"mcp"}
        assert {t.owner for t in approvals.values()} == {"Acme Helpdesk"}

    async def test_nothing_loads_with_the_labs_flag_off(self, config: InMemoryConfigService) -> None:
        instance = await _add_toolset(config, scheme="none")
        config.values[SETTINGS] = {"featureFlags": {"ENABLE_API_TOOLSETS": False}}
        registry = await _load(_context(config, [f"{instance.id}:list_tickets"]))
        assert _tools(registry) == []

    async def test_a_toolset_not_connected_is_named_for_the_model(self, config: InMemoryConfigService) -> None:
        instance = await _add_toolset(config)
        context = _context(config, [f"{instance.id}:list_tickets"])
        registry = await _load(context)
        assert _tools(registry) == []
        assert context.toolset_load_failures == {
            "Acme Helpdesk (API action; connect it under Workspace → Your actions)": "not_authenticated",
        }

    async def test_a_missing_shared_credential_is_for_an_administrator(self, config: InMemoryConfigService) -> None:
        instance = await _add_toolset(config, shared=True)
        context = _context(config, [f"{instance.id}:list_tickets"])
        await _load(context)
        assert context.toolset_load_failures == {
            "Acme Helpdesk (API action; ask an administrator to connect it under Workspace → Actions)": "not_authenticated",
        }

    async def test_one_broken_toolset_does_not_stop_the_others(
        self, config: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        broken = await _add_toolset(config, name="Broken", scheme="none")
        working = await _add_toolset(config, name="Working", scheme="none", instance_id="c" * 32)
        real_build = service.build_executor

        async def build(store: ApiToolsetStore, instance: ApiToolsetInstance, owner: str, **kw: Any) -> Any:  # noqa: ANN401
            if instance.id == broken.id:
                raise RuntimeError("config store is down")
            return await real_build(store, instance, owner, **kw)

        monkeypatch.setattr(service, "build_executor", build)
        context = _context(config, [f"{broken.id}:list_tickets", f"{working.id}:list_tickets"])
        registry = await _load(context)
        assert [t.name for t in _tools(registry)] == ["api_working_list_tickets"]
        assert context.toolset_load_failures == {"Broken (API action)": "error"}

    async def test_an_unreadable_record_is_reported_not_raised(
        self, config: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def unreadable(self: ApiToolsetStore, org_id: str, instance_id: str) -> None:
            raise RuntimeError("config store is down")

        monkeypatch.setattr(ApiToolsetStore, "get_instance", unreadable)
        context = _context(config, [f"{'b' * 32}:list_tickets"])
        assert _tools(await _load(context)) == []
        assert context.toolset_load_failures == {f"API action {'b' * 32}": "error"}

    async def test_a_removed_toolset_is_skipped(self, config: InMemoryConfigService) -> None:
        registry = await _load(_context(config, [f"{'b' * 32}:list_tickets"]))
        assert _tools(registry) == []

    async def test_another_orgs_toolset_never_loads(self, config: InMemoryConfigService) -> None:
        instance = await _add_toolset(config, scheme="none")
        registry = await _load(_context(config, [f"{instance.id}:list_tickets"], org_id="org-2"))
        assert _tools(registry) == []

    async def test_service_accounts_use_shared_credentials_only(self, config: InMemoryConfigService) -> None:
        per_member = await _add_toolset(config)
        shared = await _add_toolset(config, name="Wiki", instance_id="c" * 32, shared=True)
        store = ApiToolsetStore(config)
        await store.save_credentials(per_member.id, "user-1", ApiToolsetCredentials(api_key="k"))
        await store.save_credentials(shared.id, "_shared", ApiToolsetCredentials(api_key="k"))
        context = _context(config, [f"{per_member.id}:list_tickets", f"{shared.id}:list_tickets"], is_service_account=True)

        registry = await _load(context)

        assert [t.name for t in _tools(registry)] == ["api_wiki_list_tickets"]
        assert list(context.toolset_load_failures) == ["Acme Helpdesk (API action; connect it under Workspace → Your actions)"]

    async def test_toolsets_with_the_same_name_get_distinct_tool_names(self, config: InMemoryConfigService) -> None:
        first = await _add_toolset(config, scheme="none")
        second = await _add_toolset(config, scheme="none", instance_id="d" * 32)
        registry = await _load(_context(config, [f"{first.id}:list_tickets", f"{second.id}:list_tickets"]))
        names = sorted(t.name for t in _tools(registry))
        assert len(names) == 2 and names[0] != names[1]
        assert all(n.startswith("api_acme_helpdesk") and len(n) <= 64 for n in names)


def _load_acme_server() -> Any:  # noqa: ANN401
    spec = importlib.util.spec_from_file_location("acme_server_for_adapter_tests", ACME_DIR / "server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Any]]:
    server = _load_acme_server().serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # The SSRF guard always refuses loopback, so the Acme server gets a plain transport here.
    monkeypatch.setattr(executor_module, "GuardedTransport", lambda url, allow_private: httpx.AsyncHTTPTransport())
    monkeypatch.setattr(service, "check_mcp_url_at_save", lambda url, allow_private: None)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.server_close()


class TestCalls:
    async def _tools(self, config: InMemoryConfigService, base_url: str, api_key: str = "acme-demo-key") -> dict[str, OpenAPIToolAdapter]:
        instance = await _add_toolset(config, base_url=base_url)
        await ApiToolsetStore(config).save_credentials(instance.id, "user-1", ApiToolsetCredentials(api_key=api_key))
        registry = await _load(_context(config, [f"{instance.id}:{n}" for n in ("list_tickets", "get_ticket", "update_ticket")]))
        return {t.operation.tool_name: t for t in _tools(registry) if isinstance(t, OpenAPIToolAdapter)}

    async def test_a_read_returns_the_apis_json(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, acme[0])
        output = await tools["list_tickets"].execute(status="open")
        assert output.success and "ACME-7311" in [t["id"] for t in output.data["tickets"]]
        assert {t["status"] for t in output.data["tickets"]} == {"open"}

    async def test_the_model_sees_the_spec_and_where_the_call_goes(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        update = (await self._tools(config, acme[0]))["update_ticket"]
        assert update.short_description == "Change a ticket's status, priority or assignee."
        assert "PATCH /tickets/{ticketId} on the Acme Helpdesk API" in update.description
        schema = update.raw_input_schema
        assert schema["required"] == ["ticketId", "body"]
        assert schema["properties"]["body"]["properties"]["status"]["enum"] == ["open", "pending", "closed"]
        assert {p.name for p in update.parameters} == {"ticketId", "body"}

    async def test_validation_uses_the_spec(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, acme[0])
        with pytest.raises(ToolValidationError, match="missing required argument 'ticketId'"):
            tools["get_ticket"].validate({})
        args = {"status": "Open"}
        tools["list_tickets"].validate(args)
        assert args == {"status": "open"}  # fuzzy-matched to the spec's enum, as for MCP tools
        args = {"limit": "5"}
        tools["list_tickets"].validate(args)
        assert args == {"limit": 5}

    async def test_an_http_error_explains_itself(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, acme[0])
        output = await tools["get_ticket"].execute(ticketId="ACME-1")
        assert not output.success and output.error.startswith("Acme Helpdesk returned HTTP 404: ")
        assert "Ticket ACME-1 not found" in output.error

    async def test_a_refused_credential_says_where_to_fix_it(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, acme[0], api_key="wrong")
        output = await tools["list_tickets"].execute()
        assert not output.success and "HTTP 401" in output.error and "Workspace → Your actions" in output.error

    async def test_a_write_reaches_the_api(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, acme[0])
        output = await tools["update_ticket"].execute(ticketId="ACME-7312", body={"status": "closed"})
        assert output.success and output.data["status"] == "closed"
        assert acme[1].calls[-1]["body"] == {"status": "closed"}

    async def test_an_unreachable_api_is_an_error_not_a_crash(self, config: InMemoryConfigService, acme: tuple[str, Any]) -> None:
        tools = await self._tools(config, "http://127.0.0.1:9")
        output = await tools["list_tickets"].execute()
        assert not output.success and "Could not reach the API" in output.error


class _SignInExpired:
    async def call(self, _operation: object, _arguments: object) -> None:
        raise ApiToolsetAuthError("The sign-in for this API action has expired.")


@pytest.mark.parametrize(
    ("shared", "hint"),
    [
        (False, "Connect it under Workspace → Your actions."),
        (True, "Ask an administrator to connect it under Workspace → Actions."),
    ],
)
async def test_a_refused_sign_in_names_who_can_reconnect_as_the_ui_does(
    config: InMemoryConfigService, shared: bool, hint: str,
) -> None:
    instance = await _add_toolset(config, shared=shared)
    adapter = OpenAPIToolAdapter(instance, instance.operations[0], _SignInExpired(), name="api_acme_list_tickets")
    output = await adapter.execute()
    assert not output.success and output.error.endswith(hint)
