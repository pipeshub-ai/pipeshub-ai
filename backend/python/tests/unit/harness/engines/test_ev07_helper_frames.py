"""EV-07 / CM-01: an engine helper's progress lines as the chat nests them: the helper's start and end name it
(``subSessionId``) and the spawning call, and every step it took names it too; the run's own steps name none."""

from __future__ import annotations

from itertools import count
from typing import Any

from app.harness.engines.presenter import PROGRESS, PipesHubEngineRunPresenter
from app.harness.engines.progress import (
    SHOWN_HELPER_TASK_CHARS,
    EngineProgress,
    progress_for,
)
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineEventMeta,
    FileChanged,
    SubAgentFinished,
    SubAgentStarted,
    ToolCallSeen,
)

META = EngineEventMeta(turn_id="trn_1", seq=0)


def test_ev07_a_helpers_start_and_end_are_one_line_each_naming_it() -> None:
    assert progress_for(SubAgentStarted(META, "task-1", "toolu_1", "Explore")) == EngineProgress(
        "helper", "Explore", {"subSessionId": "task-1", "callId": "toolu_1", "status": "started"}
    )
    asked = progress_for(SubAgentStarted(META, "task-1", "toolu_1", "helper", "Find " + "x" * 400))
    assert asked is not None and asked.data["task"] == ("Find " + "x" * 400)[:SHOWN_HELPER_TASK_CHARS]
    assert progress_for(SubAgentFinished(META, "task-1", "toolu_1", "failed")) == EngineProgress(
        "helper", "", {"subSessionId": "task-1", "callId": "toolu_1", "status": "failed"}
    )


def test_ev07_a_helpers_steps_name_it_and_the_runs_own_do_not() -> None:
    helper = "task-1"
    for event in (
        CommandStarted(META, "c1", "ls", sub_session_id=helper),
        CommandOutput(META, "c1", "a.md", sub_session_id=helper),
        CommandFinished(META, "c1", 0, "completed", sub_session_id=helper),
        FileChanged(META, "w1", "a.md", "added", sub_session_id=helper),
        ToolCallSeen(META, "k1", "search", "started", sub_session_id=helper),
        ToolCallSeen(META, "k1", "search", "completed", sub_session_id=helper),
    ):
        shown = progress_for(event)
        assert shown is not None and shown.data["subSessionId"] == helper, event
    own = progress_for(CommandStarted(META, "c2", "pwd"))
    assert own is not None and "subSessionId" not in own.data


def test_ev07_the_journaled_line_keeps_the_helper_with_the_tools_display_name() -> None:
    presenter = PipesHubEngineRunPresenter(display_name={"search": "Searched the knowledge base"}.get)
    n = count()
    stamp = ProgressStamp("run-1", "trn_1", lambda: next(n))
    lines: list[Any] = [
        *presenter.event(stamp, SubAgentStarted(META, "task-1", "toolu_1", "helper")),
        *presenter.event(stamp, ToolCallSeen(META, "k1", "search", "started", sub_session_id="task-1")),
        *presenter.event(stamp, SubAgentFinished(META, "task-1", "toolu_1", "completed")),
    ]
    assert [e.type for e in lines] == [PROGRESS] * 3
    assert [(e.data["kind"], e.data["data"]) for e in lines] == [
        ("helper", {"subSessionId": "task-1", "callId": "toolu_1", "status": "started"}),
        ("tool", {"callId": "k1", "displayName": "Searched the knowledge base", "subSessionId": "task-1"}),
        ("helper", {"subSessionId": "task-1", "callId": "toolu_1", "status": "completed"}),
    ]
