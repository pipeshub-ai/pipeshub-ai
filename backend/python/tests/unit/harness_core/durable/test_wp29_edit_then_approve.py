"""AP-06 (C3.75) on the loop: a person edits a held write before approving it. The edit runs, the model is told
what ran, and the journal keeps both versions with their hashes; a rule still refuses an edit it would refuse, an
edit nobody can read runs nothing, and the journal takes an edit only where the worker offered one."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts import canonical_digest, canonical_json
from harness_core.effects.edits import EDIT_UNREADABLE
from harness_core.journal.inline import content_digest, inline_ref
from harness_core.journal.types import (
    APPROVAL_EDITS,
    APPROVAL_REASONS,
    APPROVAL_RULE_LAYERS,
    ConversationGrantNotOffered,
    EditNotOffered,
)
from harness_core.testing.interactions import ManualWakeup

from .support import RUN, World, project, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision, ToolEffect
    from harness_core.kernel.core.messages import ToolCall

FIRST = "ix.approve.t1.0"
ASKED: dict[str, JsonValue] = {"channel": "#general", "text": "Q3 numbers attached"}
EDITED: dict[str, JsonValue] = {"channel": "#team-updates", "text": "Q3 numbers, summary only"}


class AsksForWrites:
    """An org rule that asks before every write and says so; it refuses a post to ``#blocked`` outright."""

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        if call.arguments.get("channel") == "#blocked":
            return "deny"
        return "ask" if effect.needs_intent_commit else "allow"

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> dict[str, JsonValue]:
        return {"rule": "writes", "layer": "org", "toolId": effect.tool_id, "conversationGrant": False}


async def _decided(
    world: World, *, edits: dict[str, JsonValue] | None, decide: bool = True
) -> list[tuple[str, str]]:
    world.admit()
    _, port = scripted(reply("", ("post_message", ASKED)), reply("done"))
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 1 and decide:
            world.journal.resolve_interaction(RUN, FIRST, "approved", edits=edits)
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(
        world.kernel(port, approval=AsksForWrites(), interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert done.status == "completed"
    return await project(world)


def _posted(world: World) -> list[dict[str, JsonValue]]:
    return [
        {k: v for k, v in e.payload.items() if k != "invocation"} for e in world.saas.effects if e.op == "post_message"
    ]


async def test_ap06_the_offer_names_the_call_and_the_edit_runs_once_in_place_of_the_models() -> None:
    world = World()
    pairs = await _decided(world, edits=EDITED)
    [explained] = world.events("interaction.explained")
    assert explained.data["editable"] is True
    assert _posted(world) == [EDITED]
    results = [text for role, text in pairs if role == "tool"]
    assert len(results) == 1
    assert json.dumps(EDITED["text"]) in results[0] and "changed this call's arguments" in results[0]


async def test_ap06_the_journal_holds_both_versions_each_with_its_hash() -> None:
    world = World()
    _ = await _decided(world, edits=EDITED)
    response = world.events("model.response")[0]
    [called] = response.data["toolCalls"]  # type: ignore[misc]
    original = canonical_digest(ASKED)
    assert called["argsDigest"] == original  # type: ignore[index]
    [intent] = world.events("tool.intent")
    assert intent.data["argsDigest"] == original
    [resolved] = world.events("interaction.resolved")
    ref = resolved.data["answerRef"]
    assert isinstance(ref, dict)
    assert ref["hash"] == content_digest(canonical_json(EDITED)) != original


async def test_ap06_approved_as_asked_runs_the_models_arguments() -> None:
    world = World()
    _ = await _decided(world, edits=None)
    assert _posted(world) == [ASKED]
    [resolved] = world.events("interaction.resolved")
    assert "answerRef" not in resolved.data


async def test_ap06_a_rule_still_refuses_an_edit_it_would_refuse() -> None:
    world = World()
    pairs = await _decided(world, edits={**EDITED, "channel": "#blocked"})
    assert _posted(world) == []
    assert [e.data["rule"] for e in world.events("tool.denied")] != []
    assert len([text for role, text in pairs if role == "tool"]) == 1


async def test_ap06_an_edit_nobody_can_read_runs_nothing() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", ASKED)), reply("done"))
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 1:
            # A journaled edit whose bytes do not match its hash: never replaced by the model's arguments.
            ref = inline_ref(canonical_json(EDITED)).model_copy(update={"hash": content_digest(b"other")})
            world.journal._do_resolve(  # pyright: ignore[reportPrivateUsage]
                {
                    "runId": RUN,
                    "interactionId": FIRST,
                    "decision": "approved",
                    "by": "p_user",
                    "channel": "web",
                    "answerRef": ref.model_dump(mode="json"),
                },
                world.db_clock.now(),
            )
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(
        world.kernel(port, approval=AsksForWrites(), interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert done.status == "completed"
    assert _posted(world) == []
    assert [e.data["rule"] for e in world.events("tool.denied")] == [EDIT_UNREADABLE[0]]


async def test_ap06_the_journal_refuses_an_edit_with_a_scope_or_on_a_decline() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", ASKED)), reply("done"))
    wakeup = ManualWakeup(world.clock)
    refused: list[type[Exception]] = []

    def on_wait(waits: int) -> None:
        if waits == 1:
            for decision, scope in (("approved", "conversation"), ("denied", None)):
                try:
                    world.journal.resolve_interaction(RUN, FIRST, decision, edits=EDITED, scope=scope)  # type: ignore[arg-type]
                except (ConversationGrantNotOffered, EditNotOffered) as error:
                    refused.append(type(error))
            world.journal.resolve_interaction(RUN, FIRST, "denied")
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    _ = await world.run(
        world.kernel(port, approval=AsksForWrites(), interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert len(refused) == 2 and refused[1] is EditNotOffered
    assert _posted(world) == []
    assert [e.data["decision"] for e in world.events("interaction.resolved")] == ["denied"]


@pytest.mark.parametrize(
    "accepts",
    [
        frozenset({APPROVAL_REASONS, APPROVAL_RULE_LAYERS}),
        frozenset({APPROVAL_REASONS, APPROVAL_RULE_LAYERS, APPROVAL_EDITS}),
    ],
)
async def test_ap06_only_a_journal_that_takes_edits_is_offered_one(accepts: frozenset[str]) -> None:
    world = World(journal_accepts=accepts)
    world.admit()
    _, port = scripted(reply("", ("post_message", ASKED)), reply("done"))
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 1:
            if APPROVAL_EDITS in accepts:
                world.journal.resolve_interaction(RUN, FIRST, "approved", edits=EDITED)
            else:
                with pytest.raises(EditNotOffered):
                    world.journal.resolve_interaction(RUN, FIRST, "approved", edits=EDITED)
                world.journal.resolve_interaction(RUN, FIRST, "approved")
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    _ = await world.run(
        world.kernel(port, approval=AsksForWrites(), interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    [explained] = world.events("interaction.explained")
    assert ("editable" in explained.data) is (APPROVAL_EDITS in accepts)
    assert _posted(world) == [EDITED if APPROVAL_EDITS in accepts else ASKED]


async def test_wp56_order_3_the_loops_offer_names_the_tools_schema_order() -> None:
    """WP-56: the edit card follows the tool's own order (the schema's), which only the worker knows."""
    from harness_core.kernel.core.tool_schema import ToolSchema

    world = World()
    registered = world.tools.tools["post_message"]
    world.tools.add(
        "post_message",
        registered.fn,
        effect=registered.effect,
        schema=ToolSchema(
            name="post_message",
            description="Post a message",
            input_schema={"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}}},
        ),
    )
    _ = await _decided(world, edits=None)
    [explained] = world.events("interaction.explained")
    assert explained.data["editable"] is True
    assert explained.data["fieldOrder"] == ["channel", "text"]
