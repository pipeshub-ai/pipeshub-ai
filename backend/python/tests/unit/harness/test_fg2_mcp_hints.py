"""FG2 MA: an MCP tool's effect and sink come from the MCP spec's own annotations. A read-only
tool is a read, only ``openWorldHint: false`` keeps its arguments in the user's own space (a missing hint is open
world, as the spec says: SEC-B-02, ADR-OWD-09 D-MCP-OPENWORLD), and a tool with no annotations stays a send. The hints
are covered by the descriptor digest, so a server that flips one loses its approval and the run's pin; a server that
lies is held only by the admin's approval of it and by an org's own rules."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import mcp.types
import pytest

from app.agents.agent_loop.hooks.flow_guard import register_legacy_flow_guard
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.tool_kind import tool_kind
from app.harness.approvals import approval_policy
from app.harness.mcp_hub.hub import (
    _descriptor,  # pyright: ignore[reportPrivateUsage]
)
from app.harness.mcp_hub.manifests import descriptor_of
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule
from harness_core.effects.types import allow_all
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.hooks.events import HookEvent
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.logs import HarnessLog
from harness_core.mcp_hub.annotations import declared_hints, hint_tags
from harness_core.mcp_hub.errors import ToolChanged
from harness_core.mcp_hub.manifest import ApprovedManifest, review_manifest
from tests.support.config_service import FakeConfigurationService

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect

READ_ONLY = {"readOnlyHint": True}
CLOSED_READ = {"readOnlyHint": True, "openWorldHint": False}
OPEN_READ = {"readOnlyHint": True, "openWorldHint": True}
CLOSED_WRITE = {"readOnlyHint": False, "openWorldHint": False}


def _info(name: str, hints: dict[str, bool] | None = None) -> MCPToolInfo:
    return MCPToolInfo(
        name=name, namespaced_name=f"acme__{name}", description=f"{name} tool", input_schema={}, annotations=hints or {}
    )


def _adapter(name: str, hints: dict[str, bool] | None = None) -> MCPToolAdapter:
    server = SimpleNamespace(instance_id="inst-1", display_name="Acme", name="acme")
    return MCPToolAdapter(server, _info(name, hints), session_manager=None)  # type: ignore[arg-type]


def _effect(hints: dict[str, bool] | None) -> ToolEffect:
    tool = _adapter("t", hints)
    return classify_tool_effect(tool.name, tool.tags)


@pytest.mark.parametrize(
    ("hints", "effect", "egress"),
    [
        (None, "write", "external"),
        ({"destructiveHint": True}, "write", "external"),
        (READ_ONLY, "read", "external"),
        (CLOSED_READ, "read", "self"),
        (OPEN_READ, "read", "external"),
        (CLOSED_WRITE, "write", "self"),
        ({"readOnlyHint": False}, "write", "external"),
        ({"openWorldHint": False}, "write", "self"),
        ({"readOnlyHint": "yes"}, "write", "external"),
    ],
    ids=["none", "destructive", "read-only", "closed-read", "open-read", "closed-write", "open-write",
         "closed-unclassified", "unreadable"],
)
def test_ma1_the_hints_set_the_effect_and_sink(hints: dict[str, Any] | None, effect: str, egress: str) -> None:
    classified = _effect(declared_hints(hints))
    assert (classified.effect, classified.sink.egress) == (effect, egress)


def test_ma2_both_discoveries_keep_the_hints_the_server_declared() -> None:
    tool = mcp.types.Tool(
        name="list_issues", input_schema={"type": "object"}, annotations=mcp.types.ToolAnnotations(read_only_hint=True, title="x")
    )
    assert _descriptor(tool).hints == {"readOnlyHint": True}
    assert declared_hints({"openWorldHint": False, "title": "x"}) == {"openWorldHint": False}
    assert hint_tags({}) == hint_tags(declared_hints(None))


def test_ma3_a_server_that_flips_a_hint_loses_its_approval() -> None:
    approved = descriptor_of(_info("send_note", READ_ONLY))
    unannotated = descriptor_of(_info("send_note"))
    assert approved.digest() != unannotated.digest()
    # A descriptor without hints digests as before FG2, so approvals made then still hold.
    assert unannotated.digest() == descriptor_of(_info("send_note")).digest()
    manifest = ApprovedManifest({"send_note": approved.digest()}, approved_by="admin", approved_at=0)
    flipped = descriptor_of(_info("send_note", {"readOnlyHint": False}))
    review = review_manifest(manifest, [flipped])
    assert [(d.name, d.reason_code) for d in review.disabled] == [("send_note", ToolChanged.reason_code)]
    assert review_manifest(manifest, [approved]).admitted == (approved,)


async def _legacy(*tools: MCPToolAdapter) -> list[PreDecision]:
    """Today's loop with the flow guard at its default, the calls in order."""
    context = SimpleNamespace(org_id="org-1", agent_key=None, config_service=FakeConfigurationService(),
                              previous_conversations=[], tool_state={})
    hooks = HookRegistry()
    _ = await register_legacy_flow_guard(hooks, context)
    decisions: list[PreDecision] = []
    for tool in tools:
        ctx = ToolCallContext(tool_path=tool.path, tool_input={}, tags=tool.tags)
        await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)  # pyright: ignore[reportUnknownMemberType]
        decisions.append(ctx.decision)
    return decisions


@pytest.fixture
def guard_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HARNESS_FLOW_GUARD", "HARNESS_FLOW_GUARD_LEVEL", "HARNESS_FLOW_GUARD_ABOVE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("guard_default")
async def test_ma4_a_second_closed_world_read_works_on_todays_loop() -> None:
    assert await _legacy(_adapter("list_issues", CLOSED_READ), _adapter("get_issue", CLOSED_READ)) == [PreDecision.ALLOW] * 2


@pytest.mark.usefixtures("guard_default")
async def test_ma4_a_read_only_call_that_does_not_say_it_stays_closed_is_held_like_a_web_fetch() -> None:
    """SEC-B-02: ``readOnlyHint`` alone says nothing about where the query goes; the spec's default is open world."""
    assert await _legacy(_adapter("list_issues", CLOSED_READ), _adapter("search", READ_ONLY)) == [
        PreDecision.ALLOW, PreDecision.DENY
    ]


@pytest.mark.usefixtures("guard_default")
async def test_ma4_an_unannotated_second_call_is_still_refused() -> None:
    assert await _legacy(_adapter("list_issues"), _adapter("get_issue")) == [PreDecision.ALLOW, PreDecision.DENY]


@pytest.mark.usefixtures("guard_default")
async def test_ma4_an_open_world_read_after_a_result_is_held_like_a_web_fetch() -> None:
    assert await _legacy(_adapter("list_issues", READ_ONLY), _adapter("fetch", OPEN_READ)) == [PreDecision.ALLOW, PreDecision.DENY]


@pytest.mark.usefixtures("guard_default")
async def test_ma5_a_server_that_lies_is_trusted_as_far_as_its_admin() -> None:
    """A tool that sends but says it only reads: the flow guard believes the hint (the second call goes out on today's
    loop) only when it claims a closed world too. What holds it is the approval of exactly that descriptor (MA-3) and
    a rule for the tool, which applies over the hint on the durable loop and engine runs."""
    liar = _adapter("post_note", CLOSED_READ)
    assert await _legacy(_adapter("list_issues", CLOSED_READ), liar) == [PreDecision.ALLOW] * 2
    effect = classify_tool_effect(liar.name, liar.tags)
    call = ToolCall(id="c", name=liar.name, arguments={})
    log = HarnessLog.named("tests.fg2.mcp")
    by_tool = approval_policy({"default": {"tools": {effect.tool_id: "deny"}}}, org_id="org-1", agent_key=None, log=log)
    by_class = approval_policy({"default": {"classes": {"read": "ask"}}}, org_id="org-1", agent_key=None, log=log)
    assert (by_tool(call, effect), by_class(call, effect)) == ("deny", "ask")
    # The flow rule on its own does not hold a read that stays in the user's own space.
    assert FlowRule("deny").decide("unknown", effect) == "allow"


# SEC-B-02 bypass variants: whatever a server sends, only a declared boolean ``openWorldHint: false`` narrows the sink.
_NOT_FALSE = [None, "false", "False", 0, "", [], {}]


@pytest.mark.parametrize("value", _NOT_FALSE, ids=[repr(v) for v in _NOT_FALSE])
def test_sec_b_02_an_open_world_hint_that_is_not_the_boolean_false_is_external(value: object) -> None:
    effect = _effect(declared_hints({"readOnlyHint": True, "openWorldHint": value}))
    assert (effect.effect, effect.sink.egress, effect.sink.args_leave_boundary) == ("read", "external", True)


def test_sec_b_02_an_absent_open_world_hint_is_external_and_the_boolean_false_is_self() -> None:
    assert _effect(declared_hints({"readOnlyHint": True})).sink.egress == "external"
    assert _effect(declared_hints({"readOnlyHint": True, "openWorldHint": False})).sink.egress == "self"


@pytest.mark.parametrize("annotations", [None, {}, {"title": "Search"}, mcp.types.ToolAnnotations()],
                         ids=["none", "empty", "title-only", "sdk-empty"])
def test_sec_b_02_a_tool_without_hints_is_an_external_write(annotations: object) -> None:
    effect = _effect(declared_hints(annotations))
    assert (effect.effect, effect.sink.egress) == ("write", "external")


def test_sec_b_02_the_sdk_model_reads_as_the_dict_does() -> None:
    sdk = mcp.types.ToolAnnotations
    assert declared_hints(sdk(read_only_hint=True, open_world_hint=None)) == {"readOnlyHint": True}
    assert _effect(declared_hints(sdk(read_only_hint=True))).sink.egress == "external"
    assert _effect(declared_hints(sdk(read_only_hint=True, open_world_hint=False))).sink.egress == "self"


@pytest.mark.parametrize(
    "hints",
    [
        {"readOnlyHint": True, "OpenWorldHint": False},
        {"readOnlyHint": True, "openworldhint": False},
        {"readOnlyHint": True, "open_world_hint": False},
        {"readOnlyHint": True, " openWorldHint": False},
        {"readOnlyHint": True, "openWorldHint\u200b": False},
    ],
    ids=["pascal", "lower", "snake", "leading-space", "zero-width"],
)
def test_sec_b_02_a_lookalike_open_world_key_is_not_the_hint(hints: dict[str, Any]) -> None:
    assert _effect(declared_hints(hints)).sink.egress == "external"


@pytest.mark.parametrize("key", ["ReadOnlyHint", "readonlyhint", "read_only_hint"])
def test_sec_b_02_a_lookalike_read_only_key_does_not_make_a_read(key: str) -> None:
    hints = {key: True, "openWorldHint": False}
    assert _effect(declared_hints(hints)).effect == "write"
    assert tool_kind("search", hints)[0] == "write"


@pytest.mark.parametrize(
    "annotations",
    [None, {}, READ_ONLY, CLOSED_READ, OPEN_READ, CLOSED_WRITE, {"readOnlyHint": "true"}, {"readOnlyHint": 1},
     {"ReadOnlyHint": True}, {"destructiveHint": False}, {"destructiveHint": "true"}],
)
def test_sec_b_02_the_starting_rule_reads_the_hints_as_the_effect_does(annotations: dict[str, Any] | None) -> None:
    """One reading of the hints: a tool whose starting rule is a read's (allow) is a read for the flow guard too."""
    kind, _ = tool_kind("lookup", annotations)
    effect = _effect(declared_hints(annotations))
    assert (kind == "read") == (effect.effect == "read")


def test_sec_b_02_a_relisted_tool_that_drops_its_closed_world_claim_loses_its_approval_and_never_widens() -> None:
    approved = descriptor_of(_info("search", CLOSED_READ))
    manifest = ApprovedManifest({"search": approved.digest()}, approved_by="admin", approved_at=0)
    relisted = descriptor_of(_info("search", READ_ONLY))
    review = review_manifest(manifest, [relisted])
    assert [(d.name, d.reason_code) for d in review.disabled] == [("search", ToolChanged.reason_code)]
    assert _effect(dict(relisted.hints)).sink.egress == "external"


def test_sec_b_02_a_relisted_tool_that_adds_a_closed_world_claim_needs_a_new_approval() -> None:
    """The narrowing direction a rug-pull would use: approved as open world, re-listed as closed to slip past the guard."""
    approved = descriptor_of(_info("search", READ_ONLY))
    manifest = ApprovedManifest({"search": approved.digest()}, approved_by="admin", approved_at=0)
    review = review_manifest(manifest, [descriptor_of(_info("search", CLOSED_READ))])
    assert [(d.name, d.reason_code) for d in review.disabled] == [("search", ToolChanged.reason_code)]
    assert review.admitted == ()


class _PrivateAndUntrusted:
    def source_records(self, run_id: str) -> frozenset[str]:
        return frozenset({"rec-private-1"})

    def untrusted_read(self, run_id: str) -> bool:
        return True


@pytest.mark.parametrize("level", ["ask", "deny"])
def test_sec_b_02_after_a_private_read_an_open_world_read_only_call_is_held(level: str) -> None:
    policy = FlowGuardedPolicy(allow_all, FlowRule(level), _PrivateAndUntrusted(), "run-1",  # type: ignore[arg-type]
                               HarnessLog.named("tests.fg2.mcp"))
    call = ToolCall(id="c", name="acme__search", arguments={"query": "the private record"})
    assert policy(call, _effect(READ_ONLY)) == level
    assert policy(call, _effect(OPEN_READ)) == level
    assert policy(call, _effect(CLOSED_READ)) == "allow"
