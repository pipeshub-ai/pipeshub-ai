"""Facet plan parsing and the lexical coverage test."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

from app.agents.agent_loop.coverage.facets import (
    MAX_FACETS,
    Facet,
    FacetPlan,
    parse_facet_plan,
    plan_facets,
    tokens,
    uncovered_facets,
)
from app.agents.agent_loop.coverage.llm import parse_json_object


class TestParseFacetPlan:
    def test_reads_kinds_terms_and_queries_and_numbers_the_parts(self) -> None:
        plan = parse_facet_plan({
            "anchor": "Northwind rollout",
            "facets": [
                {"kind": "entity", "text": "Northwind", "terms": ["Northwind"], "query": "Northwind rollout plan"},
                {"kind": "condition", "text": "in the EU region", "terms": "EU region", "query": "EU rollout"},
                {"kind": "document type", "text": "the postmortem", "terms": ["postmortem"]},
                {"kind": "ref", "text": "ADR-021", "terms": ["ADR-021"], "query": "ADR-021"},
            ],
        })
        assert plan is not None
        assert [(f.id, f.kind) for f in plan.facets] == [
            ("f1", "entity"), ("f2", "condition"), ("f3", "document_type"), ("f4", "reference"),
        ]
        assert plan.facets[1].terms == ["EU region"]
        # A part without a query is searched by the anchor and its own words.
        assert plan.facets[2].query == "Northwind rollout the postmortem"
        assert plan.document_types == ["the postmortem"]
        assert [ref.text for ref in plan.references] == ["ADR-021"]

    def test_skips_unknown_kinds_empty_and_repeated_parts_and_caps_the_count(self) -> None:
        facets = [{"kind": "mood", "text": "x"}, {"kind": "entity", "text": ""}, {"kind": "entity", "text": "A"},
                  {"kind": "entity", "text": "a"}]
        facets += [{"kind": "sub_question", "text": f"part {i}"} for i in range(10)]
        plan = parse_facet_plan({"facets": facets})
        assert plan is not None
        assert plan.facets[0].text == "A"
        assert len(plan.facets) == MAX_FACETS

    def test_nothing_usable_is_no_plan(self) -> None:
        assert parse_facet_plan(None) is None
        assert parse_facet_plan({"facets": "entity"}) is None
        assert parse_facet_plan({"facets": [{"kind": "mood", "text": "x"}]}) is None

    def test_json_is_found_inside_fences_and_prose(self) -> None:
        assert parse_json_object('Sure:\n```json\n{"facets": []}\n```') == {"facets": []}
        assert parse_json_object("no json here") is None
        assert parse_json_object('["a list"]') is None

    async def test_plan_facets_reports_usage_and_survives_a_failed_call(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(
            content='{"anchor": "a", "facets": [{"kind": "entity", "text": "Acme", "terms": ["Acme"]}]}',
        ))
        seen: list = []
        plan = await plan_facets(llm, "What did Acme ask for?", timeout=1.0, on_usage=seen.append)
        assert plan is not None and plan.facets[0].text == "Acme"
        assert len(seen) == 1

        llm.ainvoke = AsyncMock(side_effect=TimeoutError())
        assert await plan_facets(llm, "What did Acme ask for?", timeout=1.0) is None


class TestLexicalCoverage:
    def test_units_and_word_forms_are_normalised(self) -> None:
        assert {"48h", "rout", "cach"} <= tokens("a 48-hour window for routing cached results")
        assert "48h" in tokens("within 48 hours")
        assert {"queue.depth_sampled", "depth"} <= tokens("gauge queue.depth_sampled")

    def test_a_facet_is_covered_when_most_of_its_terms_occur(self) -> None:
        covered = Facet(id="f1", kind="condition", text="within 48 hours", terms=["48-hour", "rollback"])
        missing = Facet(id="f2", kind="entity", text="Globex", terms=["Globex"])
        untermed = Facet(id="f3", kind="sub_question", text="who approved it")
        plan = FacetPlan(facets=[covered, missing, untermed])
        assert uncovered_facets(plan, ["The rollback window is 48h.", "Acme notes"]) == [missing]
        assert uncovered_facets(None, ["anything"]) == []
