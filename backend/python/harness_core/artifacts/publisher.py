"""``WorkspaceArtifactPublisher``: workspace files to versioned artifacts, once each (ARCHITECTURE §5.5).

Per output: an intent keyed by (run, path, content hash) is logged before any
registry write, so a retried call or a re-driven turn finds it and never
publishes twice. A caller that commits the bytes somewhere first (a workspace
save) logs the intent before that commit (``prepare``), so a crash between the
commit and the publication leaves an intent ``publish_pending`` completes. A file the workspace remembers (``edit_of``) or this run
already published under the same path is an edit, pinned to the version it
started from: a moved artifact is refused, never overwritten. Anything else
is new to the session and goes through the store's ``register_output``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from harness_core.artifacts.errors import (
    ArtifactPublishRefused,
    ArtifactVersionConflict,
)
from harness_core.artifacts.types import (
    ArtifactCard,
    ArtifactVersionRef,
    ContentDigest,
    Provenance,
    PublicationHistory,
    PublicationRecord,
    PublishedArtifact,
    PublishIntent,
    PublishRefusal,
    PublishReport,
    SettleReport,
    artifact_id_for,
    publish_intent_id,
    sha256_of,
    union_sources,
)
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from harness_core.artifacts.ports import ArtifactStore, PublicationLog
    from harness_core.artifacts.types import (
        ArtifactLifecycle,
        Derivation,
        DraftRun,
        OutputFile,
        PublishOutcome,
        StoredArtifact,
    )
    from harness_core.interactions.ports import RunBinding
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.types import ObjectScope, WorkspaceManifest


@dataclass
class _Ledger:
    """The run's log plus what this call has added to it, so later outputs see earlier ones."""

    log: PublicationLog
    binding: RunBinding
    history: PublicationHistory = field(default_factory=PublicationHistory)

    async def intent(self, intent: PublishIntent) -> None:
        await self.log.intent(self.binding, intent)
        self.history = replace(self.history, intents=(*self.history.intents, intent))

    async def published(self, record: PublicationRecord) -> None:
        await self.log.published(self.binding, record)
        self.history = replace(self.history, published=(*self.history.published, record))

    async def presented(self, card: ArtifactCard) -> None:
        await self.log.presented(self.binding, card)
        self.history = replace(self.history, cards=(*self.history.cards, card))


def _card(stored: StoredArtifact, lifecycle: ArtifactLifecycle) -> ArtifactCard:
    return ArtifactCard(
        artifact_id=stored.artifact_id,
        version=stored.version,
        name=stored.name,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        kind=stored.kind,
        lifecycle=lifecycle,
        storage_ref=stored.storage_ref,
    )


class WorkspaceArtifactPublisher:
    def __init__(
        self,
        store: ArtifactStore,
        log: PublicationLog,
        objects: BinaryObjectStore,
        *,
        object_scope: Callable[[RunBinding], ObjectScope],
        harness_log: HarnessLog | None = None,
    ) -> None:
        """``object_scope``: where the run's workspace objects live (the session's partition)."""
        self._store = store
        self._log = log
        self._objects = objects
        self._object_scope = object_scope
        self._harness_log = harness_log or HarnessLog.named("harness_core.artifacts")
        # Intents this publisher logged ahead of the bytes' commit (``prepare``), with the bytes read for
        # them: their publication follows in this process and is not a retry after a crash.
        self._prepared: dict[str, bytes] = {}

    async def publish_changes(
        self,
        binding: RunBinding,
        version: WorkspaceManifest,
        outputs: Sequence[OutputFile],
        *,
        draft: bool,
        derivation: Derivation,
    ) -> PublishReport:
        log = self._bound(binding)
        ledger = _Ledger(self._log, binding, await self._log.history(binding))
        sources = await self._sources(binding, derivation)
        published: list[PublishedArtifact] = []
        refused: list[PublishRefusal] = []
        for output in outputs:
            intent_id = publish_intent_id(binding.run_id, output.path, output.content.content_hash)
            try:
                result = await self._publish_one(
                    ledger, version, output, intent_id, draft=draft, derivation=derivation, sources=sources
                )
            except ArtifactPublishRefused as exc:
                refusal = _refusal(output, exc)
                refused.append(refusal)
                log.warning(
                    "artifacts.publish_refused",
                    reason=exc.reason_code,
                    intentId=intent_id,
                    artifactId=refusal.artifact_id,
                )
                continue
            published.append(result)
            log.info(
                "artifacts.published",
                intentId=intent_id,
                artifactId=result.artifact_id,
                version=result.version,
                outcome=result.outcome,
                lifecycle=result.lifecycle,
            )
        return PublishReport(tuple(published), tuple(refused))

    async def prepare(self, binding: RunBinding, outputs: Sequence[OutputFile]) -> None:
        if not outputs:
            return
        ledger = _Ledger(self._log, binding, await self._log.history(binding))
        for output in outputs:
            intent_id = publish_intent_id(binding.run_id, output.path, output.content.content_hash)
            if ledger.history.intent(intent_id) is not None:
                continue
            content = await self._objects.get(self._object_scope(binding), output.content)
            await ledger.intent(await self._new_intent(ledger, output, intent_id, content))
            self._prepared[intent_id] = content

    async def publish_pending(
        self,
        binding: RunBinding,
        version: WorkspaceManifest,
        outputs: Sequence[OutputFile],
        *,
        draft: bool,
        derivation: Derivation,
    ) -> PublishReport:
        history = await self._log.history(binding)
        pending = [
            output for output in outputs
            if (intent_id := publish_intent_id(binding.run_id, output.path, output.content.content_hash))
            and history.intent(intent_id) is not None
            and history.publication(intent_id) is None
        ]
        if not pending:
            return PublishReport((), ())
        self._bound(binding).info("artifacts.publish_pending", outputs=len(pending))
        return await self.publish_changes(binding, version, pending, draft=draft, derivation=derivation)

    async def finalize(self, binding: RunBinding, *, keep_drafts: frozenset[str] = frozenset()) -> SettleReport:
        history = await self._log.history(binding)
        settled: list[ArtifactCard] = []
        for run in history.open_drafts():
            if run.last.name in keep_drafts:
                continue
            current = await self._current_or_none(binding, run.last.artifact_id)
            if current is not None and current.version == run.last.version:
                await self._store.set_lifecycle(binding, current.artifact_id, "final")
            card = run.last.with_lifecycle("final")
            await self._log.presented(binding, card)
            settled.append(card)
        self._bound(binding).info("artifacts.drafts_settled", action="finalize", settled=len(settled), refused=0)
        return SettleReport(tuple(settled))

    async def discard_drafts(self, binding: RunBinding) -> SettleReport:
        log = self._bound(binding)
        history = await self._log.history(binding)
        settled: list[ArtifactCard] = []
        refused: list[PublishRefusal] = []
        for run in history.open_drafts():
            try:
                settled.extend(await self._discard(binding, run))
            except ArtifactPublishRefused as exc:
                refusal = PublishRefusal(
                    path=run.last.name,
                    reason_code=exc.reason_code,
                    artifact_id=run.last.artifact_id,
                    expected_version=run.last.version,
                    current_version=exc.current if isinstance(exc, ArtifactVersionConflict) else None,
                )
                refused.append(refusal)
                log.warning("artifacts.discard_refused", reason=exc.reason_code, artifactId=run.last.artifact_id)
        log.info("artifacts.drafts_settled", action="discard", settled=len(settled), refused=len(refused))
        return SettleReport(tuple(settled), tuple(refused))

    async def _publish_one(
        self,
        ledger: _Ledger,
        version: WorkspaceManifest,
        output: OutputFile,
        intent_id: str,
        *,
        draft: bool,
        derivation: Derivation,
        sources: frozenset[str] | None,
    ) -> PublishedArtifact:
        binding = ledger.binding
        done = ledger.history.publication(intent_id)
        if done is not None:
            return PublishedArtifact(
                intent_id, output.path, done.artifact_id, done.version, "unchanged",
                _shown_lifecycle(ledger.history, done.artifact_id, done.version),
            )
        intent = ledger.history.intent(intent_id)
        prepared = self._prepared.pop(intent_id, None)
        # An intent this process did not log just now is an earlier attempt's: it may have written already.
        retry = intent is not None and prepared is None
        content = prepared if prepared is not None else await self._objects.get(self._object_scope(binding), output.content)
        if intent is None:
            intent = await self._new_intent(ledger, output, intent_id, content)
            await ledger.intent(intent)
        lifecycle: ArtifactLifecycle = "draft" if draft else "final"
        target = output.edit_of or ledger.history.last_written(output.path)
        stored: StoredArtifact
        outcome: PublishOutcome
        if target is not None:
            stored, outcome = await self._edit(
                binding, target, content, output.mime_type, intent.content.sha256, lifecycle
            )
        else:
            stored, outcome = await self._store.register_output(
                binding, artifact_id=intent.artifact_id, name=output.path, mime_type=output.mime_type,
                content=content, lifecycle=lifecycle,
            )
        wrote = outcome != "unchanged" or retry
        if wrote:
            await self._store.record_provenance(
                binding, stored.artifact_id, stored.version, _provenance(binding, version, derivation, sources)
            )
        await ledger.published(PublicationRecord(intent_id, stored.artifact_id, stored.version))
        shown = lifecycle if wrote else ("final" if stored.lifecycle == "final" else None)
        if shown is not None and not ledger.history.shown(stored.artifact_id, stored.version):
            await ledger.presented(_card(stored, shown))
        return PublishedArtifact(
            intent_id, output.path, stored.artifact_id, stored.version, outcome, shown or stored.lifecycle
        )

    async def _new_intent(self, ledger: _Ledger, output: OutputFile, intent_id: str, content: bytes) -> PublishIntent:
        """The artifact an output will be: the one it edits, the session's artifact of its name, or a new one."""
        digest = ContentDigest(output.content.object_id, sha256_of(content), len(content))
        target = output.edit_of or ledger.history.last_written(output.path)
        if target is not None:
            return PublishIntent(intent_id, target.artifact_id, digest)
        existing = await self._store.find_by_name(ledger.binding, output.path)
        return PublishIntent(intent_id, existing.artifact_id if existing is not None else artifact_id_for(intent_id), digest)

    async def _edit(
        self,
        binding: RunBinding,
        target: ArtifactVersionRef,
        content: bytes,
        mime_type: str,
        sha256: str,
        lifecycle: ArtifactLifecycle,
    ) -> tuple[StoredArtifact, PublishOutcome]:
        current = await self._store.current(binding, target.artifact_id)
        if current.sha256 == sha256:
            return current, "unchanged"
        if current.version != target.version:
            raise ArtifactVersionConflict(target.artifact_id, expected=target.version, current=current.version)
        return await self._store.add_version(
            binding, target.artifact_id, content=content, mime_type=mime_type,
            expected_version=target.version, lifecycle=lifecycle,
        )

    async def _discard(self, binding: RunBinding, run: DraftRun) -> list[ArtifactCard]:
        artifact_id = run.last.artifact_id
        current = await self._store.current(binding, artifact_id)
        if current.version != run.last.version:
            raise ArtifactVersionConflict(artifact_id, expected=run.last.version, current=current.version)
        discarded = run.last.with_lifecycle("discarded")
        if run.created_by_run:
            await self._store.discard(binding, artifact_id)
            await self._log.presented(binding, discarded)
            return [discarded]
        base = run.first.version - 1
        # Read before the restoring version exists: it has no provenance yet, which reads as unknown.
        sources = (await self._store.sources_of(binding, [artifact_id])).get(artifact_id)
        restored, _ = await self._store.add_version(
            binding, artifact_id, content=await self._store.content(binding, artifact_id, base),
            mime_type=current.mime_type, expected_version=current.version, lifecycle="final",
        )
        await self._store.record_provenance(
            binding, artifact_id, restored.version,
            Provenance(
                run_id=binding.run_id, task_id=binding.run_id, workspace_id=None, workspace_revision=None,
                command=None, input_paths=(), input_artifacts=(ArtifactVersionRef(artifact_id, base),),
                source_records=sources,
            ),
        )
        restored_card = _card(restored, "final")
        await self._log.presented(binding, discarded)
        await self._log.presented(binding, restored_card)
        return [discarded, restored_card]

    async def _sources(self, binding: RunBinding, derivation: Derivation) -> frozenset[str] | None:
        """The derivation's own sources plus every input artifact's; unknown if any is."""
        if derivation.source_records is None:
            return None
        inputs = sorted({ref.artifact_id for ref in derivation.input_artifacts})
        found: Mapping[str, frozenset[str] | None] = (
            await self._store.sources_of(binding, inputs) if inputs else {}
        )
        return union_sources(derivation.source_records, *(found.get(a) for a in inputs))

    async def _current_or_none(self, binding: RunBinding, artifact_id: str) -> StoredArtifact | None:
        try:
            return await self._store.current(binding, artifact_id)
        except ArtifactPublishRefused:
            return None

    def _bound(self, binding: RunBinding) -> HarnessLog:
        return self._harness_log.bind(orgId=binding.tenant, sessionId=binding.scope, runId=binding.run_id)


def _provenance(
    binding: RunBinding, version: WorkspaceManifest, derivation: Derivation, sources: frozenset[str] | None
) -> Provenance:
    return Provenance(
        run_id=binding.run_id,
        task_id=derivation.task_id,
        workspace_id=version.workspace_id,
        workspace_revision=version.revision,
        command=derivation.command,
        input_paths=derivation.input_paths,
        input_artifacts=derivation.input_artifacts,
        source_records=sources,
    )


def _shown_lifecycle(history: PublicationHistory, artifact_id: str, version: int) -> ArtifactLifecycle:
    for card in reversed(history.cards):
        if card.artifact_id == artifact_id and card.version == version:
            return card.lifecycle
    return "final"


def _refusal(output: OutputFile, exc: ArtifactPublishRefused) -> PublishRefusal:
    if isinstance(exc, ArtifactVersionConflict):
        return PublishRefusal(output.path, exc.reason_code, exc.artifact_id, exc.expected, exc.current)
    target = output.edit_of
    return PublishRefusal(output.path, exc.reason_code, target.artifact_id if target else None)


__all__ = ["WorkspaceArtifactPublisher"]
