"""The coverage decision on a draft: the covered fast path, gaps found anywhere
in the draft, unread named references, the checker's fallback, and rulings."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

from app.agents.agent_loop.coverage.evidence import collect_evidence
from app.agents.agent_loop.coverage.facets import Facet, FacetPlan
from app.agents.agent_loop.coverage.gate import GateDecision, assess_draft

_PLAN = FacetPlan(anchor="Northwind", facets=[
    Facet(id="f1", kind="entity", text="Northwind", terms=["Northwind"], query="Northwind rollout"),
    Facet(id="f2", kind="condition", text="EU region", terms=["EU region"], query="Northwind EU region rollout"),
])
_REF_PLAN = FacetPlan(anchor="Northwind", facets=[
    *_PLAN.facets,
    Facet(id="f3", kind="reference", text="ADR-021", terms=["ADR-021"], query="ADR-021 decision record"),
])


def _tool_state(*records: tuple[str, str, str, list[str]]) -> dict:
    """(vrid, record id, name, passages shown by search)."""
    return {
        "virtual_record_id_to_result": {vrid: {"id": rid, "record_name": name} for vrid, rid, name, _ in records},
        "final_results": [
            {"virtual_record_id": vrid, "content": passage, "block_index": i}
            for vrid, _rid, _name, passages in records for i, passage in enumerate(passages)
        ],
    }


_COVERED_STATE = _tool_state(
    ("vr-1", "rec-1", "northwind-eu-rollout", ["Northwind rolls out in the EU region on May 4."]),
)


def _llm(reply: dict | str | Exception) -> MagicMock:
    llm = MagicMock()
    if isinstance(reply, Exception):
        llm.ainvoke = AsyncMock(side_effect=reply)
    else:
        llm.ainvoke = AsyncMock(return_value=AIMessage(content=reply if isinstance(reply, str) else json.dumps(reply)))
    return llm


async def _evaluate(draft: str, state: dict, llm: MagicMock, *, plan: FacetPlan | None = _PLAN,
                    searched: list[str] | None = None, cited: list[str] | None = None,
                    adjudication: bool = True) -> GateDecision:
    return await assess_draft(
        question="When does Northwind roll out in the EU region?", draft=draft, plan=plan,
        evidence=collect_evidence(state), cited_record_ids=cited or [], searched=searched or [],
        llm=llm, timeout=1.0, max_gaps=2, adjudication=adjudication,
    )


class TestFastPath:
    async def test_a_covered_draft_costs_no_model_call(self) -> None:
        llm = _llm({"facets": []})
        decision = await _evaluate("Northwind rolls out in the EU region on May 4.", _COVERED_STATE, llm)
        assert decision.covered and not decision.checked
        llm.ainvoke.assert_not_called()


class TestGaps:
    async def test_a_near_miss_at_the_end_of_a_long_draft_is_found(self) -> None:
        draft = "Northwind rolls out in the EU region on May 4. " * 20 + (
            "The closest match I found is the APAC plan, which does not meet the EU condition."
        )
        llm = _llm({"facets": [], "admissions": [{"quote": "closest match", "query": "Northwind EU go-live date"}]})
        decision = await _evaluate(draft, _COVERED_STATE, llm)
        assert decision.checked
        assert [(g.kind, g.query) for g in decision.gaps] == [("admission", "Northwind EU go-live date")]
        assert decision.signals["admissions"] >= 1

    async def test_an_unread_named_reference_falls_back_to_its_own_query_when_the_checker_fails(self) -> None:
        decision = await _evaluate(
            "Northwind rolls out in the EU region on May 4.", _COVERED_STATE, _llm(TimeoutError()), plan=_REF_PLAN,
        )
        assert decision.signals["unread_references"] == 1
        assert [(g.kind, g.label, g.query) for g in decision.gaps] == [
            ("reference", "ADR-021", "ADR-021 decision record"),
        ]

    async def test_a_reference_whose_record_was_read_is_not_a_gap(self) -> None:
        state = _tool_state(
            ("vr-1", "rec-1", "northwind-eu-rollout", ["Northwind rolls out in the EU region on May 4."]),
            ("vr-2", "rec-2", "ADR-021 rollout regions", ["Decision: EU first."]),
        )
        llm = _llm({"facets": []})
        decision = await _evaluate("Northwind rolls out in the EU region on May 4.", state, llm, plan=_REF_PLAN)
        assert decision.covered
        llm.ainvoke.assert_not_called()

    async def test_the_checker_overrules_a_lexical_miss(self) -> None:
        state = _tool_state(("vr-1", "rec-1", "northwind-rollout", ["Northwind ships to Europe on May 4."]))
        llm = _llm({"facets": [{"id": "f2", "supported": True}]})
        decision = await _evaluate("Northwind ships to Europe on May 4.", state, llm)
        assert decision.checked and decision.covered

    async def test_an_unsupported_facet_is_searched_by_the_checker_query_once(self) -> None:
        state = _tool_state(("vr-1", "rec-1", "northwind-rollout", ["Northwind ships on May 4."]))
        reply = {"facets": [{"id": "f2", "supported": False, "query": "Northwind Europe launch region"}]}
        decision = await _evaluate("Northwind ships on May 4.", state, _llm(reply))
        assert [(g.kind, g.label, g.query) for g in decision.gaps] == [
            ("facet", "EU region", "Northwind Europe launch region"),
        ]
        again = await _evaluate(
            "Northwind ships on May 4.", state, _llm(reply), searched=["northwind europe launch region"],
        )
        assert again.gaps == []

    async def test_without_a_plan_an_admission_alone_is_checked(self) -> None:
        llm = _llm({"admissions": [{"quote": "not found", "query": "Northwind EU date"}]})
        decision = await _evaluate(
            "I could not find the EU date for Northwind.", _COVERED_STATE, llm, plan=None,
        )
        assert [g.query for g in decision.gaps] == ["Northwind EU date"]


class TestRulings:
    async def test_lookalike_records_send_the_draft_to_adjudication(self) -> None:
        state = _tool_state(
            ("vr-1", "rec-1", "northwind eu rollout plan", ["Northwind EU region go-live is May 4."]),
            ("vr-2", "rec-2", "northwind eu rollout plan draft", ["Northwind EU region go-live is June 1."]),
        )
        reply = {"facets": [], "conflicts": [{
            "facet": "f2", "draft_leads_with": ["R2"],
            "values": [
                {"record": "R1", "value": "May 4", "doc_type": "plan", "same_scope_as_question": True},
                {"record": "R2", "value": "June 1", "scope": "the APAC wave", "same_scope_as_question": False},
            ],
        }]}
        decision = await _evaluate(
            "Northwind goes live in the EU region on June 1.", state, _llm(reply), cited=["rec-2"],
        )
        assert decision.signals["lookalikes"] == 1
        assert len(decision.rulings) == 1
        ruling = decision.rulings[0]
        assert ruling.startswith("For EU region: lead with the value from northwind eu rollout plan (rec-1).")
        assert "applies to another scope (the APAC wave)" in ruling

    async def test_adjudication_off_ignores_lookalikes(self) -> None:
        state = _tool_state(
            ("vr-1", "rec-1", "northwind eu rollout plan", ["Northwind EU region go-live is May 4."]),
            ("vr-2", "rec-2", "northwind eu rollout plan draft", ["Northwind EU region go-live is June 1."]),
        )
        llm = _llm({})
        decision = await _evaluate(
            "Northwind goes live in the EU region on June 1.", state, llm, cited=["rec-2"], adjudication=False,
        )
        assert decision.covered
        llm.ainvoke.assert_not_called()
