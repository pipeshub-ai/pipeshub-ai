"""EV-1..EV-5: the publisher's records are PipesHub's existing artifact events, and back."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.harness.artifacts.events import (
    INTENT,
    PRESENTED,
    PUBLISHED,
    history_from_events,
    intent_event,
    presented_event,
    published_event,
)
from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.presentation import PipesHubPresenter
from harness_core.artifacts.types import (
    ArtifactCard,
    ContentDigest,
    PublicationHistory,
    PublicationRecord,
    PublishIntent,
    sha256_of,
)
from harness_core.journal.types import NewEvent

SCHEMAS = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "ext" / "pipeshub" / "events"
INTENT_RECORD = PublishIntent("pi_abc", "art-1", ContentDigest("sealed/3/hmac/obj-1", sha256_of(b"abc"), 3))
RECORD = PublicationRecord("pi_abc", "art-1", 2)
CARD = ArtifactCard("art-1", 2, "deck.pptx", "application/pdf", 3, "DOCUMENT", "draft", "doc-1")


def _envelope(event: NewEvent) -> dict[str, object]:
    return {
        "orgId": "org_1", "sessionId": "ses_1", "streamId": "ses_1", "seq": 7, "runId": "run_1",
        "schemaVersion": 1, "critical": event.critical, "ts": "2026-10-01T12:00:00.123Z",
        "writer": "worker", "visibility": "owner", "type": event.type, "data": event.data,
        "attemptId": "att_1", "fence": 3,
    }


def test_ev1_each_record_is_an_existing_event_that_passes_its_schema() -> None:
    events = [intent_event(INTENT_RECORD), published_event(RECORD), presented_event(CARD)]
    assert [e.type for e in events] == [INTENT, PUBLISHED, PRESENTED]
    for event in events:
        EVENT_MODELS[event.type].model_validate(_envelope(event))
        assert event.critical is False
    assert events[0].data["ownerScope"] == "session"
    assert events[0].data["contentRef"] == {"ref": "sealed/3/hmac/obj-1", "hash": sha256_of(b"abc"), "size": 3}
    assert events[2].data["lifecycle"] == "draft" and events[2].data["recordId"] == "art-1"


def test_ev2_events_map_back_to_the_history() -> None:
    final = CARD.with_lifecycle("final")
    legacy = NewEvent(PRESENTED, {"artifactId": "art-9", "fileName": "x.csv", "mimeType": "text/csv", "sizeBytes": 1,
                                  "artifactType": "SPREADSHEET", "isTemporary": False, "visibility": "VISIBLE"})
    events = [intent_event(INTENT_RECORD), published_event(RECORD), presented_event(CARD), legacy, presented_event(final)]
    assert history_from_events(events) == PublicationHistory((INTENT_RECORD,), (RECORD,), (CARD, final))


def test_ev3_lifecycle_is_optional_and_closed() -> None:
    model = EVENT_MODELS[PRESENTED]
    for lifecycle in ("draft", "final", "discarded"):
        model.model_validate(_envelope(presented_event(CARD.with_lifecycle(lifecycle))))
    without = presented_event(CARD)
    del without.data["lifecycle"]
    model.model_validate(_envelope(without))
    bad = presented_event(CARD)
    bad.data["lifecycle"] = "pending"
    with pytest.raises(ValidationError):
        model.model_validate(_envelope(bad))
    invalid = json.loads((SCHEMAS / "x.pipeshub.artifact.presented.invalid.json").read_text())
    assert any(case["description"] == "unknown lifecycle" for case in invalid)


def test_ev4_markers_once_per_version_and_none_for_discarded_versions() -> None:
    created = ArtifactCard("art-new", 1, "new.md", "text/markdown", 1, "DOCUMENT", "draft")
    edited = ArtifactCard("art-old", 2, "old.md", "text/markdown", 1, "DOCUMENT", "draft")
    restored = ArtifactCard("art-old", 3, "old.md", "text/markdown", 1, "DOCUMENT", "final")
    kept = ArtifactCard("art-kept", 1, "kept.md", "text/markdown", 1, "DOCUMENT", "draft")
    events = [
        presented_event(c)
        for c in (
            created, edited, kept, kept.with_lifecycle("final"),
            created.with_lifecycle("discarded"), edited.with_lifecycle("discarded"), restored,
        )
    ]
    (task,) = PipesHubPresenter._delivered(SimpleNamespace(events=events))  # type: ignore[arg-type]
    assert [(a["recordId"], a["version"]) for a in task["artifacts"]] == [("art-kept", 1), ("art-old", 3)]


def test_ev5_no_new_event_types() -> None:
    artifact_types = sorted(t for t in EVENT_MODELS if t.startswith("x.pipeshub.artifact."))
    assert artifact_types == [PRESENTED, INTENT, PUBLISHED]
