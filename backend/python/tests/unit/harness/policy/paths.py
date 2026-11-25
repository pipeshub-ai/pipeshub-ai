"""QA-10: one approval decision, realized on each path's own production composition.

A ``Scenario`` is everything that decides a tool call: the tool's tags (its effect class and UX1 sink), what the run
has read (P2.5a's ledger over the session's journal), the approvals document, the deployment's flow settings, the
engine profile's approvals and the conversation's grants. Each path builds its policy the way production does and
answers ``allow``, ``ask/<rule>`` or ``deny/<rule>``, the rule being what the card or the model is told:

* ``durable``: ``PipesHubRuntimeAssembler.build`` (the loop's approvals, flow rules, ledger and grants);
* ``engine``: ``ConfiguredApprovals`` and ``engine_guarded`` over ``engine_default_approvals`` with the profile's narrowing among the org's rules (QA10-F3), as
  ``PipesHubEngineAssembler`` composes them;
* ``legacy``: today's loop's PRE_TOOL_USE hooks with ``register_legacy_flow_guard`` (UX3), which refuses where the
  durable paths ask, over the same approvals document (FG2).
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import importlib
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, cast, override
from unittest.mock import patch

from app import edition_config
from app.agents.agent_loop.hooks.flow_guard import (
    LEGACY_REFUSALS,
    register_legacy_flow_guard,
)
from app.harness.approvals import engine_guarded, layer_ask_answers
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import INHERITED_LABEL_KEY, SessionSourceLedger, inherited_label
from app.harness.unpinned import ConfiguredApprovals, engine_governance, flow_rules
from app.harness.web_addresses import run_addresses
from app.harness.worker.warmup import ASSEMBLY_MODULES
from app.web.addresses import web_addresses
from harness_core.effects.flow import (
    CONVERSATION_SCOPE,
    EXPLAINED_EVENT,
    GRANTED_EVENT,
    WEB_ADDRESS_RULE,
    GrantingLedger,
)
from harness_core.effects.types import POLICY_DENIAL, ExplainsAsk, ExplainsDenial
from harness_core.engines.policy import engine_default_approvals
from harness_core.engines.types import ProfileApprovals
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.hooks.events import HookEvent
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.logs import HarnessLog
from harness_core.mcp_hub.annotations import hint_tags
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.contracts.source_ledger import Stream
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import engine_profile
from harness_core.tools.base import Tag
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.tags import EFFECT_TAG_KEY, SINK_TAG_KEY
from tests.support.config_service import FakeConfigurationService
from tests.support.web_tools import product_tools
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.test_source_ledger import PipesHubLedgerWorld, answered

# As the worker does before it claims (C-17): the assembly imports its stack on first use, which would otherwise be
# paid inside the hang guard of whichever property a test worker runs first.
for _module in ASSEMBLY_MODULES:
    importlib.import_module(_module)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.effects.types import ApprovalPolicy, ToolEffect
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.hooks.middleware.pipeline import Pipeline

PathName = Literal["durable", "legacy", "engine"]
Context = Literal[
    "public", "private", "unknown", "after-write", "after-web", "child-private", "child-public", "child-unlabelled"
]
Grant = Literal["none", "same", "other-destination", "other-tool", "not-granted", "declined"]
# Where the address a page fetch opens came from: the model alone, a web search result of the run, a link in a page the
# run opened, the person's message.
Address = Literal["made-up", "searched", "linked", "user"]

RUN: Final = "run-1"
PARENT: Final = "run-parent"
EARLIER: Final = "run-earlier"
APPROVALS_KEY: Final = "/services/agentHarness/approvals"
TOOLSET: Final = "acme"
# Today's loop names a tool by its path's last two segments; the durable paths classify the same name.
TOOL: Final = "send"
TOOL_NAME: Final = f"{TOOLSET}_{TOOL}"
OTHER_TOOL: Final = "notify"
UNCLASSIFIED: Final = "unclassified"
WORLD_PREFIX: Final = "world:"
PRODUCT_PREFIX: Final = "product:"
MCP_PREFIX: Final = "mcp:"
# `mcp:<hints>`: an MCP server's tool by the annotations it declares (FG2), `none` for none.
MCP_HINTS: Final[Mapping[str, Mapping[str, bool]]] = {
    "read-only": {"readOnlyHint": True},
    "closed": {"openWorldHint": False},
    "open": {"openWorldHint": True},
}
PAGE: Final = "https://docs.example.com/guide"
SEARCHED: Final = "https://found.example.org/article?id=7"
GIVEN: Final = "https://given.example.org/notes"
LINKED: Final = "https://cited.example.com/paper.pdf?v=2"
USER_QUERY: Final = f"Compare {GIVEN} with our Q3 numbers"
ADDRESSES: Final[Mapping[Address, str]] = {"made-up": PAGE, "searched": SEARCHED, "linked": LINKED, "user": GIVEN}
DESTINATION: Final = "sales"
FLOW_ENV: Final = ("HARNESS_FLOW_GUARD", "HARNESS_FLOW_GUARD_LEVEL", "HARNESS_FLOW_GUARD_ABOVE")
LOG: Final = HarnessLog.named("tests.qa10.policy")


class Unreadable:
    """The approvals node cannot be read (the config store failed): the paths fail closed."""


UNREADABLE: Final = Unreadable()


@dataclasses.dataclass(frozen=True)
class Scenario:
    tool: str = "write/external"
    context: Context = "public"
    document: JsonValue | Unreadable = None
    deployment: Mapping[str, str] = dataclasses.field(default_factory=dict[str, str])
    profile: ProfileApprovals = dataclasses.field(default_factory=ProfileApprovals)
    grant: Grant = "none"
    address: Address = "made-up"


@dataclasses.dataclass(frozen=True)
class Probe:
    """The call a scenario decides: its tags, as a toolset declares them, and the effect the product classifies."""

    name: str
    path: str
    tags: tuple[Tag, ...]
    call: ToolCall
    effect: ToolEffect


def world_tags() -> dict[str, tuple[Tag, ...]]:
    """The eval and replay world's tools (``tests/harness_replay/world.py``) with the tags their toolsets declare."""
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from tests.harness_replay.world import TOOLSETS

    found: dict[str, tuple[Tag, ...]] = {}
    for name, (cls, _) in TOOLSETS.items():
        builder = ToolsetBuilder(cls.__new__(cls), name=name, description="", path_prefix=f"/tools/{name}")
        found.update({tool.name: tuple(tool.tags) for tool in builder.tools})
    return found


def probe(tool: str, *, name: str = TOOL, destination: str = DESTINATION, address: Address = "made-up") -> Probe:
    """``<effect>/<egress>``, ``<effect>`` (no sink tag), ``unclassified`` (no tags: an MCP server's tool),
    ``world:<name>`` or ``product:<name>`` (the product's per-request tools as a request registers them)."""
    arguments: dict[str, JsonValue] = {"channel": destination, "text": "Q3 numbers"}
    if tool.startswith(PRODUCT_PREFIX):
        product = product_tools()[tool.removeprefix(PRODUCT_PREFIX)]
        effect = classify_tool_effect(product.name, product.tags)
        # Another destination is another site: a grant for a page holds for its whole registrable domain (FLOWASK).
        url = f"https://{destination}.example.net/q?c=Q3" if destination != DESTINATION else ADDRESSES[address]
        call = ToolCall(id="call-1", name=product.name, arguments={"url": url, "query": "Q3 numbers"})
        return Probe(name=product.name, path=product.path, tags=tuple(product.tags), call=call, effect=effect)
    if tool == UNCLASSIFIED:
        tags: tuple[Tag, ...] = ()
        path = f"/mcp/{TOOLSET}/{name}"
    elif tool.startswith(MCP_PREFIX):
        declared = tool.removeprefix(MCP_PREFIX)
        hints = {k: v for token in declared.split("+") if token != "none" for k, v in MCP_HINTS[token].items()}
        tags = hint_tags(hints)
        path = f"/mcp/{TOOLSET}/{name}"
    elif tool.startswith(WORLD_PREFIX):
        world_name = tool.removeprefix(WORLD_PREFIX)
        tags = world_tags()[world_name]
        toolset, _, bare = world_name.partition("__")
        name, path = bare, f"/tools/{toolset}/{bare}"
    else:
        kind, _, egress = tool.partition("/")
        tags = (Tag(EFFECT_TAG_KEY, kind), *((Tag(SINK_TAG_KEY, egress),) if egress else ()))
        path = f"/toolsets/{TOOLSET}/{name}"
    model_name = "_".join(path.strip("/").split("/")[-2:])
    return Probe(
        name=model_name,
        path=path,
        tags=tags,
        call=ToolCall(id="call-1", name=model_name, arguments=arguments),
        effect=classify_tool_effect(model_name, tags),
    )


@contextlib.contextmanager
def deployment_env(settings: Mapping[str, str]) -> Generator[None]:
    """The deployment's flow settings, every other flow variable unset (the shipped default)."""
    with patch.dict(os.environ):
        for name in FLOW_ENV:
            _ = os.environ.pop(name, None)
        os.environ.update(settings)
        yield


@contextlib.contextmanager
def this_build_s_web_answers() -> Generator[None]:
    """The table states this repository's rules for the web tools (no layer governs them), whatever an installed
    edition extension answers instead."""
    with patch.object(edition_config, "layer_ask_answers", None):
        yield


def verdict(policy: ApprovalPolicy, call: ToolCall, effect: ToolEffect) -> str:
    """What the kernel and ``EffectRunner`` make of the policy's answer: the card's rule, or the denial's."""
    decision = policy(call, effect)
    if decision == "allow":
        return "allow"
    if decision == "ask":
        reason = policy.ask_reason(call, effect) if isinstance(policy, ExplainsAsk) else None
        return f"ask/{reason['rule']}" if reason else "ask/policy"
    rule = policy.denial(call, effect)[0] if isinstance(policy, ExplainsDenial) else POLICY_DENIAL[0]
    return f"deny/{rule}"


def _label(context: Context) -> dict[str, JsonValue] | None:
    if context == "child-private":
        return {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-parent"}]}
    if context == "child-public":
        return {"complete": True, "sources": []}
    return None


def run_spec(context: Context) -> RunSpec:
    """The spec Node pins; a child's carries the label its parent passed at the spawn (UX2), when it passed one."""
    spec = w.pinned_spec(tool_ids=[])
    label = _label(context)
    if label is None:
        return spec
    extensions = copy.deepcopy(spec.extensions or {})
    extensions["x.pipeshub"][INHERITED_LABEL_KEY] = label
    return spec.model_copy(update={"extensions": extensions})


def _grant_turn(stream: Stream, grant: Grant, subject: Probe) -> None:
    """An earlier turn of the conversation that asked, offered "allow for this conversation" and was answered; it
    read nothing (its answer's label is empty and complete), so only the grant carries over."""
    if grant == "none":
        return
    same_tool = f"{PRODUCT_PREFIX}{subject.name}" if subject.name in product_tools() else "write/external"
    offered = {
        "other-destination": probe(same_tool, destination="general"),
        "other-tool": probe("write/external", name=OTHER_TOOL),
    }.get(grant, subject)
    where = pipeshub_destination(offered.call, offered.effect)
    assert where is not None
    stream.admitted(EARLIER)
    _ = stream.add(
        EXPLAINED_EVENT,
        {
            "interactionId": "ix-1",
            "rule": "flow_external_write",
            "toolId": offered.effect.tool_id,
            "conversationGrant": True,
            "destination": {"label": where.label, "key": where.key},
        },
        run_id=EARLIER,
    )
    decision = "denied" if grant == "declined" else "approved"
    _ = stream.add("interaction.resolved", {"interactionId": "ix-1", "decision": decision}, run_id=EARLIER, writer="node")
    if grant not in ("not-granted", "declined"):
        _ = stream.add(GRANTED_EVENT, {"interactionId": "ix-1", "scope": CONVERSATION_SCOPE}, run_id=EARLIER, writer="node")
    answered(stream, EARLIER, {"complete": True, "sources": []})


def session_events(scenario: Scenario, subject: Probe) -> list[JournalEvent]:
    """The session's stream as the ledger reads it: an earlier turn's grant, then what this run read before the call."""
    stream = Stream()
    sessions = PipesHubLedgerWorld()
    _grant_turn(stream, scenario.grant, subject)
    # A child's stream holds none of its parent's work: only the label the parent passed at the spawn.
    stream.admitted(RUN, parent=PARENT if scenario.context.startswith("child") else None)
    if scenario.context == "private":
        sessions.recorded(stream, RUN, "inv-read", ["rec-1"])
    elif scenario.context in ("unknown", "after-write"):
        # An external write that ran is a connector's result to the ledger, as opaque as any other.
        sessions.unrecorded(stream, RUN, "inv-read")
    elif scenario.context == "after-web":
        sessions.browsed(stream, RUN, "inv-web")
    if scenario.address in ("searched", "linked"):
        searched(stream, RUN)
    return stream.events


def searched(stream: Stream, run_id: str) -> None:
    """A web search of the run whose results the journal recorded (``webRecords``), as the web tools record them."""
    _ = stream.add(
        "x.pipeshub.citations.recorded",
        {
            "callId": "inv-search",
            "webRecords": [{"url": SEARCHED, "title": "Found", "content": "a result"}],
            # The page the run opened from that result named this link (``webLinks``).
            "webLinks": [{"page": SEARCHED, "links": [LINKED]}],
        },
        run_id=run_id,
    )


class _UnreadableConfig(FakeConfigurationService):
    @override
    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False) -> Any:
        if key == APPROVALS_KEY:
            raise ConnectionError("config store unavailable")
        return await super().get_config(key, default, use_cache, raise_on_error=raise_on_error)


def _services(document: JsonValue | Unreadable) -> w.Services:
    services = w.Services()
    if isinstance(document, Unreadable):
        nodes = {key: value for key, value in vars(services.config)["_nodes"].items() if key != APPROVALS_KEY}
        services.config = _UnreadableConfig(nodes, prefixes=vars(services.config)["_prefixes"])
        services.graph.config_service = services.config
    else:
        services.config.put(APPROVALS_KEY, document)
    return services


class PolicyPath(Protocol):
    name: PathName

    async def decide(self, scenario: Scenario) -> str: ...


class DurableLoop:
    name: PathName = "durable"

    async def decide(self, scenario: Scenario) -> str:
        subject = probe(scenario.tool, address=scenario.address)
        services = _services(scenario.document)
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="qa10", turns=[Turn(reply=Reply(text="ok"))])))
        with deployment_env(scenario.deployment), w.deployment(), this_build_s_web_answers():
            assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
            claim = w.claim(run_spec(scenario.context), query=USER_QUERY) if scenario.address == "user" else w.claim(
                run_spec(scenario.context)
            )
            runtime = await assembler.build(claim)  # pyright: ignore[reportArgumentType]
            try:
                if runtime.ledger is not None:
                    runtime.ledger.apply(session_events(scenario, subject))
                return verdict(runtime.approval, subject.call, subject.effect)
            finally:
                if runtime.close is not None:
                    await runtime.close()


class EngineRun:
    name: PathName = "engine"

    async def decide(self, scenario: Scenario) -> str:
        subject = probe(scenario.tool, address=scenario.address)
        services = _services(scenario.document)
        profile = dataclasses.replace(engine_profile(), approvals=scenario.profile)
        with deployment_env(scenario.deployment), this_build_s_web_answers():
            org = await ConfiguredApprovals(services.config_service()).policy(org_id=w.ORG, agent_key=w.AGENT, log=LOG)
            sessions = SessionSourceLedger(inherited=inherited_label(run_spec(scenario.context)))
            ledger = GrantingLedger(sessions)
            ledger.apply(session_events(scenario, subject))
            # As ``PipesHubEngineAssembler`` binds them: the person's message (a child's goal is not theirs) and the
            # session's recorded pages.
            run = SimpleNamespace(
                sub_agent=object() if scenario.context.startswith("child") else None,
                query=USER_QUERY if scenario.address == "user" else "Q3?",
                previous_conversations=(),
            )
            policy = engine_guarded(
                engine_default_approvals(),
                # As the engine assembler wraps the run's governance (``layer_ask_answers``).
                layer_ask_answers(
                    await engine_governance(services.config_service(), org, profile, w.ORG, LOG),
                    context=SimpleNamespace(user_id=None, tool_state={}),  # type: ignore[arg-type]
                    grants=ledger.grants,
                    log=LOG,
                ),
                rules=flow_rules(LOG, org.flows),
                ledger=ledger,
                run_id=RUN,
                log=LOG,
                addresses=run_addresses({}, run, sessions),  # type: ignore[arg-type]
            )
            return verdict(policy, subject.call, subject.effect)


READ_FIRST: Final = Probe(
    name="retrieval_search",
    path="/toolsets/retrieval/search",
    tags=(Tag("category", "read"),),
    call=ToolCall(id="call-0", name="retrieval_search", arguments={"query": "Q3"}),
    effect=classify_tool_effect("retrieval_search", (Tag("category", "read"),)),
)


def _legacy_scope() -> SimpleNamespace:
    """What a request's hooks resolve tool names through: the product's per-request tools, as a request registers them."""
    registry = ToolRegistry()
    for tool in product_tools().values():
        registry.register_tool(tool)
    return SimpleNamespace(turn=SimpleNamespace(run=SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry))))


class LegacyLoop:
    """Today's loop keeps no ledger: a request that ran a call whose result may carry private content counts as having
    read something nobody can vouch for, and so does a conversation with history (UX3 D-5, QA10-F2). It reads the
    approvals document's flow layers (QA10-F1) and has no grants."""

    name: PathName = "legacy"

    async def decide(self, scenario: Scenario) -> str:
        subject = probe(scenario.tool, address=scenario.address)
        history = [{"role": "user_query", "content": "Q3?"}] if scenario.context == "unknown" else []
        config = _services(scenario.document).config_service()
        context = SimpleNamespace(
            org_id=w.ORG,
            agent_key=w.AGENT,
            config_service=config,
            previous_conversations=history,
            tool_state={},
            query=USER_QUERY if scenario.address == "user" else "Q3?",
        )
        if scenario.address in ("searched", "linked"):
            # What the web tools' adapter notes of a search's results, and of the page it opened from one.
            web_addresses(context.tool_state).note_result({"web_results": [{"link": SEARCHED}]})
            web_addresses(context.tool_state).note_result(
                {"result_type": "url_content", "url": SEARCHED, "blocks": [{"content": f"See [the paper]({LINKED})."}]}
            )
        hooks = HookRegistry()
        with deployment_env(scenario.deployment):
            _ = await register_legacy_flow_guard(hooks, context)
        # The hook library's Pipeline is not generic over its context.
        pre = cast("Pipeline[ToolCallContext]", hooks.on(HookEvent.PRE_TOOL_USE))  # pyright: ignore[reportUnknownMemberType]
        registry = _legacy_scope()

        def scope(probe_: Probe) -> SimpleNamespace | None:
            # The product's per-request tools resolve through a request's registry; the others are named by their path.
            return registry if probe_.name in product_tools() else None

        first = {
            "private": READ_FIRST,
            "after-write": probe("write/external", name=OTHER_TOOL),
            "after-web": probe(f"{PRODUCT_PREFIX}dynamic__fetch_url"),
        }.get(scenario.context)
        if first is not None:
            ran = await pre.dispatch(ToolCallContext(tool_path=first.path, tool_input={}, tags=first.tags, scope=scope(first)))  # pyright: ignore[reportArgumentType]
            assert ran.decision != PreDecision.DENY
        ctx = await pre.dispatch(
            ToolCallContext(tool_path=subject.path, tool_input=dict(subject.call.arguments), tags=subject.tags, scope=scope(subject))  # pyright: ignore[reportArgumentType]
        )
        if ctx.decision == PreDecision.DENY:
            assert ctx.decision_reason in LEGACY_REFUSALS
            if ctx.decision_code == WEB_ADDRESS_RULE:
                return f"deny/{WEB_ADDRESS_RULE}"
            return "deny/" + str(ctx.decision_reason).split(":", 1)[0]
        return "allow"


PATHS: Final[Mapping[PathName, PolicyPath]] = {"durable": DurableLoop(), "engine": EngineRun(), "legacy": LegacyLoop()}


def paths(names: Sequence[PathName]) -> list[PolicyPath]:
    return [PATHS[name] for name in names]


__all__ = [
    "ADDRESSES",
    "PATHS",
    "PRODUCT_PREFIX",
    "RUN",
    "TOOL_NAME",
    "UNCLASSIFIED",
    "UNREADABLE",
    "WORLD_PREFIX",
    "Address",
    "Context",
    "DurableLoop",
    "EngineRun",
    "Grant",
    "LegacyLoop",
    "PathName",
    "PolicyPath",
    "Probe",
    "Scenario",
    "Unreadable",
    "deployment_env",
    "probe",
    "run_spec",
    "session_events",
    "verdict",
    "world_tags",
]
