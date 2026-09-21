"""The whole pipeline on fakes: dataset -> corpus -> prepare -> ask -> search
-> grade -> score -> report, plus resume and an invalid (cheating) run."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from frames_testkit import FakeArticleSource, FakeLLM, write_frames_tsv

from benchmarks.frames.systems.baselines.answering import ANSWER_PROMPT_VERSION
from benchmarks.frames.config import RunConfig
from benchmarks.frames.corpus.manifest import record_name
from benchmarks.frames.credentials import Credentials
from benchmarks.frames.dataset.loader import FRAMES_REVISION
import benchmarks.frames.dataset.plugin  # noqa: F401  (registers the dataset)
from benchmarks.frames.dataset.split import write_split
from benchmarks.frames.datasets import dataset_plugin
from benchmarks.frames.llm.registry import ModelResolver
from benchmarks.frames.models import (
    AskItem,
    Citation,
    CorpusManifest,
    IndexReport,
    IngestedRecord,
    IngestManifest,
    Prediction,
    RetrievalEvent,
    RetrievedRecordRef,
    StreamTrace,
    ToolCallTrace,
)
from benchmarks.frames.pipeline import run_pipeline
from benchmarks.frames.services import RunContext, Services
from benchmarks.frames.stages import (
    AskStage,
    CorpusStage,
    DatasetStage,
    GradeStage,
    PrepareStage,
    ReportStage,
    ScoreStage,
    SearchStage,
)
from benchmarks.frames.store import RunStore
from benchmarks.frames.systems import ADAPTER_REGISTRY, AdapterDeps, AdapterSpec
from benchmarks.frames.systems.base import AdapterCapabilities, PreparedCorpus

QUESTIONS = [
    {"prompt": "What is the capital of the country ruled by Rufus T. Firefly?", "answer": "Fredville",
     "links": ["https://en.wikipedia.org/wiki/Freedonia", "https://en.wikipedia.org/wiki/Rufus_T._Firefly"]},
    {"prompt": "In what year was the film set in Freedonia released?", "answer": "1933",
     "links": ["https://en.wikipedia.org/wiki/Duck_Soup", "https://en.wikipedia.org/wiki/Freedonia"],
     "types": "Temporal reasoning | Numerical reasoning"},
    {"prompt": "Which rival nation borders Freedonia?", "answer": "Sylvania",
     "links": ["https://en.wikipedia.org/wiki/Sylvania", "https://en.wikipedia.org/wiki/Freedonia"], "types": "Tabular reasoning"},
]
PAGES = {
    "Freedonia": ('<p>Freedonia is ruled by <a href="/wiki/Rufus_T._Firefly">Rufus T. Firefly</a>. The capital is Fredville.</p>'
                  '<table class="wikitable"><tr><th>Neighbour</th><th>Relation</th></tr><tr><td>Sylvania</td><td>Rival</td></tr></table>'
                  '<p>It is the setting of <a href="/wiki/Duck_Soup">Duck Soup</a>.</p>'),
    "Rufus T. Firefly": "<p>Rufus T. Firefly is the leader of Freedonia.</p>",
    "Duck Soup": "<p>Duck Soup is a comedy film released in 1933.</p>",
    "Sylvania": "<p>Sylvania is a rival nation.</p>",
}
EVIDENCE = {q["prompt"]: q["answer"] for q in QUESTIONS}
REGISTRY = [
    {"provider": "openAI", "modelKey": "answerer", "configuration": {"model": "gpt-5.6-luna"}},
    {"provider": "anthropic", "modelKey": "judge", "configuration": {"model": "claude-sonnet-5"}},
    {"provider": "gemini", "modelKey": "judge2", "configuration": {"model": "gemini-3.8-flash"}},
]


def _field(text: str, label: str) -> str:
    match = re.search(rf"{label}:\s*(.*)", text)
    return match.group(1).strip() if match else ""


def _responder(request) -> str:  # noqa: ANN001
    content = request.messages[-1].content
    if request.prompt_version == ANSWER_PROMPT_VERSION:
        question = _field(content, "Question")
        answer = EVIDENCE[question]
        return f"The answer is {answer}." if "Wikipedia articles" in content and answer in content else "I don't know."
    if request.prompt_version == "frames-autorater-v1":
        predicted, gold = _field(content, "- Predicted Answer"), _field(content, "- Ground Truth Answer")
        return f"Explanation: compared.\nDecision: {'TRUE' if gold.lower() in predicted.lower() else 'FALSE'}"
    if request.prompt_version == "simpleqa-grader-v1":
        gold, predicted = _field(content, "Gold target"), _field(content, "Predicted answer")
        return "A" if gold in predicted else ("C" if "don't know" in predicted else "B")
    return "Support: FULL"


class FakeTraceSystem:
    """A retrieval-trace system (PipesHub-shaped) that finds every gold article."""

    # Fabricates perfect retrieval from the gold refs, so it needs them —
    # the same grant the oracle upper bound declares.
    capabilities = AdapterCapabilities(
        ingests_corpus=True, retrieval_trace=True, citations=True, ranked_search=True,
        needs_gold_refs=True,
    )

    def __init__(self, system_id: str, corpus, cheat: bool) -> None:  # noqa: ANN001
        self.system_id = system_id
        self._corpus = corpus
        self._cheat = cheat

    def ingestor(self) -> FakeTraceSystem:
        return self

    def retriever(self) -> FakeTraceSystem:
        return self

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        records = [IngestedRecord(record_id=f"rec-{i}", record_name=record_name(d.filename), canonical_url=d.canonical_url)
                   for i, d in enumerate(manifest.documents)]
        ingest = IngestManifest(system=self.system_id, kb_id="kb", corpus_version=manifest.corpus_version, base_url="u", records=records)
        return PreparedCorpus(system=self.system_id, corpus_version=manifest.corpus_version, ingest=ingest)

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        n = len(manifest.documents)
        return IndexReport(total=n, status_counts={"COMPLETED": n}, gold_total=n, gold_indexed=n)

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        by_url = {r.canonical_url: r for r in prepared.ingest.records}
        gold = [by_url[u] for u in self._corpus.resolve_refs(item.gold_refs)]
        event = RetrievalEvent(seq=1, source="prefetch", status="ok", records=[
            RetrievedRecordRef(virtual_record_id=f"vr-{r.record_id}", record_id=r.record_id, block_indices=[0]) for r in gold
        ])
        first = gold[0]
        citation = Citation(display_index=1, record_id=first.record_id, content=self._corpus.text(first.canonical_url)[:80])
        tools = [ToolCallTrace(tool_call_id="t1", name="knowledgegraph__search")]
        if self._cheat:
            tools.append(ToolCallTrace(tool_call_id="t2", name="dynamic__web_search"))
        return Prediction(
            system=self.system_id, question_id=item.question_id, repeat=repeat,
            answer=f"The answer is {EVIDENCE[item.prompt]} [1].", citations=[citation],
            trace=StreamTrace(retrieval_events=[event], tool_calls=tools, tool_waves=1, finished=True),
            policy_violations=["dynamic__web_search"] if self._cheat else [],
        )

    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int):  # noqa: ANN201
        from benchmarks.frames.models import RankedList

        return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=self._corpus.resolve_refs(item.gold_refs))


def _registry(cheat: bool) -> dict[str, AdapterSpec]:
    def _factory(system, deps: AdapterDeps):  # noqa: ANN001, ANN202
        return FakeTraceSystem(system.id, deps.corpus, cheat)

    return {**ADAPTER_REGISTRY, "fake_trace": AdapterSpec(_factory, FakeTraceSystem.capabilities)}


CONFIG = {
    "run_name": "e2e",
    "dataset": {"split": "all"},
    "corpus": {"tier": "G", "scope": "all"},
    "answerer": {"model": "gpt-5.6-luna", "provider": "openAI"},
    "systems": [{"kind": "fake_trace"}, {"kind": "closed_book"}, {"kind": "bm25", "options": {"n_docs": 2}}, {"kind": "oracle"}],
    "grading": {"primary": {"model": "claude-sonnet-5"}, "secondary": {"model": "gemini-3.8-flash"}},
    "stats": {"bootstrap_samples": 200},
}
STAGES = [DatasetStage, CorpusStage, PrepareStage, AskStage, SearchStage, GradeStage, ScoreStage, ReportStage]


def _context(tmp_path: Path, *, cheat: bool = False, store: RunStore | None = None) -> RunContext:
    config = RunConfig.model_validate(CONFIG)
    tsv = write_frames_tsv(tmp_path / "test.tsv", QUESTIONS)
    split = tmp_path / "split.json"
    write_split(split, {"0": "dev", "1": "dev", "2": "heldout"}, seed=1, dataset_revision=FRAMES_REVISION)
    services = Services(
        config, Credentials(), cache_dir=tmp_path / "cache", llm=FakeLLM(_responder),
        resolver=ModelResolver(lambda: REGISTRY), dataset_path=tsv, split_path=split, expected_question_count=3,
        article_source_factory=lambda _host: FakeArticleSource(PAGES), adapter_registry=_registry(cheat),
    )
    return RunContext(
        config=config, store=store or RunStore.create(tmp_path / "reports", config),
        services=services, dataset=dataset_plugin(config.dataset.name),
    )


def test_full_pipeline_produces_a_valid_board(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    run_pipeline([stage() for stage in STAGES], ctx)

    summary = ctx.summary
    assert summary is not None and summary.valid
    board = {s.system: s for s in summary.systems}
    assert set(board) == {"fake_trace", "closed_book", "bm25", "oracle"}
    assert board["oracle"].accuracy.value == 1.0
    assert board["closed_book"].accuracy.value == 0.0
    assert board["closed_book"].hedge_rate == 1.0
    assert board["fake_trace"].all_gold_in_context.value == 1.0
    assert board["fake_trace"].citation_integrity == 1.0
    assert board["fake_trace"].recall_at["5"] == 1.0
    assert board["fake_trace"].judge_agreement_kappa is not None
    assert board["oracle"].by_reasoning_type["Tabular reasoning"] == 1.0
    for name in ("report.md", "summary.json", "failures.csv", "run_meta.json", "scores.jsonl"):
        assert ctx.store.path(name).exists(), name
    assert "FRAMES benchmark" in ctx.store.path("report.md").read_text()
    assert json.loads(ctx.store.path("run_meta.json").read_text())["dataset_revision"] == FRAMES_REVISION
    assert len(ctx.store.read("predictions.jsonl", Prediction)) == 12


def test_resume_does_not_repeat_paid_work(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    run_pipeline([stage() for stage in STAGES], ctx)
    llm_calls = len(ctx.services.llm.requests)

    resumed = _context(tmp_path, store=RunStore.resume(tmp_path / "reports", ctx.store.run_id, ctx.config))
    reports = run_pipeline([stage() for stage in STAGES], resumed)

    by_stage = {r.stage: r for r in reports}
    assert by_stage["ask"].processed == 0 and by_stage["ask"].skipped == 12
    assert by_stage["grade"].processed == 0
    assert len(resumed.services.llm.requests) == 0
    assert llm_calls > 0


def test_a_disallowed_tool_call_invalidates_the_run(tmp_path: Path) -> None:
    ctx = _context(tmp_path, cheat=True)
    run_pipeline([stage() for stage in STAGES], ctx)
    assert ctx.summary is not None and not ctx.summary.valid
    assert all("dynamic__web_search" in v for v in ctx.summary.violations)
    assert "INVALID" in ctx.store.path("report.md").read_text()


def test_missing_prefetch_frame_is_a_contract_error(tmp_path: Path) -> None:
    from benchmarks.frames.errors import BackendContractError
    from benchmarks.frames.stages import _check_trace_contract

    with pytest.raises(BackendContractError):
        _check_trace_contract(Prediction(system="s", question_id="1", repeat=0, trace=StreamTrace()))
