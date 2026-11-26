"""PA-12: an attempt that resumes at ``dispatch_pending`` runs its tools inside a ``ToolScope``.

The worker dies right after the response commit: the next attempt starts by
dispatching the journaled calls, before any model call shapes a turn. The
PRE/POST_TOOL_USE hooks read the run through the call's scope (citation
tracking grants ``knowledgegraph__fetch_record``, artifact registration
stamps the turn, the error tracker names the tool), so the resumed run must
open the scope the next ``shape`` would have built before it dispatches
(DP-1, DP-2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from tests.contracts import golden_support
from tests.contracts.golden_support import Scenario
from tests.contracts.test_durable_citations import ANSWER, CitedDocs
from tests.contracts.test_durable_goldens import AGENT_SCENARIOS, run_durably

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

CRASH = "worker.model.after_response_commit"


@pytest.fixture(autouse=True)
def _cited_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = golden_support._GoldenToolsetRegistry.get_all_toolsets

    def with_cited_docs(self: object) -> dict[str, dict[str, Any]]:
        return {
            **catalog(self),  # type: ignore[arg-type]
            "citeddocs": {"class": CitedDocs, "isInternal": True, "provider": "platform", "essential": False, "description": "Policies"},
        }

    monkeypatch.setattr(golden_support._GoldenToolsetRegistry, "get_all_toolsets", with_cited_docs)


def _cited(t: ScriptedTransport) -> None:
    t.add_tool_call(ToolCall(id="call-policy", name="citeddocs__search_policy", arguments={"query": "refund"}))
    t.add_text(ANSWER)


def _presented(events: list[JournalEvent]) -> list[tuple[str, dict[str, Any]]]:
    """What the run showed and decided, without attempt bookkeeping."""
    skip = {"run.claimed", "run.released", "run.attempt_started", "loop.state_saved"}
    return [(e.type, dict(e.data)) for e in events if e.type.startswith("x.pipeshub.") or (
        e.type not in skip and e.type.startswith(("tool.", "message.", "run.completed"))
    )]


def _tools_offered(transport: ScriptedTransport) -> list[list[str]]:
    return [[t.name for t in call["tools"] or []] for call in transport.calls]


async def test_dp1_a_resumed_dispatch_runs_the_tool_hooks_citations_survive() -> None:
    scenario = Scenario("agent_cited", "agent", "quick", "What is the refund policy?", _cited)
    whole_transports: list[ScriptedTransport] = []
    whole, _ = await run_durably(scenario, transports=whole_transports)
    resumed_transports: list[ScriptedTransport] = []
    resumed, _ = await run_durably(scenario, crash_at=CRASH, transports=resumed_transports)

    assert [e.type for e in resumed].count("run.claimed") == 2
    assert [e.type for e in resumed].count("model.response") == 2, "the search's response is not re-sampled"
    # Citation tracking ran on the resumed dispatch: the answer's request offers fetch_record.
    assert "knowledgegraph__fetch_record" in _tools_offered(whole_transports[0])[-1]
    assert _tools_offered(resumed_transports[0])[-1:] == _tools_offered(whole_transports[0])[-1:]
    assert _presented(resumed) == _presented(whole)
    [finalized] = [e.data for e in resumed if e.type == "x.pipeshub.answer.finalized"]
    assert finalized["citations"]


@pytest.mark.parametrize(
    "name", ["agent_artifact_marker_answer", "agent_react_tool_error_then_success"]
)
async def test_dp1_artifacts_and_tool_results_survive_a_resumed_dispatch(name: str) -> None:
    """The promoted artifact (and its card) and a failing call's outcome are
    what the uncrashed run shows."""
    scenario = next(s for s in AGENT_SCENARIOS if s.name == name)
    whole, _ = await run_durably(scenario)
    resumed, _ = await run_durably(scenario, crash_at=CRASH)
    assert [e.type for e in resumed].count("run.claimed") == 2
    assert _presented(resumed) == _presented(whole)
