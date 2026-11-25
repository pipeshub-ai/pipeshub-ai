"""PA-8: citation sources survive a crash and a re-claim on another worker.

A retrieval-like tool fills the run's citation state the way
``retrieval.search_internal_knowledge`` does (records, blocks, reference
numbers). The durable run journals what the call added with its outcome
(CT-1); a worker that dies after that outcome and a fresh worker that
re-claims the run give the same answer and citations as an uninterrupted run
(CT-2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from tests.contracts import golden_support
from tests.contracts.golden_support import FRONTEND, Scenario, _flat, _record
from tests.contracts.test_durable_goldens import run_durably

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

ANSWER = "Refunds are accepted within 30 days [source](ref1) to the original method [source](ref2)."


class CitedDocs:
    """Fills citation state like the retrieval tool: records, blocks, refs."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state if state is not None else {}

    @tool(
        path="/tools/citeddocs/search_policy",
        short_description="Search the policies",
        description="Search the company policy documents.",
        parameters=[ToolParameter(name="query", type=ParameterType.STRING, description="Topic", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def search_policy(self, query: str) -> str:
        from app.utils.chat_helpers import CitationRefMapper, build_block_web_url

        state = self._state
        mapper = state.get("citation_ref_mapper") or CitationRefMapper()
        state["citation_ref_mapper"] = mapper
        state["virtual_record_id_to_result"] = {
            **(state.get("virtual_record_id_to_result") or {}),
            "vr-refund": _record("vr-refund", "rec-refund", "Refund policy.md"),
        }
        blocks = [
            _flat("vr-refund", 0, "Customers may request a refund within 30 days of purchase."),
            _flat("vr-refund", 1, "Refunds are paid back to the original payment method."),
        ]
        lines = []
        for block in blocks:
            block["block_web_url"] = build_block_web_url(FRONTEND, "rec-refund", block["block_index"])
            ref = mapper.get_or_create_ref(block["block_web_url"])
            lines.append(f"[{block['block_index']}|{ref}] {block['content']}")
        state["final_results"] = [*(state.get("final_results") or []), *blocks]
        return "\n".join(lines)


def _script(replies: int) -> Any:  # noqa: ANN401
    def script(t: ScriptedTransport) -> None:
        t.add_tool_call(ToolCall(id="call-policy", name="citeddocs__search_policy", arguments={"query": "refund"}))
        for _ in range(replies):
            t.add_text(ANSWER)

    return script


@pytest.fixture(autouse=True)
def _cited_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = golden_support._GoldenToolsetRegistry.get_all_toolsets

    def with_cited_docs(self: object) -> dict[str, dict[str, Any]]:
        return {
            **catalog(self),  # type: ignore[arg-type]
            "citeddocs": {"class": CitedDocs, "isInternal": True, "provider": "platform", "essential": False, "description": "Policies"},
        }

    monkeypatch.setattr(golden_support._GoldenToolsetRegistry, "get_all_toolsets", with_cited_docs)


def _finalized(events: list[JournalEvent]) -> dict[str, Any]:
    [finalized] = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    return dict(finalized.data)


async def test_ct1_a_call_journals_the_citation_sources_it_added() -> None:
    scenario = Scenario("agent_cited", "agent", "quick", "What is the refund policy?", _script(1))
    events, _ = await run_durably(scenario)
    recorded = [e for e in events if e.type == "x.pipeshub.citations.recorded"]
    assert len(recorded) == 1
    data = recorded[0].data
    assert data["callId"] == "call-policy"
    assert [r["ref"] for r in data["refs"]] == ["ref1", "ref2"]
    assert list(data["records"]) == ["vr-refund"]
    assert len(data["finalResults"]) == 2
    outcome = next(e for e in events if e.type == "tool.outcome")
    assert recorded[0].seq > outcome.seq, "journaled with the outcome it belongs to"
    assert _finalized(events)["citations"], "the uninterrupted answer cites the policy"


async def test_ct2_citations_survive_a_crash_and_a_reclaim_on_another_worker() -> None:
    whole, _ = await run_durably(
        Scenario("agent_cited", "agent", "quick", "What is the refund policy?", _script(1))
    )
    # The worker dies while sampling the answer, after the search's outcome
    # (and its citation sources) are durable; a fresh assembler re-claims.
    resumed, _ = await run_durably(
        Scenario("agent_cited", "agent", "quick", "What is the refund policy?", _script(2)),
        crash_at="worker.model.before_response_commit",
        crash_on=2,
    )
    assert [e.type for e in resumed].count("run.claimed") == 2
    assert [e.type for e in resumed].count("tool.outcome") == 1, "the search is not re-run"
    expected, actual = _finalized(whole), _finalized(resumed)
    assert actual["citations"] == expected["citations"]
    assert actual["citations"]
    assert actual["answer"] == expected["answer"]
