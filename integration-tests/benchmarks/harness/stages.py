"""Pipeline stages.

Each stage is idempotent: it diffs its work keys against the run store, so a
resumed run continues exactly where it stopped and never re-pays for work
already recorded. Derived artefacts (scores, summary, report) are rewritten
in full every time.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from benchmarks.harness import HARNESS_VERSION
from benchmarks.harness.concurrency import CircuitBreaker, run_parallel
from benchmarks.harness.corpus.manifest import (
    MANIFEST_FILE,
    load_manifest,
    load_run_manifest,
    save_run_manifest,
)
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.dataset.split import apply_split, load_split, select_questions, split_sha256
from benchmarks.harness.config import SystemConfig
from benchmarks.harness.errors import (
    BackendContractError,
    CorpusError,
    IndexTimeoutError,
    IngestError,
)
from benchmarks.harness.grading.claims import ClaimSupportJudge
from benchmarks.harness.grading.judges import AnswerJudge, GradingSubject
from benchmarks.harness.grading.prompts import answer_prompt_texts, verify_prompt_pins
from benchmarks.harness.metrics.mapping import ArticleResolver
from benchmarks.harness.metrics.scoring import Scorer, ScoringInputs
from benchmarks.harness.models import (
    INDEXED,
    AskItem,
    ClaimSupport,
    IngestManifest,
    Judgment,
    Prediction,
    Question,
    QuestionScore,
    RankedList,
    RunMeta,
    answer_fingerprint,
)
from benchmarks.harness.paths import INTEGRATION_TESTS_DIR
from benchmarks.harness.pipeline import StageReport
from benchmarks.harness.report.diagnostics import diagnose, diagnostics_jsonl, render_diagnostics
from benchmarks.harness.report.markdown import failures_csv, render_report
from benchmarks.harness.report.summary import summarize
from benchmarks.harness.services import RunContext
from benchmarks.harness.systems import AdapterDeps, adapter_spec
from benchmarks.harness.systems.base import PreparedCorpus

logger = logging.getLogger(__name__)

QUESTIONS_FILE = "questions.jsonl"
PREDICTIONS_FILE = "predictions.jsonl"
RANKINGS_FILE = "rankings.jsonl"
JUDGMENTS_FILE = "judgments.jsonl"
CLAIMS_FILE = "claims.jsonl"
SCORES_FILE = "scores.jsonl"
SUMMARY_FILE = "summary.json"
REPORT_FILE = "report.md"
FAILURES_FILE = "failures.csv"
META_FILE = "run_meta.json"
RANK_K = 100


def ingest_file(system_id: str) -> str:
    return f"ingest_{system_id}.json"


class DatasetStage:
    name = "dataset"

    def run(self, ctx: RunContext) -> StageReport:
        dataset = ctx.dataset
        questions = apply_split(dataset.load(ctx.services), load_split(dataset.split_path(ctx.services)))
        ctx.all_questions = questions
        ctx.questions = select_questions(questions, ctx.config.dataset, ctx.config.seed)
        if not ctx.store.path(QUESTIONS_FILE).exists():
            ctx.store.write_text(QUESTIONS_FILE, "".join(q.model_dump_json() + "\n" for q in ctx.questions))
        logger.info("selected %d questions (%s split)", len(ctx.questions), ctx.config.dataset.split)
        return StageReport(self.name, processed=len(ctx.questions))


class CorpusStage:
    name = "corpus"

    def __init__(self, *, load_only: bool = False, reuse_existing: bool = False) -> None:
        self._load_only = load_only
        # A built corpus is frozen for runs: re-fetching articles that failed
        # before would change `corpus_version` whenever one now succeeds, and
        # with it the PipesHub KB (a full re-ingest). Only the `corpus`
        # command retries failures.
        self._reuse_existing = reuse_existing

    def run(self, ctx: RunContext) -> StageReport:
        cfg = ctx.config.corpus
        source = ctx.questions if cfg.scope == "selected_questions" else ctx.all_questions
        corpus_dir = ctx.services.corpus_dir(cfg, [q.id for q in source])
        if self._load_only or (self._reuse_existing and (corpus_dir / MANIFEST_FILE).exists()):
            # The shared cache is not part of the run; offline grade/score/
            # report must survive it being cleared, so the run's own copy is
            # authoritative when the cache is gone.
            manifest = load_manifest(corpus_dir) if (corpus_dir / MANIFEST_FILE).exists() else None
            if manifest is None:
                manifest = load_run_manifest(ctx.store.run_dir)
            if manifest is None:
                raise CorpusError(
                    f"corpus {corpus_dir.name} is not in the cache and this run has no copy of "
                    f"its manifest; rebuild the corpus with the `corpus` command",
                )
        else:
            builder = ctx.dataset.corpus_source(ctx.services, cfg, corpus_dir)
            manifest = builder.build(
                [ref for q in source for ref in q.gold_refs],
                tier=cfg.tier, distractor_count=cfg.distractor_count,
                seed=ctx.config.seed, max_failed_gold_ratio=cfg.max_failed_gold_ratio,
            )
        save_run_manifest(ctx.store.run_dir, manifest)
        ctx.corpus = CorpusView(corpus_dir, manifest, ctx.dataset.normalize_ref)
        return StageReport(self.name, processed=len(manifest.documents), notes=[f"corpus {manifest.corpus_version[:12]}"])


class PrepareStage:
    """Builds adapters, ingests the corpus into systems that need it, and gates
    on how many gold articles actually got indexed."""

    name = "prepare"

    def run(self, ctx: RunContext) -> StageReport:
        corpus = ctx.require_corpus()
        report = StageReport(self.name)
        for system in ctx.config.systems:
            adapter = adapter_spec(system.kind, ctx.services.adapter_registry).factory(
                system, AdapterDeps(ctx.config, ctx.services, corpus),
            )
            ctx.adapters[system.id] = adapter
            ingestor = adapter.ingestor()
            if ingestor is None:
                ctx.prepared[system.id] = PreparedCorpus(system=system.id, corpus_version=corpus.manifest.corpus_version)
                continue
            prepared = ingestor.prepare(corpus.manifest)
            ctx.store.write_model(ingest_file(system.id), prepared.ingest)
            index_report = ingestor.wait_ready(prepared, corpus.manifest)
            ctx.store.write_model(f"index_{system.id}.json", index_report)
            if index_report.gold_ratio < ctx.config.pipeshub.min_gold_indexed_ratio:
                raise IndexTimeoutError(
                    f"{system.id}: only {index_report.gold_indexed}/{index_report.gold_total} gold articles indexed",
                )
            corpus_size = len(corpus.manifest.documents)
            if index_report.indexed_ratio(corpus_size) < ctx.config.pipeshub.min_indexed_ratio:
                raise IndexTimeoutError(
                    f"{system.id}: only {index_report.status_counts.get(INDEXED, 0)}/{corpus_size} articles "
                    f"indexed ({index_report.status_counts})",
                )
            logger.info("%s: indexed %s of %d articles", system.id, index_report.status_counts, corpus_size)
            ctx.prepared[system.id] = prepared
            report.processed += 1
        return report


class LoadPreparedStage:
    """Offline counterpart of `PrepareStage` for grade/score/report re-runs."""

    name = "load-prepared"

    def run(self, ctx: RunContext) -> StageReport:
        version = ctx.require_corpus().manifest.corpus_version
        for system in ctx.config.systems:
            ingest = ctx.store.read_model(ingest_file(system.id), IngestManifest)
            # Without the manifest an `ArticleResolver` cannot map a record id
            # back to an article, so every retrieval and citation metric
            # degrades to a name-only lookup and scores near zero — numbers
            # that look like a retrieval failure rather than a missing file.
            # A system that never ingests (closed book, oracle) has none by
            # design; one that does must not be scored without it.
            if ingest is None and adapter_spec(
                system.kind, ctx.services.adapter_registry,
            ).capabilities.ingests_corpus:
                raise IngestError(
                    f"{system.id}: {ingest_file(system.id)} is missing from this run, so its "
                    "retrieval and citation metrics cannot be computed; re-run `prepare` "
                    "or drop the system from the config",
                )
            ctx.prepared[system.id] = PreparedCorpus(system=system.id, corpus_version=version, ingest=ingest)
        return StageReport(self.name, processed=len(ctx.prepared))


def _latest_predictions(ctx: RunContext) -> dict[tuple[str, int, int], Prediction]:
    return ctx.store.latest_by_key(PREDICTIONS_FILE, Prediction, lambda p: p.key)  # type: ignore[return-value]


class AskStage:
    name = "ask"

    def run(self, ctx: RunContext) -> StageReport:
        existing = _latest_predictions(ctx)
        report = StageReport(self.name)
        for system in ctx.config.systems:
            todo = [
                (q, r) for q in ctx.questions for r in range(system.repeats)
                if (p := existing.get((system.id, q.id, r))) is None or (ctx.retry_errors and p.error is not None)
            ]
            report.skipped += len(ctx.questions) * system.repeats - len(todo)
            if todo:
                self._ask_system(ctx, system.id, system.concurrency, todo, report)
        return report

    def _record(self, ctx: RunContext, prediction: Prediction, report: StageReport) -> None:
        ctx.store.append(PREDICTIONS_FILE, prediction)
        report.processed += 1
        report.failed += prediction.error is not None
        if prediction.policy_violations:
            logger.error("q%s: disallowed tools %s", prediction.question_id, prediction.policy_violations)
        ctx.services.cost.add(prediction.cost_usd)

    def _ask_system(
        self, ctx: RunContext, system_id: str, workers: int, todo: list[tuple[Question, int]], report: StageReport,
    ) -> None:
        adapter, prepared = ctx.adapters[system_id], ctx.prepared[system_id]
        with_gold = adapter.capabilities.needs_gold_refs
        if adapter.capabilities.retrieval_trace:
            question, repeat = todo.pop(0)
            first = adapter.answer(AskItem.from_question(question, with_gold=with_gold), prepared, repeat)
            self._record(ctx, first, report)
            _check_trace_contract(first)
        breaker = CircuitBreaker(ctx.config.limits.max_error_rate, ctx.config.limits.min_items_for_breaker)
        run_parallel(
            todo,
            lambda qr: adapter.answer(AskItem.from_question(qr[0], with_gold=with_gold), prepared, qr[1]),
            workers=workers,
            on_result=lambda _qr, prediction: self._record(ctx, prediction, report),
            is_failure=lambda prediction: prediction.error is not None,
            breaker=breaker,
        )


def _check_trace_contract(prediction: Prediction) -> None:
    """A trace-capable system must emit a prefetch `retrieval_context` frame;
    without it retrieval metrics would silently read as zero."""
    if prediction.error is not None:
        logger.warning("preflight question failed (%s); cannot verify the trace contract yet", prediction.error.message)
        return
    events = prediction.trace.retrieval_events if prediction.trace else []
    if not any(event.source == "prefetch" for event in events):
        raise BackendContractError(
            "PipesHub streamed no prefetch `retrieval_context` frame — deploy a build that supports "
            "`includeRetrievalContext`",
        )


class SearchStage:
    name = "search"

    def run(self, ctx: RunContext) -> StageReport:
        done = {(r.system, r.question_id) for r in ctx.store.read(RANKINGS_FILE, RankedList)}
        report = StageReport(self.name)
        for system in ctx.config.systems:
            adapter = ctx.adapters[system.id]
            retriever = adapter.retriever()
            if retriever is None:
                continue
            prepared = ctx.prepared[system.id]
            todo = [q for q in ctx.questions if (system.id, q.id) not in done]
            report.skipped += len(ctx.questions) - len(todo)

            def _append(_q: Question, ranking: RankedList) -> None:
                ctx.store.append(RANKINGS_FILE, ranking)
                report.processed += 1

            run_parallel(
                todo,
                lambda q: retriever.ranked_search(
                    AskItem.from_question(q, with_gold=adapter.capabilities.needs_gold_refs), prepared, RANK_K,
                ),
                workers=system.concurrency, on_result=_append,
            )
        return report


class GradeStage:
    name = "grade"

    def _judges(self, ctx: RunContext) -> list[AnswerJudge]:
        """Which rubrics grade this run is the dataset's call — FRAMES scores
        with its paper auto-rater, another dataset may score by EM or F1."""
        return ctx.dataset.judges(ctx.services, ctx.config.grading)

    def run(self, ctx: RunContext) -> StageReport:
        verify_prompt_pins()
        questions = {q.id: q for q in ctx.questions}
        predictions = [p for p in _latest_predictions(ctx).values() if p.question_id in questions]
        done = {j.key for j in ctx.store.read(JUDGMENTS_FILE, Judgment)}
        todo = []
        for prediction in predictions:
            subject = GradingSubject.of(prediction, questions[prediction.question_id])
            for judge in self._judges_cached(ctx):
                key = (subject.system, subject.question_id, subject.repeat, subject.answer_sha, judge.rubric, judge.role)
                if key not in done:
                    todo.append((judge, subject))
        report = StageReport(self.name, skipped=len(predictions) * len(self._judges_cached(ctx)) - len(todo))

        def _append(_item: object, judgment: Judgment) -> None:
            ctx.store.append(JUDGMENTS_FILE, judgment)
            ctx.services.cost.add(judgment.cost_usd)
            report.processed += 1
            report.failed += not judgment.parse_ok

        run_parallel(
            todo, lambda item: item[0].grade(item[1]),
            workers=ctx.config.grading.concurrency, on_result=_append,
            is_failure=lambda judgment: not judgment.parse_ok,
            breaker=CircuitBreaker(
                ctx.config.limits.max_error_rate, ctx.config.limits.min_items_for_breaker,
            ),
        )
        if ctx.config.grading.claim_support:
            report.notes.append(f"claims judged for {self._grade_claims(ctx, predictions)} answers")
        return report

    def _judges_cached(self, ctx: RunContext) -> list[AnswerJudge]:
        if not hasattr(self, "_cache"):
            self._cache = self._judges(ctx)
        return self._cache

    def _grade_claims(self, ctx: RunContext, predictions: list[Prediction]) -> int:
        done = {(c.system, c.question_id, c.repeat, c.answer_sha) for c in ctx.store.read(CLAIMS_FILE, ClaimSupport)}
        corpus = ctx.require_corpus()
        resolvers = {s.id: ArticleResolver(corpus, ctx.prepared[s.id].ingest) for s in ctx.config.systems}

        def title_for(system: str):  # noqa: ANN202
            def _title(citation) -> str:  # noqa: ANN001
                url = resolvers[system].url_for(citation.record_id, citation.record_name)
                document = corpus.document(url) if url else None
                return document.title if document else (citation.record_name or "")
            return _title

        todo = [
            p for p in predictions
            if p.citations and p.error is None
            and (p.system, p.question_id, p.repeat, answer_fingerprint(p.answer)) not in done
        ]
        model = ctx.services.judge_model(ctx.config.grading.primary)

        def _judge(prediction: Prediction) -> list[ClaimSupport]:
            judge = ClaimSupportJudge(ctx.services.llm, model, title_for(prediction.system))
            return judge.judge(prediction, ctx.config.grading.max_claims)

        def _append(_p: Prediction, claims: list[ClaimSupport]) -> None:
            for claim in claims:
                ctx.store.append(CLAIMS_FILE, claim)
                ctx.services.cost.add(claim.cost_usd)

        run_parallel(
            todo, _judge, workers=ctx.config.grading.concurrency, on_result=_append,
            breaker=CircuitBreaker(
                ctx.config.limits.max_error_rate, ctx.config.limits.min_items_for_breaker,
            ),
        )
        return len(todo)


class ScoreStage:
    name = "score"

    def run(self, ctx: RunContext) -> StageReport:
        corpus = ctx.require_corpus()
        questions = {q.id: q for q in ctx.questions}
        judgments: dict[tuple[str, int, int, str], dict[tuple[str, str], Judgment]] = defaultdict(dict)
        for j in ctx.store.read(JUDGMENTS_FILE, Judgment):
            judgments[(j.system, j.question_id, j.repeat, j.answer_sha)][(j.rubric, j.role)] = j
        claims: dict[tuple[str, int, int, str], list[ClaimSupport]] = defaultdict(list)
        for c in ctx.store.read(CLAIMS_FILE, ClaimSupport):
            claims[(c.system, c.question_id, c.repeat, c.answer_sha)].append(c)
        scorers = {
            s.id: Scorer(
                ArticleResolver(corpus, ctx.prepared[s.id].ingest if s.id in ctx.prepared else None), corpus,
                adapter_spec(s.kind, ctx.services.adapter_registry).capabilities, ctx.config.grading.fuzzy_threshold,
            )
            for s in ctx.config.systems
        }
        scores = []
        for p in _latest_predictions(ctx).values():
            if p.question_id not in questions or p.system not in scorers:
                continue
            key = (p.system, p.question_id, p.repeat, answer_fingerprint(p.answer))
            scores.append(scorers[p.system].score(ScoringInputs(
                question=questions[p.question_id], prediction=p, judgments=judgments.get(key, {}), claims=claims.get(key, []),
            )))
        # Sorted, not in thread-completion order: the bootstrap resamples this
        # sequence by index, so append order would otherwise move the published
        # confidence intervals for identical scores and an identical seed.
        scores.sort(key=lambda s: (s.system, s.question_id, s.repeat))
        ctx.store.write_text(SCORES_FILE, "".join(s.model_dump_json() + "\n" for s in scores))
        return StageReport(self.name, processed=len(scores))


def _git_sha() -> str | None:
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=INTEGRATION_TESTS_DIR,  # noqa: S607
    )
    return result.stdout.strip() or None


class ReportStage:
    name = "report"

    def _meta(self, ctx: RunContext) -> RunMeta:
        grading = ctx.config.grading
        revision = ctx.dataset.revision(ctx.services)
        answer_prompts = {
            version: hashlib.sha256(text.encode()).hexdigest()
            for version, text in answer_prompt_texts().items()
        }
        models = {"answerer": ctx.config.answerer.model, "judge_primary": grading.primary.model}
        if grading.secondary is not None:
            models["judge_secondary"] = grading.secondary.model
        return RunMeta(
            run_id=ctx.store.run_id, run_name=ctx.config.run_name, created_at=datetime.now(UTC),
            config_hash=ctx.config.config_hash(), harness_version=HARNESS_VERSION, git_sha=_git_sha(),
            dataset_revision=revision.revision, dataset_sha256=revision.sha256,
            answer_prompts=answer_prompts,
            split_sha256=split_sha256(ctx.dataset.split_path(ctx.services)),
            corpus_version=ctx.require_corpus().manifest.corpus_version,
            snapshot=ctx.config.corpus.snapshot, models=models,
        )

    @staticmethod
    def _diagnostics(ctx: RunContext, scores: list[QuestionScore], gold_for: Callable[[int], list[str]]) -> None:
        index_of = {
            s.id: adapter_spec(s.kind, ctx.services.adapter_registry).reads_index(s)
            for s in ctx.config.systems
        }
        predictions = _latest_predictions(ctx)
        for system in ctx.config.systems:
            if not adapter_spec(system.kind, ctx.services.adapter_registry).capabilities.retrieval_trace:
                continue
            diagnoses = diagnose(system.id, scores, predictions, index_of, gold_for)
            ctx.store.write_text(f"diagnostics_{system.id}.md", render_diagnostics(system.id, diagnoses))
            ctx.store.write_text(f"diagnostics_{system.id}.jsonl", diagnostics_jsonl(diagnoses))

    def run(self, ctx: RunContext) -> StageReport:
        corpus = ctx.require_corpus()
        scores = ctx.store.read(SCORES_FILE, QuestionScore)
        questions = {q.id: q for q in ctx.questions}
        # Re-derived from the recorded traces rather than read off the
        # predictions: the tool policy is a scoring-time judgement, so
        # correcting the allowlist must not require re-asking every question.
        guard = ctx.services.guard
        violations = sorted({
            f"{p.system} q{p.question_id} r{p.repeat}: {', '.join(found)}"
            for p in _latest_predictions(ctx).values() if p.question_id in questions
            for found in [
                guard.violations(call.name for call in p.trace.tool_calls)
                if p.trace is not None else p.policy_violations
            ]
            if found
        })
        meta = self._meta(ctx)
        summary = summarize(
            ctx.store.run_id, scores, ctx.store.read(RANKINGS_FILE, RankedList),
            lambda qid: corpus.resolve_refs(questions[qid].gold_refs) if qid in questions else [],
            ctx.config.stats, ctx.config.seed, violations=violations, meta=meta,
        )
        ctx.store.write_model(META_FILE, meta)
        ctx.store.write_model(SUMMARY_FILE, summary)
        ctx.store.write_text(REPORT_FILE, render_report(summary))
        ctx.store.write_text(FAILURES_FILE, failures_csv(scores))
        self._diagnostics(ctx, scores, lambda qid: corpus.resolve_refs(questions[qid].gold_refs) if qid in questions else [])
        ctx.summary = summary
        return StageReport(self.name, processed=len(summary.systems), notes=["VALID" if summary.valid else "INVALID"])
