"""Artifact publication value types (ARCHITECTURE §4.7, §5.5).

A run's deliverable files become artifacts through an ``ArtifactPublisher``.
The records a ``PublicationLog`` keeps are exactly what the host's three
publication events carry (intent, published, presented), so a journal-backed
log is a mapping of those events and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from harness_core.workspace.types import normalize_workspace_file_path

if TYPE_CHECKING:
    from harness_core.workspace.types import ObjectRef

ArtifactLifecycle = Literal["draft", "final", "discarded"]
PublishOutcome = Literal["created", "versioned", "unchanged"]

# Bounds one version's provenance document; above it the sources are recorded
# as unknown, which hides the artifact from other readers (fail closed).
MAX_PROVENANCE_SOURCES = 2000


@dataclass(frozen=True, slots=True)
class ArtifactVersionRef:
    artifact_id: str
    version: int

    def __post_init__(self) -> None:
        if not self.artifact_id or self.version < 1:
            raise ValueError("Artifact version reference is incomplete")


@dataclass(frozen=True, slots=True)
class OutputFile:
    """A deliverable file of the workspace.

    ``path`` is relative to the deliverables folder (the caller strips its
    ``out/`` prefix) and is the artifact's name within the session.
    ``edit_of`` is where the file came from when it was copied in for editing.
    """

    path: str
    content: ObjectRef
    mime_type: str = "application/octet-stream"
    edit_of: ArtifactVersionRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_workspace_file_path(self.path))


@dataclass(frozen=True, slots=True)
class Derivation:
    """How a publish's files were made.

    ``source_records`` are the records every tool call of the session's runs
    returned to the model before this workspace version (read, not only
    cited); ``None`` when that is not known, which hides the artifacts from
    other readers.
    """

    task_id: str
    command: str | None = None
    input_paths: tuple[str, ...] = ()
    input_artifacts: tuple[ArtifactVersionRef, ...] = ()
    source_records: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    """What one artifact version records about how it was made."""

    run_id: str
    task_id: str
    workspace_id: str | None
    workspace_revision: int | None
    command: str | None
    input_paths: tuple[str, ...]
    input_artifacts: tuple[ArtifactVersionRef, ...]
    source_records: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class ContentDigest:
    """The bytes an intent publishes: the workspace object and its plain SHA-256."""

    ref: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PublishIntent:
    intent_id: str
    artifact_id: str
    content: ContentDigest


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    intent_id: str
    artifact_id: str
    version: int


@dataclass(frozen=True, slots=True)
class ArtifactCard:
    """What the user is shown for one artifact version; ``kind`` is the host's type label."""

    artifact_id: str
    version: int
    name: str
    mime_type: str
    size_bytes: int
    kind: str
    lifecycle: ArtifactLifecycle
    storage_ref: str | None = None

    def with_lifecycle(self, lifecycle: ArtifactLifecycle) -> ArtifactCard:
        return replace(self, lifecycle=lifecycle)


@dataclass(frozen=True, slots=True)
class PublicationHistory:
    """Everything one run logged, in order."""

    intents: tuple[PublishIntent, ...] = ()
    published: tuple[PublicationRecord, ...] = ()
    cards: tuple[ArtifactCard, ...] = ()

    def intent(self, intent_id: str) -> PublishIntent | None:
        return next((i for i in self.intents if i.intent_id == intent_id), None)

    def publication(self, intent_id: str) -> PublicationRecord | None:
        return next((p for p in self.published if p.intent_id == intent_id), None)

    def shown(self, artifact_id: str, version: int) -> bool:
        return any(c.artifact_id == artifact_id and c.version == version for c in self.cards)

    def last_written(self, name: str) -> ArtifactVersionRef | None:
        """The artifact and version this run last wrote under ``name``."""
        for card in reversed(self.cards):
            if card.name == name and card.lifecycle != "discarded":
                return ArtifactVersionRef(card.artifact_id, card.version)
        return None

    def open_drafts(self) -> list[DraftRun]:
        """Per artifact whose last card is a draft: the trailing drafts since its last final card."""
        by_artifact: dict[str, list[ArtifactCard]] = {}
        for card in self.cards:
            by_artifact.setdefault(card.artifact_id, []).append(card)
        drafts: list[DraftRun] = []
        for cards in by_artifact.values():
            trailing: list[ArtifactCard] = []
            for card in reversed(cards):
                if card.lifecycle != "draft":
                    break
                trailing.append(card)
            if trailing:
                trailing.reverse()
                drafts.append(DraftRun(first=trailing[0], last=trailing[-1]))
        return drafts


@dataclass(frozen=True, slots=True)
class DraftRun:
    """A run's consecutive draft versions of one artifact."""

    first: ArtifactCard
    last: ArtifactCard

    @property
    def created_by_run(self) -> bool:
        return self.first.version == 1


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    intent_id: str
    path: str
    artifact_id: str
    version: int
    outcome: PublishOutcome
    lifecycle: ArtifactLifecycle


@dataclass(frozen=True, slots=True)
class PublishRefusal:
    path: str
    reason_code: str
    artifact_id: str | None = None
    expected_version: int | None = None
    current_version: int | None = None


@dataclass(frozen=True, slots=True)
class PublishReport:
    published: tuple[PublishedArtifact, ...] = ()
    refused: tuple[PublishRefusal, ...] = ()


@dataclass(frozen=True, slots=True)
class SettleReport:
    """What ``finalize`` or ``discard_drafts`` changed."""

    settled: tuple[ArtifactCard, ...] = ()
    refused: tuple[PublishRefusal, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """The store's current state of one artifact."""

    artifact_id: str
    name: str
    version: int
    sha256: str
    mime_type: str
    size_bytes: int
    kind: str
    lifecycle: ArtifactLifecycle
    storage_ref: str | None = None


def publish_intent_id(run_id: str, path: str, content_hash: str) -> str:
    """Deterministic per (run, path, content): a retried or re-driven publish finds its intent."""
    digest = hashlib.sha256(
        json.dumps([run_id, path, content_hash], separators=(",", ":")).encode()
    ).hexdigest()
    return f"pi_{digest[:40]}"


def artifact_id_for(intent_id: str) -> str:
    """The id a new artifact gets, derived from its intent so a retry finds the artifact it made."""
    return str(uuid.UUID(bytes=hashlib.sha256(intent_id.encode()).digest()[:16], version=4))


def sha256_of(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def union_sources(*sources: frozenset[str] | None) -> frozenset[str] | None:
    """Unknown wins; above ``MAX_PROVENANCE_SOURCES`` the union is unknown too."""
    collected: set[str] = set()
    for part in sources:
        if part is None:
            return None
        collected |= part
    return frozenset(collected) if len(collected) <= MAX_PROVENANCE_SOURCES else None


__all__ = [
    "MAX_PROVENANCE_SOURCES",
    "ArtifactCard",
    "ArtifactLifecycle",
    "ArtifactVersionRef",
    "ContentDigest",
    "Derivation",
    "DraftRun",
    "OutputFile",
    "Provenance",
    "PublicationHistory",
    "PublicationRecord",
    "PublishIntent",
    "PublishOutcome",
    "PublishRefusal",
    "PublishReport",
    "PublishedArtifact",
    "SettleReport",
    "StoredArtifact",
    "artifact_id_for",
    "publish_intent_id",
    "sha256_of",
    "union_sources",
]
