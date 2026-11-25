"""P2.5a LB-2, LB-3: a PipesHub run's own-work label, from its journal, and where the worker pins it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_answer_finalized import (
    PipeshubAnswerFinalizedData,
)
from app.harness.labels import (
    LabelSource,
    classify_record,
    label_from_wire,
    label_wire,
    own_work_label,
    tool_kind,
    unknown_label,
)
from harness_core.contracts import validate_contract
from harness_core.journal.types import JournalEvent
from harness_core.kernel.fold import invocation_id
from harness_core.security.labels import label_of
from tests.contracts.golden_support import Scenario
from tests.contracts.scenarios import SCENARIOS
from tests.contracts.test_durable_goldens import run_durably

if TYPE_CHECKING:
    from pydantic import JsonValue

    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

RUN = "run-1"
DIGEST = "sha256:" + "a" * 64
REF: dict[str, JsonValue] = {"ref": "cas/m", "hash": DIGEST, "size": 1}


class _Run:
    def __init__(self, run_id: str = RUN) -> None:
        self.run_id = run_id
        self.events: list[JournalEvent] = []
        self.turn = 0

    def _add(self, event_type: str, data: dict[str, JsonValue], *, writer: str = "worker") -> None:
        self.events.append(
            JournalEvent(
                org_id="o",
                session_id="s",
                stream_id="s",
                seq=len(self.events) + 1,
                run_id=self.run_id,
                type=event_type,
                critical=not event_type.startswith("x."),
                ts="2026-10-04T00:00:00Z",
                writer=writer,  # type: ignore[arg-type]
                data=data,
            )
        )

    def cited(self, **delta: Any) -> _Run:  # noqa: ANN401
        self._add("x.pipeshub.citations.recorded", delta)
        return self

    def calls(self, *names: str, status: str = "ok", child: bool = False) -> list[str]:
        """One model turn calling ``names``; every call gets its outcome."""
        self.turn += 1
        self._add("turn.started", {"turn": self.turn})
        self._add(
            "model.response",
            {
                "messageRef": REF,
                "toolCalls": [{"callId": f"c{i}", "modelName": n, "argsDigest": DIGEST} for i, n in enumerate(names)],
                "usage": {"inputTokens": 1, "outputTokens": 1},
                "provider": "p",
                "transport": "t",
            },
        )
        invocations = [invocation_id(self.turn, i) for i in range(len(names))]
        for inv in invocations:
            if child:
                self._add("child.spawned", {"childRunId": f"child-{inv}", "invocationId": inv, "grantDigest": DIGEST}, writer="node")
            self._add("tool.outcome", {"invocationId": inv, "status": status, "labels": {"integrity": "internal"}})
        return invocations

    def denied(self, name: str) -> None:
        self.turn += 1
        self._add("turn.started", {"turn": self.turn})
        self._add(
            "model.response",
            {
                "messageRef": REF,
                "toolCalls": [{"callId": "d", "modelName": name, "argsDigest": DIGEST}],
                "usage": {"inputTokens": 1, "outputTokens": 1},
                "provider": "p",
                "transport": "t",
            },
        )
        inv = invocation_id(self.turn, 0)
        self._add("tool.denied", {"invocationId": inv, "rule": "policy"})
        self._add("tool.outcome", {"invocationId": inv, "status": "error", "labels": {"integrity": "trusted"}})

    def label(self) -> Any:  # noqa: ANN401
        return own_work_label(self.events, self.run_id)


SEARCH = "knowledgegraph__search"
INDEXED = LabelSource("indexed", record_id="rec-1")


# ---- LB-2: what the label names ----


def test_lb2_a_knowledge_search_names_the_records_it_put_in_the_citation_state() -> None:
    run = _Run()
    run.calls(SEARCH)
    run.cited(records={"vr-1": {"id": "rec-1", "connector_name": "CONFLUENCE"}, "vr-2": {"_key": "rec-2"}})
    assert run.label() == label_of({INDEXED, LabelSource("indexed", record_id="rec-2")})


def test_lb2_every_kind_of_source_is_classified_as_c18_classifies_it() -> None:
    assert classify_record({"id": "rec-a", "connector_name": "ATTACHMENTS"}) == LabelSource("attachment", record_id="rec-a")
    assert classify_record({"recordId": "rec-g", "recordType": "ARTIFACT"}) == LabelSource("generated", record_id="rec-g")
    assert classify_record({"id": "rec-c", "connector_name": "CODING_SANDBOX"}) == LabelSource("generated", record_id="rec-c")
    assert classify_record(
        {"id": "live:abc", "connector_name": "SHAREPOINT ONLINE", "connector_id": "c1", "external_record_id": "01AB"}
    ) == LabelSource("live", connector="SHAREPOINT ONLINE", connector_id="c1", external_record_id="01AB")
    assert classify_record({"origin": "WEB_SEARCH", "id": "w"}) == "web"
    assert classify_record({"sourceKind": "live", "recordId": "live:x"}) is None
    assert classify_record({"recordName": "no id"}) is None


def test_lb2_web_records_are_public_and_never_named() -> None:
    run = _Run()
    run.calls(SEARCH)
    run.cited(webRecords=[{"url": "https://example.com"}], finalResults=[{"metadata": {"origin": "WEB_SEARCH"}}])
    assert run.label() == label_of(())


def test_lb2_blocks_tool_records_and_surfaced_ids_are_named_too() -> None:
    run = _Run()
    run.calls(SEARCH)
    run.cited(
        finalResults=[{"metadata": {"recordId": "rec-b", "virtualRecordId": "vr-b", "connector": "JIRA"}}],
        toolRecords=[{"id": "rec-t"}],
        knownRecordIds=["rec-k"],
    )
    assert {s.record_id for s in run.label().sources} == {"rec-b", "rec-t", "rec-k"}


def test_lb2_a_record_that_names_nothing_checkable_makes_it_incomplete() -> None:
    run = _Run()
    run.calls(SEARCH)
    run.cited(records={"vr-1": {"recordName": "untitled"}})
    assert run.label() == unknown_label()


def test_lb2_the_pre_run_s_attachments_are_sources() -> None:
    run = _Run().cited(records={"vr-a": {"id": "rec-up", "connector_name": "ATTACHMENTS"}})
    assert run.label() == label_of({LabelSource("attachment", record_id="rec-up")})


@pytest.mark.parametrize("name", ["internaltools__ask_user_question", "create_plan", "search_tools", "fetch_tools"])
def test_lb2_a_tool_whose_results_carry_no_record_content_keeps_it_complete(name: str) -> None:
    run = _Run()
    run.calls(name)
    assert run.label() == label_of(())


@pytest.mark.parametrize("name", ["jira__get_issue", "goldendrive__read_file", "mcp_x__anything", "web_fetch"])
def test_lb2_any_other_tool_outcome_makes_it_incomplete(name: str) -> None:
    run = _Run()
    run.calls(SEARCH, name)
    run.cited(records={"vr-1": {"id": "rec-1"}})
    assert run.label() == unknown_label()


def test_lb2_refusals_cancellations_and_children_name_nothing_themselves() -> None:
    run = _Run()
    run.denied("jira__delete_issue")
    run.calls("jira__get_issue", status="cancelled")
    run.calls("spawn_agent", child=True)
    assert run.label() == label_of(())


def test_lb2_imported_history_makes_it_incomplete() -> None:
    run = _Run()
    run._add("session.imported", {"upToMessageSeq": 2, "transcriptDigest": DIGEST}, writer="node")
    assert run.label() == unknown_label()


def test_lb2_another_run_s_events_are_not_this_run_s() -> None:
    mine, theirs = _Run(), _Run("run-2")
    theirs.calls("jira__get_issue")
    mine.calls(SEARCH)
    mine.cited(records={"vr-1": {"id": "rec-1"}})
    assert own_work_label([*theirs.events, *mine.events], RUN) == label_of({INDEXED})


def test_lb2_an_engine_s_gateway_call_is_named_by_its_tool_id() -> None:
    run = _Run()
    run._add("tool.intent", {"invocationId": "g1", "toolId": "pipeshub@agents/knowledgegraph-search", "argsDigest": DIGEST,
                             "effect": "read", "sink": {"egress": "none", "argsLeaveBoundary": False}})
    run._add("tool.outcome", {"invocationId": "g1", "status": "ok", "labels": {"integrity": "internal"}})
    assert run.label() == unknown_label()
    named = own_work_label(run.events, RUN, tool_names={"pipeshub@agents/knowledgegraph-search": SEARCH})
    assert named == label_of(())
    assert tool_kind("pipeshub@agents/" + SEARCH) == "records"


def _recalled(run: _Run, *labels: JsonValue) -> _Run:
    entries: list[JsonValue] = [
        {"memoryId": f"mem_{i}", "scope": "user", "kind": "fact", "label": label} for i, label in enumerate(labels)
    ]
    run._add("x.pipeshub.memory.recalled", {"entries": entries, "chars": 10, "truncated": False})
    return run


def test_py7_a_recalled_memory_s_sources_are_the_run_s_sources() -> None:
    run = _recalled(_Run(), {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}]})
    assert run.label() == label_of({INDEXED})


def test_py7_a_recalled_memory_with_an_unknown_label_makes_it_incomplete() -> None:
    assert _recalled(_Run(), {"complete": False, "sources": []}).label() == unknown_label()
    assert _recalled(_Run(), "not a label").label() == unknown_label()


def test_py7_remember_carries_no_record_content() -> None:
    run = _Run()
    run.calls("remember")
    assert tool_kind("remember") == "none"
    assert run.label() == label_of(())


def test_lb2_the_wire_form_round_trips_and_is_schema_valid() -> None:
    label = label_of({INDEXED, LabelSource("live", connector="GOOGLE_DRIVE", external_record_id="f1")})
    wire = label_wire(label)
    _ = validate_contract(
        PipeshubAnswerFinalizedData,
        {"outcome": "answered", "answer": "", "partTexts": [], "citations": [], "label": wire},
    )
    assert label_from_wire(wire) == label
    assert label_from_wire({"complete": True, "sources": [{"kind": "web"}]}) is None
    assert label_from_wire(None) is None


# ---- LB-3: the worker pins the own-work label on x.pipeshub.answer.finalized ----


def _scenario(name: str) -> Scenario:
    return next(s for s in SCENARIOS if s.name == name)


async def _finalized(name: str) -> dict[str, JsonValue]:
    events, _ = await run_durably(_scenario(name))
    (answer,) = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    return answer.data


async def test_lb3_a_chat_answer_from_retrieval_is_labelled_with_its_records() -> None:
    data = await _finalized("chat_answer_with_citations")
    label = label_from_wire(data["label"])
    assert label is not None and label.complete
    assert label.sources and all(s.kind != "live" for s in label.sources)
    cited = {c["metadata"]["recordId"] for c in data["citations"]}  # type: ignore[index]
    assert cited <= {s.record_id for s in label.sources}


@pytest.mark.parametrize("name", ["agent_quick_tool_call", "agent_live_file_citation"])
async def test_lb3_an_answer_built_with_a_connector_tool_is_incomplete(name: str) -> None:
    label = (await _finalized(name))["label"]
    assert isinstance(label, dict)
    assert {k: v for k, v in label.items() if k != "integrity"} == {"complete": False, "sources": []}
    # SEC-B-05: every answer says its integrity, so Node's memory settlement never has to guess.
    assert label["integrity"] in ("first_party", "external")


def _server_error(transport: ScriptedTransport) -> None:
    _ = transport.add_error(Exception("Error code: 500 - {'error': {'message': 'internal server error'}}"))


@pytest.mark.parametrize("name", ["agent_ask_user_question", "agent_pre_run_clarification", "agent_llm_error"])
async def test_lb3_every_ending_carries_a_label(name: str) -> None:
    scenario = (
        Scenario("agent_llm_error", "agent", "quick", "Hi there", _server_error)
        if name == "agent_llm_error"
        else _scenario(name)
    )
    events, _ = await run_durably(scenario)
    (answer,) = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert label_from_wire(answer.data["label"]) is not None
    assert answer.data["label"]["integrity"] in ("first_party", "external")  # type: ignore[index,call-overload]
