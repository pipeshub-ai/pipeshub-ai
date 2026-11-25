"""AP-13 / J-25 (C3.70): an engine's ``ask_user`` as the chat shows it, on the in-memory world with PipesHub's event
catalog validated as Node validates it. The card is the loop's own question card (``ask_user_question``'s payload),
so the chat renders and answers it with no new UI; the run ends ``needs_input`` as the loop's does."""

from __future__ import annotations

from typing import Any, cast

from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from app.harness.engines.presenter import (
    ANSWER,
    PROGRESS,
    QUESTION,
    PipesHubEngineRunPresenter,
)
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.gateway.asking import AskedQuestion
from harness_core.testing.engines.durable import EngineWorld
from harness_core.tools.base import Tag
from tests.unit.harness.engines.test_durable_presenter import (
    _PresentingAssembler,  # pyright: ignore[reportPrivateUsage]
)

RUN = "run-q"
ASK = 'tool ask_user {"question": "Which region should the report cover?", "options": ["EU", "US"]}'


def _world() -> EngineWorld:
    return EngineWorld(extension_writers=cast("Any", EVENT_WRITERS), extension_models=cast("Any", EVENT_MODELS))


async def test_ap13_the_question_is_the_loops_card_and_the_run_ends_needs_input() -> None:
    world = _world()
    world.admit(RUN, f"{ASK}\nsay I need the region first.")
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    disposition = await world.attempt(RUN, kernel=kernel)
    assert disposition.status == "completed"
    events = world.events(RUN)
    asked = [e for e in events if e.type == QUESTION]
    assert len(asked) == 1
    card = cast("dict[str, Any]", asked[0].data)
    expected_id = AskedQuestion("Which region should the report cover?", ("EU", "US")).call_id
    assert card["callId"] == expected_id and card["status"] == "success"
    tool_data = card["toolData"]
    assert tool_data["name"] == "ask_user_question"
    [question] = tool_data["questions"]
    assert question["uuid"] == expected_id
    assert question["question"] == "Which region should the report cover?"
    assert [o["label"] for o in question["options"]] == ["EU", "US"]
    answer = next(e for e in events if e.type == ANSWER)
    assert answer.data["outcome"] == "needs_input"
    assert answer.data["answerMatchType"] == "Clarification Needed"
    assert "I need the region first." in str(answer.data["answer"])
    completed = next(e for e in events if e.type == "run.completed")
    assert completed.data["outcome"] == "needs_input"
    assert not asked[0].critical


async def test_ap13_the_activity_shows_no_meta_step_for_the_question() -> None:
    """Activity clarity: the card is the question's only presentation; no "ask_user" tool step or result line."""
    world = _world()
    world.admit(RUN, f'{ASK}\ntool lookup {{"q": "x"}}\nsay Which region?')
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    assert (await world.attempt(RUN, kernel=kernel)).status == "completed"
    lines = [cast("dict[str, Any]", e.data) for e in world.events(RUN) if e.type == PROGRESS]
    tools = [line["text"] for line in lines if line["kind"] in ("tool", "tool_result")]
    assert tools and all("ask_user" not in str(t) for t in tools)
    assert any("lookup" in str(t) for t in tools)


def test_ux03_an_engine_approval_line_carries_the_preview() -> None:
    tags = {"images__generate": [Tag("cost_usd", "0.04")]}
    presenter = PipesHubEngineRunPresenter(tags_of=lambda name: tags.get(name, []))
    stamp = ProgressStamp("run-1", "trn_1", iter(range(10)).__next__)
    [costed] = presenter.approval(stamp, "ix-1", "images__generate", {"prompt": "a lighthouse"})
    assert costed.data["data"]["effect"] == {"costEstimate": {"usd": 0.04, "basis": "declared"}}  # type: ignore[index]
    [edited] = presenter.approval(stamp, "ix-2", "files__edit", {"path": "a.md", "old_string": "x", "new_string": "y"})
    assert "+y" in edited.data["data"]["effect"]["diff"]  # type: ignore[index]
    [plain] = presenter.approval(stamp, "ix-3", "tickets__create", {"title": "T"})
    assert "effect" not in plain.data["data"]  # type: ignore[operator]
