"""HANDOFF-CTX HC-1: a delegation is decided with what the run's earlier turns already gathered.

Found in a manual test: the chat loop made six Jira MCP calls, then handed the work to Codex; the engine child
started from the model's brief alone and called getAccessibleAtlassianResources, searchJiraIssuesUsingJql and
getJiraIssue again (13 calls, one refused for the cloudId the parent's first call had found). The registry was never
shown the parent's results, so no delegation could pass them on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.journal.children import ChildRequest
from harness_core.kernel.durable.delegation import (
    Decision,
    Delegated,
    DelegatedOutcome,
    DelegationView,
    delegate_effect,
    goal_tool,
    is_success,
)
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema


class _Recording:
    """`delegate{goal}`: spawns its goal and keeps every view it was decided with."""

    def __init__(self) -> None:
        self.views: list[DelegationView] = []

    def delegates(self, call: ToolCall) -> bool:
        return call.name == "delegate"

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        self.views.append(view)
        return ChildRequest(invocation_id=call.invocation_id, goal=str(call.call.arguments.get("goal")))

    async def outcome(self, call: ToolCall, document: JsonValue) -> DelegatedOutcome:
        return DelegatedOutcome("shown", error=not is_success(document))

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [goal_tool("delegate", "Hand one task to a sub-agent")]


async def test_hc1_the_view_holds_each_earlier_tool_call_with_its_result() -> None:
    world = World()
    world.admit(spec=minimal_run_spec(max_children=2))
    recording = _Recording()
    _, port = scripted(
        reply("", ("lookup", {"q": "cloud"})),
        reply("", ("lookup", {"q": "jql open bugs"})),
        reply("", ("delegate", {"goal": "write the report"}), ("lookup", {"q": "same turn"})),
        reply("done"),
    )
    first = await world.run(world.kernel(port, children=world.journal, delegation=recording))
    assert (first.status, first.reason) == ("suspended", "join")

    view = recording.views[-1]
    gathered = [(g.call.name, dict(g.call.arguments), g.status, g.outcome, g.turn) for g in view.gathered]
    # Earlier turns only, in order; never the delegation itself nor a call of the hand-off's own turn.
    assert gathered == [
        ("lookup", {"q": "cloud"}, "ok", {"found": "cloud"}, 1),
        ("lookup", {"q": "jql open bugs"}, "ok", {"found": "jql open bugs"}, 2),
    ]
    (child,) = world.journal.children_of(RUN)
    assert world.journal._runs[child].input_text == "write the report"  # pyright: ignore[reportPrivateUsage]


async def test_hc1_a_reclaimed_attempt_is_shown_the_same_results() -> None:
    """``decide`` is a pure function of the view, so the view a re-claim builds must be the same."""
    world = World()
    world.admit(spec=minimal_run_spec(max_children=2))
    recording = _Recording()
    _, port = scripted(
        reply("", ("lookup", {"q": "cloud"})),
        reply("", ("delegate", {"goal": "go"})),
        reply("done"),
    )
    _ = await world.run(world.kernel(port, children=world.journal, delegation=recording))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("child done"))
    assert (await world.run(world.kernel(child_port), child)).status == "completed"
    _ = await world.run(world.kernel(port, children=world.journal, delegation=recording))
    shown = [tuple((g.call.name, g.outcome) for g in v.gathered) for v in recording.views]
    assert shown and all(s == (("lookup", {"found": "cloud"}),) for s in shown), shown
