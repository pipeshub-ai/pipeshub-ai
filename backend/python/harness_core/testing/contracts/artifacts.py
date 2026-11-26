"""Contract suites for artifact publication (P3.5, ``impl/P3.5-artifact-publisher.md``).

Subclass a contract in a test module and provide its fixture:

- :class:`ArtifactStoreContract` (AS-1..AS-6): ``store_world`` on every
  ``ArtifactStore`` (in-memory, a host registry);
- :class:`PublicationLogContract` (PL-1, PL-2): ``publication_log`` on every
  ``PublicationLog`` (in-memory, a journal mapping);
- :class:`ArtifactPublisherContract` (AP-1..AP-14): ``store_world`` again; the
  publisher runs over that store, an in-memory log and object store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.artifacts.errors import ArtifactVersionConflict
from harness_core.artifacts.memory import InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.artifacts.types import (
    ArtifactCard,
    ArtifactVersionRef,
    ContentDigest,
    Derivation,
    OutputFile,
    Provenance,
    PublicationRecord,
    PublishIntent,
    artifact_id_for,
    publish_intent_id,
    sha256_of,
)
from harness_core.interactions.ports import RunBinding
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.types import ObjectScope, build_manifest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.artifacts.ports import (
        ArtifactPublisher,
        ArtifactStore,
        PublicationLog,
    )
    from harness_core.artifacts.types import (
        ArtifactLifecycle,
        PublicationHistory,
        PublishReport,
        StoredArtifact,
    )
    from harness_core.workspace.types import WorkspaceManifest

TENANT = "org-1"
SCOPE = "conv-1"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def binding(run_id: str = "run-1") -> RunBinding:
    return RunBinding(tenant=TENANT, scope=SCOPE, run_id=run_id)


def provenance(sources: frozenset[str] | None, *, run_id: str = "run-1") -> Provenance:
    return Provenance(
        run_id=run_id, task_id="task-1", workspace_id="ws:conv-1", workspace_revision=1,
        command="python make.py", input_paths=("data.csv",), input_artifacts=(), source_records=sources,
    )


class StoreWorld(Protocol):
    """An ``ArtifactStore`` whose bindings use ``TENANT`` and ``SCOPE``, with a size cap of ``max_bytes``."""

    @property
    def store(self) -> ArtifactStore: ...

    @property
    def max_bytes(self) -> int: ...


class ArtifactStoreContract:
    @pytest.fixture
    def store_world(self) -> StoreWorld:
        raise NotImplementedError("provide a store_world fixture")

    async def test_as1_register_output_creates_versions_and_dedupes(self, store_world: StoreWorld) -> None:
        store, b = store_world.store, binding()
        first, created = await store.register_output(
            b, artifact_id=artifact_id_for("pi_1"), name="report.md", mime_type="text/markdown",
            content=b"v1", lifecycle="final",
        )
        assert (created, first.version, first.name) == ("created", 1, "report.md")
        assert first.artifact_id == artifact_id_for("pi_1")
        assert first.sha256 == sha256_of(b"v1")
        second, versioned = await store.register_output(
            b, artifact_id=artifact_id_for("pi_2"), name="report.md", mime_type="text/markdown",
            content=b"v2!", lifecycle="final",
        )
        assert (versioned, second.artifact_id, second.version) == ("versioned", first.artifact_id, 2)
        same, unchanged = await store.register_output(
            b, artifact_id=artifact_id_for("pi_3"), name="report.md", mime_type="text/markdown",
            content=b"v2!", lifecycle="final",
        )
        assert (unchanged, same.version) == ("unchanged", 2)
        found = await store.find_by_name(b, "report.md")
        assert found is not None and found.artifact_id == first.artifact_id

    async def test_as2_register_output_with_an_existing_id_is_a_retry(self, store_world: StoreWorld) -> None:
        store, b = store_world.store, binding()
        aid = artifact_id_for("pi_retry")
        await store.register_output(b, artifact_id=aid, name="a.txt", mime_type="text/plain", content=b"x", lifecycle="draft")
        again, outcome = await store.register_output(
            b, artifact_id=aid, name="a.txt", mime_type="text/plain", content=b"x", lifecycle="draft"
        )
        assert (outcome, again.artifact_id, again.version) == ("unchanged", aid, 1)

    async def test_as3_add_version_refuses_a_stale_expected_version(self, store_world: StoreWorld) -> None:
        store, b = store_world.store, binding()
        made, _ = await store.register_output(
            b, artifact_id=artifact_id_for("pi_c"), name="c.txt", mime_type="text/plain", content=b"1", lifecycle="final"
        )
        await store.add_version(b, made.artifact_id, content=b"22", mime_type="text/plain", expected_version=1, lifecycle="final")
        with pytest.raises(ArtifactVersionConflict) as refused:
            await store.add_version(
                b, made.artifact_id, content=b"333", mime_type="text/plain", expected_version=1, lifecycle="final"
            )
        assert (refused.value.expected, refused.value.current) == (1, 2)
        assert refused.value.reason_code == "artifact_version_conflict"
        now = await store.current(b, made.artifact_id)
        assert (now.version, now.sha256) == (2, sha256_of(b"22"))

    async def test_as4_content_reads_each_version(self, store_world: StoreWorld) -> None:
        store, b = store_world.store, binding()
        made, _ = await store.register_output(
            b, artifact_id=artifact_id_for("pi_v"), name="v.txt", mime_type="text/plain", content=b"one", lifecycle="final"
        )
        await store.add_version(b, made.artifact_id, content=b"two", mime_type="text/plain", expected_version=1, lifecycle="final")
        assert await store.content(b, made.artifact_id, 1) == b"one"
        assert await store.content(b, made.artifact_id, 2) == b"two"

    async def test_as5_lifecycle_and_discard(self, store_world: StoreWorld) -> None:
        from harness_core.artifacts.errors import ArtifactNotWritable

        store, b = store_world.store, binding()
        made, _ = await store.register_output(
            b, artifact_id=artifact_id_for("pi_d"), name="d.txt", mime_type="text/plain", content=b"d", lifecycle="draft"
        )
        assert made.lifecycle == "draft"
        await store.set_lifecycle(b, made.artifact_id, "final")
        assert (await store.current(b, made.artifact_id)).lifecycle == "final"
        await store.discard(b, made.artifact_id)
        assert await store.find_by_name(b, "d.txt") is None
        with pytest.raises(ArtifactNotWritable):
            await store.current(b, made.artifact_id)

    async def test_as6_provenance_is_a_union_over_every_version(self, store_world: StoreWorld) -> None:
        store, b = store_world.store, binding()
        made, _ = await store.register_output(
            b, artifact_id=artifact_id_for("pi_p"), name="p.txt", mime_type="text/plain", content=b"p1", lifecycle="final"
        )
        aid = made.artifact_id
        assert (await store.sources_of(b, [aid]))[aid] is None
        await store.record_provenance(b, aid, 1, provenance(frozenset({"r1"})))
        assert (await store.sources_of(b, [aid]))[aid] == frozenset({"r1"})
        await store.add_version(b, aid, content=b"p2", mime_type="text/plain", expected_version=1, lifecycle="final")
        assert (await store.sources_of(b, [aid]))[aid] is None
        await store.record_provenance(b, aid, 2, provenance(frozenset({"r2"})))
        assert (await store.sources_of(b, [aid]))[aid] == frozenset({"r1", "r2"})
        await store.record_provenance(b, aid, 2, provenance(frozenset({"r3"})))
        assert (await store.sources_of(b, [aid]))[aid] == frozenset({"r1", "r2", "r3"})
        await store.record_provenance(b, aid, 1, provenance(None))
        assert (await store.sources_of(b, [aid, "missing"])) == {aid: None, "missing": None}


class PublicationLogContract:
    @pytest.fixture
    def publication_log(self) -> PublicationLog:
        raise NotImplementedError("provide a publication_log fixture")

    async def test_pl1_history_is_per_run(self, publication_log: PublicationLog) -> None:
        empty = await publication_log.history(binding("run-a"))
        assert (empty.intents, empty.published, empty.cards) == ((), (), ())
        await publication_log.intent(binding("run-a"), _intent("pi_a"))
        assert (await publication_log.history(binding("run-b"))).intents == ()

    async def test_pl2_records_come_back_in_order(self, publication_log: PublicationLog) -> None:
        b = binding("run-c")
        card = ArtifactCard("art-1", 1, "a.txt", "text/plain", 3, "DOCUMENT", "draft", "doc-1")
        await publication_log.intent(b, _intent("pi_1"))
        await publication_log.published(b, PublicationRecord("pi_1", "art-1", 1))
        await publication_log.presented(b, card)
        await publication_log.presented(b, card.with_lifecycle("final"))
        history = await publication_log.history(b)
        assert history.intent("pi_1") == _intent("pi_1")
        assert history.publication("pi_1") == PublicationRecord("pi_1", "art-1", 1)
        assert [c.lifecycle for c in history.cards] == ["draft", "final"]
        assert history.cards[0] == card


def _intent(intent_id: str) -> PublishIntent:
    return PublishIntent(intent_id, "art-1", ContentDigest("obj-1", sha256_of(b"abc"), 3))


@dataclass
class _Probe:
    """Wraps a store and a log to count writes and to fail once at a chosen step."""

    store: ArtifactStore
    log: PublicationLog = field(default_factory=InMemoryPublicationLog)
    writes: list[str] = field(default_factory=list[str])
    events: list[str] = field(default_factory=list[str])
    fail_at: str | None = None

    def maybe_fail(self, step: str) -> None:
        if self.fail_at == step:
            self.fail_at = None
            raise ConnectionError(f"crash at {step}")


class _CountingStore:
    def __init__(self, probe: _Probe) -> None:
        self._probe = probe
        self._inner = probe.store

    async def find_by_name(self, binding: RunBinding, name: str) -> StoredArtifact | None:
        return await self._inner.find_by_name(binding, name)

    async def current(self, binding: RunBinding, artifact_id: str) -> StoredArtifact:
        return await self._inner.current(binding, artifact_id)

    async def register_output(self, binding: RunBinding, **kw: object) -> tuple[StoredArtifact, str]:
        self._probe.maybe_fail("before_write")
        result = await self._inner.register_output(binding, **kw)  # type: ignore[arg-type]
        self._probe.writes.append(f"register:{result[1]}")
        self._probe.maybe_fail("after_write")
        return result

    async def add_version(self, binding: RunBinding, artifact_id: str, **kw: object) -> tuple[StoredArtifact, str]:
        self._probe.maybe_fail("before_write")
        result = await self._inner.add_version(binding, artifact_id, **kw)  # type: ignore[arg-type]
        self._probe.writes.append(f"version:{result[1]}")
        self._probe.maybe_fail("after_write")
        return result

    async def content(self, binding: RunBinding, artifact_id: str, version: int) -> bytes:
        return await self._inner.content(binding, artifact_id, version)

    async def set_lifecycle(self, binding: RunBinding, artifact_id: str, lifecycle: ArtifactLifecycle) -> None:
        self._probe.writes.append(f"lifecycle:{lifecycle}")
        await self._inner.set_lifecycle(binding, artifact_id, lifecycle)

    async def discard(self, binding: RunBinding, artifact_id: str) -> None:
        self._probe.writes.append("discard")
        await self._inner.discard(binding, artifact_id)

    async def record_provenance(self, binding: RunBinding, artifact_id: str, version: int, provenance: Provenance) -> None:
        self._probe.maybe_fail("before_provenance")
        self._probe.writes.append(f"provenance:{version}")
        await self._inner.record_provenance(binding, artifact_id, version, provenance)

    async def sources_of(self, binding: RunBinding, artifact_ids: Sequence[str]) -> object:
        return await self._inner.sources_of(binding, artifact_ids)


class _CountingLog:
    def __init__(self, probe: _Probe) -> None:
        self._probe = probe

    async def intent(self, binding: RunBinding, intent: PublishIntent) -> None:
        self._probe.events.append("intent")
        await self._probe.log.intent(binding, intent)

    async def published(self, binding: RunBinding, record: PublicationRecord) -> None:
        self._probe.maybe_fail("before_published")
        self._probe.events.append("published")
        await self._probe.log.published(binding, record)

    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None:
        self._probe.events.append(f"presented:{card.lifecycle}")
        await self._probe.log.presented(binding, card)

    async def history(self, binding: RunBinding) -> PublicationHistory:
        return await self._probe.log.history(binding)


@dataclass
class PublisherSetup:
    publisher: ArtifactPublisher
    probe: _Probe
    objects: InMemoryBinaryObjectStore
    scope: ObjectScope

    async def output(self, path: str, text: str, *, edit_of: ArtifactVersionRef | None = None) -> OutputFile:
        ref = await self.objects.put(self.scope, text.encode())
        return OutputFile(path, ref, "text/plain", edit_of)


def manifest(revision: int = 1) -> WorkspaceManifest:
    return build_manifest("ws:conv-1", revision=revision, parent_revision=revision - 1, files={}, created_at_ms=0)


DERIVATION = Derivation(task_id="task-1", command="python make.py", source_records=frozenset({"rec-1"}))


class ArtifactPublisherContract:
    @pytest.fixture
    def store_world(self) -> StoreWorld:
        raise NotImplementedError("provide a store_world fixture")

    def make_publisher(self, store: ArtifactStore, log: PublicationLog, objects: InMemoryBinaryObjectStore) -> ArtifactPublisher:
        """Override to run the contract against another ``ArtifactPublisher``."""
        return WorkspaceArtifactPublisher(store, log, objects, object_scope=lambda b: ObjectScope(b.tenant, b.scope))

    @pytest.fixture
    def setup(self, store_world: StoreWorld) -> PublisherSetup:
        probe = _Probe(store_world.store)
        objects = InMemoryBinaryObjectStore()
        publisher = self.make_publisher(_CountingStore(probe), _CountingLog(probe), objects)  # type: ignore[arg-type]
        return PublisherSetup(publisher, probe, objects, ObjectScope(TENANT, SCOPE))

    async def _publish(
        self, setup: PublisherSetup, outputs: Sequence[OutputFile], *, draft: bool = False,
        run_id: str = "run-1", derivation: Derivation = DERIVATION,
    ) -> PublishReport:
        return await setup.publisher.publish_changes(
            binding(run_id), manifest(), outputs, draft=draft, derivation=derivation
        )

    async def test_ap1_a_new_file_becomes_an_artifact(self, setup: PublisherSetup) -> None:
        report = await self._publish(setup, [await setup.output("deck.md", "slides")])
        assert report.refused == ()
        (done,) = report.published
        assert (done.outcome, done.version, done.lifecycle, done.path) == ("created", 1, "final", "deck.md")
        assert setup.probe.events == ["intent", "published", "presented:final"]
        history = await setup.probe.log.history(binding())
        assert history.intents[0].artifact_id == done.artifact_id == history.published[0].artifact_id
        assert history.intents[0].content.sha256 == sha256_of(b"slides")
        assert history.cards[0].name == "deck.md"
        drafted = await self._publish(setup, [await setup.output("notes.md", "n")], draft=True)
        assert drafted.published[0].lifecycle == "draft"
        assert (await setup.probe.store.current(binding(), drafted.published[0].artifact_id)).lifecycle == "draft"

    async def test_ap2_an_edit_adds_a_version_of_the_same_artifact(self, setup: PublisherSetup) -> None:
        (made,) = (await self._publish(setup, [await setup.output("a.md", "v1")])).published
        report = await self._publish(
            setup, [await setup.output("a.md", "v2", edit_of=ArtifactVersionRef(made.artifact_id, 1))], run_id="run-2"
        )
        (edited,) = report.published
        assert (edited.outcome, edited.artifact_id, edited.version) == ("versioned", made.artifact_id, 2)

    async def test_ap3_a_stale_edit_is_refused_and_overwrites_nothing(self, setup: PublisherSetup) -> None:
        (made,) = (await self._publish(setup, [await setup.output("a.md", "v1")])).published
        await setup.probe.store.add_version(
            binding("other"), made.artifact_id, content=b"theirs", mime_type="text/plain", expected_version=1, lifecycle="final"
        )
        setup.probe.events.clear()
        report = await self._publish(
            setup,
            [
                await setup.output("a.md", "mine", edit_of=ArtifactVersionRef(made.artifact_id, 1)),
                await setup.output("b.md", "fine"),
            ],
            run_id="run-2",
        )
        (refusal,) = report.refused
        assert (refusal.reason_code, refusal.artifact_id, refusal.expected_version, refusal.current_version) == (
            "artifact_version_conflict", made.artifact_id, 1, 2,
        )
        assert [p.path for p in report.published] == ["b.md"]
        now = await setup.probe.store.current(binding(), made.artifact_id)
        assert (now.version, now.sha256) == (2, sha256_of(b"theirs"))
        history = await setup.probe.log.history(binding("run-2"))
        assert len(history.intents) == 2 and len(history.published) == 1

    async def test_ap4_identical_content_adds_no_version(self, setup: PublisherSetup) -> None:
        (made,) = (await self._publish(setup, [await setup.output("a.md", "same")])).published
        setup.probe.writes.clear()
        setup.probe.events.clear()
        report = await self._publish(
            setup, [await setup.output("a.md", "same", edit_of=ArtifactVersionRef(made.artifact_id, 1))], run_id="run-2"
        )
        (same,) = report.published
        assert (same.outcome, same.version) == ("unchanged", 1)
        assert not any(w.startswith(("register", "version")) for w in setup.probe.writes)
        assert setup.probe.events == ["intent", "published", "presented:final"]
        report = await self._publish(
            setup, [await setup.output("a.md", "same", edit_of=ArtifactVersionRef(made.artifact_id, 1))], run_id="run-2"
        )
        assert setup.probe.events.count("presented:final") == 1

    async def test_ap5_the_same_call_twice_publishes_once(self, setup: PublisherSetup) -> None:
        outputs = [await setup.output("a.md", "x"), await setup.output("b.md", "y")]
        first = await self._publish(setup, outputs)
        writes, events = list(setup.probe.writes), list(setup.probe.events)
        second = await self._publish(setup, outputs)
        assert [(p.artifact_id, p.version) for p in second.published] == [
            (p.artifact_id, p.version) for p in first.published
        ]
        assert (setup.probe.writes, setup.probe.events) == (writes, events)

    @pytest.mark.parametrize("crash", ["before_write", "after_write", "before_provenance", "before_published"])
    async def test_ap6_a_redriven_publish_after_a_crash_writes_once(self, setup: PublisherSetup, crash: str) -> None:
        output = await setup.output("a.md", "x")
        setup.probe.fail_at = crash
        with pytest.raises(ConnectionError):
            await self._publish(setup, [output])
        (done,) = (await self._publish(setup, [output])).published
        assert done.version == 1
        assert sum(w.startswith("register:created") for w in setup.probe.writes) == 1
        assert setup.probe.events.count("intent") == 1 and setup.probe.events.count("published") == 1
        assert setup.probe.events.count("presented:final") == 1
        assert (await setup.probe.store.sources_of(binding(), [done.artifact_id]))[done.artifact_id] == frozenset(
            {"rec-1"}
        )
        found = await setup.probe.store.find_by_name(binding(), "a.md")
        assert found is not None and found.artifact_id == done.artifact_id

    async def test_ap15_prepare_logs_intents_before_the_bytes_are_committed(self, setup: PublisherSetup) -> None:
        outputs = [await setup.output("a.md", "x"), await setup.output("b.md", "y")]
        await setup.publisher.prepare(binding(), outputs)
        assert (setup.probe.events, setup.probe.writes) == (["intent", "intent"], [])
        await setup.publisher.prepare(binding(), outputs)
        assert setup.probe.events == ["intent", "intent"]
        report = await self._publish(setup, outputs, draft=True)
        assert [p.outcome for p in report.published] == ["created", "created"]
        assert setup.probe.events == [
            "intent", "intent", "published", "presented:draft", "published", "presented:draft",
        ]
        history = await setup.probe.log.history(binding())
        assert [i.artifact_id for i in history.intents] == [p.artifact_id for p in report.published]
        (made,) = (await self._publish(setup, [await setup.output("c.md", "same")])).published
        same = await setup.output("c.md", "same", edit_of=ArtifactVersionRef(made.artifact_id, 1))
        setup.probe.events.clear()
        setup.probe.writes.clear()
        await setup.publisher.prepare(binding("run-2"), [same])
        # A prepared intent is not a crash's: unchanged content writes nothing and shows the final it is.
        (unchanged,) = (await self._publish(setup, [same], draft=True, run_id="run-2")).published
        assert (unchanged.outcome, unchanged.version) == ("unchanged", 1)
        assert setup.probe.events == ["intent", "published", "presented:final"]
        assert setup.probe.writes == []

    async def test_ap16_publish_pending_publishes_what_was_intended_and_never_published(
        self, setup: PublisherSetup
    ) -> None:
        done, pending = await setup.output("a.md", "x"), await setup.output("b.md", "y")
        another_run, never = await setup.output("c.md", "z"), await setup.output("d.md", "w")
        replaced = await setup.output("e.md", "theirs")
        await setup.publisher.prepare(binding(), [done, pending, await setup.output("e.md", "mine")])
        _ = await self._publish(setup, [done])
        await setup.publisher.prepare(binding("run-other"), [another_run])
        # The next attempt: a new publisher over the same store and log.
        fresh = self.make_publisher(_CountingStore(setup.probe), _CountingLog(setup.probe), setup.objects)  # type: ignore[arg-type]
        head = [done, pending, another_run, never, replaced]
        report = await fresh.publish_pending(binding(), manifest(2), head, draft=True, derivation=DERIVATION)
        assert [(p.path, p.outcome, p.lifecycle) for p in report.published] == [("b.md", "created", "draft")]
        writes, events = list(setup.probe.writes), list(setup.probe.events)
        again = await fresh.publish_pending(binding(), manifest(2), head, draft=True, derivation=DERIVATION)
        assert again.published == () and again.refused == ()
        assert (setup.probe.writes, setup.probe.events) == (writes, events)
        assert await setup.probe.store.find_by_name(binding(), "e.md") is None

    async def test_ap7_the_same_path_twice_in_a_run_versions_one_artifact(self, setup: PublisherSetup) -> None:
        (first,) = (await self._publish(setup, [await setup.output("a.md", "autosave")], draft=True)).published
        (second,) = (await self._publish(setup, [await setup.output("a.md", "turn end")], draft=True)).published
        assert (second.artifact_id, first.version, second.version) == (first.artifact_id, 1, 2)
        assert second.outcome == "versioned"

    async def test_ap8_a_new_file_named_like_an_existing_artifact_versions_it(self, setup: PublisherSetup) -> None:
        (made,) = (await self._publish(setup, [await setup.output("chart.md", "q2")], run_id="run-0")).published
        (again,) = (await self._publish(setup, [await setup.output("chart.md", "q3")], run_id="run-1")).published
        assert (again.artifact_id, again.version, again.outcome) == (made.artifact_id, 2, "versioned")

    async def test_ap9_finalize_turns_drafts_final_once(self, setup: PublisherSetup) -> None:
        report = await self._publish(setup, [await setup.output("a.md", "1"), await setup.output("b.md", "2")], draft=True)
        settled = await setup.publisher.finalize(binding())
        assert sorted(c.artifact_id for c in settled.settled) == sorted(p.artifact_id for p in report.published)
        assert {c.lifecycle for c in settled.settled} == {"final"}
        for p in report.published:
            assert (await setup.probe.store.current(binding(), p.artifact_id)).lifecycle == "final"
        assert (await setup.publisher.finalize(binding())).settled == ()

    async def test_ap10_cancel_discards_created_drafts_and_restores_edited_ones(self, setup: PublisherSetup) -> None:
        (old,) = (await self._publish(setup, [await setup.output("old.md", "original")], run_id="run-0")).published
        await self._publish(
            setup,
            [
                await setup.output("new.md", "draft"),
                await setup.output("old.md", "edit 1", edit_of=ArtifactVersionRef(old.artifact_id, 1)),
            ],
            draft=True,
        )
        await self._publish(setup, [await setup.output("old.md", "edit 2")], draft=True)
        settled = await setup.publisher.discard_drafts(binding())
        assert settled.refused == ()
        assert await setup.probe.store.find_by_name(binding(), "new.md") is None
        restored = await setup.probe.store.current(binding(), old.artifact_id)
        assert (restored.version, restored.sha256, restored.lifecycle) == (4, sha256_of(b"original"), "final")
        assert sorted(c.lifecycle for c in settled.settled) == ["discarded", "discarded", "final"]
        assert (await setup.probe.store.sources_of(binding(), [old.artifact_id]))[old.artifact_id] == frozenset({"rec-1"})
        assert (await setup.publisher.discard_drafts(binding())).settled == ()

    async def test_ap11_discard_after_another_writer_keeps_their_version(self, setup: PublisherSetup) -> None:
        (old,) = (await self._publish(setup, [await setup.output("a.md", "v1")], run_id="run-0")).published
        await self._publish(
            setup, [await setup.output("a.md", "draft", edit_of=ArtifactVersionRef(old.artifact_id, 1))], draft=True
        )
        await setup.probe.store.add_version(
            binding("other"), old.artifact_id, content=b"theirs", mime_type="text/plain", expected_version=2, lifecycle="final"
        )
        settled = await setup.publisher.discard_drafts(binding())
        (refusal,) = settled.refused
        assert (refusal.reason_code, refusal.expected_version, refusal.current_version) == ("artifact_version_conflict", 2, 3)
        assert (await setup.probe.store.current(binding(), old.artifact_id)).sha256 == sha256_of(b"theirs")

    async def test_ap12_final_publishes_are_not_discarded(self, setup: PublisherSetup) -> None:
        (made,) = (await self._publish(setup, [await setup.output("a.md", "v1")])).published
        assert (await setup.publisher.discard_drafts(binding())).settled == ()
        assert (await setup.probe.store.current(binding(), made.artifact_id)).version == 1

    async def test_ap13_too_large_is_refused(self, setup: PublisherSetup, store_world: StoreWorld) -> None:
        report = await self._publish(setup, [await setup.output("big.txt", "x" * (store_world.max_bytes + 1))])
        assert [r.reason_code for r in report.refused] == ["artifact_too_large"]
        assert await setup.probe.store.find_by_name(binding(), "big.txt") is None

    def test_ap14_intent_ids_are_deterministic_and_contract_ids(self) -> None:
        base = publish_intent_id("run-1", "a.md", "sha256:1")
        assert base == publish_intent_id("run-1", "a.md", "sha256:1")
        others = {
            publish_intent_id("run-2", "a.md", "sha256:1"),
            publish_intent_id("run-1", "b.md", "sha256:1"),
            publish_intent_id("run-1", "a.md", "sha256:2"),
        }
        assert base not in others and len(others) == 3
        assert ID_PATTERN.match(base) and ID_PATTERN.match(artifact_id_for(base))
        assert artifact_id_for(base) == artifact_id_for(base)

    async def test_input_artifact_sources_flow_into_the_output(self, setup: PublisherSetup) -> None:
        """PV-3: B made from A carries A's sources; an input of unknown provenance makes B unknown."""
        (a,) = (
            await self._publish(
                setup, [await setup.output("a.md", "A")],
                derivation=Derivation(task_id="t", source_records=frozenset({"rec-a"})),
            )
        ).published
        uses_a = Derivation(task_id="t", input_artifacts=(ArtifactVersionRef(a.artifact_id, 1),), source_records=frozenset({"rec-b"}))
        (b,) = (await self._publish(setup, [await setup.output("b.md", "B")], derivation=uses_a)).published
        assert (await setup.probe.store.sources_of(binding(), [b.artifact_id]))[b.artifact_id] == frozenset({"rec-a", "rec-b"})
        (c,) = (
            await self._publish(
                setup, [await setup.output("c.md", "C")], derivation=Derivation(task_id="t", source_records=None)
            )
        ).published
        uses_c = Derivation(task_id="t", input_artifacts=(ArtifactVersionRef(c.artifact_id, 1),), source_records=frozenset())
        (d,) = (await self._publish(setup, [await setup.output("d.md", "D")], derivation=uses_c)).published
        assert (await setup.probe.store.sources_of(binding(), [c.artifact_id, d.artifact_id])) == {
            c.artifact_id: None, d.artifact_id: None,
        }


__all__ = [
    "ArtifactPublisherContract",
    "ArtifactStoreContract",
    "PublicationLogContract",
    "PublisherSetup",
    "StoreWorld",
    "binding",
]
