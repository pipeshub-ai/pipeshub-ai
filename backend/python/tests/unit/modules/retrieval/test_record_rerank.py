"""The record reranker: switches, strategy selection, the listwise LLM
strategy's prompt, parsing and model choice, and the order the pipeline
makes of a strategy's answer."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.retrieval.record_rerank import (
    HITS,
    POINTS,
    RecordCandidate,
    RecordRerank,
    RecordReranker,
    RecordRerankError,
    RecordRerankSettings,
    RecordTexts,
    RerankerDeps,
    RerankRequest,
    create_record_reranker,
    record_rerank_enabled,
    record_rerank_for,
    record_rerank_settings,
    register_record_reranker,
    reorder_items,
    rerank_order,
    search_record_rerank,
)
from app.modules.retrieval.record_rerank import factory as factory_mod
from app.modules.retrieval.record_rerank.llm_listwise import (
    SYSTEM_PROMPT,
    LlmListwiseReranker,
    parse_ranking,
    user_prompt,
)
from app.modules.retrieval.record_rerank.pipeline import (
    PoolRecord,
    candidate_text,
    display_title,
    final_record_order,
    record_pool,
)
from app.services.vector_db.models import SearchResult
from app.utils.llm import LLMNotConfiguredError

_LISTWISE = "app.modules.retrieval.record_rerank.llm_listwise"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)
    for name in [name for name in os.environ if name.startswith("RECORD_RERANK")]:
        monkeypatch.delenv(name)


def _point(vrid: str | None, block: int, score: float, *, summary: bool = False, text: str = "") -> SearchResult:
    metadata: dict[str, Any] = {"virtualRecordId": vrid, "blockId": f"b{block}"}
    if summary:
        metadata.update(blockType="record_summary", isRecordSummary=True)
    return SearchResult(
        id=f"{vrid}#{block}", score=score,
        payload={"page_content": text or f"{vrid} block {block}", "metadata": metadata},
    )


def _hit(vrid: str, block: int, score: float) -> dict[str, Any]:
    return {"score": score, "content": f"{vrid} {block}", "metadata": {"virtualRecordId": vrid, "point_id": block}}


class _Fixed(RecordReranker):
    def __init__(
        self, ranking: list[str] | None = None, *, error: Exception | None = None, delay: float = 0.0,
    ) -> None:
        self.name = "fixed"
        self.ranking = ranking or []
        self.error = error
        self.delay = delay
        self.requests: list[RerankRequest] = []

    async def rank(self, request: RerankRequest) -> list[str]:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return list(self.ranking)


def _texts(titles: dict[str, str] | None = None, summaries: dict[str, str] | None = None) -> RecordTexts:
    async def read_titles(vrids: list[str]) -> dict[str, str]:
        return {vrid: (titles or {}).get(vrid, f"{vrid}_name.pdf") for vrid in vrids}

    async def read_summaries(vrids: list[str]) -> dict[str, str]:
        return {vrid: text for vrid, text in (summaries or {}).items() if vrid in vrids}

    return RecordTexts(titles=read_titles, summaries=read_summaries)


def _rerank(**settings: object) -> RecordRerank:
    return RecordRerank(question="who signs off the depot roof work?", settings=RecordRerankSettings(**settings))


def _ten_records() -> list[SearchResult]:
    return [_point(f"r{i}", 0, 1.0 - i / 100) for i in range(10)]


class TestSwitch:
    def test_off_by_default_and_high_accuracy_mode_does_not_turn_it_on(self, monkeypatch) -> None:
        assert record_rerank_enabled() is False
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        assert record_rerank_enabled() is False
        assert record_rerank_for("question") is None
        assert search_record_rerank("question") == {}

    @pytest.mark.parametrize("value", ["true", "TRUE", " true "])
    def test_on_when_set(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("RECORD_RERANK", value)
        rerank = record_rerank_for("question")
        assert rerank == RecordRerank(question="question", settings=RecordRerankSettings())
        assert search_record_rerank("question") == {"record_rerank": rerank}

    @pytest.mark.parametrize("value", ["false", "0", "off", "yes", ""])
    def test_any_other_value_is_off_even_in_high_accuracy_mode(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        monkeypatch.setenv("RECORD_RERANK", value)
        assert record_rerank_enabled() is False

    def test_a_blank_question_is_not_reranked(self, monkeypatch) -> None:
        monkeypatch.setenv("RECORD_RERANK", "true")
        assert record_rerank_for("  ") is None

    def test_defaults(self) -> None:
        assert record_rerank_settings() == RecordRerankSettings(
            strategy="llm_listwise", pool=100, top=8, keep_top=1, timeout_s=6.0, lookup_timeout_s=10.0,
            text_chars=300,
        )

    def test_knobs_are_read_and_clamped(self, monkeypatch) -> None:
        for name, value in {
            "RECORD_RERANK_STRATEGY": " Some_Strategy ", "RECORD_RERANK_POOL": "5000", "RECORD_RERANK_TOP": "0",
            "RECORD_RERANK_TIMEOUT_S": "2.5", "RECORD_RERANK_KEEP_TOP": "-1", "RECORD_RERANK_TEXT_CHARS": "600",
            "RECORD_RERANK_LOOKUP_TIMEOUT_S": "3",
        }.items():
            monkeypatch.setenv(name, value)
        assert record_rerank_settings() == RecordRerankSettings(
            strategy="some_strategy", pool=500, top=1, keep_top=0, timeout_s=2.5, lookup_timeout_s=3.0,
            text_chars=600,
        )

    def test_malformed_numbers_keep_the_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("RECORD_RERANK_POOL", "lots")
        monkeypatch.setenv("RECORD_RERANK_TIMEOUT_S", "nan")
        settings = record_rerank_settings()
        assert (settings.pool, settings.timeout_s) == (100, 6.0)


class TestFactory:
    def test_selects_the_listwise_strategy(self) -> None:
        reranker = create_record_reranker("llm_listwise", RerankerDeps())
        assert isinstance(reranker, LlmListwiseReranker)
        assert reranker.name == "llm_listwise"

    def test_an_unknown_strategy_is_none_and_warned_once(self, caplog) -> None:
        factory_mod._warned_unknown.discard("nope")
        with caplog.at_level(logging.WARNING):
            assert create_record_reranker("nope", RerankerDeps()) is None
            assert create_record_reranker("nope", RerankerDeps()) is None
        assert sum("unknown strategy 'nope'" in r.getMessage() for r in caplog.records) == 1

    def test_a_new_strategy_registers_without_touching_the_pipeline(self) -> None:
        @register_record_reranker("unit_test_strategy")
        class _New(_Fixed):
            def __init__(self, deps: RerankerDeps) -> None:
                super().__init__(["r1"])
                self.deps = deps

        try:
            deps = RerankerDeps(config_service=MagicMock())
            reranker = create_record_reranker("unit_test_strategy", deps)
            assert isinstance(reranker, _New)
            assert reranker.deps is deps
        finally:
            factory_mod._REGISTRY.pop("unit_test_strategy")


class TestFinalOrder:
    current = [f"r{i}" for i in range(12)]

    def _order(self, ranked: list[str], *, top: int = 8, keep_top: int = 1) -> list:
        return final_record_order(self.current, ranked, candidates=self.current, top=top, keep_top=keep_top)

    def test_the_ranked_records_lead_and_the_rest_keep_their_order(self) -> None:
        order = self._order(["r9", "r0", "r11"])
        assert order == ["r9", "r0", "r11", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r10"]

    def test_only_top_ranked_records_are_placed(self) -> None:
        ranked = ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "r0", "r4", "r3"]
        order = self._order(ranked, top=8)
        assert order[:8] == ranked[:8]
        assert order[8:] == ["r1", "r2", "r3", "r4"]

    def test_keep_top_puts_the_current_first_record_in_the_last_slot(self) -> None:
        ranked = ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "r4"]
        order = self._order(ranked)
        assert order[:8] == ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "r0"]
        assert order[8] == "r4"
        assert sorted(order) == sorted(self.current)

    def test_without_keep_top_the_current_first_record_may_leave_the_top(self) -> None:
        ranked = ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "r4"]
        order = self._order(ranked, keep_top=0)
        assert order[:8] == ranked
        assert order.index("r0") == 8

    def test_keep_top_leaves_a_kept_first_record_where_the_strategy_put_it(self) -> None:
        assert self._order(["r3", "r0"])[:3] == ["r3", "r0", "r1"]

    def test_unknown_duplicate_and_missing_ids(self) -> None:
        order = self._order(["ghost", "r5", "r5", "r2", "elsewhere", "r5"])
        assert order[:3] == ["r5", "r2", "r0"]
        assert sorted(order) == sorted(self.current)

    def test_a_repeated_id_does_not_use_up_a_top_slot(self) -> None:
        assert self._order(["r5", "r5", "r2"], top=2, keep_top=0)[:2] == ["r5", "r2"]

    def test_a_record_outside_the_pool_is_not_promoted(self) -> None:
        order = final_record_order(self.current, ["r11", "r1"], candidates=self.current[:5], top=8, keep_top=1)
        assert order[:2] == ["r1", "r0"]

    def test_keep_top_two_puts_the_current_first_two_in_the_last_slots_in_their_order(self) -> None:
        ranked = ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "r4"]
        order = self._order(ranked, keep_top=2)
        assert order[:8] == ["r11", "r10", "r9", "r8", "r7", "r6", "r0", "r1"]
        assert order[8:10] == ["r5", "r4"]
        assert sorted(order) == sorted(self.current)

    def test_keep_top_only_moves_the_protected_records_the_strategy_left_out(self) -> None:
        ranked = ["r11", "r1", "r9", "r8", "r7", "r6", "r5", "r4"]
        assert self._order(ranked, keep_top=2)[:8] == ["r11", "r1", "r9", "r8", "r7", "r6", "r5", "r0"]

    def test_keep_top_beyond_top_protects_only_top(self) -> None:
        assert self._order(["r9", "r8", "r7"], top=2, keep_top=5)[:2] == ["r0", "r1"]


class TestReorder:
    def test_records_move_together_and_scores_follow_the_new_order(self) -> None:
        points = [_point("a", 0, 0.9), _point("b", 0, 0.8), _point("a", 1, 0.7), _point("c", 0, 0.6)]
        out = reorder_items(points, ["c", "a", "b"], POINTS)
        assert [p.id for p in out] == ["c#0", "a#0", "a#1", "b#0"]
        assert [p.score for p in out] == [0.9, 0.8, 0.7, 0.6]
        assert {p.id for p in out} == {p.id for p in points}
        assert points[0].score == 0.9

    def test_ties_become_strict_so_a_later_sort_keeps_the_order(self) -> None:
        hits = [_hit("a", 0, 0.5), _hit("b", 0, 0.5), _hit("c", 0, 0.5)]
        out = reorder_items(hits, ["c", "b", "a"], HITS)
        assert [h["metadata"]["virtualRecordId"] for h in sorted(out, key=lambda h: -h["score"])] == ["c", "b", "a"]

    def test_the_same_order_returns_the_items_untouched(self) -> None:
        points = _ten_records()
        out = reorder_items(points, [f"r{i}" for i in range(10)], POINTS)
        assert out == points
        assert all(a is b for a, b in zip(out, points))


class TestPoolAndText:
    def test_pool_takes_records_in_score_order_with_their_passage_and_summary(self) -> None:
        points = [
            _point("b", 0, 0.95, summary=True, text="B summary"),
            _point("a", 0, 0.9, text="a passage"),
            _point("b", 1, 0.85, text="b passage"),
            _point(None, 0, 0.8),
            _point("c", 0, 0.7),
        ]
        pool = record_pool(points, POINTS, 2)
        assert pool == [PoolRecord("b", passage="b passage", summary="B summary"), PoolRecord("a", passage="a passage")]

    def test_the_text_is_the_summary_else_the_passage_on_one_line_and_capped(self) -> None:
        record = PoolRecord("a", passage="passage  text here", summary="")
        assert candidate_text(record, "summary \n  text here", 10) == "summary te"
        assert candidate_text(record, "", 10) == "passage te"
        assert candidate_text(PoolRecord("a"), "", 10) == ""

    def test_display_title(self) -> None:
        assert display_title("Depot_roof--quote  v2.pdf") == "Depot roof quote v2.pdf"
        assert display_title(None) == ""

    async def test_candidates_carry_titles_and_summary_or_passage(self) -> None:
        points = [_point("r0", 0, 0.9, summary=True, text="S0 summary"), _point("r0", 1, 0.8), _point("r1", 0, 0.7)]
        reranker = _Fixed(["r1"])
        await rerank_order(_rerank(), points, POINTS, reranker=reranker, texts=_texts(), org_id="o")
        (request,) = reranker.requests
        assert [c.text for c in request.candidates] == ["S0 summary", "r1 block 0"]
        assert [c.title for c in request.candidates] == ["r0 name.pdf", "r1 name.pdf"]

    async def test_summaries_are_read_only_for_records_without_one(self) -> None:
        points = [_point("r0", 0, 0.9, summary=True, text="S0"), _point("r1", 0, 0.8), _point("r2", 0, 0.7)]
        asked: list[list[str]] = []

        async def summaries(vrids: list[str]) -> dict[str, str]:
            asked.append(vrids)
            return {"r1": "read summary"}

        reranker = _Fixed(["r1"])
        await rerank_order(
            _rerank(), points, POINTS, reranker=reranker, texts=replace(_texts(), summaries=summaries), org_id="o",
        )
        assert asked == [["r1", "r2"]]
        assert [c.text for c in reranker.requests[0].candidates] == ["S0", "read summary", "r2 block 0"]

    async def test_a_record_without_a_title_is_not_offered(self) -> None:
        async def titles(vrids: list[str]) -> dict[str, str]:
            return {vrid: vrid for vrid in vrids if vrid != "r1"}

        reranker = _Fixed(["r1", "r2"])
        order = await rerank_order(
            _rerank(), _ten_records(), POINTS, reranker=reranker, texts=replace(_texts(), titles=titles), org_id="o",
        )
        assert "r1" not in [c.record_id for c in reranker.requests[0].candidates]
        assert order is not None and order.records[:2] == ["r2", "r0"]
        assert order.records.index("r1") == 2
        assert order.placed == {"r2"}

    async def test_fewer_than_two_readable_records_are_not_reranked(self) -> None:
        async def titles(vrids: list[str]) -> dict[str, str]:
            return {"r0": "r0"}

        reranker = _Fixed(["r0"])
        order = await rerank_order(
            _rerank(), _ten_records(), POINTS, reranker=reranker, texts=replace(_texts(), titles=titles), org_id="o",
        )
        assert order is None
        assert reranker.requests == []


class TestRerankOrder:
    async def test_applies_the_strategy_and_keeps_the_first_record(self) -> None:
        reranker = _Fixed(["r9", "r8", "r7"])
        order = await rerank_order(_rerank(top=3), _ten_records(), POINTS, reranker=reranker, texts=_texts(), org_id="o")
        assert order.records[:3] == ["r9", "r8", "r0"]
        assert order.records[3:] == ["r7", "r1", "r2", "r3", "r4", "r5", "r6"]
        assert order.placed == {"r9", "r8", "r7"}

    async def test_the_pool_bounds_what_the_strategy_sees(self) -> None:
        reranker = _Fixed(["r3"])
        await rerank_order(_rerank(pool=4), _ten_records(), POINTS, reranker=reranker, texts=_texts(), org_id="o")
        assert [c.record_id for c in reranker.requests[0].candidates] == ["r0", "r1", "r2", "r3"]
        assert reranker.requests[0].top == 8

    async def test_the_same_order_leaves_the_items_untouched(self) -> None:
        reranker = _Fixed(["r0", "r1"])
        points = _ten_records()
        order = await rerank_order(_rerank(), points, POINTS, reranker=reranker, texts=_texts(), org_id="o")
        assert order.records == [f"r{i}" for i in range(10)]
        assert order.placed == {"r0", "r1"}
        assert all(a is b for a, b in zip(reorder_items(points, order.records, POINTS), points))

    async def test_one_record_or_no_strategy_is_not_reranked(self) -> None:
        reranker = _Fixed(["r0"])
        one = [_point("r0", 0, 0.9), _point("r0", 1, 0.8)]
        assert await rerank_order(_rerank(), one, POINTS, reranker=reranker, texts=_texts(), org_id="o") is None
        assert reranker.requests == []
        assert await rerank_order(_rerank(), _ten_records(), POINTS, reranker=None, texts=_texts(), org_id="o") is None

    @pytest.mark.parametrize("make", [
        lambda: _Fixed(["r9"], delay=1.0),
        lambda: _Fixed(error=RecordRerankError("bad reply")),
        lambda: _Fixed(error=RuntimeError("boom")),
    ])
    async def test_timeout_and_errors_keep_the_search_order_and_log_once(self, make, caplog) -> None:
        reranker = make()
        with caplog.at_level(logging.WARNING, logger="app.modules.retrieval.record_rerank.pipeline"):
            order = await rerank_order(
                _rerank(timeout_s=0.05), _ten_records(), POINTS, reranker=reranker, texts=_texts(), org_id="o",
            )
        assert order is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "keeping the search order" in warnings[0].getMessage()

    async def test_a_slow_title_lookup_does_not_count_against_the_strategy_timeout(self) -> None:
        async def slow(vrids: list[str]) -> dict[str, str]:
            await asyncio.sleep(0.2)
            return {vrid: f"{vrid} title" for vrid in vrids}

        reranker = _Fixed(["r9"])
        order = await rerank_order(
            _rerank(timeout_s=0.05), _ten_records(), POINTS,
            reranker=reranker, texts=replace(_texts(), titles=slow), org_id="o",
        )
        assert order is not None and order.records[0] == "r9"
        assert reranker.requests[0].candidates[0].title == "r0 title"

    async def test_a_lookup_past_its_own_bound_keeps_the_search_order(self, caplog) -> None:
        async def stuck(_vrids: list[str]) -> dict[str, str]:
            await asyncio.sleep(1.0)
            return {}

        reranker = _Fixed(["r9"])
        with caplog.at_level(logging.WARNING, logger="app.modules.retrieval.record_rerank.pipeline"):
            order = await rerank_order(
                _rerank(timeout_s=5.0, lookup_timeout_s=0.05), _ten_records(), POINTS,
                reranker=reranker, texts=replace(_texts(), titles=stuck), org_id="o",
            )
        assert order is None
        assert reranker.requests == []
        assert "reading titles and summaries took longer" in caplog.records[-1].getMessage()

    async def test_a_failed_lookup_keeps_the_search_order(self) -> None:
        async def broken(_vrids: list[str]) -> dict[str, str]:
            raise ConnectionError("graph down")

        reranker = _Fixed(["r9"])
        order = await rerank_order(
            _rerank(), _ten_records(), POINTS, reranker=reranker, texts=replace(_texts(), titles=broken), org_id="o",
        )
        assert order is None
        assert reranker.requests == []


class _FakeLLM:
    def __init__(self, reply: Any) -> None:  # noqa: ANN401 - str or content blocks
        self.reply = reply
        self.messages: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> Any:  # noqa: ANN401
        self.messages.append(messages)
        return MagicMock(content=self.reply)


def _request(n: int = 5, top: int = 8) -> RerankRequest:
    return RerankRequest(
        question="who signs off the depot roof work?",
        candidates=[RecordCandidate(f"v{i}", f"Doc {i}", f"text {i}" if i else "") for i in range(n)],
        top=top, org_id="org-1",
    )


# Pinned: a change to the prompt changes every ranking.
_EXPECTED_SYSTEM_PROMPT = (
    "You rank search results for an enterprise knowledge base. Given a user question and numbered candidate "
    "documents (title and a snippet), select the 8 documents most likely to contain the information needed to "
    "answer the question, most relevant first. Prefer the document the question describes (its type, project, "
    "meeting, version, or scope) over documents on a related topic. Return JSON only: "
    '{"ranking": [8 candidate numbers]}.'
)


def _configured(key: str, model: str, *, default: bool) -> dict[str, Any]:
    return {
        "provider": "openAICompatible", "modelKey": key, "isDefault": default,
        "configuration": {"model": model, "endpoint": "https://models.example.test/v1", "apiKey": "placeholder"},
    }


class TestLlmListwise:
    def test_prompt_text(self) -> None:
        assert SYSTEM_PROMPT.format(top=8) == _EXPECTED_SYSTEM_PROMPT
        assert user_prompt(_request(3)) == (
            "Question: who signs off the depot roof work?\n\nCandidates:\n"
            "[1] Doc 0\n[2] Doc 1 :: text 1\n[3] Doc 2 :: text 2"
        )

    @pytest.mark.parametrize(("reply", "expected"), [
        ('{"ranking": [3, 1, 2]}', [2, 0, 1]),
        ('```json\n{"ranking": [2, 5]}\n```', [1, 4]),
        ('{"ranking": ["[4]", "2"]}', [3, 1]),
        ("Best: 4, then 1", [3, 0]),
        ('{"ranking": [9, 0, 2, 2, 3, 2]}', [1, 2]),
        ('{"order": [1, 2]}', []),
        ("{not json 3, 1}", [2, 0]),
        ("", []),
    ])
    def test_parse_ranking(self, reply: str, expected: list[int]) -> None:
        assert parse_ranking(reply, count=5, top=8) == expected

    def test_parse_ranking_stops_at_top(self) -> None:
        assert parse_ranking(json.dumps({"ranking": list(range(1, 11))}), count=10, top=8) == list(range(8))

    async def test_ranks_with_the_default_chat_model_and_maps_numbers_to_records(self) -> None:
        llm = _FakeLLM([{"type": "text", "text": '{"ranking": [3, 1]}'}])
        config_service = MagicMock()
        with patch(f"{_LISTWISE}.get_llm", AsyncMock(return_value=(llm, {}))) as default, \
                patch(f"{_LISTWISE}.get_llm_by_name", AsyncMock()) as named:
            reranker = LlmListwiseReranker(RerankerDeps(config_service=config_service))
            assert await reranker.rank(_request()) == ["v2", "v0"]
            assert await reranker.rank(_request()) == ["v2", "v0"]
        default.assert_awaited_once_with(config_service, reasoning_effort="low")
        named.assert_not_awaited()
        system, user = llm.messages[0]
        assert system.content == _EXPECTED_SYSTEM_PROMPT
        assert user.content == user_prompt(_request())

    async def test_the_model_is_built_per_org(self) -> None:
        with patch(f"{_LISTWISE}.get_llm", AsyncMock(return_value=(_FakeLLM('{"ranking": [1]}'), {}))) as default:
            reranker = LlmListwiseReranker(RerankerDeps(config_service=MagicMock()))
            await reranker.rank(_request())
            await reranker.rank(replace(_request(), org_id="org-2"))
        assert default.await_count == 2

    async def test_a_named_configured_model_is_used(self, monkeypatch) -> None:
        monkeypatch.setenv("RECORD_RERANK_MODEL", "ranker-small")
        built: list[tuple[str, str | None]] = []

        def build(provider: str, config: dict, model_name: str | None = None, effort: str | None = None) -> Any:  # noqa: ANN401
            built.append((config["modelKey"], model_name, effort))
            return _FakeLLM('{"ranking": [2]}')

        ai_models = {"llm": [
            _configured("main", "chat-large", default=True),
            _configured("side", "chat-medium, ranker-small", default=False),
        ]}
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=ai_models)), \
                patch("app.utils.llm.get_generator_model", side_effect=build):
            assert await LlmListwiseReranker(RerankerDeps(config_service=MagicMock())).rank(_request()) == ["v1"]
        assert built == [("side", "ranker-small", "low")]

    async def test_a_model_key_picks_that_entry(self, monkeypatch) -> None:
        monkeypatch.setenv("RECORD_RERANK_MODEL", "side")
        built: list[tuple[str, str | None]] = []

        def build(provider: str, config: dict, model_name: str | None = None, effort: str | None = None) -> Any:  # noqa: ANN401
            built.append((config["modelKey"], model_name))
            return _FakeLLM('{"ranking": [2]}')

        ai_models = {"llm": [_configured("main", "chat-large", default=True), _configured("side", "m1", default=False)]}
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=ai_models)), \
                patch("app.utils.llm.get_generator_model", side_effect=build):
            await LlmListwiseReranker(RerankerDeps(config_service=MagicMock())).rank(_request())
        assert built == [("side", None)]

    async def test_a_model_that_is_not_configured_leaves_the_default_in_use(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("RECORD_RERANK_MODEL", "no-such-model")
        built: list[str] = []

        def build(provider: str, config: dict, model_name: str | None = None, effort: str | None = None) -> Any:  # noqa: ANN401
            built.append(config["modelKey"])
            return _FakeLLM('{"ranking": [1]}')

        ai_models = {"llm": [_configured("side", "m1", default=False), _configured("main", "m2", default=True)]}
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=ai_models)), \
                patch("app.utils.llm.get_generator_model", side_effect=build), \
                caplog.at_level(logging.WARNING, logger=_LISTWISE):
            await LlmListwiseReranker(RerankerDeps(config_service=MagicMock())).rank(_request())
        assert built == ["main"]
        assert "no configured LLM is named 'no-such-model'" in caplog.records[-1].getMessage()

    async def test_no_configured_model_keeps_the_search_order(self) -> None:
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value={})):
            reranker = LlmListwiseReranker(RerankerDeps(config_service=MagicMock()))
            with pytest.raises(LLMNotConfiguredError):
                await reranker.rank(_request())
            order = await rerank_order(_rerank(), _ten_records(), POINTS, reranker=reranker, texts=_texts(), org_id="o")
        assert order is None

    async def test_no_configuration_service_is_an_error(self) -> None:
        with pytest.raises(RecordRerankError):
            await LlmListwiseReranker(RerankerDeps()).rank(_request())

    @pytest.mark.parametrize("reply", ['{"ranking": []}', "no idea", '{"ranking": [42, 0]}'])
    async def test_malformed_output_raises(self, reply: str) -> None:
        with patch(f"{_LISTWISE}.get_llm", AsyncMock(return_value=(_FakeLLM(reply), {}))), \
                pytest.raises(RecordRerankError):
            await LlmListwiseReranker(RerankerDeps(config_service=MagicMock())).rank(_request())

    async def test_malformed_output_keeps_the_search_order(self) -> None:
        with patch(f"{_LISTWISE}.get_llm", AsyncMock(return_value=(_FakeLLM("I cannot rank these."), {}))):
            reranker = LlmListwiseReranker(RerankerDeps(config_service=MagicMock()))
            order = await rerank_order(_rerank(), _ten_records(), POINTS, reranker=reranker, texts=_texts(), org_id="o")
        assert order is None
