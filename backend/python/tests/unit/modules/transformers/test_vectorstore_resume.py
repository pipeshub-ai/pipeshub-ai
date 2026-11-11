"""An interrupted embedding run is resumed by a retry of the same event.

The blocks come from the real JSON parser; the vector store, the embed-and-upsert path and the point
ids are the real ones, over the in-memory vector service. What the tests stand in for is the embedding
server (a counting embedder that can be cut part way, as a cancelled attempt is), the graph node (a
dict that merges partial writes) and the record blob (the container as stored, reloaded the way the
handler reloads it). Each test says which behaviour it would lose if the code under test were wrong.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import org_context
from app.exceptions.indexing_exceptions import IndexingError
from app.models.blocks import BlocksContainer
from app.modules.indexing.embed_resume import (
    EMBED_DIGEST,
    EMBED_FIELDS,
    EMBED_KEY,
    EMBED_REV,
    EmbedAttempt,
    begin_attempt,
    cleared_fields,
    end_attempt,
    marker_of,
    may_resume,
)
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.models import (
    CollectionConfig,
    VectorDBCapabilities,
    VectorPoint,
)
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "a" * 24
OTHER_ORG = "b" * 24
VRID = "vr-1"
RECORD = "rec-1"
COLL = "records"
BATCH = 20  # the local-embedding batch size
_WORLDS = int(os.environ.get("RESUME_WORLDS", "24"))
_SEED_BASE = int(os.environ.get("RESUME_SEED", "0")) * 1000


class Cut(BaseException):
    """What cancelling an attempt looks like to the code under it: it passes every ``except Exception``."""


class Embedder:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.cut_after: int | None = None
        self.cut_calls = 0

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.cut_after is not None and len(self.batches) >= self.cut_after:
            self.cut_calls += 1
            raise Cut
        self.batches.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    @property
    def texts(self) -> int:
        return sum(len(b) for b in self.batches)


class Graph:
    """A record node with the partial-update semantics every graph provider shares for flat fields."""

    def __init__(self) -> None:
        self.node: dict[str, Any] = {"_key": RECORD, "orgId": ORG, "virtualRecordId": VRID}
        self.writes: list[dict[str, Any]] = []
        self.fail_writes = False

    async def get_document(self, key: str, collection: str, **_kw: Any) -> dict[str, Any] | None:  # noqa: ANN401
        return dict(self.node)

    async def update_node(self, key: str, collection: str, fields: dict[str, Any], transaction: Any = None) -> bool:  # noqa: ANN401
        if self.fail_writes:
            return False
        self.writes.append(dict(fields))
        self.node.update(fields)
        return True


def _json_bytes(items: int, salt: str = "") -> bytes:
    return json.dumps(
        [{"id": i, "name": f"item {i}{salt}", "note": f"note {i} {'x' * (i % 7)}"} for i in range(items)]
    ).encode()


async def _store(db: InMemoryVectorDBService, graph: Graph, embedder: Embedder, *, model: str = "m1") -> VectorStore:
    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    store = VectorStore(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=graph,
        collection_registry=make_collection_registry(), vector_db_service=db,
    )
    store.dense_embeddings = embedder
    store.embedding_size = 2
    store.embedding_provider = None  # unconfigured counts as a local model: sequential batches of 20
    store.model_name = model
    store.get_embedding_model_instance = AsyncMock(return_value=False)  # type: ignore[method-assign]
    store._ensure_collection = AsyncMock(return_value=COLL)  # type: ignore[method-assign]
    store._bind_membership = AsyncMock(return_value=None)  # type: ignore[method-assign]
    store._resync_membership_after_write = AsyncMock()  # type: ignore[method-assign]
    store._cleanup_orphaned_embeddings_if_needed = AsyncMock()  # type: ignore[method-assign]
    return store


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[None]:
    end_attempt()
    with org_context.for_org(ORG, job="test"), patch(
        "app.modules.transformers.vectorstore.reset_membership_context", lambda _tokens: None
    ):
        yield
    end_attempt()


class Chain:
    """One event's attempts against one record: the parse, the blob, the node's marker and the vector store."""

    def __init__(self, items: int = 120, *, sampling_llm: bool = False, content: bytes | None = None) -> None:
        self.content = content or _json_bytes(items)
        self.sampling_llm = sampling_llm
        self.parse_calls = 0
        self.llm_calls = 0
        self.blob: dict[str, Any] | None = None
        self.blob_rev: str | None = None
        self.db = InMemoryVectorDBService()
        self.graph = Graph()
        self.embedder = Embedder()
        self.store: VectorStore

    async def start(self) -> Chain:
        await self.db.create_collection(COLL, CollectionConfig(embedding_size=2))
        self.store = await _store(self.db, self.graph, self.embedder)
        return self

    async def parse(self) -> BlocksContainer:
        self.parse_calls += 1
        container = (await JSONParser().parse(self.content, "data.json")).block_container
        if self.sampling_llm:
            # An LLM describing each row differently on every call.
            self.llm_calls += 1
            for block in container.blocks:
                if isinstance(block.data, dict) and block.data.get("row_natural_language_text"):
                    block.data["row_natural_language_text"] += f" ({random.Random().random():.9f})"
        return container

    def store_blob(self, container: BlocksContainer, rev: str | None = None) -> None:
        self.blob = container.model_dump(mode="json")
        self.blob_rev = rev

    def load_blob(self, rev: str | None = None) -> BlocksContainer | None:
        """What ``Processor.load_checkpoint`` returns: nothing when the blob is another revision's."""
        if rev is not None and self.blob_rev != rev:
            return None
        return BlocksContainer.model_validate(self.blob) if self.blob else None

    async def attempt(
        self,
        delivery_id: str = "d1",
        *,
        rev: str = "rev1",
        cut_after: int | None = None,
        force_reindex: bool = False,
        always_parse: bool = False,
        complete: bool = True,
    ) -> None:
        """What ``on_event`` and the sink do for one attempt, around the real ``index_documents``."""
        marker = marker_of(self.graph.node)
        resume = may_resume(
            marker, delivery_id=delivery_id, rev=rev,
            node_vrid=self.graph.node.get("virtualRecordId"), force_reindex=force_reindex,
        ) and not always_parse
        container = self.load_blob(rev) if resume else None
        if container is None:
            resume = False
            container = await self.parse()
            self.store_blob(container, rev)
        self.embedder.cut_after = None if cut_after is None else len(self.embedder.batches) + cut_after
        begin_attempt(EmbedAttempt(delivery_id=delivery_id, rev=rev, vrid=VRID, prior=marker, resume=resume))
        try:
            await self.store.index_documents(container, ORG, RECORD, VRID)
        finally:
            end_attempt()
        if complete:
            self.graph.node.update(cleared_fields())  # the COMPLETED write

    def ids(self) -> set[str]:
        return set(self.db.collections[COLL])

    async def wanted_ids(self) -> set[str]:
        """The points the stored blocks need, built by the real path on a store of their own."""
        db = InMemoryVectorDBService()
        await db.create_collection(COLL, CollectionConfig(embedding_size=2))
        graph = Graph()
        store = await _store(db, graph, Embedder())
        await store.index_documents(self.load_blob(), ORG, RECORD, VRID)
        return set(db.collections[COLL])


def _chain_text(exc: BaseException | None) -> str:
    parts = []
    while exc is not None:
        parts.append(f"{exc} {getattr(exc, 'details', '')}")
        exc = exc.__cause__
    return " | ".join(parts)


def _batches(points: int) -> int:
    return math.ceil(points / BATCH)


@pytest.mark.asyncio
class TestResume:
    async def test_a_cut_run_is_finished_by_its_retry_without_parsing_or_embedding_twice(self) -> None:
        # Kills: delete-then-embed on every attempt; skip-by-count; resuming without the stored blocks.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        assert len(chain.embedder.batches) == 3
        await chain.attempt()
        wanted = await chain.wanted_ids()
        assert chain.ids() == wanted
        assert chain.parse_calls == 1
        assert len(chain.embedder.batches) == _batches(len(wanted))  # only the batch in flight at the cut is lost
        assert chain.embedder.cut_calls == 1

    async def test_without_the_stored_blocks_a_retry_cannot_reuse_what_was_written(self) -> None:
        # Kills: a resume that parses again. Block ids are random per parse, so no point id matches.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        first = chain.ids()
        await chain.attempt(always_parse=True)
        assert chain.parse_calls == 2
        assert not (first & chain.ids())

    async def test_a_sampling_llm_is_not_asked_again_on_the_retry(self) -> None:
        # Kills: enrichment re-run on resume. The points of the first run stay the right ones.
        chain = await Chain(sampling_llm=True).start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=2)
        first = chain.ids()
        await chain.attempt()
        assert chain.llm_calls == 1
        assert first <= chain.ids()
        assert chain.ids() == await chain.wanted_ids()

    async def test_the_control_without_the_checkpoint_leaves_a_different_set(self) -> None:
        chain = await Chain(sampling_llm=True).start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=2)
        first = chain.ids()
        await chain.attempt(always_parse=True)
        assert chain.llm_calls == 2
        assert not (first & chain.ids())

    async def test_a_run_cut_more_than_once_embeds_every_point_once_plus_the_batches_in_flight(self) -> None:
        # Kills: a batch marked done before its upsert.
        chain = await Chain().start()
        for _ in range(3):
            with pytest.raises(Cut):
                await chain.attempt(cut_after=2)
        await chain.attempt()
        wanted = await chain.wanted_ids()
        assert chain.ids() == wanted
        assert len(chain.embedder.batches) == _batches(len(wanted))
        assert chain.embedder.cut_calls == 3

    async def test_an_attempt_that_dies_after_the_last_batch_is_completed_by_its_retry_with_nothing_to_embed(self) -> None:
        chain = await Chain().start()
        await chain.attempt(complete=False)
        batches = len(chain.embedder.batches)
        await chain.attempt()
        assert len(chain.embedder.batches) == batches
        assert chain.parse_calls == 1
        assert chain.ids() == await chain.wanted_ids()


async def _ids_of(chain: Chain) -> set[str]:
    probe = await Chain(content=chain.content).start()
    probe.store_blob(await probe.parse())
    return await probe.wanted_ids()


@pytest.mark.asyncio
class TestWhenNotToResume:
    async def test_a_different_event_embeds_everything_again(self) -> None:
        # Kills: reuse keyed on the record or its status rather than on the event.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt("d1", cut_after=3)
        await chain.attempt("d2")
        assert chain.parse_calls == 2
        assert chain.ids() == await chain.wanted_ids()

    async def test_a_forced_reindex_never_resumes(self) -> None:
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        await chain.attempt(force_reindex=True)
        assert chain.parse_calls == 2

    async def test_content_that_changed_between_attempts_leaves_none_of_the_old_points(self) -> None:
        # Kills: reuse across revisions; a delete after the first write.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(rev="rev1", cut_after=3)
        old = chain.ids()
        chain.content = _json_bytes(120, salt="!")
        await chain.attempt(rev="rev2")
        assert chain.parse_calls == 2
        assert not (old & chain.ids())
        assert chain.ids() == await chain.wanted_ids()
        revs = {p.payload["metadata"]["contentRev"] for p in chain.db.collections[COLL].values()}
        assert revs == {"rev2"}

    async def test_a_changed_embedding_model_leaves_no_point_of_the_old_one(self) -> None:
        # Kills: the embedding setup missing from the marker or the points.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        chain.store.model_name = "m2"
        await chain.attempt()
        keys = {p.payload["metadata"]["embedKey"] for p in chain.db.collections[COLL].values()}
        assert len(keys) == 1
        assert chain.ids() == await chain.wanted_ids()
        # The stored blocks were still used: the parse is skipped, only the embedding is redone.
        assert chain.parse_calls == 1

    async def test_a_marker_without_a_digest_is_not_trusted(self) -> None:
        # The attempt died before it knew which points the record needs.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        chain.graph.node[EMBED_DIGEST] = None
        await chain.attempt()
        assert chain.parse_calls == 2

    async def test_blocks_that_differ_from_the_ones_the_marker_describes_are_replaced(self) -> None:
        # Kills: skipping batches on a digest that is not compared.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        stale = chain.ids()
        chain.store_blob(await chain.parse(), "rev1")  # the stored blocks are another parse's
        await chain.attempt()
        assert chain.parse_calls == 2
        assert not (stale & chain.ids())
        assert chain.ids() == await chain.wanted_ids()

    async def test_a_blob_of_another_revision_is_not_embedded_under_this_ones(self) -> None:
        # Kills: loading whatever blob is stored under the vrid. Another run stored its blocks there and died.
        chain = await Chain(60).start()
        with pytest.raises(Cut):
            await chain.attempt("d1", rev="R1", cut_after=1)
        chain.store_blob(await Chain(60, content=_json_bytes(60, salt="-CHANGED")).parse(), "R2")
        parsed = chain.parse_calls
        await chain.attempt("d1", rev="R1")
        assert chain.parse_calls == parsed + 1
        assert not any("-CHANGED" in str(p.payload) for p in chain.db.collections[COLL].values())
        assert chain.ids() == await chain.wanted_ids()

    async def test_a_marker_of_another_vrid_is_not_trusted(self) -> None:
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        chain.graph.node["virtualRecordId"] = "vr-2"
        await chain.attempt()
        assert chain.parse_calls == 2


@pytest.mark.asyncio
class TestConvergence:
    @pytest.mark.parametrize("seed", range(_WORLDS))
    async def test_whatever_a_cut_left_behind_the_final_set_is_exactly_the_wanted_one(self, seed: int) -> None:
        # Kills: assumptions about order, duplicates and partial batches. RESUME_SEED picks another family of worlds.
        rng = random.Random((seed + _SEED_BASE) * 7919 + 13)
        chain = await Chain(items=rng.choice([1, 19, 20, 21, 60, 97])).start()
        cuts = rng.randint(0, 3)
        finished = False
        for _ in range(cuts):
            try:
                await chain.attempt(cut_after=rng.randint(0, 3))
            except Cut:
                continue
            finished = True  # a small record, or a late cut point: the run completed and cleared its marker
            break
        missing = 0
        if cuts == 0:
            # Damage the written set the way a crashed bulk write does: some points missing, some intact.
            await chain.attempt(complete=False)
            written = sorted(chain.ids())
            lost = rng.sample(written, k=len(written) // 3)
            for point_id in lost:
                del chain.db.collections[COLL][point_id]
            missing = len(lost)
        before = chain.embedder.texts
        if not finished:
            await chain.attempt()
        wanted = await chain.wanted_ids()
        assert chain.ids() == wanted
        if cuts:
            assert len(chain.embedder.batches) <= _batches(len(wanted)) + cuts
        else:
            assert chain.embedder.texts - before == missing

    async def test_points_missing_from_an_otherwise_complete_run_are_embedded_and_nothing_else(self) -> None:
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=4)
        written = sorted(chain.ids())
        for point_id in written[:5]:
            del chain.db.collections[COLL][point_id]
        before = chain.embedder.texts
        await chain.attempt()
        assert chain.ids() == await chain.wanted_ids()
        assert chain.embedder.texts - before == len(await chain.wanted_ids()) - (len(written) - 5)


@pytest.mark.asyncio
class TestWithoutTheFenceIndexes:
    async def test_a_resume_on_a_collection_whose_fence_is_not_ready_takes_the_full_path(self) -> None:
        # Kills: resuming, then failing on every retry in the fence query of an unindexed field.
        chain = await Chain().start()
        chain.store.collection_registry.fence_ready = MagicMock(return_value=False)
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        filters: list[Any] = []
        real = chain.db.filter_collection

        async def spy(*args: Any, **kw: Any) -> Any:  # noqa: ANN401
            filters.append(kw)
            return await real(*args, **kw)

        chain.db.filter_collection = spy  # type: ignore[method-assign]
        before = chain.embedder.texts
        await chain.attempt()
        wanted = await chain.wanted_ids()
        assert chain.ids() == wanted
        assert chain.embedder.texts - before == len(wanted)
        assert not [f for f in filters if "must_not" in f]
        chain.store.collection_registry.fence_ready.assert_called_with(COLL)


class TestFailuresDeleteNothing:
    async def test_an_existence_error_on_a_resumed_attempt_fails_it_and_keeps_every_point(self) -> None:
        # Kills: the destructive fallback ("any doubt: replace").
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        kept = chain.ids()
        embedded = chain.embedder.texts
        deletes = AsyncMock()
        chain.db.delete_points = deletes  # type: ignore[method-assign]
        chain.db.existing_point_ids = AsyncMock(side_effect=RuntimeError("backend down"))  # type: ignore[method-assign]
        with pytest.raises(Exception) as failed:
            await chain.attempt()
        assert "backend down" in _chain_text(failed.value)
        deletes.assert_not_awaited()
        assert chain.embedder.texts == embedded
        assert kept <= chain.ids()
        assert chain.parse_calls == 1

    async def test_an_upsert_error_after_the_first_write_deletes_nothing(self) -> None:
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        kept = chain.ids()
        real = chain.db.upsert_points
        deletes = AsyncMock()
        chain.db.delete_points = deletes  # type: ignore[method-assign]
        chain.db.upsert_points = AsyncMock(side_effect=RuntimeError("write refused"))  # type: ignore[method-assign]
        with pytest.raises(Exception) as failed:
            await chain.attempt()
        assert "write refused" in _chain_text(failed.value)
        deletes.assert_not_awaited()
        assert kept <= chain.ids()
        chain.db.upsert_points = real  # type: ignore[method-assign]

    async def test_a_marker_that_cannot_be_written_fails_the_attempt_before_any_point(self) -> None:
        chain = await Chain().start()
        chain.graph.fail_writes = True
        with pytest.raises(IndexingError):
            await chain.attempt()
        assert chain.ids() == set()
        assert chain.embedder.batches == []


@pytest.mark.asyncio
class TestTheEndOfAResumedRun:
    async def test_a_late_write_of_another_revision_is_removed_by_the_end_fence(self) -> None:
        # Kills: no fence; a fence only on fresh attempts. A pod that lost its lease writes after the new one began.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        stray = VectorPoint(
            id="stray", dense_vector=[1.0, 0.0],
            payload={"page_content": "old", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "b", "contentRev": "rev0", "embedKey": "k"}},
        )
        legacy = VectorPoint(
            id="legacy", dense_vector=[1.0, 0.0],
            payload={"page_content": "older", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "b"}},
        )
        await chain.db.upsert_points(COLL, [stray, legacy])
        await chain.attempt()
        assert "stray" not in chain.ids() and "legacy" not in chain.ids()
        assert chain.ids() == await chain.wanted_ids()

    async def test_a_late_write_of_the_right_revision_and_the_wrong_embedding_setup_is_removed_too(self) -> None:
        # Kills: a fence on the revision alone.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        wrong_key = VectorPoint(
            id="wrong-key", dense_vector=[1.0, 0.0],
            payload={"page_content": "x", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "b", "contentRev": "rev1", "embedKey": "another-model"}},
        )
        await chain.db.upsert_points(COLL, [wrong_key])
        await chain.attempt()
        assert "wrong-key" not in chain.ids()
        assert chain.ids() == await chain.wanted_ids()

    async def test_the_fence_never_touches_another_orgs_points_or_another_record(self) -> None:
        # Kills: a filter without the org or the vrid.
        chain = await Chain().start()
        foreign = VectorPoint(
            id="foreign", dense_vector=[1.0, 0.0],
            payload={"page_content": "x", "metadata": {"orgId": OTHER_ORG, "virtualRecordId": VRID, "blockId": "b"}},
        )
        sibling = VectorPoint(
            id="sibling", dense_vector=[1.0, 0.0],
            payload={"page_content": "x", "metadata": {"orgId": ORG, "virtualRecordId": "vr-other", "blockId": "b"}},
        )
        await chain.db.upsert_points(COLL, [foreign, sibling])
        with pytest.raises(Cut):
            await chain.attempt(cut_after=2)
        await chain.attempt()
        assert {"foreign", "sibling"} <= chain.ids()

    async def test_a_resumed_run_that_cannot_find_every_point_at_the_end_fails(self) -> None:
        # Kills: no verification. A backend that acknowledged a write it did not keep.
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        real = chain.db.upsert_points

        async def lossy(collection_name: str, points: list[VectorPoint]) -> None:
            await real(collection_name, points[:-1])

        chain.db.upsert_points = lossy  # type: ignore[method-assign]
        with pytest.raises(IndexingError, match="incomplete"):
            await chain.attempt()

    async def test_a_fresh_attempt_asks_the_vector_store_nothing_it_did_not_before(self) -> None:
        # Kills: a probe or a fence on the first attempt.
        chain = await Chain().start()
        deletes: list[Any] = []
        real = chain.db.delete_points

        async def spy(collection: str, flt: Any, refresh: bool = False) -> None:  # noqa: ANN401
            deletes.append(flt)
            await real(collection, flt, refresh)

        chain.db.delete_points = spy  # type: ignore[method-assign]
        await chain.attempt()
        assert chain.db.existence_calls == []
        assert len(deletes) == 1  # today's one delete by virtual record


@pytest.mark.asyncio
class TestThePointsAndTheMarker:
    async def test_the_marker_is_written_before_the_first_point_and_describes_the_wanted_set(self) -> None:
        # Kills: a marker written after the writes, or without the digest.
        chain = await Chain().start()
        order: list[str] = []
        real = chain.db.upsert_points

        async def spy(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            order.append("points")
            await real(*args, **kwargs)

        chain.db.upsert_points = spy  # type: ignore[method-assign]
        real_update = chain.graph.update_node

        async def marker_spy(*args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
            order.append("marker")
            return await real_update(*args, **kwargs)

        chain.graph.update_node = marker_spy  # type: ignore[method-assign]
        with pytest.raises(Cut):
            await chain.attempt(cut_after=1)
        assert order[0] == "marker"
        marker = marker_of(chain.graph.node)
        assert set(marker) == set(EMBED_FIELDS)
        assert marker[EMBED_REV] == "rev1" and marker[EMBED_DIGEST] and marker[EMBED_KEY]
        assert marker["embedTotal"] == len(await _ids_of(chain))

    async def test_every_write_leaves_the_other_marker_fields_alone_and_the_clear_nulls_all(self) -> None:
        chain = await Chain().start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=1)
        marker = marker_of(chain.graph.node)
        assert all(set(w) <= set(EMBED_FIELDS) for w in chain.graph.writes)
        assert all(marker[name] is not None for name in EMBED_FIELDS)
        chain.graph.node.update(cleared_fields())
        assert marker_of(chain.graph.node) == {}

    async def test_point_ids_do_not_depend_on_the_stamp(self) -> None:
        # Kills: the revision or setup folded into the id, which would orphan every point written before.
        chain = await Chain().start()
        await chain.attempt()
        stamped = chain.ids()
        plain = await Chain(content=chain.content).start()
        plain.store_blob(chain.load_blob() or BlocksContainer())
        await plain.store.index_documents(plain.load_blob(), ORG, RECORD, VRID)  # no attempt: no stamp
        assert plain.ids() == stamped
        assert "contentRev" not in next(iter(plain.db.collections[COLL].values())).payload["metadata"]

    async def test_duplicate_texts_collapse_to_one_point_and_resume_the_same_way(self) -> None:
        content = json.dumps([{"a": "same"} for _ in range(30)]).encode()
        chain = await Chain(content=content).start()
        with pytest.raises(Cut):
            await chain.attempt(cut_after=1)
        await chain.attempt()
        assert chain.ids() == await chain.wanted_ids()


@pytest.mark.asyncio
class TestOpenSearchRefreshLag:
    async def test_writes_that_search_cannot_see_yet_are_still_found_by_the_existence_check(self) -> None:
        # Kills: a search-based probe. The double hides new writes from scroll for a while, as a 30 s refresh does.
        chain = await Chain().start()

        async def lagging_scroll(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
            raise AssertionError("the resume must not read through search")

        chain.db.scroll = lagging_scroll  # type: ignore[method-assign]
        chain.db.retrieve_points = AsyncMock(side_effect=AssertionError("payloads must not be read"))  # type: ignore[method-assign]
        with pytest.raises(Cut):
            await chain.attempt(cut_after=3)
        await chain.attempt()
        assert len(chain.embedder.batches) == _batches(len(await chain.wanted_ids()))
        assert chain.ids() == await chain.wanted_ids()


@pytest.mark.asyncio
async def test_two_pods_the_first_losing_its_lease_end_with_exactly_the_wanted_points() -> None:
    # Kills: a fence only on fresh attempts. Pod A is cut; pod B resumes; A's in-flight upsert lands afterwards.
    chain = await Chain().start()
    with pytest.raises(Cut):
        await chain.attempt(cut_after=3)
    late = VectorPoint(
        id="late-a", dense_vector=[1.0, 0.0],
        payload={"page_content": "from a", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "x", "contentRev": "rev0", "embedKey": "other"}},
    )
    original = chain.db.upsert_points
    landed = False

    async def upsert_then_late_write(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        nonlocal landed
        await original(*args, **kwargs)
        if not landed:
            landed = True
            await original(COLL, [late])

    chain.db.upsert_points = upsert_then_late_write  # type: ignore[method-assign]
    await chain.attempt()
    assert "late-a" not in chain.ids()
    assert chain.ids() == await chain.wanted_ids()
    await asyncio.sleep(0)


def _tables_pdf() -> bytes:
    import io

    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    story: list[Any] = []
    for page in range(3):
        story.append(Paragraph(f"Page {page} heading", styles["Heading2"]))
        for t in range(2):
            rows = [[f"H{c}" for c in range(5)]] + [[f"v{page}{t}{r}x{c}" for c in range(5)] for r in range(12)]
            table = Table(rows)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black)]))
            story += [table, Spacer(1, 14), Paragraph(f"Some text for page {page} table {t}.", styles["BodyText"]), Spacer(1, 10)]
        if page < 2:
            story.append(PageBreak())
    buf = io.BytesIO()
    SimpleDocTemplate(buf).build(story)
    return buf.getvalue()


class PdfChain(Chain):
    """Blocks from the real pdfplumber and OpenCV parser, whose blocks all carry explicit random ids."""

    async def parse(self) -> BlocksContainer:
        import logging

        from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )

        self.parse_calls += 1
        pages = await asyncio.to_thread(parse_pdf_layout, self.content)
        return await PDFPlumberOpenCVProcessor(logging.getLogger("pdf"), None).create_blocks(pages, skip_llm_enrichment=True)

    def load_blob(self, rev: str | None = None) -> BlocksContainer | None:
        # The way the parsing-service client rebuilds a container from a dump.
        if rev is not None and self.blob_rev != rev:
            return None
        return BlocksContainer(**self.blob) if self.blob else None


@pytest.mark.asyncio
async def test_a_cut_pdf_is_resumed_from_its_stored_blocks_with_the_ids_the_first_attempt_wrote() -> None:
    # Kills: resuming by re-parsing (every pdfplumber block id is random), and an id-pinning shortcut.
    chain = await PdfChain(content=_tables_pdf()).start()
    with pytest.raises(Cut):
        await chain.attempt(cut_after=2)
    first = chain.ids()
    await chain.attempt()
    wanted = await chain.wanted_ids()
    assert chain.parse_calls == 1
    assert first <= chain.ids() == wanted
    assert len(chain.embedder.batches) == _batches(len(wanted))


@pytest.mark.asyncio
async def test_the_existence_check_goes_out_in_requests_of_500_ids() -> None:
    # Kills: one request for the whole record, which is a 100,000-term request on a large one.
    chain = await Chain().start()
    sizes: list[int] = []
    real = chain.db.existing_point_ids

    async def spy(collection_name: str, ids: list[str]) -> set[str]:
        sizes.append(len(ids))
        return await real(collection_name, ids)

    chain.db.existing_point_ids = spy  # type: ignore[method-assign]
    ids = [f"id-{n}" for n in range(1201)]
    await chain.db.upsert_points(COLL, [VectorPoint(id=ids[7], dense_vector=[1.0, 0.0], payload={}), VectorPoint(id=ids[1100], dense_vector=[1.0, 0.0], payload={})])
    assert await chain.store._existing_ids(COLL, ids) == {ids[7], ids[1100]}
    assert sizes == [500, 500, 201]


@pytest.mark.asyncio
async def test_a_document_without_a_block_id_gets_the_same_point_id_with_or_without_a_marker() -> None:
    # The stamp goes on the payload after the id is derived. An id with no block id hashes all of its metadata,
    # so this is the case where folding the stamp into the id would show.
    from langchain_core.documents import Document

    from app.modules.transformers.vectorstore import deterministic_point_id

    chain = await Chain().start()
    metadata = {"orgId": ORG}
    wanted = deterministic_point_id(metadata, "text")
    begin_attempt(EmbedAttempt(delivery_id="d1", rev="rev1", vrid=VRID))
    from app.modules.indexing.embed_resume import current_attempt

    current_attempt().stamp = {"contentRev": "rev1", "embedKey": "k"}  # type: ignore[union-attr]
    await chain.store._embed_and_upsert_documents([Document(page_content="text", metadata=dict(metadata))], RECORD, COLL)
    assert chain.ids() == {wanted}
    assert chain.db.collections[COLL][wanted].payload["metadata"]["contentRev"] == "rev1"
