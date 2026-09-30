"""The whole pipeline on fakes: dataset -> corpus -> prepare -> ask -> search
-> grade -> score -> report, plus resume and an invalid (cheating) run."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from frames_testkit import FakeArticleSource, FakeLLM, write_frames_tsv

from benchmarks.harness.systems.baselines.answering import (
    ANSWER_PROMPT_VERSION,
    GROUNDED_ANSWER_PROMPT_VERSION,
)
from benchmarks.harness.config import RunConfig
from benchmarks.harness.corpus.manifest import record_name
from benchmarks.harness.credentials import Credentials
from benchmarks.datasets.frames.loader import FRAMES_REVISION
import benchmarks.datasets.frames.plugin  # noqa: F401  (registers the dataset)
from benchmarks.harness.dataset.split import write_split
from benchmarks.harness.datasets import dataset_plugin
from benchmarks.harness.evidence import captured
from benchmarks.harness.llm.registry import ModelResolver
from benchmarks.harness.models import (
    AskItem,
    Citation,
    CorpusManifest,
    EvidencePassage,
    IndexReport,
    IngestedRecord,
    IngestManifest,
    Prediction,
    RetrievalEvent,
    RetrievedRecordRef,
    StreamTrace,
    ToolCallTrace,
)
from benchmarks.harness.pipeline import run_pipeline
from benchmarks.harness.services import RunContext, Services
from benchmarks.harness.stages import (
    AskStage,
    CorpusStage,
    DatasetStage,
    EvidenceStage,
    GradeStage,
    PrepareStage,
    ReportStage,
    ScoreStage,
    SearchStage,
    SupportStage,
)
from benchmarks.harness.store import RunStore
from benchmarks.harness.systems import ADAPTER_REGISTRY, AdapterDeps, AdapterSpec
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus

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


def _support(content: str) -> str:
    answer = content.split("Answer given by the system:\n", 1)[1].split("\n\nEvidence the system was shown:", 1)[0]
    evidence = content.split("Evidence the system was shown:\n", 1)[1].split("\n\nJudge only against", 1)[0]
    value = answer.removeprefix("The answer is ").split(" ")[0].rstrip(".")
    return f"Reason: checked {value}.\nEvidence support: {'SUPPORTED' if value in evidence else 'UNSUPPORTED'}"


def _responder(request, *, from_memory: bool = False) -> str:  # noqa: ANN001
    content = request.messages[-1].content
    if request.prompt_version in (ANSWER_PROMPT_VERSION, GROUNDED_ANSWER_PROMPT_VERSION):
        question = _field(content, "Question")
        answer = EVIDENCE[question]
        if from_memory:
            return f"The answer is {answer}."
        return f"The answer is {answer}." if "Wikipedia articles" in content and answer in content else "I don't know."
    if request.prompt_version == "evidence-support-v1":
        return _support(content)
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
            evidence=captured([EvidencePassage(text=self._corpus.text(r.canonical_url)) for r in gold], "fake"),
            trace=StreamTrace(retrieval_events=[event], tool_calls=tools, tool_waves=1, finished=True),
            policy_violations=["dynamic__web_search"] if self._cheat else [],
        )

    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int):  # noqa: ANN201
        from benchmarks.harness.models import RankedList

        return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=self._corpus.resolve_refs(item.gold_refs))


class WrongContextSystem:
    """Always shows its model the Sylvania article, and answers correctly anyway."""

    capabilities = AdapterCapabilities()

    def __init__(self, system_id: str, corpus) -> None:  # noqa: ANN001
        self.system_id = system_id
        self._corpus = corpus

    def ingestor(self) -> None:
        return None

    def retriever(self) -> None:
        return None

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        text = self._corpus.text("https://en.wikipedia.org/wiki/Sylvania")
        return Prediction(
            system=self.system_id, question_id=item.question_id, repeat=repeat,
            answer=f"The answer is {EVIDENCE[item.prompt]}.",
            evidence=captured([EvidencePassage(header="### Sylvania\n", text=text)], "fake"),
        )


def _registry(cheat: bool) -> dict[str, AdapterSpec]:
    def _factory(system, deps: AdapterDeps):  # noqa: ANN001, ANN202
        return FakeTraceSystem(system.id, deps.corpus, cheat)

    return {
        **ADAPTER_REGISTRY,
        "fake_trace": AdapterSpec(_factory, FakeTraceSystem.capabilities),
        "wrong_context": AdapterSpec(
            lambda system, deps: WrongContextSystem(system.id, deps.corpus), WrongContextSystem.capabilities,
        ),
    }


CONFIG = {
    "run_name": "e2e",
    "dataset": {"split": "all"},
    "corpus": {"tier": "G", "scope": "all"},
    "answerer": {"model": "gpt-5.6-luna", "provider": "openAI"},
    "systems": [{"kind": "fake_trace"}, {"kind": "closed_book"}, {"kind": "bm25", "options": {"n_docs": 2}}, {"kind": "oracle"}],
    "grading": {"primary": {"model": "claude-sonnet-5"}, "secondary": {"model": "gemini-3.8-flash"}},
    "stats": {"bootstrap_samples": 200},
}
STAGES = [
    DatasetStage, CorpusStage, PrepareStage, AskStage, SearchStage, GradeStage, EvidenceStage, SupportStage,
    ScoreStage, ReportStage,
]


def _context(
    tmp_path: Path, *, cheat: bool = False, store: RunStore | None = None, from_memory: bool = False,
    config: dict | None = None,
) -> RunContext:
    config = RunConfig.model_validate(config or CONFIG)
    tsv = write_frames_tsv(tmp_path / "test.tsv", QUESTIONS)
    split = tmp_path / "split.json"
    write_split(split, {"0": "dev", "1": "dev", "2": "heldout"}, seed=1, dataset_revision=FRAMES_REVISION)
    services = Services(
        config, Credentials(), cache_dir=tmp_path / "cache",
        llm=FakeLLM(lambda request: _responder(request, from_memory=from_memory)),
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
    assert board["oracle"].grounded_accuracy.value == 1.0
    assert board["fake_trace"].grounded_accuracy.value == 1.0
    # Closed book is not verified unless asked for: it is shown nothing.
    assert board["closed_book"].grounded_accuracy is None and board["closed_book"].support_coverage is None
    assert all(s.memory_suspect == 0 for s in board.values())
    assert "evidence" not in ctx.store.path("predictions.jsonl").read_text()
    report = ctx.store.path("report.md").read_text()
    assert "Grounded acc %" in report

    # split: all — the dataset's dev/held-out assignment breaks the headline down.
    oracle = board["oracle"]
    assert set(oracle.accuracy_by_split) == {"dev", "heldout"}
    assert oracle.accuracy_by_split["dev"].n == 2 and oracle.accuracy_by_split["heldout"].n == 1
    assert oracle.grounded_accuracy_by_split["heldout"].value == 1.0
    assert oracle.memory_suspect_rate_by_split["dev"].value == 0.0
    assert "### By split (95% CI)" in report and "| heldout | Grounded acc % |" in report
    summary_json = json.loads(ctx.store.path("summary.json").read_text())
    assert next(x for x in summary_json["systems"] if x["system"] == "oracle")["accuracy_by_split"]["dev"]["n"] == 2


def test_answers_from_memory_are_caught_at_judging_time(tmp_path: Path) -> None:
    """An answering model that ignores its sources: every answer is correct,
    but only the ones whose context held the fact count as grounded."""
    config = {
        **CONFIG,
        "systems": [{"kind": "closed_book"}, {"kind": "wrong_context"}, {"kind": "oracle"}],
        # Closed book opted in as the control: shown nothing, so all its correct answers are memory.
        "grading": {**CONFIG["grading"], "evidence_support": {"systems": ["closed_book", "wrong_context", "oracle"]}},
    }
    ctx = _context(tmp_path, from_memory=True, config=config)
    run_pipeline([stage() for stage in STAGES], ctx)

    board = {s.system: s for s in ctx.summary.systems}
    assert all(s.accuracy.value == 1.0 for s in board.values())
    assert board["closed_book"].memory_suspect == 3 and board["closed_book"].grounded_accuracy.value == 0.0
    assert board["closed_book"].memory_suspect_questions == ["0", "1", "2"]
    assert board["oracle"].grounded_accuracy.value == 1.0 and board["oracle"].memory_suspect == 0
    # Only "Sylvania" is in the Sylvania article; the other two came from memory.
    assert board["wrong_context"].supported == 1 and board["wrong_context"].memory_suspect == 2
    assert board["wrong_context"].memory_suspect_questions == ["0", "1"]
    assert board["wrong_context"].grounded_accuracy.value == pytest.approx(1 / 3)
    grounded_pairs = {(t.a, t.b): t for t in ctx.summary.pairwise_grounded}
    assert grounded_pairs[("closed_book", "oracle")].b_only == 3
    report = ctx.store.path("report.md").read_text()
    assert "Memory-suspect questions" in report and "**closed_book**: 0, 1, 2" in report
    # Closed book is shown nothing, so its verdicts need no judge call.
    support_calls = [r for r in ctx.services.llm.requests if r.prompt_version == "evidence-support-v1"]
    assert len(support_calls) == 3 + 3


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
    from benchmarks.harness.errors import BackendContractError
    from benchmarks.harness.stages import _check_trace_contract

    with pytest.raises(BackendContractError):
        _check_trace_contract(Prediction(system="s", question_id="1", repeat=0, trace=StreamTrace()))


def test_a_failed_ranking_is_retried_on_resume_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vector-store timeout on one question must not end a stage with hours
    of other questions left; the next resume ranks the missing one."""
    from benchmarks.harness.models import RankedList

    original = FakeTraceSystem.ranked_search
    fail_once = {"0"}

    def flaky(self, item, prepared, k):  # noqa: ANN001, ANN202
        if item.question_id in fail_once:
            fail_once.discard(item.question_id)
            raise TimeoutError("vector store timed out")
        return original(self, item, prepared, k)

    monkeypatch.setattr(FakeTraceSystem, "ranked_search", flaky)
    ctx = _context(tmp_path)
    run_pipeline([DatasetStage(), CorpusStage(), PrepareStage(), SearchStage()], ctx)

    ranked = {(r.system, r.question_id) for r in ctx.store.read("rankings.jsonl", RankedList)}
    assert ("fake_trace", "0") not in ranked and ("fake_trace", "1") in ranked

    run_pipeline([SearchStage()], ctx)

    ranked = {(r.system, r.question_id) for r in ctx.store.read("rankings.jsonl", RankedList)}
    assert ("fake_trace", "0") in ranked


def test_an_unavailable_second_judge_defers_instead_of_stopping_the_scores(tmp_path: Path) -> None:
    """The second judge only measures agreement; a provider's daily quota
    must not stop the primary verdicts or the board. After the first failure
    calls not yet started are skipped, and a later resume adds them."""
    from benchmarks.harness.models import Judgment

    def quota_exhausted(request):  # noqa: ANN001, ANN202
        if request.model.provider == "gemini":
            raise RuntimeError("429 You exceeded your current quota")
        return _responder(request)

    ctx = _context(tmp_path)
    ctx.services.llm = FakeLLM(quota_exhausted)
    run_pipeline([stage() for stage in STAGES], ctx)

    judgments = ctx.store.read("judgments.jsonl", Judgment)
    assert judgments and all(j.role == "primary" for j in judgments)
    # Only calls already in flight when the first one failed are made.
    gemini_calls = sum(r.model.provider == "gemini" for r in ctx.services.llm.requests)
    assert gemini_calls <= ctx.config.grading.concurrency and gemini_calls < len(judgments)
    board = {s.system: s for s in ctx.summary.systems}
    assert board["oracle"].accuracy.value == 1.0 and board["fake_trace"].judge_agreement_kappa is None

    resumed = _context(tmp_path, store=RunStore.resume(tmp_path / "reports", ctx.store.run_id, ctx.config))
    run_pipeline([stage() for stage in STAGES], resumed)

    roles = {j.role for j in resumed.store.read("judgments.jsonl", Judgment)}
    assert roles == {"primary", "secondary"}
    assert {s.system: s for s in resumed.summary.systems}["fake_trace"].judge_agreement_kappa is not None
