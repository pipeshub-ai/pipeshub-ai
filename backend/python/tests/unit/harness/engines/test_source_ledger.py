"""P2.5a EA-1, EA-3, EA-4, EA-5: PipesHub's session ledger for engine artifacts.

An engine run's files may hold anything its session read before (P3.5's rule):
the ledger joins every run's recorded label, its in-progress ancestors' and its
own work, and answers record ids only when nothing unknown reached it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.labels import (
    ANSWER_EVENT,
    SessionSourceLedger,
    label_from_wire,
    label_wire,
    own_work_label,
)
from app.services.artifact_registry.viewer_access import ArtifactViewerAccess
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.testing.contracts.source_ledger import (
    EARLIER,
    ENGINE,
    PARENT,
    LedgerWorld,
    SourceLedgerContract,
    Stream,
)
from tests.unit.services.artifact_registry.test_viewer_access import (
    VIEWER,
    Graph,
    KnownProvenance,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

DIGEST = "sha256:" + "a" * 64
SEARCH_ID = "pipeshub@agents/knowledgegraph__search"
JIRA_ID = "pipeshub@jira/jira__get_issue"
WEB_ID = "pipeshub@web/dynamic__fetch_url"
TOOL_NAMES = {SEARCH_ID: "knowledgegraph__search", JIRA_ID: "jira__get_issue", WEB_ID: "dynamic__fetch_url"}


def _intent(stream: Stream, run_id: str, invocation: str, tool_id: str) -> None:
    _ = stream.add(
        "tool.intent",
        {
            "invocationId": invocation,
            "toolId": tool_id,
            "argsDigest": DIGEST,
            "effect": "read",
            "sink": cast("JsonValue", {"egress": "none", "argsLeaveBoundary": False}),
        },
        run_id=run_id,
    )


def _outcome(stream: Stream, run_id: str, invocation: str, status: str = "ok") -> None:
    _ = stream.add(
        "tool.outcome",
        {"invocationId": invocation, "status": status, "labels": {"integrity": "internal"}},
        run_id=run_id,
    )


def _records(stream: Stream, run_id: str, invocation: str, records: dict[str, dict[str, str]]) -> None:
    _ = stream.add(
        "x.pipeshub.citations.recorded",
        {"callId": invocation, "records": cast("JsonValue", records)},
        run_id=run_id,
    )


class PipesHubLedgerWorld:
    def __init__(self) -> None:
        self._ledger = SessionSourceLedger(tool_names=TOOL_NAMES)

    @property
    def ledger(self) -> SessionSourceLedger:
        return self._ledger

    def recorded(self, stream: Stream, run_id: str, invocation_id: str, record_ids: Sequence[str]) -> None:
        _intent(stream, run_id, invocation_id, SEARCH_ID)
        _outcome(stream, run_id, invocation_id)
        _records(stream, run_id, invocation_id, {f"vr-{r}": {"id": r} for r in record_ids})

    def unrecorded(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        _intent(stream, run_id, invocation_id, JIRA_ID)
        _outcome(stream, run_id, invocation_id)

    def browsed(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        """A web page fetched: public content (FG2)."""
        _intent(stream, run_id, invocation_id, WEB_ID)
        _outcome(stream, run_id, invocation_id)

    def refused(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        _ = stream.add("tool.denied", {"invocationId": invocation_id, "rule": "policy"}, run_id=run_id)
        _outcome(stream, run_id, invocation_id, status="error")

    def ended(self, stream: Stream, run_id: str) -> None:
        answered(stream, run_id, label_wire(own_work_label(stream.events, run_id, tool_names=TOOL_NAMES)))


def answered(stream: Stream, run_id: str, label: dict[str, JsonValue] | None) -> None:
    data: dict[str, JsonValue] = {"outcome": "answered", "answer": "a", "partTexts": ["a"], "citations": []}
    if label is not None:
        data["label"] = label
    _ = stream.add(ANSWER_EVENT, data, run_id=run_id)
    _ = stream.add("run.completed", {"outcome": "answered"}, run_id=run_id, writer="node")


class TestSessionSourceLedger(SourceLedgerContract):
    """EA-5: the PipesHub adapter passes the port's contract."""

    @pytest.fixture
    def ledger_world(self) -> LedgerWorld:
        return PipesHubLedgerWorld()


def _session(*build: Any) -> tuple[SessionSourceLedger, Stream]:  # noqa: ANN401
    world = PipesHubLedgerWorld()
    stream = Stream()
    stream.admitted(EARLIER)
    world.recorded(stream, EARLIER, "inv-e", ["rec-earlier"])
    for step in build:
        step(world, stream)
    stream.admitted(PARENT)
    stream.admitted(ENGINE, parent=PARENT)
    world.recorded(stream, ENGINE, "inv-1", ["rec-1"])
    world.ledger.apply(stream.events)
    return world.ledger, stream


def test_ea1_a_finished_run_counts_by_the_label_its_answer_recorded() -> None:
    ledger, _ = _session(lambda w, s: w.ended(s, EARLIER))
    assert ledger.source_records(ENGINE) == frozenset({"rec-earlier", "rec-1"})


def test_ea1_a_finished_run_without_a_label_makes_it_unknown() -> None:
    ledger, _ = _session(lambda w, s: answered(s, EARLIER, None))
    assert ledger.source_records(ENGINE) is None
    assert not ledger.label_for(ENGINE).complete


def test_ea1_a_run_still_going_that_is_not_an_ancestor_makes_it_unknown() -> None:
    ledger, _ = _session()
    assert ledger.source_records(ENGINE) is None


def test_ea1_an_incomplete_recorded_label_makes_it_unknown() -> None:
    ledger, _ = _session(lambda w, s: answered(s, EARLIER, {"complete": False, "sources": []}))
    assert ledger.source_records(ENGINE) is None


def test_ea1_a_live_source_cannot_be_a_record_id_so_the_files_stay_hidden() -> None:
    live = {"kind": "live", "connector": "GOOGLE_DRIVE", "externalRecordId": "f1"}
    ledger, _ = _session(lambda w, s: answered(s, EARLIER, {"complete": True, "sources": [live]}))
    assert ledger.source_records(ENGINE) is None
    assert ledger.label_for(ENGINE).complete


def test_ea1_an_opaque_gateway_call_of_the_engine_makes_it_unknown() -> None:
    ledger, stream = _session(lambda w, s: w.ended(s, EARLIER))
    assert ledger.source_records(ENGINE) is not None
    _intent(stream, ENGINE, "inv-j", JIRA_ID)
    _outcome(stream, ENGINE, "inv-j")
    ledger.apply(stream.events)
    assert ledger.source_records(ENGINE) is None


def test_ea1_a_child_run_that_cannot_see_its_parent_is_unknown() -> None:
    """A durable engine child reads only its own stream: the goal its parent wrote came from work it never sees."""
    world = PipesHubLedgerWorld()
    stream = Stream()
    stream.admitted(ENGINE, parent=PARENT)
    world.recorded(stream, ENGINE, "inv-1", ["rec-1"])
    world.ledger.apply(stream.events)
    assert world.ledger.source_records(ENGINE) is None


def test_ea3_the_engine_answer_carries_the_ledgers_label() -> None:
    ledger, _ = _session(lambda w, s: w.ended(s, EARLIER))
    presenter = PipesHubEngineRunPresenter(label=lambda: label_wire(ledger.label_for(ENGINE)))
    events = presenter.finished(ProgressStamp(ENGINE, "t1", lambda: 1), success=True, reason=None, output="done")
    (answer,) = [e for e in events if e.type == ANSWER_EVENT]
    model = EVENT_MODELS[ANSWER_EVENT]
    _ = cast("Any", model).model_fields["data"].annotation.model_validate(answer.data)
    assert EVENT_WRITERS[ANSWER_EVENT] == frozenset({"worker"})
    label = label_from_wire(answer.data["label"])
    assert label is not None and {s.record_id for s in label.sources} == {"rec-earlier", "rec-1"}


@pytest.mark.asyncio
async def test_ea4_the_engines_files_show_to_a_viewer_who_can_read_every_source() -> None:
    def kb_open(w: PipesHubLedgerWorld, s: Stream) -> None:
        answered(s, EARLIER, {"complete": True, "sources": [{"kind": "indexed", "recordId": "kb-open"}]})

    readable, _ = _session(kb_open)
    graph = Graph()
    graph.records["rec-1"] = graph.records["kb-open"]
    graph.acl.add(("u-viewer", "rec-1"))
    access = ArtifactViewerAccess(graph, KnownProvenance({"art-1": readable.source_records(ENGINE)}))
    assert await access.decide(VIEWER, ["art-1"]) == {"art-1": "readable"}

    def kb_closed(w: PipesHubLedgerWorld, s: Stream) -> None:
        answered(s, EARLIER, {"complete": True, "sources": [{"kind": "indexed", "recordId": "kb-closed"}]})

    hidden, _ = _session(kb_closed)
    access = ArtifactViewerAccess(Graph(), KnownProvenance({"art-1": hidden.source_records(ENGINE)}))
    assert await access.decide(VIEWER, ["art-1"]) == {"art-1": "denied"}
