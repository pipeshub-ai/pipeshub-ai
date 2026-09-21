"""Token cost, the `run_usage` stream frame, and the naive/advanced RAG systems."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from frames_testkit import FakeArticleSource, FakeLLM, make_model

from benchmarks.harness.config import FRAMES_SNAPSHOT, ModelPrice
from benchmarks.datasets.frames.builder import CorpusBuilder
from benchmarks.datasets.frames.plugin import FramesDataset
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.datasets.frames.urls import normalize_wiki_url
from benchmarks.harness.llm.client import LLMRequest
from benchmarks.harness.models import AskItem, CallUsage, IngestManifest, QuestionScore
from benchmarks.harness.pricing import call_cost, calls_cost
from benchmarks.harness.report.summary import _cost
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.pipeshub.adapter import build_stream_body
from benchmarks.harness.systems.pipeshub.stream import parse_run_usage
from benchmarks.harness.systems.rag.answerer import RAG_ANSWER_PROMPT_VERSION, RagAnswerer, RagOptions, RecordRef
from benchmarks.harness.systems.rag.retrieval import Chunk, interleave, rrf_merge
from benchmarks.harness.systems.rag.transforms import DECOMPOSITION_PROMPT_VERSION, EXPANSION_PROMPT_VERSION

LUNA = ModelPrice(
    input_per_mtok=0.20, cached_input_per_mtok=0.02, output_per_mtok=1.20,
    long_context_threshold=272_000, long_context_input_multiplier=2.0, long_context_output_multiplier=1.5,
)


class TestPricing:
    def test_cached_input_is_billed_at_the_cached_rate(self) -> None:
        cost = call_cost(LUNA, CallUsage(input_tokens=100_000, cached_tokens=40_000, output_tokens=10_000))
        assert cost == pytest.approx((60_000 * 0.20 + 40_000 * 0.02 + 10_000 * 1.20) / 1e6)

    def test_long_context_surcharge_applies_to_the_whole_request(self) -> None:
        cost = call_cost(LUNA, CallUsage(input_tokens=300_000, output_tokens=10_000))
        assert cost == pytest.approx((0.3 * 0.20 * 2.0) + (0.01 * 1.20 * 1.5))

    def test_no_price_means_no_cost(self) -> None:
        assert calls_cost(None, [CallUsage(input_tokens=1)]) is None


def _score(cost: float | None, tokens: int | None, *, correct: bool = True, error: str | None = None) -> QuestionScore:
    return QuestionScore(
        system="s", question_id="1", repeat=0, split="dev", labels=[], gold_count=2,
        correct=correct, cost_usd=cost, input_tokens=tokens, error_kind=error,
    )


class TestCostSummary:
    def test_partial_usage_hides_cost_instead_of_understating_it(self) -> None:
        summary = _cost([_score(0.01, 100), _score(None, None)])
        assert summary["usage_coverage"] == 0.5
        assert summary["cost_usd"] is None and summary["cost_per_correct_usd"] is None

    def test_cost_per_correct_counts_every_answer_spent(self) -> None:
        summary = _cost([_score(0.01, 100), _score(0.03, 300, correct=False)])
        assert summary["cost_per_correct_usd"] == pytest.approx(0.04)
        assert summary["input_tokens_per_question"] == 200


class TestRunUsageFrame:
    def test_per_call_breakdown_wins_over_totals(self) -> None:
        calls = parse_run_usage({"inputTokens": 999, "calls": [[100, 10, 40], [200, 20, 0]]})
        assert [(c.input_tokens, c.output_tokens, c.cached_tokens) for c in calls] == [(100, 10, 40), (200, 20, 0)]

    def test_totals_are_the_fallback(self) -> None:
        [call] = parse_run_usage({"inputTokens": 5, "outputTokens": 2, "cacheReadTokens": 1})
        assert (call.input_tokens, call.output_tokens, call.cached_tokens) == (5, 2, 1)

    def test_reasoning_effort_is_sent_to_pipeshub(self) -> None:
        model = make_model("gpt-5.6-luna", "azureOpenAI", reasoning=True).model_copy(update={"reasoning_effort": "high"})
        body = build_stream_body(AskItem(question_id="1", prompt="q"), "kb", model, FRAMES_SNAPSHOT)
        assert body["reasoningEffort"] == "high"


def _chunk(vrid: str, block: int, text: str = "") -> Chunk:
    return Chunk(vrid, block, text or f"{vrid}-{block}", 1.0)


class TestMerging:
    def test_rrf_rewards_chunks_found_by_several_queries(self) -> None:
        a, b, c = _chunk("a", 0), _chunk("b", 0), _chunk("c", 0)
        assert [x.key for x in rrf_merge([[a, b], [c, b]])][0] == b.key

    def test_interleave_keeps_each_subquestion_represented(self) -> None:
        merged = interleave([[_chunk("a", 0), _chunk("a", 1)], [_chunk("b", 0)]])
        assert [c.key for c in merged] == [("a", 0), ("b", 0), ("a", 1)]


PAGES = {
    "Harriet Lane": "<p>Harriet Lane was the niece of James Buchanan. Her mother was Jane Buchanan.</p>",
    "James A. Garfield": "<p>Garfield's mother was Eliza Ballou.</p>",
}
REFS = [f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}" for t in PAGES]
PREPARED = PreparedCorpus(
    system="rag", corpus_version="v",
    ingest=IngestManifest(system="rag", kb_id="kb", corpus_version="v", base_url="u", records=[]),
)
ITEM = AskItem(question_id="7", prompt="Whose mother was Eliza Ballou?")


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusView:
    manifest = CorpusBuilder(
        lambda _h: FakeArticleSource(PAGES), tmp_path, snapshot=FRAMES_SNAPSHOT, workers=1, harness_version="t",
    ).build(REFS, tier="G", distractor_count=0, seed=1, max_failed_gold_ratio=0.0)
    return CorpusView(tmp_path, manifest, FramesDataset().normalize_ref)


@pytest.fixture
def url(corpus: CorpusView) -> dict[str, str]:
    return {d.title: d.canonical_url for d in corpus.manifest.documents}


class FakeIndex:
    def __init__(self) -> None:
        self.searches: list[tuple[str, str, int]] = []

    def search(self, query: str, mode: str, limit: int) -> list[Chunk]:
        self.searches.append((query, mode, limit))
        if "Garfield" in query:
            return [_chunk("v-garfield", 0, "Garfield's mother was Eliza Ballou.")]
        return [_chunk("v-lane", 0, "Her mother was Jane Buchanan."), _chunk("v-unknown", 0)]


def _responder(request: LLMRequest) -> str:
    if request.prompt_version == EXPANSION_PROMPT_VERSION:
        return "1. James A. Garfield mother\n2. Eliza Ballou son"
    if request.prompt_version == DECOMPOSITION_PROMPT_VERSION:
        return "- Who was Eliza Ballou's son?\n- Garfield family"
    return "Eliza Ballou was the mother of James A. Garfield [2]."


class TestRag:
    @pytest.fixture(autouse=True)
    def _setup(self, corpus: CorpusView, url: dict[str, str]) -> None:
        self.corpus, self.url = corpus, url
        self.records = {
            "v-lane": RecordRef("rec-lane", url["Harriet Lane"], "Harriet Lane"),
            "v-garfield": RecordRef("rec-garfield", url["James A. Garfield"], "James A. Garfield"),
        }
        self.index, self.llm = FakeIndex(), FakeLLM(_responder)

    def _answer(self, options: RagOptions):  # noqa: ANN202
        rag = RagAnswerer(
            "rag", self.llm, make_model("gpt-5.6-luna", "azureOpenAI", reasoning=True), options,
            ingestor=None,  # type: ignore[arg-type]
            index_factory=lambda _ingest: (self.index, self.records),  # type: ignore[arg-type,return-value]
            corpus=self.corpus, price=LUNA,
        )
        return rag.answer(ITEM, PREPARED, 0)

    def test_naive_rag_is_one_dense_search_and_one_llm_call(self) -> None:
        prediction = self._answer(RagOptions(top_k=10))
        assert self.index.searches == [(ITEM.prompt, "dense", 10)]
        assert [r.prompt_version for r in self.llm.requests] == [RAG_ANSWER_PROMPT_VERSION]
        assert [c.purpose for c in prediction.llm_calls] == ["answer"]
        assert prediction.cost_usd == pytest.approx(call_cost(LUNA, CallUsage(input_tokens=10, output_tokens=5)))
        assert "Today's date is 2024-10-15" in self.llm.requests[0].messages[0].content

    def test_unknown_records_never_reach_the_prompt(self) -> None:
        prediction = self._answer(RagOptions())
        assert prediction.context_urls == [self.url["Harriet Lane"]]
        assert "v-unknown" not in self.llm.requests[0].messages[1].content

    def test_expansion_counts_its_llm_call_and_searches_every_query(self) -> None:
        prediction = self._answer(RagOptions(retrieval="hybrid", transform="expansion"))
        assert [q for q, _m, _l in self.index.searches] == [ITEM.prompt, "James A. Garfield mother", "Eliza Ballou son"]
        assert [c.purpose for c in prediction.llm_calls] == ["query_expansion", "answer"]
        assert prediction.prompt_tokens == 20
        assert set(prediction.context_urls) == {self.url["Harriet Lane"], self.url["James A. Garfield"]}

    def test_citations_map_to_the_numbered_source(self) -> None:
        prediction = self._answer(RagOptions(transform="decomposition"))
        [citation] = prediction.citations
        assert citation.display_index == 2 and citation.record_id == "rec-garfield"
        assert "Eliza Ballou" in citation.content

    def test_small_to_big_swaps_chunks_for_the_whole_article(self) -> None:
        self._answer(RagOptions(small_to_big=1))
        assert "niece of James Buchanan" in self.llm.requests[0].messages[1].content

    def test_rerank_depth_defaults_to_twice_top_k(self) -> None:
        assert RagOptions(top_k=20, rerank=True).depth == 40
        assert RagOptions(top_k=20).depth == 20


def test_rerank_keeps_the_best_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.harness.systems.rag.rerank import CrossEncoderReranker

    class _Model:
        def predict(self, pairs: Sequence[tuple[str, str]], **_kw: object) -> list[float]:
            return [float(len(text)) for _q, text in pairs]

    reranker = CrossEncoderReranker("fake")
    monkeypatch.setattr(reranker, "_ensure", lambda: _Model())
    chunks = [_chunk("a", 0, "x"), _chunk("b", 0, "xxx"), _chunk("c", 0, "xx")]
    assert [c.virtual_record_id for c in reranker.rerank("q", chunks, 2)] == ["b", "c"]


class TestVectorCoverage:
    def _services(self, present: dict[str, int]):  # noqa: ANN202
        from types import SimpleNamespace

        from benchmarks.harness.config import RunConfig
        from benchmarks.harness.services import Services

        services = Services.__new__(Services)
        services.config = RunConfig.model_validate({
            "run_name": "t", "answerer": {"model": "m"}, "systems": [{"kind": "closed_book"}],
            "grading": {"primary": {"model": "j"}}, "pipeshub": {"min_indexed_ratio": 0.5},
        })
        hits = [SimpleNamespace(value=v, count=c) for v, c in present.items()]
        services.__dict__["qdrant"] = SimpleNamespace(facet=lambda *_a, **_k: SimpleNamespace(hits=hits))
        return services

    def test_missing_gold_vectors_fail_the_run(self) -> None:
        from benchmarks.harness.errors import IngestError

        services = self._services({"v1": 3})
        with pytest.raises(IngestError, match="1 gold"):
            services.verify_vectors("c", "k", {"v1": "u1", "v2": "u2"}, {"u2"})

    def test_a_few_missing_distractors_are_tolerated(self) -> None:
        self._services({"v1": 3}).verify_vectors("c", "k", {"v1": "u1", "v2": "u2"}, {"u1"})


def test_stream_keeps_transcript_previews_and_run_stats() -> None:
    import json

    from benchmarks.harness.systems.pipeshub.stream import StreamCollector

    frames = [
        {"type": "TOOL_CALL_START", "toolCallId": "t1", "toolCallName": "knowledgegraph__search"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "t1", "status": "blocked", "content": "same call failed twice"},
        {"type": "CUSTOM", "name": "run_usage", "value": {"calls": [[10, 2, 0]], "turns": 15, "maxTurns": 15, "completionGateNudges": 1}},
        {"type": "STATE_SNAPSHOT", "snapshot": {"final": True, "answer": "a", "parts": [{"type": "text", "content": "thinking out loud"}]}},
        {"type": "RUN_FINISHED", "result": {"answer": "a"}},
    ]
    collector = StreamCollector()
    for frame in frames:
        collector.feed({"event": frame["type"], "data": json.dumps(frame)})
    trace, final, _ = collector.result()
    assert trace.tool_calls[0].result_preview == "same call failed twice"
    assert trace.run_stats is not None and trace.run_stats.hit_turn_cap and trace.run_stats.completion_gate_nudges == 1
    assert trace.transcript == [{"type": "text", "content": "thinking out loud"}]
    assert final.llm_calls is not None and final.llm_calls[0].input_tokens == 10


def test_services_rag_lock_is_reentrant() -> None:
    """`rag_index` holds the lock and calls `virtual_record_ids`, which takes it
    again via `kb_records`; a plain Lock deadlocks the first RAG answer."""
    from benchmarks.harness.config import RunConfig
    from benchmarks.harness.credentials import Credentials
    from benchmarks.harness.services import Services

    services = Services(
        RunConfig.model_validate({
            "run_name": "t", "answerer": {"model": "m"}, "systems": [{"kind": "closed_book"}],
            "grading": {"primary": {"model": "j"}},
        }),
        Credentials.from_env({}),
    )
    with services._rag_lock, services._rag_lock:
        pass


class TestAuxiliaryCallBudget:
    """Query rewrites must not inherit the answerer's reasoning budget: at high
    effort with a 16k output reservation, a three-line rewrite can run for
    minutes and stall the run."""

    def _kwargs(self, **overrides):  # noqa: ANN202
        from benchmarks.harness.llm.client import ChatMessage, LLMRequest, build_completion_kwargs

        model = make_model("gpt-5.6-luna", "openAI", reasoning=True).model_copy(update={"reasoning_effort": "high"})
        request = LLMRequest(
            model=model, messages=(ChatMessage(role="user", content="q"),),
            max_tokens=1024, prompt_version="v", **overrides,
        )
        return build_completion_kwargs(request, "sk-test", 600)

    def test_answer_calls_keep_high_effort_and_reasoning_headroom(self) -> None:
        kwargs = self._kwargs()
        assert kwargs["reasoning_effort"] == "high" and kwargs["max_tokens"] >= 16_384

    def test_transform_calls_are_low_effort_and_bounded(self) -> None:
        kwargs = self._kwargs(effort="low", reserve_reasoning_tokens=False)
        assert kwargs["reasoning_effort"] == "low" and kwargs["max_tokens"] == 1024

    def test_transforms_use_those_settings(self) -> None:
        from benchmarks.harness.systems.rag.transforms import expand

        llm = FakeLLM(lambda _r: "a\nb")
        expand(llm, make_model("m", "openAI", reasoning=True), "q", 2, FRAMES_SNAPSHOT)
        [request] = llm.requests
        assert request.effort == "low" and request.reserve_reasoning_tokens is False
        assert request.timeout_s is not None and request.timeout_s <= 120
