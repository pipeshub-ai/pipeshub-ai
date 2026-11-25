"""R30ENG-FU-1: a job's call that asks first is one step that reads as waiting, whatever the engine called it and in
whichever order its line and the ask reach the worker.

On the durable lane (opencode, scripted model, `r31du`) the job called the SaaS's tool by its own name at its server
(``create_ticket``, through ``call_tool``; R27REST-FU-2), which the gateway runs as ``mcp_e2esaas_create_ticket``. The
ask named the resolved tool and reached the journal before the engine's line for the call; the presenter matched an
ask to a step by the engine's name, and only when the step came first. So the ask named no step, the step named no
ask and had no display name, and the card's steps drew the held call as running (the spec waited for its "Needs
approval" in vain). The presenter now resolves the engine's name as the gateway does (``tool_named``) and, when the
ask comes first, names it on the step's line when that comes.
"""

from __future__ import annotations

from typing import Any

from app.harness.engines.presenter import PipesHubEngineRunPresenter
from harness_core.engines.events import EngineEventMeta, ToolCallSeen

TOOL = "mcp_e2esaas_create_ticket"
CALLED = "create_ticket"
ARGS = {"title": "TW 72ab32", "body": "From FIX-TOOLWORDS."}
ASK = "ix.approve.kd19a17212e9a0769cea4ac797595643c5136b330"


class _Stamp:
    task_id = "run_1"
    turn_id = "trn_0123456789abcdef0123456789abcdef"

    def __init__(self) -> None:
        self._n = 0

    def allocate(self) -> int:
        self._n += 1
        return self._n


def _lines(events: list[Any]) -> list[dict[str, Any]]:
    return [dict(e.data) for e in events if e.type == "x.pipeshub.engine.progress"]


def _presenter(*, resolves: bool = True) -> PipesHubEngineRunPresenter:
    return PipesHubEngineRunPresenter(
        display_name=lambda tool: "E2Esaas · create_ticket" if tool == TOOL else None,
        tool_named=(lambda name: TOOL if name in (CALLED, TOOL) else None) if resolves else None,
    )


def _started(presenter: PipesHubEngineRunPresenter, stamp: _Stamp, tool: str = CALLED, call_id: str = "call_e2e_5") -> dict[str, Any]:
    (line,) = _lines(presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 1), call_id, tool, "started", arguments=ARGS)))
    return line


def test_fu1_1_an_ask_before_the_engines_line_is_named_on_that_line() -> None:
    """The lane's order: the gateway held the call, then the engine reported it."""
    presenter, stamp = _presenter(), _Stamp()
    (ask,) = _lines(presenter.approval(stamp, ASK, TOOL, ARGS))
    assert "callId" not in ask["data"]
    step = _started(presenter, stamp)
    assert (step["kind"], step["data"]["callId"], step["data"]["interactionId"]) == ("tool", "call_e2e_5", ASK)
    assert step["data"]["displayName"] == "E2Esaas · create_ticket"


def test_fu1_2_an_ask_after_a_step_called_by_its_servers_name_names_that_step() -> None:
    presenter, stamp = _presenter(), _Stamp()
    assert "interactionId" not in _started(presenter, stamp)["data"]
    (ask,) = _lines(presenter.approval(stamp, ASK, TOOL, ARGS))
    assert ask["data"]["callId"] == "call_e2e_5"


def test_fu1_3_an_early_ask_names_one_step_only() -> None:
    """Two calls of the tool with other arguments: each asks on its own step, the ask taken once."""
    presenter, stamp = _presenter(), _Stamp()
    other = {"title": "second", "body": "b"}
    _ = presenter.approval(stamp, "ix.approve.k2", TOOL, other)
    _ = presenter.approval(stamp, ASK, TOOL, ARGS)
    first = _started(presenter, stamp, call_id="c1")
    (second,) = _lines(presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 2), "c2", CALLED, "started", arguments=other)))
    third = _started(presenter, stamp, call_id="c3")
    assert (first["data"]["interactionId"], second["data"]["interactionId"]) == (ASK, "ix.approve.k2")
    assert "interactionId" not in third["data"]


def test_fu1_4_control_without_the_gateways_resolution_the_ask_names_no_step() -> None:
    """What the lane met: keyed by the engine's name alone, the ask and the step never meet."""
    presenter, stamp = _presenter(resolves=False), _Stamp()
    _ = _started(presenter, stamp)
    (ask,) = _lines(presenter.approval(stamp, ASK, TOOL, ARGS))
    assert "callId" not in ask["data"]
