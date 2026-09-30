"""Judge-time verification that answers came from the context a system showed
its answering model, not from the model's training data: evidence capture,
reconstruction, passage selection, the support judge, and the metrics and
report built on it."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from frames_testkit import FakeLLM, make_model
from pydantic import ValidationError

import benchmarks.datasets.frames.plugin  # noqa: F401  (registers the dataset)
from benchmarks.harness.config import EvidenceSupportConfig, RunConfig, StatsConfig
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.credentials import Credentials
from benchmarks.harness.datasets import dataset_plugin
from benchmarks.harness.errors import ConfigError, CostLimitError
from benchmarks.harness.evidence import EVIDENCE_FILE, MAX_EVIDENCE_CHARS, EvidenceSourceError, captured
from benchmarks.harness.grading import prompts
from benchmarks.harness.grading.evidence_support import EvidenceSupportJudge, SupportSubject, select_passages
from benchmarks.harness.grading.prompts import EVIDENCE_SUPPORT, PromptTemplate, render_evidence_support, verify_prompt_pins
from benchmarks.harness.grading.verdicts import parse_evidence_support, parse_reason
from benchmarks.harness.llm.client import LLMRequest, LLMResponse
from benchmarks.harness.models import (
    AskItem,
    CorpusManifest,
    Evidence,
    EvidencePassage,
    EvidenceRecord,
    FetchedRange,
    IngestManifest,
    Judgment,
    Prediction,
    Question,
    QuestionScore,
    RetrievalEvent,
    RetrievedChunk,
    RetrievedRecordRef,
    StreamTrace,
    SupportJudgment,
    answer_fingerprint,
)
from benchmarks.harness.report.markdown import render_report
from benchmarks.harness.report.summary import summarize
from benchmarks.harness.services import RunContext, Services
from benchmarks.harness.stages import SUPPORT_FILE, EvidenceStage, SupportStage
from benchmarks.harness.store import RunStore
from benchmarks.harness.systems import ADAPTER_REGISTRY, AdapterSpec, EvidenceDeps
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus
from benchmarks.harness.systems.baselines.answering import BaselineAnswerer, ContextDocument
from benchmarks.harness.systems.openwebui.adapter import evidence_of as openwebui_evidence
from benchmarks.harness.systems.pipeshub.evidence import RecordPointsCache, TraceEvidenceBuilder
from benchmarks.harness.systems.rag.standard_index import point_id
from benchmarks.harness.systems.ragflow.adapter import evidence_of as ragflow_evidence

DEFAULT_BUDGET = EvidenceSupportConfig().max_evidence_tokens


def _passage(text: str, header: str = "") -> EvidencePassage:
    return EvidencePassage(header=header, text=text)


def _rendered(passages: list[EvidencePassage]) -> str:
    return "\n\n".join(p.header + p.text for p in passages)


class TestCapture:
    def test_large_evidence_is_capped_but_described_in_full(self) -> None:
        passages = [_passage("a" * 300_000, "[1] A\n"), _passage("b" * 300_000, "[2] B\n")]
        evidence = captured(passages, "test")
        full = "[1] A\n" + "a" * 300_000 + "\n\n[2] B\n" + "b" * 300_000
        assert evidence.truncated and evidence.chars == len(full)
        assert evidence.sha256 == hashlib.sha256(full.encode()).hexdigest()
        assert len(evidence.text()) <= MAX_EVIDENCE_CHARS
        assert evidence.text().startswith("[1] A\naaa") and "[2] B\nbbb" in evidence.text()

    def test_no_text_is_empty_evidence(self) -> None:
        assert captured([], "test").status == "empty"
        assert captured([_passage("")], "test").status == "empty"


class _Docs(BaselineAnswerer):
    def __init__(self, docs: list[ContextDocument]) -> None:
        super().__init__("oracle", FakeLLM(lambda _r: "Paris."), make_model("gpt"))
        self._docs = docs

    def documents_for(self, item: AskItem) -> list[ContextDocument]:
        return self._docs


class TestBaselineCapture:
    def test_evidence_is_the_articles_block_of_the_prompt(self) -> None:
        answerer = _Docs([ContextDocument("u1", "France", "Paris is the capital."), ContextDocument("u2", "Seine", "A river.")])
        prediction = answerer.answer(AskItem(question_id="1", prompt="Capital?"), None, 0)  # type: ignore[arg-type]
        user = answerer._llm.requests[0].messages[1].content
        assert user == f"Wikipedia articles:\n\n{prediction.evidence.text()}\n\nQuestion: Capital?"

    def test_closed_book_is_shown_nothing(self) -> None:
        prediction = _Docs([]).answer(AskItem(question_id="1", prompt="Capital?"), None, 0)  # type: ignore[arg-type]
        assert prediction.evidence.status == "empty"


class TestCompetitorCapture:
    def test_openwebui_chunk_texts(self) -> None:
        body = {"sources": [{"source": {"name": "kb"}, "document": ["Paris is the capital.", "A river."],
                             "metadata": [{"file_id": "f1", "name": "France.html"}, {"file_id": "f2"}]}]}
        assert openwebui_evidence(body).text() == "France.html\nParis is the capital.\n\nkb\nA river."

    def test_openwebui_metadata_without_text_is_unavailable(self) -> None:
        assert openwebui_evidence({"sources": [{"metadata": [{"file_id": "f"}]}]}).status == "unavailable"

    def test_ragflow_reference_chunks(self) -> None:
        data = {"reference": {"chunks": [{"document_id": "d", "document_name": "France.html", "content": "Paris."}]}}
        assert ragflow_evidence(data, reasoning=False).text() == "France.html\nParis."

    def test_ragflow_research_loop_without_a_pool_is_unknown_not_empty(self) -> None:
        assert ragflow_evidence({"reference": {}}, reasoning=True).status == "unavailable"
        assert ragflow_evidence({"reference": {}}, reasoning=False).status == "empty"


def _point(
    text: str, *, block: int | None = None, is_block: bool | None = None, group: bool = False,
    summary: bool = False, block_id: str = "", vrid: str = "vr",
) -> dict[str, Any]:
    meta: dict[str, Any] = {"virtualRecordId": vrid, "isBlockGroup": group, "blockId": block_id}
    if block is not None:
        meta["blockIndex"] = block
    if is_block is not None:
        meta["isBlock"] = is_block
    if summary:
        meta["isRecordSummary"] = True
    return {"page_content": text, "metadata": meta}


class FakePoints:
    def __init__(self, records: Mapping[str, list[dict[str, Any]]], *, down: bool = False) -> None:
        self.records = records
        self.down = down
        self.calls: list[str] = []

    def payloads(self, virtual_record_id: str) -> list[Mapping[str, Any]]:
        self.calls.append(virtual_record_id)
        if self.down:
            raise EvidenceSourceError("ConnectError: connection refused")
        return self.records.get(virtual_record_id, [])


ARTICLE = [
    _point("Block zero.", block=0, is_block=True),
    _point("Block one. It has two sentences.", block=1, is_block=True),
    _point("Block one.", block=1, is_block=False),
    _point("It has two sentences.", block=1, is_block=False),
    _point("Block two.", block=2, is_block=True),
    _point("Window A of an oversized block.", block=3, is_block=False),
    _point("Window B of an oversized block.", block=3, is_block=False),
    _point("Block four.", block=4, is_block=True),
    _point("The record summary.", is_block=False, summary=True),
    _point("Row: Sylvania | Rival", is_block=True, block_id="row-2"),
    _point("Row: Fredonia | Home", is_block=True, block_id="row-1"),
    _point("A table summary.", is_block=False, group=True, block_id="tbl"),
]


def _trace_prediction(*refs: RetrievedRecordRef) -> Prediction:
    events = [RetrievalEvent(seq=i, source="tool", status="ok", records=[ref]) for i, ref in enumerate(refs, 1)]
    return Prediction(system="pipeshub", question_id="1", repeat=0, answer="x", trace=StreamTrace(retrieval_events=events))


class TestPipesHubReconstruction:
    def _build(self, *refs: RetrievedRecordRef, down: bool = False) -> tuple[Evidence, FakePoints]:
        source = FakePoints({"vr": ARTICLE}, down=down)
        return TraceEvidenceBuilder(RecordPointsCache(source)).evidence_for(_trace_prediction(*refs)), source

    def test_search_hits_are_whole_blocks_and_sentence_copies_are_skipped(self) -> None:
        evidence, _ = self._build(RetrievedRecordRef(virtual_record_id="vr", record_name="Freedonia", block_indices=[1]))
        assert evidence.text() == "Freedonia (block 1)\nBlock one. It has two sentences."
        assert evidence.status == "reconstructed"

    def test_fetched_ranges_cover_start_to_start_plus_rendered(self) -> None:
        evidence, _ = self._build(RetrievedRecordRef(
            virtual_record_id="vr", record_name="F", fetched=[FetchedRange(start_block=2, blocks_rendered=2, complete=False)],
        ))
        texts = [p.text for p in evidence.passages]
        assert texts[:2] == ["Block two.", "Window A of an oversized block.\nWindow B of an oversized block."]
        assert "Block four." not in texts and "Block zero." not in texts
        # A rendered fetch may have shown any table row; the summary it did not.
        assert "Row: Fredonia | Home" in texts and "The record summary." not in texts

    def test_a_fetch_that_names_its_shown_blocks_is_rebuilt_from_them(self) -> None:
        """`blocks_rendered` counts units: a table is one unit however many
        rows it showed, so the range under-reports a fetched table."""
        evidence, _ = self._build(RetrievedRecordRef(
            virtual_record_id="vr", record_name="F",
            fetched=[FetchedRange(start_block=0, blocks_rendered=1, complete=False, shown_blocks=[0, 4])],
        ))
        texts = [p.text for p in evidence.passages]
        assert texts[:2] == ["Block zero.", "Block four."]
        assert "Block one. It has two sentences." not in texts

    def test_summary_hit_is_the_record_summary_and_its_block_less_points(self) -> None:
        evidence, _ = self._build(RetrievedRecordRef(virtual_record_id="vr", record_name="F", summary_hit=True))
        texts = [p.text for p in evidence.passages]
        assert texts[0] == "The record summary."
        assert set(texts[1:]) == {"Row: Fredonia | Home", "Row: Sylvania | Rival", "A table summary."}
        assert all("Block" not in t for t in texts)

    def test_a_block_reported_twice_appears_once(self) -> None:
        evidence, source = self._build(
            RetrievedRecordRef(virtual_record_id="vr", block_indices=[0]),
            RetrievedRecordRef(virtual_record_id="vr", fetched=[FetchedRange(start_block=0, blocks_rendered=1, complete=False)]),
        )
        assert [p.text for p in evidence.passages].count("Block zero.") == 1
        assert source.calls == ["vr"]

    def test_blocks_missing_from_the_store_are_counted(self) -> None:
        evidence, _ = self._build(RetrievedRecordRef(virtual_record_id="vr", block_indices=[0, 99]))
        assert evidence.status == "reconstructed" and evidence.missing_blocks == 1

    def test_unreachable_store_is_unavailable_and_retryable_without_failing(self) -> None:
        source = FakePoints({"vr": ARTICLE}, down=True)
        builder = TraceEvidenceBuilder(RecordPointsCache(source))
        ref = RetrievedRecordRef(virtual_record_id="vr", block_indices=[0])
        first = builder.evidence_for(_trace_prediction(ref))
        second = builder.evidence_for(_trace_prediction(ref))
        assert first.status == second.status == "unavailable"
        assert first.retryable and "unreachable" in (first.reason or "")
        assert len(source.calls) == 1

    def test_nothing_reached_the_model_is_empty(self) -> None:
        evidence, _ = self._build(RetrievedRecordRef(virtual_record_id="vr"))
        assert evidence.status == "empty"

    def test_no_trace_is_unavailable(self) -> None:
        builder = TraceEvidenceBuilder(RecordPointsCache(FakePoints({})))
        evidence = builder.evidence_for(Prediction(system="p", question_id="1", repeat=0))
        assert evidence.status == "unavailable" and not evidence.retryable


class FakeQdrant:
    """`scroll` by record (PipesHub's collection) and `retrieve` by id (the standard index)."""

    def __init__(self, points: list[dict[str, Any]], standard: dict[str, dict[str, Any]]) -> None:
        self.points = points
        self.standard = standard

    def scroll(self, *, collection_name: str, scroll_filter: Any, limit: int, offset: Any, **_kw: Any) -> Any:  # noqa: ANN401
        vrid = scroll_filter.must[0].match.value
        return [SimpleNamespace(payload=p) for p in self.points if p["metadata"]["virtualRecordId"] == vrid], None

    def retrieve(self, *, collection_name: str, ids: list[str], **_kw: Any) -> list[Any]:  # noqa: ANN401
        return [SimpleNamespace(payload=self.standard[i]) for i in ids if i in self.standard]


class TestRagReconstructionHook:
    """Retroactive evidence for RAG runs asked before capture existed, through
    the registry hook the evidence stage uses."""

    URL = "https://en.wikipedia.org/wiki/Freedonia"

    def _deps(self, tmp_path: Path, index: str) -> tuple[EvidenceDeps, Any]:
        config = RunConfig.model_validate({
            "run_name": "r", "answerer": {"model": "gpt", "provider": "openAI"},
            "systems": [{"kind": "advanced_rag", "options": {"index": index}}],
            "grading": {"primary": {"model": "j", "provider": "anthropic"}},
        })
        services = Services(config, Credentials(), cache_dir=tmp_path)
        services.__dict__["qdrant"] = FakeQdrant(
            [_point("Freedonia's capital is Fredville.", block=4, is_block=True, vrid="vr-f")],
            {point_id(self.URL, 2): {"url": self.URL, "chunk_index": 2, "text": "Fredville is the capital."}},
        )
        corpus = CorpusView(tmp_path, CorpusManifest(
            snapshot=datetime(2024, 10, 15, tzinfo=UTC), tier="G", harness_version="t", documents=[],
        ))
        ingest = IngestManifest(system="r", kb_id="frames_std_x", corpus_version="v", base_url="qdrant", records=[])
        return EvidenceDeps(config, services, corpus, PreparedCorpus(system="r", corpus_version="v", ingest=ingest)), config

    def _prediction(self, vrid: str, block: int) -> Prediction:
        return Prediction(
            system="advanced_rag", question_id="1", repeat=0, answer="Fredville",
            retrieved=[RetrievedChunk(url=self.URL, virtual_record_id=vrid, block_index=block)], context_urls=[self.URL],
        )

    @pytest.mark.parametrize(
        ("index", "vrid", "block", "text"),
        [("pipeshub", "vr-f", 4, "Freedonia's capital is Fredville."), ("standard", URL, 2, "Fredville is the capital.")],
    )
    def test_rebuilds_the_numbered_sources_from_the_index_it_read(
        self, tmp_path: Path, index: str, vrid: str, block: int, text: str,
    ) -> None:
        deps, config = self._deps(tmp_path, index)
        rebuild = ADAPTER_REGISTRY["advanced_rag"].rebuild_evidence
        evidence = rebuild(config.systems[0], deps).evidence_for(self._prediction(vrid, block))
        assert evidence.status == "reconstructed"
        assert evidence.text() == f"[1] {self.URL}\n{text}"


class TestSelection:
    def _passages(self) -> list[EvidencePassage]:
        filler = "\n".join(f"Paragraph {i} about weather patterns and ocean currents." for i in range(400))
        return [
            _passage(filler, "[1] Weather\n"),
            _passage("Unrelated sports trivia.\nThe Treaty of Fredville was signed in 1911 by Rufus Firefly.", "[2] Treaty\n"),
            _passage(filler, "[3] Oceans\n"),
        ]

    def test_under_budget_nothing_is_cut(self) -> None:
        passages = [_passage("short", "[1] A\n")]
        chosen, selection = select_passages(passages, "q", "a", DEFAULT_BUDGET)
        assert chosen == passages and not selection.selected

    def test_over_budget_keeps_the_relevant_segment_within_budget(self) -> None:
        budget = 1_000
        chosen, selection = select_passages(self._passages(), "When was the Treaty of Fredville signed?", "1911", budget)
        assert selection.selected and selection.chars_kept == len(_rendered(chosen))
        assert len(_rendered(chosen)) <= budget * 4
        assert selection.segments_kept < selection.segments_total
        assert any("signed in 1911" in p.text for p in chosen)
        order = [p.header for p in self._passages()]
        assert [p.header for p in chosen] == sorted({p.header for p in chosen}, key=order.index)

    def test_neighbouring_segments_come_with_the_selected_one(self) -> None:
        paragraphs = [f"Filler {k} " + "lorem ipsum " * 45 for k in range(30)]
        paragraphs[12] = "The Treaty of Fredville was signed in 1911. " + "lorem ipsum " * 40
        chosen, selection = select_passages(
            [_passage("\n".join(paragraphs), "[1] Treaty\n")], "When was the Treaty of Fredville signed?", "1911", 450,
        )
        [passage] = chosen
        assert selection.segments_kept == 3
        assert passage.text.startswith("Filler 11 ") and "signed in 1911" in passage.text and "Filler 13 " in passage.text
        assert "…" not in passage.text

    def test_selection_is_deterministic(self) -> None:
        args = (self._passages(), "When was the Treaty of Fredville signed?", "1911", 1_000)
        assert select_passages(*args) == select_passages(*args)

    def test_default_budget_bounds_a_huge_context(self) -> None:
        passages = [_passage(("word " * 250 + "\n") * 2000, f"[{i}] T\n") for i in range(3)]
        chosen, selection = select_passages(passages, "question", "answer", DEFAULT_BUDGET)
        assert DEFAULT_BUDGET == 6_000
        assert len(_rendered(chosen)) <= DEFAULT_BUDGET * 4
        assert selection.selected and selection.budget_tokens == DEFAULT_BUDGET


class TestVerdictParsing:
    @pytest.mark.parametrize(
        ("text", "label"),
        [
            ("Reason: stated.\nEvidence support: SUPPORTED", "SUPPORTED"),
            ("Reason: missing.\n**Evidence support:** UNSUPPORTED", "UNSUPPORTED"),
            ("evidence support: partial.", "PARTIAL"),
            ("Evidence support: SUPPORTED\nEvidence support: UNSUPPORTED", "UNSUPPORTED"),
            ("Evidence support: SUPPORTED, PARTIAL or UNSUPPORTED", None),
            ("Support: FULL", None),
        ],
    )
    def test_label(self, text: str, label: str | None) -> None:
        assert parse_evidence_support(text) == label

    def test_reason(self) -> None:
        assert parse_reason("Reason: the year 1911 is stated in [2].\nEvidence support: SUPPORTED") == (
            "the year 1911 is stated in [2]."
        )


def _subject(evidence: Evidence | None, answer: str = "1911") -> SupportSubject:
    return SupportSubject.of(
        system="s", question_id="1", repeat=0, answer_sha=answer_fingerprint(answer),
        question="When was it signed?", answer=answer, evidence=evidence, verifier="v", budget_tokens=DEFAULT_BUDGET,
    )


class TestJudge:
    def test_supported_verdict_records_model_prompt_and_cost(self) -> None:
        llm = FakeLLM(lambda _r: "Reason: 1911 is stated.\nEvidence support: SUPPORTED")
        judgment = EvidenceSupportJudge(llm, make_model()).judge(_subject(captured([_passage("Signed in 1911.")], "t")))
        assert judgment.label == "SUPPORTED" and judgment.judged and not judgment.reasked
        assert judgment.prompt_version == EVIDENCE_SUPPORT.version and judgment.cost_usd == 0.001
        assert judgment.reason == "1911 is stated." and judgment.verifier == "v"
        [request] = llm.requests
        assert request.temperature == 0.0 and request.cacheable
        assert "Signed in 1911." in request.messages[0].content

    def test_reconstructed_evidence_is_judged(self) -> None:
        llm = FakeLLM(lambda _r: "Evidence support: SUPPORTED")
        evidence = captured([_passage("Signed in 1911.")], "t", reconstructed=True)
        assert EvidenceSupportJudge(llm, make_model()).judge(_subject(evidence)).evidence_status == "reconstructed"
        assert len(llm.requests) == 1

    def test_unparseable_reply_is_reasked_once(self) -> None:
        replies = iter(["It looks fine to me.", "Reason: ok.\nEvidence support: PARTIAL"])
        llm = FakeLLM(lambda _r: next(replies))
        judgment = EvidenceSupportJudge(llm, make_model()).judge(_subject(captured([_passage("x")], "t")))
        assert judgment.label == "PARTIAL" and judgment.reasked and judgment.parse_ok
        assert len(llm.requests) == 2 and llm.requests[1].messages[-1].role == "user"
        assert judgment.cost_usd == pytest.approx(0.002)

    def test_still_unparseable_after_the_reask(self) -> None:
        judgment = EvidenceSupportJudge(FakeLLM(lambda _r: "no idea"), make_model()).judge(
            _subject(captured([_passage("x")], "t")),
        )
        assert judgment.label == "UNPARSEABLE" and not judgment.parse_ok

    @pytest.mark.parametrize(
        ("evidence", "label", "status"),
        [
            (None, "NO_EVIDENCE", "missing"),
            (Evidence(status="unavailable", source="t", reason="store down"), "NO_EVIDENCE", "unavailable"),
            (captured([], "t"), "UNSUPPORTED", "empty"),
        ],
    )
    def test_verdicts_that_need_no_call(self, evidence: Evidence | None, label: str, status: str) -> None:
        llm = FakeLLM(lambda _r: "unused")
        judgment = EvidenceSupportJudge(llm, None).judge(_subject(evidence))
        assert (judgment.label, judgment.evidence_status, judgment.judged) == (label, status, False)
        assert llm.requests == []

    def test_a_cache_hit_costs_nothing(self) -> None:
        class _Cached:
            def complete(self, _request: LLMRequest) -> LLMResponse:
                return LLMResponse(text="Evidence support: SUPPORTED", cost_usd=0.5, cached=True)

        judgment = EvidenceSupportJudge(_Cached(), make_model()).judge(_subject(captured([_passage("x")], "t")))  # type: ignore[arg-type]
        assert judgment.cost_usd == 0.0 and judgment.cache_hit


class TestPromptPin:
    def test_the_evidence_prompt_is_pinned(self) -> None:
        assert EVIDENCE_SUPPORT in prompts.ALL_PROMPTS
        verify_prompt_pins()

    def test_an_edited_evidence_prompt_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        edited = PromptTemplate(EVIDENCE_SUPPORT.version, EVIDENCE_SUPPORT.filename, "0" * 64)
        monkeypatch.setattr(prompts, "ALL_PROMPTS", (*prompts.ALL_PROMPTS[:-1], edited))
        with pytest.raises(ConfigError, match="evidence_support_v1"):
            verify_prompt_pins()

    def test_rendering_keeps_braces_in_the_answer(self) -> None:
        rendered = render_evidence_support("Q?", "It is {x}.", "[1] T\nfact")
        assert "Answer given by the system:\nIt is {x}." in rendered and "[1] T\nfact" in rendered


BASE_CONFIG: dict[str, Any] = {
    "run_name": "verify",
    "answerer": {"model": "gpt", "provider": "openAI"},
    "systems": [{"kind": "closed_book"}, {"kind": "trace", "label": "pipeshub"}, {"kind": "trace", "label": "rag"}],
    "grading": {"primary": {"model": "judge-model", "provider": "anthropic"},
                "secondary": {"model": "cheap-judge", "provider": "anthropic"}},
    "stats": {"bootstrap_samples": 200},
}


class TestConfig:
    def test_commands_verify_after_grading_and_an_existing_run_can_be_verified(self) -> None:
        from benchmarks.harness.cli import _NEEDS_RESUME, PIPELINES

        names = {command: [stage.name for stage in PIPELINES[command]()] for command in ("run", "grade", "verify")}
        for command in ("run", "grade"):
            assert names[command][-5:] == ["grade", "evidence", "verify", "score", "report"]
        assert names["verify"] == ["dataset", "corpus", "load-prepared", "evidence", "verify", "score", "report"]
        assert "verify" in _NEEDS_RESUME

    def test_evidence_settings_leave_the_config_hash_alone(self) -> None:
        """Runs started before the check existed must still resume to be verified."""
        tuned = {**BASE_CONFIG, "grading": {**BASE_CONFIG["grading"], "evidence_support": {
            "judge": "secondary", "max_evidence_tokens": 9_000, "systems": ["rag"],
        }}}
        assert RunConfig.model_validate(tuned).config_hash() == RunConfig.model_validate(BASE_CONFIG).config_hash()

    def test_defaults(self) -> None:
        config = RunConfig.model_validate(BASE_CONFIG)
        assert config.grading.evidence_judge() == config.grading.primary
        assert config.evidence_verified_systems() == {"pipeshub", "rag"}

    def test_unknown_system_in_the_allowlist_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown systems"):
            RunConfig.model_validate({**BASE_CONFIG, "grading": {
                **BASE_CONFIG["grading"], "evidence_support": {"systems": ["nope"]},
            }})

    def test_secondary_judge_needs_one_configured(self) -> None:
        grading = {"primary": {"model": "j"}, "evidence_support": {"judge": "secondary"}}
        with pytest.raises(ConfigError, match="no secondary judge"):
            RunConfig.model_validate({**BASE_CONFIG, "grading": grading}).grading.evidence_judge()


def _context(
    tmp_path: Path, llm: FakeLLM, *, source: FakePoints | None = None, limit: float | None = None,
    evidence_support: dict[str, Any] | None = None, store: RunStore | None = None, pricing: dict | None = None,
) -> RunContext:
    grading = {**BASE_CONFIG["grading"], **({"evidence_support": evidence_support} if evidence_support else {})}
    config = RunConfig.model_validate({
        **BASE_CONFIG, "grading": grading, "limits": {"max_cost_usd": limit} if limit else {},
        "pricing": pricing or {},
    })
    points = RecordPointsCache(source or FakePoints({}))
    registry = {
        **ADAPTER_REGISTRY,
        "trace": AdapterSpec(
            lambda _s, _d: None, AdapterCapabilities(retrieval_trace=True),  # type: ignore[arg-type,return-value]
            rebuild_evidence=lambda _s, _deps: TraceEvidenceBuilder(points),
        ),
    }
    services = Services(config, Credentials(), cache_dir=tmp_path / "cache", llm=llm, adapter_registry=registry)
    ctx = RunContext(
        config=config, store=store or RunStore.create(tmp_path / "reports", config), services=services,
        dataset=dataset_plugin("frames"),
    )
    ctx.questions = [Question(id=str(i), prompt=f"Question {i}?", answer="1911") for i in range(3)]
    ctx.corpus = CorpusView(tmp_path, CorpusManifest(
        snapshot=datetime(2024, 10, 15, tzinfo=UTC), tier="G", harness_version="t", documents=[],
    ))
    return ctx


def _record(ctx: RunContext, system: str, qid: str, answer: str, *, correct: bool, evidence: Evidence | None = None,
            trace: StreamTrace | None = None) -> None:
    ctx.store.append("predictions.jsonl", Prediction(system=system, question_id=qid, repeat=0, answer=answer, trace=trace))
    ctx.store.append("judgments.jsonl", Judgment(
        system=system, question_id=qid, repeat=0, answer_sha=answer_fingerprint(answer), rubric="frames",
        role="primary", judge_model="j", prompt_version="frames-autorater-v1",
        label="TRUE" if correct else "FALSE", parse_ok=True,
    ))
    if evidence is not None:
        ctx.store.append_compressed(EVIDENCE_FILE, EvidenceRecord(
            system=system, question_id=qid, repeat=0, answer_sha=answer_fingerprint(answer), evidence=evidence,
        ))


def _supported_if_1911(request: LLMRequest) -> str:
    evidence = request.messages[-1].content.split("Evidence the system was shown:\n", 1)[1]
    return f"Reason: r.\nEvidence support: {'SUPPORTED' if '1911' in evidence.split('Judge only')[0] else 'UNSUPPORTED'}"


FACT = captured([_passage("Signed in 1911.")], "t")


class TestSupportStage:
    def test_only_answers_judged_correct_are_verified(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_1911)
        ctx = _context(tmp_path, llm)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=FACT)
        _record(ctx, "rag", "1", "1911", correct=False, evidence=FACT)
        _record(ctx, "rag", "2", "1912", correct=True, evidence=captured([_passage("Signed in 1912.")], "t"))
        SupportStage().run(ctx)
        verdicts = {j.question_id: j.label for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)}
        assert verdicts == {"0": "SUPPORTED", "2": "UNSUPPORTED"}
        assert len(llm.requests) == 2

    def test_closed_book_and_unlisted_systems_are_skipped(self, tmp_path: Path) -> None:
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911), evidence_support={"systems": ["rag"]})
        _record(ctx, "closed_book", "0", "1911", correct=True, evidence=captured([], "t"))
        _record(ctx, "pipeshub", "0", "1911", correct=True, evidence=FACT)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=FACT)
        SupportStage().run(ctx)
        assert [j.system for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)] == ["rag"]

    def test_the_configured_judge_and_budget_are_used_and_changing_them_reverifies(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_1911)
        ctx = _context(tmp_path, llm)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=FACT)
        SupportStage().run(ctx)
        SupportStage().run(ctx)
        cheap = _context(tmp_path, llm, store=ctx.store, evidence_support={"judge": "secondary", "max_evidence_tokens": 800})
        SupportStage().run(cheap)
        assert [r.model.model_name for r in llm.requests] == ["judge-model", "cheap-judge"]
        verdicts = cheap.store.read(SUPPORT_FILE, SupportJudgment)
        assert [j.judge_model for j in verdicts] == ["anthropic:judge-model", "anthropic:cheap-judge"]
        assert verdicts[1].verifier.endswith(":cheap-judge:800")

    def test_a_verdict_for_a_superseded_answer_does_not_count(self, tmp_path: Path) -> None:
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911))
        _record(ctx, "rag", "0", "1911", correct=True)
        ctx.store.append("predictions.jsonl", Prediction(system="rag", question_id="0", repeat=0, answer="1911!"))
        SupportStage().run(ctx)
        assert ctx.store.read(SUPPORT_FILE, SupportJudgment) == []

    def test_resume_skips_verified_answers_and_rejudges_new_evidence(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_1911)
        ctx = _context(tmp_path, llm)
        _record(ctx, "rag", "0", "1911", correct=True)
        SupportStage().run(ctx)
        assert [j.label for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)] == ["NO_EVIDENCE"]
        assert SupportStage().run(ctx).processed == 0
        ctx.store.append_compressed(EVIDENCE_FILE, EvidenceRecord(
            system="rag", question_id="0", repeat=0, answer_sha=answer_fingerprint("1911"), evidence=FACT,
        ))
        SupportStage().run(ctx)
        assert [j.label for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)] == ["NO_EVIDENCE", "SUPPORTED"]
        assert len(llm.requests) == 1

    def test_judge_spend_counts_against_the_run_budget(self, tmp_path: Path) -> None:
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911), limit=0.0015)
        for qid in ("0", "1"):
            _record(ctx, "rag", qid, "1911", correct=True, evidence=FACT)
        with pytest.raises(CostLimitError):
            SupportStage().run(ctx)

    def test_projected_cost_is_logged_before_judging(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        pricing = {"judge-model": {"input_per_mtok": 1.0, "output_per_mtok": 4.0}}
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911), pricing=pricing, limit=0.0005)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=FACT)
        with caplog.at_level(logging.INFO, logger="benchmarks.harness.stages"), pytest.raises(CostLimitError):
            SupportStage().run(ctx)
        messages = [r.getMessage() for r in caplog.records]
        assert any("projected cost $0.00 for 1 calls" in m for m in messages)
        assert any("projection exceeds the run budget" in m for m in messages)
        assert not any("Signed in 1911" in m or "Question 0?" in m for m in messages)


class TestEvidenceStage:
    def _trace(self) -> StreamTrace:
        ref = RetrievedRecordRef(virtual_record_id="vr", record_name="Treaty", block_indices=[0])
        return StreamTrace(retrieval_events=[RetrievalEvent(seq=1, source="prefetch", status="ok", records=[ref])])

    def test_rebuilds_retries_when_the_store_returns_and_then_skips(self, tmp_path: Path) -> None:
        source = FakePoints({"vr": [_point("Signed in 1911.", block=0, is_block=True)]}, down=True)
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911), source=source)
        _record(ctx, "pipeshub", "0", "1911", correct=True, trace=self._trace())

        first = EvidenceStage().run(ctx)
        assert (first.processed, first.failed) == (1, 1)
        source.down = False
        points = RecordPointsCache(source)
        ctx.services.adapter_registry["trace"] = AdapterSpec(
            lambda _s, _d: None, AdapterCapabilities(retrieval_trace=True),  # type: ignore[arg-type,return-value]
            rebuild_evidence=lambda _s, _deps: TraceEvidenceBuilder(points),
        )
        second = EvidenceStage().run(ctx)
        assert (second.processed, second.failed) == (1, 0)
        assert EvidenceStage().run(ctx).skipped == 1

        records = list(ctx.store.iter_compressed(EVIDENCE_FILE, EvidenceRecord))
        assert [r.evidence.status for r in records] == ["unavailable", "reconstructed"]
        SupportStage().run(ctx)
        [verdict] = ctx.store.read(SUPPORT_FILE, SupportJudgment)
        assert verdict.label == "SUPPORTED" and verdict.evidence_status == "reconstructed"

    def test_evidence_recorded_at_ask_time_is_not_rebuilt(self, tmp_path: Path) -> None:
        source = FakePoints({})
        ctx = _context(tmp_path, FakeLLM(_supported_if_1911), source=source)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=FACT)
        report = EvidenceStage().run(ctx)
        assert (report.processed, report.skipped) == (0, 1) and source.calls == []


class TestSidecar:
    def test_a_torn_final_record_is_skipped(self, tmp_path: Path) -> None:
        store = RunStore(tmp_path / "run")
        record = EvidenceRecord(system="s", question_id="1", repeat=0, answer_sha="a", evidence=captured([_passage("x")], "t"))
        store.append_compressed(EVIDENCE_FILE, record)
        store.append_compressed(EVIDENCE_FILE, record.model_copy(update={"question_id": "2"}))
        path = store.path(EVIDENCE_FILE)
        path.write_bytes(path.read_bytes()[:-12])
        assert [r.question_id for r in store.iter_compressed(EVIDENCE_FILE, EvidenceRecord)] == ["1"]

    def test_records_are_gzip_jsonl(self, tmp_path: Path) -> None:
        store = RunStore(tmp_path / "run")
        store.append_compressed(EVIDENCE_FILE, EvidenceRecord(
            system="s", question_id="1", repeat=0, answer_sha="a", evidence=captured([_passage("x")], "t"),
        ))
        [line] = gzip.decompress(store.path(EVIDENCE_FILE).read_bytes()).decode().splitlines()
        assert json.loads(line)["evidence"]["passages"] == [{"header": "", "text": "x"}]


def _score(system: str, qid: str, correct: bool | None, label: str | None = None, split: str = "dev") -> QuestionScore:
    return QuestionScore(
        system=system, question_id=qid, repeat=0, split=split, gold_count=1, correct=correct,  # type: ignore[arg-type]
        support_label=label,
    )


class TestMetrics:
    def _scores(self) -> list[QuestionScore]:
        grounded = [_score("rag", str(i), True, "SUPPORTED") for i in range(6)]
        memory = [_score("rag", "6", True, "UNSUPPORTED"), _score("rag", "10", True, "UNSUPPORTED", "heldout")]
        other = [_score("rag", "7", True, "PARTIAL"), _score("rag", "8", True, "NO_EVIDENCE", "heldout"),
                 _score("rag", "9", False, split="heldout")]
        closed = [_score("closed_book", str(i), i < 5, "UNSUPPORTED" if i < 5 else None) for i in range(11)]
        return [*grounded, *memory, *other, *closed]

    def _summary(self, scores: list[QuestionScore], verified: set[str] | None = None):  # noqa: ANN202
        return summarize("run", scores, [], lambda _q: [], StatsConfig(bootstrap_samples=500), 1, verified_systems=verified)

    def test_grounded_accuracy_and_memory_suspects(self) -> None:
        rag = {s.system: s for s in self._summary(self._scores()).systems}["rag"]
        assert rag.accuracy.value == pytest.approx(10 / 11)
        assert rag.grounded_accuracy.value == pytest.approx(6 / 11)
        assert rag.grounded_accuracy.low <= rag.grounded_accuracy.value <= rag.grounded_accuracy.high
        assert rag.grounded_accuracy.high < rag.accuracy.high
        assert rag.memory_suspect_rate.value == pytest.approx(2 / 11)
        assert (rag.supported, rag.partial, rag.memory_suspect, rag.no_evidence) == (6, 1, 2, 1)
        assert rag.memory_suspect_questions == ["6", "10"]
        assert rag.support_coverage == 1.0

    def test_breakdown_by_split(self) -> None:
        rag = {s.system: s for s in self._summary(self._scores()).systems}["rag"]
        assert rag.accuracy_by_split["dev"].value == 1.0 and rag.accuracy_by_split["dev"].n == 8
        assert rag.accuracy_by_split["heldout"].value == pytest.approx(2 / 3)
        assert rag.grounded_accuracy_by_split["dev"].value == pytest.approx(6 / 8)
        assert rag.grounded_accuracy_by_split["heldout"].value == 0.0
        assert rag.memory_suspect_rate_by_split["heldout"].value == pytest.approx(1 / 3)
        report = render_report(self._summary(self._scores()))
        assert "### By split (95% CI)" in report
        assert "| heldout | FRAMES acc % |" in report and "| dev | Memory-suspect % |" in report

    def test_a_single_split_run_has_no_split_table(self) -> None:
        scores = [s for s in self._scores() if s.split == "dev"]
        assert "### By split" not in render_report(self._summary(scores))

    def test_unverified_correct_answers_withhold_the_rates(self) -> None:
        scores = [_score("rag", "0", True, "SUPPORTED"), _score("rag", "1", True, None)]
        rag = self._summary(scores).systems[0]
        assert rag.grounded_accuracy is None and rag.memory_suspect_rate is None
        assert rag.support_coverage == 0.5 and rag.supported == 1

    def test_systems_outside_the_check_get_no_grounding_numbers(self) -> None:
        board = {s.system: s for s in self._summary(self._scores(), verified={"rag"}).systems}
        closed = board["closed_book"]
        assert closed.grounded_accuracy is None and closed.support_coverage is None and closed.memory_suspect == 0
        assert closed.accuracy_by_split and not closed.grounded_accuracy_by_split

    def test_paired_test_on_grounded_correctness(self) -> None:
        summary = self._summary(self._scores())
        [plain] = summary.pairwise
        [grounded] = summary.pairwise_grounded
        assert (grounded.a, grounded.b) == ("closed_book", "rag")
        assert (grounded.a_only, grounded.b_only) == (0, 6)
        assert grounded.p_value == pytest.approx(2 * 0.5 ** 6)
        assert plain.b_only == 5

    def test_unverified_systems_are_left_out_of_grounded_pairs(self) -> None:
        scores = [*self._scores(), _score("other", "0", True, None)]
        assert {(t.a, t.b) for t in self._summary(scores).pairwise_grounded} == {("closed_book", "rag")}

    def test_report_and_summary_json_carry_the_columns(self) -> None:
        summary = self._summary(self._scores())
        report = render_report(summary)
        assert "Grounded acc % (95% CI)" in report and "| Memory-suspect |" in report
        assert "| rag | 90.9" in report and "| 54.5 (" in report and "| 2 (18.2%) |" in report
        assert "### Evidence verification" in report
        assert "- **rag**: 6, 10" in report and "- **closed_book**: 0, 1, 2, 3, 4" in report
        assert "Paired comparisons on grounded correctness" in report
        payload = json.loads(summary.model_dump_json())
        rag = next(s for s in payload["systems"] if s["system"] == "rag")
        for key in ("grounded_accuracy", "memory_suspect_rate", "memory_suspect", "partial", "no_evidence",
                    "memory_suspect_questions", "support_coverage", "accuracy_by_split",
                    "grounded_accuracy_by_split", "memory_suspect_rate_by_split"):
            assert key in rag
        assert payload["pairwise_grounded"]

    def test_a_run_never_verified_shows_no_grounded_numbers(self) -> None:
        report = render_report(self._summary([_score("rag", "0", True), _score("rag", "1", False)]))
        assert "### Evidence verification" not in report
        rag_row = next(line for line in report.splitlines() if line.startswith("| rag | 50.0"))
        assert rag_row.split(" | ")[2] == "–" and rag_row.split(" | ")[3] == "–"


def _supported_if_bridge(request: LLMRequest) -> str:
    evidence = request.messages[-1].content.split("Evidence the system was shown:\n", 1)[1].split("Judge only")[0]
    return f"Reason: r.\nEvidence support: {'SUPPORTED' if 'Ballou' in evidence else 'UNSUPPORTED'}"


# Many passages that match the question and answer, and one bridge fact that
# shares no word with either: the cut keeps the former and drops the latter.
LONG = captured(
    [_passage(f"Question 0 answer 1911, note {i}: 1911 again for question 0.") for i in range(80)]
    + [_passage("Her maiden name was Ballou.")],
    "t",
)


class TestRecheckOfCutEvidence:
    SETTINGS = {"max_evidence_tokens": 500, "recheck_evidence_tokens": 20_000}

    def test_a_cut_that_dropped_the_bridge_fact_is_rechecked_in_full(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_bridge)
        ctx = _context(tmp_path, llm, evidence_support=self.SETTINGS)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=LONG)
        SupportStage().run(ctx)

        verdicts = ctx.store.read(SUPPORT_FILE, SupportJudgment)
        assert [(j.label, j.selection.selected) for j in verdicts] == [("UNSUPPORTED", True), ("SUPPORTED", False)]
        assert verdicts[-1].verifier.endswith(":judge-model:20000")

    def test_only_cut_evidence_without_support_is_rechecked(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_1911)
        ctx = _context(tmp_path, llm, evidence_support=self.SETTINGS)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=LONG)  # cut, but SUPPORTED
        _record(ctx, "rag", "1", "1911", correct=True, evidence=captured([_passage("Signed in 1912.")], "t"))  # not cut
        SupportStage().run(ctx)

        assert len(llm.requests) == 2
        verdicts = {j.question_id: j.label for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)}
        assert verdicts == {"0": "SUPPORTED", "1": "UNSUPPORTED"}

    def test_resume_does_not_recheck_again(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_bridge)
        ctx = _context(tmp_path, llm, evidence_support=self.SETTINGS)
        _record(ctx, "rag", "0", "1911", correct=True, evidence=LONG)
        SupportStage().run(ctx)
        assert SupportStage().run(ctx).processed == 0
        assert len(llm.requests) == 2

    def test_recheck_can_be_turned_off(self, tmp_path: Path) -> None:
        llm = FakeLLM(_supported_if_bridge)
        ctx = _context(tmp_path, llm, evidence_support={"max_evidence_tokens": 500, "recheck_evidence_tokens": None})
        _record(ctx, "rag", "0", "1911", correct=True, evidence=LONG)
        SupportStage().run(ctx)
        assert [j.label for j in ctx.store.read(SUPPORT_FILE, SupportJudgment)] == ["UNSUPPORTED"]
