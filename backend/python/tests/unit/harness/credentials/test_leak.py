"""P2.4a/P2.4b LK: no credential reaches the journal, payloads, checkpoints, model requests, logs or run state.

A durable run through the real assembler, ``RunDriver`` and kernel calls a
connector read, a connector write and an MCP tool. The toolset and the MCP
instance hold vendor-shaped tokens; the client factory and the MCP session
must receive them (the effect boundary works), and nothing else may: the
whole in-memory journal (events of every stream, sealed and claim-checked
content, checkpoints), every model request, every log record at DEBUG, the
``RunContext`` and the JSON data of ``tool_state`` are scanned for the actual
values and for any shape on the shared credential-pattern list.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.agent_loop.tool_approvals import TOOLSET_ENABLED_ENV
from app.agents.mcp.client import MCPClientManager
from app.config.constants.service import config_node_constants
from app.harness.access.configured import ConfiguredModelAccess
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from harness_core.access.errors import AccountRevoked
from harness_core.access.types import ENGINE_MODEL_ID, PinnedRoute, user_account_ref
from harness_core.driver import RunDriver
from harness_core.security.token_patterns import find_forbidden
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.canary import strings_in
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.model_gateway import RecordedTransport, body_of
from tests.unit.harness.access.accounts_support import TENANT, Accounts
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agents.mcp.models import MCPToolInfo

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

JIRA_TOKEN = "ghp_" + "LeakTest0123456789abcdefghijKLMNOPQRST"
MCP_TOKEN = "xoxb-" + "4242424242-leaktestslacktokenvalue"
MODEL_KEY = "sk-proj-" + "LeakTestModelKey0123456789abcdef"
SECRETS = (JIRA_TOKEN, MCP_TOKEN, MODEL_KEY)


class _Client:
    """A connector client: an object holding its token, as real clients do."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.token = config["credentials"]["access_token"]


class _Factory:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def create_client(self, config_service: object, logger: object, config: dict, state: dict) -> _Client:
        client = _Client(config)
        self.tokens.append(client.token)
        return client


class _Services(w.Services):
    def __init__(self) -> None:
        super().__init__()
        self.config.answer(
            "/services/toolsets/",
            lambda _path: {"isAuthenticated": True, "credentials": {"access_token": JIRA_TOKEN}, "oauthConfigId": "oc-1"},
        )
        self.config.answer(
            "/services/mcp/credentials/",
            lambda _path: {"isAuthenticated": True, "credentials": {"apiToken": MCP_TOKEN}},
        )


def _instance_doc(instance_id: str) -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": w.ORG, "createdBy": w.ACTOR, "name": "GitHubMCP", "typeId": "github",
        "transport": "sse", "url": "https://mcp.example.test/sse", "authMode": "api_token",
        "createdAt": 0, "updatedAt": 0,
    }


@dataclasses.dataclass
class _Seen:
    discovery: list[dict[str, Any]] = dataclasses.field(default_factory=list[dict[str, Any]])
    headers: list[dict[str, str]] = dataclasses.field(default_factory=list[dict[str, str]])
    contexts: list[Any] = dataclasses.field(default_factory=list[Any])


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Seen]:
    # The run calls a Jira write; main's toolset rules (#4256) would ask for it, and this is about leaks.
    monkeypatch.setenv(TOOLSET_ENABLED_ENV, "false")
    record = _Seen()

    async def discover(
        config: Any, credentials: dict, timeout_seconds: float = 30.0, *, runner: object = None,  # noqa: ANN401
    ) -> list[MCPToolInfo]:
        record.discovery.append(credentials)
        return [tool.model_copy() for tool in w.MCP_TOOLS[w.MCP]]

    async def instance(instance_id: str, config_service: object, *_: object) -> dict[str, Any]:
        return _instance_doc(instance_id)

    async def opened(self: MCPClientManager) -> None:
        record.headers.append(dict(self.headers or {}))

    async def called(self: MCPClientManager, name: str, arguments: dict[str, Any], **_: object) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "no open pull requests"}]}

    from app.harness.assembly import teardown

    real_close = teardown.close_environment

    async def recording_close(context: Any, log: Any) -> None:  # noqa: ANN401
        record.contexts.append(context)
        await real_close(context, log)

    from app.agents.mcp import service as mcp_service

    real_auth = mcp_service.resolve_effective_user_auth
    with (
        w.deployment(),
        patch("app.agents.agent_loop.mcp_session.MCPSessionManager.tools", w.session_tools(discover)),
        patch("app.edition_config.get_mcp_instance_resolved", instance),
        patch("app.agents.mcp.service.resolve_effective_user_auth", real_auth),
        patch("app.agents.mcp.service.is_effective_auth_authenticated", mcp_service.is_effective_auth_authenticated),
        patch.object(MCPClientManager, "open", opened),
        patch.object(MCPClientManager, "call_tool_in_session", called),
        patch.object(MCPClientManager, "aclose", AsyncMock()),
        patch("app.harness.assembly.assembler.close_environment", recording_close),
    ):
        from app.agents.tools.factories.registry import ClientFactoryRegistry

        factory = _Factory()
        with patch.object(ClientFactoryRegistry, "_factories", {"jira": factory}):
            record.factory = factory  # type: ignore[attr-defined]
            yield record


def _json_data(value: object) -> object:
    """The JSON-shaped part of a state dict (live clients and sessions are objects, not data)."""
    if isinstance(value, dict):
        return {str(k): _json_data(v) for k, v in value.items() if isinstance(k, str)}  # type: ignore[union-attr]
    if isinstance(value, list | tuple):
        return [_json_data(v) for v in value]  # type: ignore[union-attr]
    return value if isinstance(value, str | int | float | bool) or value is None else None


def _assert_clean(where: str, texts: list[str], secrets: tuple[str, ...] = SECRETS) -> None:
    blob = "\n".join(texts)
    for secret in secrets:
        assert secret not in blob, f"{where} carries {secret[:8]}..."
    assert find_forbidden(blob) == (), f"{where} carries a credential shape: {find_forbidden(blob)}"


async def test_lk1_a_durable_run_journals_logs_and_prompts_no_credential(
    seen: _Seen, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    tools = [f"jira@{w.JIRA}/search_issues", f"jira@{w.JIRA}/create_issue", f"mcp@{w.MCP}/list_prs"]
    spec = w.pinned_spec(tool_ids=tools, mcp=[w.MCP], kb=[])
    journal.admit("run-leak", org_id=w.ORG, session_id="ses-leak", spec=spec, input_text="triage the board")
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="leak",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": "open"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__create_issue", args={"title": "triage"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="mcp_github_list_prs", args={"repo": "a"})])),
                    Turn(reply=Reply(text="done")),
                ],
            )
        )
    )
    assembler = PipesHubRuntimeAssembler(_Services(), model_factory=w.model_factory(port))
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock)
    claim = await w.claim_next(journal, "run-leak")
    done = await driver.run_claimed(claim)
    events = await journal.read(claim.stream_id)

    assert done.status == "completed", done
    assert [e.type for e in events].count("tool.outcome") == 3
    # The effect boundary got the real credentials.
    assert seen.factory.tokens == [JIRA_TOKEN]  # type: ignore[attr-defined]
    assert {"apiToken": MCP_TOKEN} in seen.discovery
    assert any(MCP_TOKEN in value for headers in seen.headers for value in headers.values())

    _assert_clean("the journal (events, content, checkpoints)", list(strings_in(journal)))
    _assert_clean("the model requests", list(strings_in(port.calls)))
    _assert_clean(
        "the logs",
        [f"{r.getMessage()} {getattr(r, 'harness', '')} {r.exc_text or ''}" for r in caplog.records],
    )
    (context,) = seen.contexts
    _assert_clean("tool_state data", [json.dumps(_json_data(context.tool_state), default=str)])
    _assert_clean("the run context", [RunContext.from_claim(w.claim(spec)).model_dump_json()])  # type: ignore[arg-type]
    issued = [r.harness["handle"] for r in caplog.records if getattr(r, "harness_event", None) == "credential.issued"]  # type: ignore[attr-defined]
    revoked = [r.harness["handle"] for r in caplog.records if getattr(r, "harness_event", None) == "credential.revoked"]  # type: ignore[attr-defined]
    assert len(issued) == 2 and sorted(revoked) == sorted(issued), "teardown revokes every handle of the attempt"
    assert context.tool_credentials().handles() == ()


async def test_lk2_the_legacy_loops_state_has_references_only(seen: _Seen) -> None:
    from app.agents.agent_loop.context import AgentContext
    from app.api.routes.agent import (
        _resolve_mcp_server_configs,
        _resolve_toolset_configs,
    )
    from app.modules.agents.qna.chat_state import build_initial_state

    services = _Services()
    toolsets, *_ = await _resolve_toolset_configs(
        services.config, [{"name": "jira", "instanceId": w.JIRA}], credential_lookup_id=w.ACTOR,
        executing_user_id=w.ACTOR, prefetched={}, agent_id=w.AGENT, is_service_account=False,
        logger=logging.getLogger("lk2"),
    )
    mcp, *_ = await _resolve_mcp_server_configs(
        services.config, [{"instanceId": w.MCP, "name": "GitHubMCP"}], org_id=w.ORG, credential_lookup_id=w.ACTOR,
        executing_user_id=w.ACTOR, agent_id=w.AGENT, is_service_account=False, logger=logging.getLogger("lk2"),
    )
    query = {
        "query": "q", "messages": [], "chatMode": "auto", "toolsets": [{"name": "jira", "instanceId": w.JIRA}],
        "toolsetConfigs": toolsets, "mcpServers": [{"instanceId": w.MCP}], "mcpServerConfigs": mcp,
    }
    state = build_initial_state(
        query, {"userId": w.ACTOR, "orgId": w.ORG}, None, logging.getLogger("lk2"), None, None, None,
        services.config, "m", "k", {}, "react", has_sql_connector=False,
    )
    context = AgentContext.from_chat_state(state, protocol="agui")
    _assert_clean("the legacy ChatState", [json.dumps(_json_data(dict(context.tool_state)), default=str)])


async def test_lk3_the_model_gateway_resolves_a_key_without_logging_or_exposing_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    class _Config:
        async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
            assert key == config_node_constants.AI_MODELS.value
            return copy.deepcopy(
                {"llm": [{"provider": "openAI", "modelKey": "key-1", "isDefault": True,
                          "configuration": {"model": "gpt-4o", "apiKey": MODEL_KEY}}]}
            )

    built: list[dict[str, object]] = []

    async def transports(provider: str, entry: dict[str, object], model: str, key: str) -> RecordedTransport:
        built.append(entry)
        return RecordedTransport(family="openai")

    access = ConfiguredModelAccess(_Config(), transports=transports)  # type: ignore[arg-type]
    resolved = await access.resolve("org-1", PinnedRoute("openai", "gpt-4o", "key-1", "gpt-4o"), run_id="run-1")
    assert built[0]["configuration"]["apiKey"] == MODEL_KEY  # type: ignore[index]
    _assert_clean("the resolved route", [repr(resolved.access), resolved.pricing_model])
    _assert_clean("the logs", [f"{r.getMessage()} {getattr(r, 'harness', '')}" for r in caplog.records])


USER_OPENAI_KEY = "sk-proj-" + "LeakTestUserKey0123456789abcdefgh"
USER_ANTHROPIC_KEY = "sk-ant-api03-" + "LeakTestUserKey0123456789abcdefgh"


async def test_lk4_the_gateway_resolves_and_revokes_a_users_key_without_logging_or_exposing_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    a = Accounts()
    openai, anthropic = user_account_ref("user-a", "openAI"), user_account_ref("user-a", "anthropic")
    await a.put(openai, "openAI", USER_OPENAI_KEY)
    await a.put(anthropic, "anthropic", USER_ANTHROPIC_KEY)
    rig = a.rig()
    bodies: list[str] = []
    bindings = [
        rig.pin(run_id, routes=(PinnedRoute(provider, model, config_model=model, account=ref),), pays="user")
        for run_id, provider, model, ref in (
            ("run-1", "openai", "gpt-4o", openai),
            ("run-2", "anthropic", "claude-haiku-4-5", anthropic),
        )
    ]
    request = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}
    for binding in bindings:
        outcome = await rig.gateway.chat(rig.token(binding), {}, body_of(request))
        bodies.append(json.dumps(outcome.body))  # type: ignore[union-attr]
    assert [_entry_key(e) for _, e, _, _ in a.builds.entries] == [USER_OPENAI_KEY, USER_ANTHROPIC_KEY]

    _ = await a.store.remove(TENANT, anthropic, actor="user-a")
    with pytest.raises(AccountRevoked) as refused:
        _ = await rig.gateway.chat(rig.token(bindings[1]), {}, body_of(request))
    listed = await a.store.accounts(TENANT, "user-a")

    secrets = (USER_OPENAI_KEY, USER_ANTHROPIC_KEY)
    resolved = [repr(entry[2].access) for entry in a.access._cache.values()]  # pyright: ignore[reportPrivateUsage]
    _assert_clean("the resolved routes", resolved, secrets)
    _assert_clean("the answers and the refusal", [*bodies, str(refused.value), repr(refused.value)], secrets)
    _assert_clean("the account metadata", [repr(listed)], secrets)
    _assert_clean("the stored documents", [json.dumps(a.config.values)], secrets)
    _assert_clean(
        "the logs",
        [f"{r.getMessage()} {getattr(r, 'harness', '')} {r.exc_text or ''}" for r in caplog.records],
        secrets,
    )


def _entry_key(entry: dict[str, object]) -> object:
    return entry["configuration"]["apiKey"]  # type: ignore[index]
