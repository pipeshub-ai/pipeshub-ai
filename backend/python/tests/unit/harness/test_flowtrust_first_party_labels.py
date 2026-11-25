"""FLOWTRUST: a first-party tool whose result is computed from its own arguments (the calculators,
the image generator) shows the model nothing the run had not read, so the flow rule judges a later web search on what
the run read before it, not as ``unknown``. What the run read still decides: a prompt built from a private record
leaves the run private, and an MCP or connector result stays unknown whatever the tool is called.

Evidence: a recorded run asked six web_search/fetch_url calls under ``flow_external_write`` only because an
earlier generate_image result counted as ``unknown``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.hooks.flow_guard import _note_result, _seen, request_label
from app.agents.registry.toolset_registry import get_toolset_registry
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.labels import (
    SessionSourceLedger,
    derived_output,
    label_wire,
    own_work_label,
    unknown_label,
)
from app.harness.unpinned import FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FLOW_RULE
from harness_core.effects.types import ToolEffect
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from harness_core.tools.base import Tag
from harness_core.tools.tags import TAG_OUTPUT_DERIVED, TAG_PROVIDER_PLATFORM
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

IMAGE = "image_generator__generate_image"
IMAGE_ID = "pipeshub@image_generator/generate_image"
SEARCH = "dynamic__web_search"
SECRET = "Q3 acquisition target: Contoso"
# Reviewed per tool: every tool that declares its result derived from its arguments. A new one is a review, not a tag.
DERIVED = {
    IMAGE_ID,
    "pipeshub@calculator/calculate_single_operand",
    "pipeshub@calculator/calculate_two_operands",
    "pipeshub@calculator/evaluate_expression",
    "pipeshub@calculator/date_difference",
    "pipeshub@calculator/count_text",
    "pipeshub@date_calculator/get_exclusion_dates",
    "pipeshub@date_calculator/list_weekend_dates",
    "pipeshub@date_calculator/parse_holiday_dates",
}


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.delenv(FLOW_LEVEL_ENV, raising=False)


def test_ft1_the_derived_tools_are_the_reviewed_platform_tools() -> None:
    registry = get_toolset_registry()
    if not registry.get_all_toolsets():
        registry.auto_discover_toolsets()
    declared = {"key": TAG_OUTPUT_DERIVED.key, "value": TAG_OUTPUT_DERIVED.value}
    found: set[tuple[str, str]] = set()
    for name in registry.list_toolsets():
        metadata = registry.get_toolset_metadata(name, serialize=False) or {}
        for tool in metadata.get("tools") or []:
            if declared in (tool.get("tags") or []):
                assert metadata.get("provider") == TAG_PROVIDER_PLATFORM.value, name
                found.add((name, tool["name"]))
    toolset_and_tool = (tool_id.removeprefix("pipeshub@").split("/") for tool_id in DERIVED)
    assert found == {(toolset.replace("_", ""), tool) for toolset, tool in toolset_and_tool}
    assert all(derived_output(tool_id) for tool_id in DERIVED)


@pytest.mark.parametrize(
    "tool_id",
    [
        "mcp@f6cf3be4/generate_image",  # an MCP server's tool of the same name
        "jira@inst-1/calculate_two_operands",
        "web@duckduckgo/web_search",
        "pipeshub@image_generator/other",
        "pipeshub@sandbox/run_code",  # code that may reach the network or the workspace: still unknown
        "pipeshub@knowledgehub/generate_image",
        "generate_image",
    ],
)
def test_ft2_no_other_tool_id_is_derived(tool_id: str) -> None:
    assert not derived_output(tool_id)


async def _image(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content='{"success": true, "message": "Generated 1 image(s)."}')


async def _search(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content="Seven wonders: Giza, ...")


async def _fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


def _world(image_id: str = IMAGE_ID) -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    image = ToolEffect(
        tool_id=image_id, effect="write", idempotent=False, sink=SinkDescriptor(egress="self", args_leave_boundary=False)
    )
    world.tools.add(IMAGE, _image, effect=image)
    world.tools.add(SEARCH, _search, tags=[("effect", "read"), ("sink", "external")])
    world.tools.add(FETCH_RECORD_TOOL_NAME, _fetch_record, tags=[("category", "read")])
    world.admit()
    return world


def _kernel(world: World, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = SessionSourceLedger()
    log = HarnessLog.named("tests.flowtrust")
    approval = flow_guarded(allow_all, rules=flow_rules(log), ledger=ledger, run_id=RUN, log=log)
    return world.kernel(port, approval=approval, ledger=ledger)


DRAW = reply("", (IMAGE, {"prompt": "the seven wonders", "file_name": "seven_wonders"}))
LOOK_UP = reply("", (SEARCH, {"query": "seven wonders of the ancient world"}))


def _explained(world: World) -> list[Mapping[str, JsonValue]]:
    return [e.data for e in world.events() if e.type == "interaction.explained"]


async def test_ft3_a_search_after_an_image_from_the_users_own_prompt_does_not_ask() -> None:
    world = _world()
    done = await world.run(_kernel(world, DRAW, LOOK_UP, reply("done")))
    assert done.status == "completed"
    assert "interaction.requested" not in world.types()
    assert sorted(name for name, _ in world.tools.executed) == sorted([IMAGE, SEARCH])


async def test_ft4_an_image_whose_prompt_carried_a_private_record_still_asks_and_says_why() -> None:
    world = _world()
    read = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))
    draw = reply("", (IMAGE, {"prompt": f"a poster about {SECRET}", "file_name": "poster"}))
    done = await world.run(_kernel(world, read, draw, LOOK_UP, reply("unused")))
    assert done.status == "suspended"
    assert SEARCH not in [name for name, _ in world.tools.executed]
    [why] = _explained(world)
    assert (why["rule"], why["sensitivity"]) == (FLOW_RULE, "private")
    assert why["sources"] == [{"title": "Q3 plan", "recordId": "rec-1"}]


async def test_ft5_an_mcp_tool_of_the_same_name_stays_unknown_and_the_card_says_so() -> None:
    world = _world(image_id="mcp@f6cf3be4/generate_image")
    done = await world.run(_kernel(world, DRAW, LOOK_UP, reply("unused")))
    assert done.status == "suspended"
    [why] = _explained(world)
    assert (why["rule"], why["sensitivity"]) == (FLOW_RULE, "unknown")


async def test_ft6_the_label_is_the_journals_on_replay_and_in_the_next_turn() -> None:
    """The label is a function of the journal and the release's code: a fresh ledger fed the same events, in one batch
    or event by event, says the same, and the answer's recorded label keeps the next turn public."""
    world = _world()
    assert (await world.run(_kernel(world, DRAW, LOOK_UP, reply("done")))).status == "completed"
    events = world.events()
    once = label_wire(own_work_label(events, RUN))
    assert once == label_wire(own_work_label(events, RUN)) == {"complete": True, "sources": []}
    whole, piecewise = SessionSourceLedger(), SessionSourceLedger()
    whole.apply(events)
    for event in events:
        piecewise.apply([event])
    piecewise.apply(events)
    assert whole.source_records(RUN) == piecewise.source_records(RUN) == frozenset()


@pytest.mark.parametrize(
    ("tags", "seen"),
    [
        ((TAG_OUTPUT_DERIVED, TAG_PROVIDER_PLATFORM), "public"),
        ((TAG_OUTPUT_DERIVED,), "unknown"),  # the tag counts only on the platform's own tool
        ((TAG_PROVIDER_PLATFORM,), "unknown"),
    ],
)
def test_ft7_todays_loop_reads_the_same_declaration(tags: tuple[Tag, ...], seen: str) -> None:
    context = SimpleNamespace(tool_state={"flow_guard_watched": True}, previous_conversations=None)
    ctx = SimpleNamespace(tool_path="/tools/image_generator/generate_image", tags=tags)
    effect = classify_tool_effect(IMAGE, [*tags, Tag("effect", "write"), Tag("sink", "self")])
    _note_result(context, IMAGE, ctx, effect)  # type: ignore[arg-type]
    assert _seen(context) == seen
    public = {"complete": True, "sources": []}
    assert request_label(context) == (public if seen == "public" else label_wire(unknown_label()))
