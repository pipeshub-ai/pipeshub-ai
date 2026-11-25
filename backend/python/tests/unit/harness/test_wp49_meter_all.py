"""WP-49 METER-ALL (BU-01, BU-05, G-45 / GA-24): every model call a harness run makes, its tools' helpers included,
is charged to the run's ledger account and takes its provider slot.

Before, the legacy chat-state and tool-state helpers were handed the provider's own LangChain model
(``ResolvedModel.llm``), and helpers that build a model for themselves (grep generation and the entity name check
on the ``query`` role, a spreadsheet's tables on ``indexing``) built one from the org's configuration: neither went
through ``MeteredTransport`` or ``AdmittedTransport``, so the run's spending limit, the cost ledger and the shared
provider limit never saw them.

The fake provider model here refuses any call that does not hold both a provider slot and an open ledger
reservation, so a helper that reaches it any other way fails the test. The static checks below keep the provider's
model out of the tools' reach.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from app.harness.assembly.model import (
    HelperMeter,
    ResolvedModel,
    admitted,
    build_transport,
    helper_meter,
    helper_model_scope,
    metered,
)
from app.harness.classification import ModelGuard, PassthroughGuard
from app.harness.run_chat_model import RunChatModel
from app.utils.run_model_scope import run_model_scope
from harness_core.budget.errors import BudgetRefused
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.testing import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import Iterator

RUN = RunAccountRef("org-1", "run-1")
PROVIDER = "openAI"
KEY = "openai"
PYTHON_ROOT = Path(__file__).resolve().parents[3]


# Whatever a helper asks for: the grep generation's commands, a test schema's.
ANSWER = {"commands": ["grep -ri leave ."], "grep_commands": ["grep -ri leave ."], "reasoning": "r"}


class UnmeteredCallError(AssertionError):
    """The provider was asked outside the run's ledger reservation or provider slot."""


class _Grep(BaseModel):
    commands: list[str]


class _CountingLedger(InMemoryBudgetLedger):
    def __init__(self) -> None:
        super().__init__(ManualClock())
        self.open = 0
        self.reserved = 0
        self.committed = 0

    async def reserve(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        reservation = await super().reserve(*args, **kwargs)
        self.open += 1
        self.reserved += 1
        return reservation

    async def commit(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        settled = await super().commit(*args, **kwargs)
        self.open -= 1
        self.committed += 1
        return settled

    async def release(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        settled = await super().release(*args, **kwargs)
        self.open -= 1
        return settled


class _World:
    def __init__(self) -> None:
        self.gate = PriorityGate(4, floor_every=0, monotonic=ManualClock().monotonic)
        self.ledger = _CountingLedger()
        self.calls = 0

    def admitted_and_metered(self) -> bool:
        return self.gate.in_flight(KEY) > 0 and self.ledger.open > 0


class ProviderModel(BaseChatModel):
    """The provider's own chat model: answers only a call the run's transport is making."""

    world: Any
    model_name: str = "gpt-x"

    model_config = {"arbitrary_types_allowed": True, "protected_namespaces": ()}

    @property
    def _llm_type(self) -> str:
        return "provider-fake"

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:  # noqa: ANN401
        raise UnmeteredCallError("a blocking provider call")

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any  # noqa: ANN401
    ) -> ChatResult:
        if not self.world.admitted_and_metered():
            raise UnmeteredCallError("the provider was called outside the run's metered, admitted transport")
        self.world.calls += 1
        usage = {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50}
        tools = kwargs.get("tools") or []
        if tools:
            name = tools[0]["function"]["name"]
            message = AIMessage(content="", tool_calls=[{"name": name, "args": ANSWER, "id": "c1"}], usage_metadata=usage)
        else:
            message = AIMessage(content=json.dumps(ANSWER), usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:  # noqa: ANN401
        from langchain_core.utils.function_calling import convert_to_openai_tool

        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], tool_choice=tool_choice, **kwargs)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[_World]:
    monkeypatch.setattr("app.harness.assembly.model.ledger_enabled", lambda: True)
    monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "langchain")
    found = _World()
    asyncio.run(found.ledger.open_root(RUN, Cap(None, 1_000_000)))
    yield found


def _resolved(world: _World, llm: Any = None) -> ResolvedModel:  # noqa: ANN401
    raw = llm or ProviderModel(world=world)
    transport, name = build_transport(raw, model_name="gpt-x", model_key="model-1")
    return ResolvedModel(
        raw_llm=raw, provider=PROVIDER, is_multimodal=False, context_length=None, is_reasoning=False,
        port=None, transport=transport, transport_name=name,  # type: ignore[arg-type]
    )


def _guard() -> ModelGuard:
    """No classification: the guard is there and lets every call through."""
    return PassthroughGuard()


def _run_model(world: _World) -> ResolvedModel:
    """The run's model as the assembler hands it on: admitted at the claim's class, metered, then guarded."""
    model, _ = metered(
        admitted(_resolved(world), world.gate, "background", None),
        accounts=world.ledger, account=RUN, pricing_model="gpt-x", ids=SeededIds(0),
    )
    return _guard().wrap(model, "gpt-x")


def _meter(world: _World) -> HelperMeter:
    ids = SeededIds(1)
    return helper_meter(
        admission=world.gate, priority_class="background", run_kind=None, accounts=world.ledger, account=RUN,
        ids=lambda: ids,
    )


class _Run:
    org_id = "org-1"
    session_id = "conv-1"


class _Config:
    """The org's AI models: one LLM, also the ``query`` and ``indexing`` roles' model."""

    async def get_config(self, key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        entry = {"provider": PROVIDER, "isDefault": True, "modelKey": "model-1", "configuration": {"model": "gpt-x"}}
        return {
            "llm": [entry],
            "modelRoles": {role: {"modelType": "llm", "modelKey": "model-1"} for role in ("query", "indexing")},
        }


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, world: _World) -> _Config:
    """The configuration's model factory builds the provider's own model, as ``get_generator_model`` does."""
    monkeypatch.setattr("app.utils.llm.get_generator_model", lambda *a, **k: ProviderModel(world=world))
    monkeypatch.setattr("app.utils.llm.load_ai_models", lambda config_service: config_service.get_config("aiModels"))
    return _Config()


# ---- the helpers a run's tools call, each with the model the run hands them -----------------------------------


async def _grep_generation(chat: Any, config: _Config) -> None:  # noqa: ANN401
    from app.utils.pattern_match import (  # pyright: ignore[reportPrivateUsage]
        _grep_generation_llm,
        generate_grep_command_via_llm,
    )

    seen: list[BaseMessage] = []
    llm = await _grep_generation_llm(config, "org-1", chat)  # type: ignore[arg-type]
    commands = await generate_grep_command_via_llm(
        query="leave policy", llm=llm, logger_instance=logging.getLogger("t"), on_llm_usage=seen.append,
    )
    assert commands, "the grep commands came back"
    assert seen and getattr(seen[0], "usage_metadata", None), "the run's usage accounting still sees the call"


async def _entity_name_check(chat: Any, config: _Config) -> None:  # noqa: ANN401
    from app.agents.actions.knowledge_graph.ops.entity_search import verifier_model

    llm, _ = await verifier_model({"config_service": config, "llm": chat})  # type: ignore[arg-type]
    assert llm is not None
    reply = await llm.ainvoke([SystemMessage(content="check"), HumanMessage(content="Is Acme the same as ACME?")])
    assert reply.content


async def _structured_with_reflection(chat: Any, config: _Config) -> None:  # noqa: ANN401
    from app.utils.streaming import invoke_with_structured_output_and_reflection

    parsed = await invoke_with_structured_output_and_reflection(chat, [HumanMessage(content="grep?")], _Grep)
    assert isinstance(parsed, _Grep) and parsed.commands


async def _spreadsheet_tables(chat: Any, config: _Config) -> None:  # noqa: ANN401
    from app.utils.llm import get_llm_for_role

    llm, _ = await get_llm_for_role(config, "indexing", reasoning_effort="low")  # type: ignore[arg-type]
    assert isinstance(llm, RunChatModel), "the parse tool's own model is the run's"
    _ = await llm.ainvoke([HumanMessage(content="describe the table")])


async def _streamed(chat: Any, config: _Config) -> None:  # noqa: ANN401
    text = "".join([str(chunk.content) async for chunk in chat.astream([HumanMessage(content="hi")])])
    assert text


async def _tool_calling(chat: Any, config: _Config) -> None:  # noqa: ANN401
    from app.modules.named_entities.strategies.selector import model_supports_tools

    assert model_supports_tools(chat, PROVIDER, "gpt-x-wp49", None)
    reply = await chat.bind_tools([_Grep]).ainvoke([HumanMessage(content="grep?")])
    assert reply.tool_calls and reply.tool_calls[0]["args"] == ANSWER


HELPERS = {
    "grep generation (query role)": _grep_generation,
    "entity name check (query role)": _entity_name_check,
    "structured output with reflection": _structured_with_reflection,
    "spreadsheet tables (indexing role)": _spreadsheet_tables,
    "streamed": _streamed,
    "tool calling": _tool_calling,
}


@pytest.mark.parametrize("helper", list(HELPERS), ids=list(HELPERS))
async def test_bu05_ga24_every_helper_call_is_charged_to_the_run_and_takes_its_provider_slot(
    world: _World, configured: _Config, helper: str
) -> None:
    chat = _run_model(world).chat_model()
    assert isinstance(chat, RunChatModel)
    with run_model_scope(helper_model_scope(_Run(), _meter(world), _guard())):  # type: ignore[arg-type]
        await HELPERS[helper](chat, configured)
    assert world.calls >= 1
    assert world.ledger.committed == world.calls, "each provider call was settled on the run's account"
    assert world.ledger.open == 0 and world.gate.in_flight(KEY) == 0


async def test_without_a_run_a_legacy_helper_gets_the_provider_model_unchanged(
    world: _World, configured: _Config
) -> None:
    """Classic chat and indexing bind no scope: the model comes back as built, and it is the fake that refuses."""
    from app.utils.llm import get_llm_for_role

    llm, _ = await get_llm_for_role(configured, "query")  # type: ignore[arg-type]
    assert isinstance(llm, ProviderModel)
    with pytest.raises(UnmeteredCallError):
        _ = await llm.ainvoke([HumanMessage(content="hi")])


async def test_bu01_a_run_out_of_budget_refuses_a_helper_call_before_the_provider(world: _World) -> None:
    exhausted = RunAccountRef("org-1", "run-spent")
    await world.ledger.open_root(exhausted, Cap(None, 1))
    model, _ = metered(
        admitted(_resolved(world), world.gate, "interactive", None),
        accounts=world.ledger, account=exhausted, pricing_model="gpt-x", ids=SeededIds(0),
    )
    with pytest.raises(BudgetRefused):
        _ = await _guard().wrap(model, "gpt-x").chat_model().ainvoke([HumanMessage(content="a long helper prompt " * 50)])
    assert world.calls == 0


async def test_ga24_a_tool_gateway_call_in_its_own_task_still_meters_its_helpers(
    world: _World, configured: _Config
) -> None:
    """An engine's tool call arrives on the tool gateway's request task, not the attempt's: the run's tool port
    binds the scope around each call (``around``), so a model the tool builds is still the run's."""
    from app.harness.assembly.tool_environment import ToolEnvironment
    from app.utils.llm import get_llm_for_role
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import ToolContext, classify_tool_effect
    from harness_core.tools.base import Tag
    from harness_core.tools.decorators import tool
    from harness_core.tools.durable_port import RegistryToolPort
    from harness_core.tools.registry import ToolRegistry
    from harness_core.tools.toolset import ToolsetBuilder

    class _Sheets:
        @tool(path="/tools/sheets/read", short_description="Read", description="Read a sheet.", parameters=[])
        async def read(self) -> str:
            llm, _ = await get_llm_for_role(configured, "indexing")  # type: ignore[arg-type]
            reply = await llm.ainvoke([HumanMessage(content="tables?")])
            return str(reply.content)

    registry = ToolRegistry()
    ToolsetBuilder(_Sheets(), name="sheets", description="Sheets", path_prefix="/tools/sheets").register_into(registry)
    environment = ToolEnvironment(context=_Context(), agent=None, web_search_provider=None)  # type: ignore[arg-type]
    environment.helper_models = helper_model_scope(_Run(), _meter(world), _guard())  # type: ignore[arg-type]
    port = RegistryToolPort(
        registry, {"sheets__read": classify_tool_effect("sheets__read", [Tag("category", "read")])},
        around=environment.tool_call_scope,
    )
    ctx = ToolContext("run-1", "org-1", "t1.0", "a1", "org-1:run-1:t1.0")
    executed = await asyncio.create_task(port.execute(ToolCall(id="c1", name="sheets__read", arguments={}), ctx))
    assert executed.status == "ok", executed.content
    assert world.calls == 1 and world.ledger.committed == 1


class _Context:
    harness_run = False


@pytest.mark.parametrize("pin", ["agent", "assistant chat"])
async def test_bu05_the_tool_state_the_run_builds_holds_the_runs_model(world: _World, pin: str) -> None:
    """The run's tool environment (the legacy tool state, or the assistant's chat state, its helpers read ``llm``
    from) is handed the run's model, never the provider's."""
    from unittest.mock import AsyncMock

    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import build_tool_environment
    from tests.unit.harness.assembly import world as w

    if pin == "agent":
        agent = w.agent_doc()
        services = w.Services(agent=agent)
        spec = w.pinned_spec(tool_ids=[])
    else:
        services = w.Services(agent={})
        services.retrieval.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        spec = w.pinned_spec(tool_ids=[], kb=[], agent=False, chat={"mode": "internal_search", "internalSearch": True},
                             strict_scope=False)
    run = RunContext.from_claim(
        w.claim(spec, query="How much leave?", **({} if pin == "agent" else {"chat_mode": "internal_search"}))
    )
    env = await build_tool_environment(run, services, _run_model(world))  # type: ignore[arg-type]
    held = env.context.tool_state.get("llm")
    assert isinstance(held, RunChatModel)
    assert isinstance(env.context.llm, RunChatModel)
    _ = await held.ainvoke([HumanMessage(content="hi")])
    assert world.calls == 1 and world.ledger.committed == 1


# ---- static: nothing a run's tools reach can get at the provider's model ---------------------------------------

# Reading ``raw_llm`` builds a transport from it or asks what it supports; nothing else may.
RAW_LLM_READERS = {
    "app/harness/assembly/model.py",
    "app/harness/assembly/hooks.py",
    "app/harness/assembly/loop.py",
}
# Where a run's own model (and the model gateway's routes) are built; each is wrapped in the run's transport.
RUN_MODEL_BUILDERS = {
    "app/harness/run_policy/pinned.py",
    "app/harness/access/configured.py",
    "app/harness/unpinned.py",
}
# Code a harness run's tools run: none of it may build a provider client or model of its own.
TOOL_REACHABLE = ("app/harness", "app/agents/actions", "app/modules/agents", "app/agents/agent_loop/hooks")
PROVIDER_FACTORIES = {"get_generator_model", "get_generator_model_async", "get_llm_for_chat"}
PROVIDER_CLIENTS = {"ChatOpenAI", "AzureChatOpenAI", "ChatAnthropic", "ChatGoogleGenerativeAI", "ChatBedrock",
                    "ChatBedrockConverse", "ChatMistralAI", "ChatOllama", "AsyncOpenAI", "OpenAI", "AsyncAnthropic",
                    "Anthropic", "acompletion", "completion"}


def _sources(*roots: str) -> Iterator[tuple[str, ast.Module]]:
    for root in roots:
        for path in sorted((PYTHON_ROOT / root).rglob("*.py")):
            relative = path.relative_to(PYTHON_ROOT).as_posix()
            yield relative, ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)


def test_bu05_only_the_transport_builders_read_the_provider_model() -> None:
    readers = {
        name
        for name, tree in _sources("app")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "raw_llm"
    }
    assert readers <= RAW_LLM_READERS, f"handed the provider's model past the run's transport: {readers - RAW_LLM_READERS}"


def test_ga24_harness_tool_code_builds_no_provider_model_or_client_of_its_own() -> None:
    """A helper that wants its own model asks ``app.utils.llm`` for it, which hands back the run's in a run."""
    found: list[str] = []
    for name, tree in _sources(*TOOL_REACHABLE):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("app.utils.aimodels", "app.api.routes.chatbot")):
                hits = {a.name for a in node.names} & PROVIDER_FACTORIES
                if hits and name not in RUN_MODEL_BUILDERS:
                    found.append(f"{name}: imports {sorted(hits)}")
            if isinstance(node, ast.Call):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if callee in PROVIDER_CLIENTS:
                    found.append(f"{name}:{node.lineno}: {callee}(...)")
    assert found == []


def test_bu05_the_configured_model_builders_all_go_through_the_run_scope() -> None:
    """Every place ``app.utils.llm`` builds a chat model hands it through ``scoped_helper_model`` first."""
    tree = ast.parse((PYTHON_ROOT / "app/utils/llm.py").read_text(encoding="utf-8-sig"))
    unscoped: list[int] = []
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)):
        builds = any(
            isinstance(n, ast.Name) and n.id == "get_generator_model" for n in ast.walk(function)
        )
        if not builds:
            continue
        for ret in (n for n in ast.walk(function) if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)):
            first = ret.value.elts[0]  # type: ignore[union-attr]
            scoped = isinstance(first, ast.Call) and getattr(first.func, "id", "") == "scoped_helper_model"
            if not scoped:
                unscoped.append(ret.lineno)
    assert unscoped == [], f"app/utils/llm.py returns a built model unscoped at lines {unscoped}"
