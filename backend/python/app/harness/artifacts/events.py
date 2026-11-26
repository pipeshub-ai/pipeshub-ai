"""The publisher's log records as PipesHub's existing artifact events, and back.

``x.pipeshub.artifact.publish_intent`` / ``published`` / ``presented`` carry
exactly a ``PublicationLog``'s records, so a durable run's log (E0: append
through the run's fenced commit, read the run's journal) is an
``EventPublicationLog``.

All three are written non-critical: the core fold does not know ``x.pipeshub``
types, and an unknown critical event would make it fail closed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from harness_core.artifacts.types import (
    ArtifactCard,
    ContentDigest,
    PublicationHistory,
    PublicationRecord,
    PublishIntent,
)
from harness_core.contracts import validate_contract
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

    from pydantic import BaseModel, JsonValue

    from harness_core.artifacts.types import ArtifactLifecycle
    from harness_core.interactions.ports import RunBinding

INTENT = "x.pipeshub.artifact.publish_intent"
PUBLISHED = "x.pipeshub.artifact.published"
PRESENTED = "x.pipeshub.artifact.presented"
LIFECYCLES: frozenset[str] = frozenset({"draft", "final", "discarded"})


class PublicationEvent(Protocol):
    """A journal event or a ``NewEvent``: what the mapping back reads."""

    @property
    def type(self) -> str: ...

    @property
    def data(self) -> Mapping[str, JsonValue]: ...


def _event(event_type: str, data: dict[str, Any]) -> NewEvent:
    model = EVENT_MODELS[event_type]
    data_model = cast("type[BaseModel]", model.model_fields["data"].annotation)
    clean = cast("dict[str, JsonValue]", json.loads(json.dumps(data)))
    _ = validate_contract(data_model, clean)
    return NewEvent(event_type, clean, critical=False)


def intent_event(intent: PublishIntent) -> NewEvent:
    return _event(
        INTENT,
        {
            "intentId": intent.intent_id,
            "artifactId": intent.artifact_id,
            "ownerScope": "session",
            "contentRef": {
                "ref": intent.content.ref,
                "hash": intent.content.sha256,
                "size": intent.content.size_bytes,
            },
        },
    )


def published_event(record: PublicationRecord) -> NewEvent:
    return _event(
        PUBLISHED,
        {"intentId": record.intent_id, "artifactId": record.artifact_id, "publishedVersion": record.version},
    )


def presented_event(card: ArtifactCard) -> NewEvent:
    data: dict[str, Any] = {
        "artifactId": card.artifact_id,
        "fileName": card.name,
        "mimeType": card.mime_type,
        "sizeBytes": card.size_bytes,
        "artifactType": card.kind,
        "isTemporary": False,
        "recordId": card.artifact_id,
        "version": card.version,
        "visibility": "VISIBLE",
        "card": True,
        "lifecycle": card.lifecycle,
    }
    if card.storage_ref:
        data["documentId"] = card.storage_ref
    return _event(PRESENTED, data)


def presented_names(events: Iterable[PublicationEvent], run_id: str) -> tuple[str, ...]:
    """The names of the files ``run_id`` showed the person as cards (``x.pipeshub.artifact.presented``), once each,
    in the order shown: what a cut-short answer says it saved (R36COVER-FU-5)."""
    names: list[str] = []
    for event in events:
        if event.type != PRESENTED or getattr(event, "run_id", run_id) != run_id:
            continue
        name = event.data.get("fileName")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return tuple(names)


def history_from_events(events: Iterable[PublicationEvent]) -> PublicationHistory:
    """The log a run's events describe; ``presented`` without ``lifecycle`` is a legacy tool's card."""
    intents: list[PublishIntent] = []
    published: list[PublicationRecord] = []
    cards: list[ArtifactCard] = []
    for event in events:
        data = event.data
        if event.type == INTENT:
            ref = cast("Mapping[str, JsonValue]", data["contentRef"])
            intents.append(
                PublishIntent(
                    str(data["intentId"]),
                    str(data["artifactId"]),
                    ContentDigest(str(ref["ref"]), str(ref["hash"]), int(cast("int", ref["size"]))),
                )
            )
        elif event.type == PUBLISHED:
            published.append(
                PublicationRecord(
                    str(data["intentId"]), str(data["artifactId"]), int(cast("int", data["publishedVersion"]))
                )
            )
        elif event.type == PRESENTED and data.get("lifecycle") in LIFECYCLES:
            cards.append(
                ArtifactCard(
                    artifact_id=str(data["artifactId"]),
                    version=int(cast("int", data["version"])),
                    name=str(data["fileName"]),
                    mime_type=str(data["mimeType"]),
                    size_bytes=int(cast("int", data["sizeBytes"])),
                    kind=str(data["artifactType"]),
                    lifecycle=cast("ArtifactLifecycle", data["lifecycle"]),
                    storage_ref=str(data["documentId"]) if data.get("documentId") else None,
                )
            )
    return PublicationHistory(tuple(intents), tuple(published), tuple(cards))


class EventPublicationLog:
    """A ``PublicationLog`` over a run's event stream.

    ``append(binding, event)`` writes one event (E0: the run's fenced commit);
    ``events(binding)`` reads the run's events back.
    """

    def __init__(
        self,
        append: Callable[[RunBinding, NewEvent], Awaitable[None]],
        events: Callable[[RunBinding], Awaitable[Sequence[PublicationEvent]]],
    ) -> None:
        self._append = append
        self._events = events

    async def intent(self, binding: RunBinding, intent: PublishIntent) -> None:
        await self._append(binding, intent_event(intent))

    async def published(self, binding: RunBinding, record: PublicationRecord) -> None:
        await self._append(binding, published_event(record))

    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None:
        await self._append(binding, presented_event(card))

    async def history(self, binding: RunBinding) -> PublicationHistory:
        return history_from_events(await self._events(binding))


__all__ = [
    "INTENT",
    "PRESENTED",
    "PUBLISHED",
    "EventPublicationLog",
    "PublicationEvent",
    "history_from_events",
    "intent_event",
    "presented_event",
    "presented_names",
    "published_event",
]
