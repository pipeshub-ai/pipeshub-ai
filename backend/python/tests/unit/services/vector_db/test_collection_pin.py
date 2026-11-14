"""An index keeps one weight precision: the pin, its claim, and the checks in front of a write."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import (
    FP32,
    IMAGE_TAG,
    INT8,
    PIN_DIRECTORY,
    STAMP_TAG,
    CollectionPinStore,
)
from app.services.vector_db.precision_guard import PrecisionGuard, PrecisionUnreadable

log = logging.getLogger("pin-test")


class _FakeDb:
    """Just enough of a vector store: points with an org, a record id and a tag."""

    def __init__(self) -> None:
        self.points: list[dict] = []
        self.counts = 0

    def add(self, tag: str | None, *, org: str = "o1", vrid: str = "v1", n: int = 1) -> None:
        self.points += [{"orgId": org, "virtualRecordId": vrid, "embedPrecision": tag} for _ in range(n)]

    async def filter_collection(self, must=None, **_k):  # noqa: ANN202
        return dict(must or {})

    async def count_points(self, collection, expression):  # noqa: ANN202
        self.counts += 1
        return sum(
            all(
                p.get(k) in v if isinstance(v, list) else p.get(k) == v
                for k, v in expression.items()
            )
            for p in self.points
        )

    async def delete_points(self, collection, expression):  # noqa: ANN202
        self.points = [
            p for p in self.points
            if not all((p.get(k) in v if isinstance(v, list) else p.get(k) == v) for k, v in expression.items())
        ]

    async def get_collection_info(self, collection):  # noqa: ANN202
        return SimpleNamespace(exists=True, points_count=len(self.points), dense_dimension=4)

    async def make_searchable(self, collection):  # noqa: ANN202
        return None


def _service(store=None) -> ConfigurationService:
    return ConfigurationService(log, store or InMemoryKeyValueStore(log))


def _guard(db=None, store=None, clock=None):  # noqa: ANN202
    config = _service(store)
    pins = CollectionPinStore(config, log, **({"clock": clock} if clock else {}))
    return PrecisionGuard(pins, db or _FakeDb(), log), pins, config


async def _verify(guard, precision, *, fallback=False, collection="c", org="o1"):  # noqa: ANN202
    return await guard.verify_batch(collection, org, precision, fallback=fallback, intent=INT8)


class TestFirstWrite:
    @pytest.mark.asyncio
    async def test_the_first_write_claims_the_pin(self) -> None:
        guard, pins, _ = _guard()
        await _verify(guard, INT8)
        assert (await pins.get("c", "o1", fresh=True)).precision == INT8

    @pytest.mark.asyncio
    async def test_every_batch_reads_the_pin_uncached_and_counts_nothing(self) -> None:
        db = _FakeDb()
        guard, _, config = _guard(db)
        await _verify(guard, INT8)
        reads, counts = [], db.counts
        original = config.get_config

        async def counting(*a, **k):  # noqa: ANN202
            reads.append(k.get("use_cache"))
            return await original(*a, **k)

        config.get_config = counting
        for _ in range(5):
            await _verify(guard, INT8)
        assert reads == [False] * 5 and db.counts == counts

    @pytest.mark.asyncio
    async def test_image_points_do_not_make_an_index_legacy(self) -> None:
        db = _FakeDb()
        db.add(IMAGE_TAG, n=3)
        db.add(STAMP_TAG)
        guard, pins, _ = _guard(db)
        await _verify(guard, INT8)  # an empty index as far as text goes: the first writer claims
        assert (await pins.get("c", "o1", fresh=True)).precision == INT8


class TestRefusal:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("pinned", "writer"), [(INT8, FP32), (FP32, INT8)])
    async def test_the_other_precision_is_refused_before_anything_is_written(self, pinned, writer) -> None:
        guard, pins, _ = _guard()
        await _verify(guard, pinned)
        with pytest.raises(PrecisionFlip):
            await _verify(guard, writer)
        assert (await pins.get("c", "o1", fresh=True)).precision == pinned

    @pytest.mark.asyncio
    async def test_the_refusal_says_what_to_set_back_and_what_the_cleanup_deletes(self) -> None:
        guard, _, _ = _guard()
        await _verify(guard, INT8)
        with pytest.raises(PrecisionFlip) as refused:
            await _verify(guard, FP32)
        text = str(refused.value)
        assert "EMBEDDING_SERVER_PRECISION=int8" in text and "restart the embedding server" in text
        assert "Clean up embeddings" in text and "deletes the whole search index" in text

    @pytest.mark.asyncio
    async def test_no_other_writer_can_change_a_pin(self) -> None:
        guard, pins, _ = _guard()
        await _verify(guard, INT8)
        other, _, _ = _guard(store=None)
        pins2 = CollectionPinStore(_service(pins._config.store), log)
        standing = await pins2.claim("c", FP32, "o1")
        assert standing.precision == INT8

    @pytest.mark.asyncio
    async def test_an_unreadable_pin_fails_closed(self) -> None:
        guard, _, config = _guard()

        async def broken(*_a, **_k):  # noqa: ANN202
            raise ConnectionError("kv down")

        config.get_config = broken
        with pytest.raises(PrecisionUnreadable):
            await _verify(guard, FP32)


class TestFallback:
    @pytest.mark.asyncio
    async def test_a_fallback_batch_never_claims(self) -> None:
        guard, pins, _ = _guard()
        with pytest.raises(PrecisionFlip, match="fell back"):
            await _verify(guard, FP32, fallback=True)
        assert await pins.get("c", "o1", fresh=True) is None
        await _verify(guard, INT8)  # the next boot serves int8: it claims int8
        assert (await pins.get("c", "o1", fresh=True)).precision == INT8

    @pytest.mark.asyncio
    async def test_a_fallback_into_an_fp32_index_continues(self) -> None:
        guard, _, _ = _guard()
        await _verify(guard, FP32)
        await _verify(guard, FP32, fallback=True)

    @pytest.mark.asyncio
    async def test_a_fallback_into_an_int8_index_is_refused(self) -> None:
        guard, _, _ = _guard()
        await _verify(guard, INT8)
        with pytest.raises(PrecisionFlip):
            await _verify(guard, FP32, fallback=True)


class TestFirstClaimOnAnExistingIndex:
    @pytest.mark.asyncio
    async def test_tagged_int8_points_make_the_index_int8_and_restore_the_pin(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=2)
        guard, pins, _ = _guard(db)
        with pytest.raises(PrecisionFlip):
            await _verify(guard, FP32)
        restored = await pins.get("c", "o1", fresh=True)
        assert restored.precision == INT8 and restored.derived is True

    @pytest.mark.asyncio
    async def test_tagged_fp32_points_refuse_an_int8_writer(self) -> None:
        db = _FakeDb()
        db.add(FP32)
        guard, _, _ = _guard(db)
        with pytest.raises(PrecisionFlip):
            await _verify(guard, INT8)

    @pytest.mark.asyncio
    async def test_legacy_untagged_points_are_fp32(self) -> None:
        db = _FakeDb()
        db.add(None, n=4)
        guard, pins, _ = _guard(db)
        with pytest.raises(PrecisionFlip):
            await _verify(guard, INT8)
        assert await pins.get("c", "o1", fresh=True) is None  # an implicit pin writes nothing
        await _verify(guard, FP32)

    @pytest.mark.asyncio
    async def test_both_precisions_in_one_index_is_unreadable_not_guessed(self) -> None:
        db = _FakeDb()
        db.add(INT8)
        db.add(FP32)
        guard, _, _ = _guard(db)
        with pytest.raises(PrecisionUnreadable):
            await _verify(guard, FP32)


class TestTwoProcesses:
    @pytest.mark.asyncio
    async def test_two_processes_claiming_with_different_precision_leave_one_pin(self) -> None:
        store = InMemoryKeyValueStore(log)
        original = store.create_key

        async def slow(*a, **k):  # noqa: ANN202
            await asyncio.sleep(0.01)
            return await original(*a, **k)

        store.create_key = slow
        a, _, _ = _guard(store=store)
        b, _, _ = _guard(store=store)
        results = await asyncio.gather(_verify(a, INT8), _verify(b, FP32), return_exceptions=True)
        refused = [r for r in results if isinstance(r, PrecisionFlip)]
        assert len(refused) == 1

    @pytest.mark.asyncio
    async def test_a_process_that_never_heard_of_a_cleanup_does_not_store_beside_the_new_precision(self) -> None:
        db = _FakeDb()
        store = InMemoryKeyValueStore(log)
        a, _, _ = _guard(db, store)
        b, pins_b, config_b = _guard(db, store)

        async def write(guard, precision):  # noqa: ANN202
            await _verify(guard, precision)
            db.add(precision)

        await write(a, INT8)  # A has read the pin and holds it in its own cache
        db.points.clear()  # the cleanup runs in B: vectors first, then the pin; A is told nothing
        await pins_b.release_collection("c")
        config_b.cache.clear()
        await write(b, FP32)
        with pytest.raises(PrecisionFlip):
            await write(a, INT8)  # the server is back to int8; the batch is refused, not stored
        assert {p["embedPrecision"] for p in db.points} == {FP32}

    @pytest.mark.asyncio
    async def test_a_derived_restore_never_uses_a_cached_count_after_a_drop(self) -> None:
        db = _FakeDb()
        db.add(FP32, n=2)
        store = InMemoryKeyValueStore(log)
        a, pins_a, _ = _guard(db, store)
        b, pins_b, _ = _guard(db, store)
        standing = await b.standing("c", "o1")  # B caches "tagged fp32 present"
        assert standing.precision == FP32
        # The cleanup on A: vectors first, then the pin, nothing left behind.
        db.points.clear()
        await pins_a.release("c", "o1")
        await b.restore("c", "o1", FP32)  # B acts on what it saw before the drop
        assert await pins_a.get("c", "o1", fresh=True) is None

    @pytest.mark.asyncio
    async def test_a_derived_pin_over_an_empty_collection_is_released(self) -> None:
        db = _FakeDb()
        db.add(INT8)
        guard, pins, _ = _guard(db)
        await guard.restore("c", "o1", INT8)
        db.points.clear()
        assert await guard.release_stale_derived("c", "o1") is True
        assert await pins.get("c", "o1", fresh=True) is None

    @pytest.mark.asyncio
    async def test_an_explicit_pin_is_never_released_by_the_derived_rule(self) -> None:
        guard, pins, _ = _guard()
        await _verify(guard, INT8)
        assert await guard.release_stale_derived("c", "o1") is False
        assert await pins.get("c", "o1", fresh=True) is not None


class TestStalePin:
    @pytest.mark.asyncio
    async def test_a_creator_never_replaces_a_pin_claimed_after_it_observed_absence(self) -> None:
        now = [1000.0]
        guard, pins, _ = _guard(clock=lambda: now[0])
        t0 = now[0]
        now[0] += 5  # another writer claims after the collection came to exist
        await pins.claim("c", INT8, "o1")
        assert await pins.replace_stale("c", FP32, t0, "o1") is False
        assert (await pins.get("c", "o1", fresh=True)).precision == INT8

    @pytest.mark.asyncio
    async def test_a_pin_from_before_the_observed_absence_is_replaced(self) -> None:
        now = [1000.0]
        _, pins, _ = _guard(clock=lambda: now[0])
        await pins.claim("c", INT8, "o1")
        now[0] += 3600
        assert await pins.replace_stale("c", FP32, now[0], "o1") is True
        assert (await pins.get("c", "o1", fresh=True)).precision == FP32

    @pytest.mark.asyncio
    async def test_a_young_pin_over_a_crash_leftover_is_kept(self) -> None:
        now = [1000.0]
        _, pins, _ = _guard(clock=lambda: now[0])
        await pins.claim("c", INT8, "o1")
        now[0] += 10
        assert await pins.replace_stale("c", FP32, now[0], "o1") is False


class TestRelease:
    @pytest.mark.asyncio
    async def test_dropping_a_collection_releases_its_pin_and_every_orphan(self) -> None:
        guard, pins, config = _guard()
        await _verify(guard, INT8, collection="c")
        await pins.claim("gone", FP32, "o9")
        assert len(await pins.keys()) == 2
        assert await pins.release_all() == 2
        assert await pins.keys() == []
        await _verify(guard, FP32, collection="c")  # a new index may take the other precision

    @pytest.mark.asyncio
    async def test_a_delete_the_store_cannot_confirm_is_raised(self) -> None:
        _, pins, config = _guard()
        await pins.claim("c", INT8, "o1")

        async def refuses(_key):  # noqa: ANN202
            return False

        async def unreadable(*_a, **_k):  # noqa: ANN202
            raise ConnectionError("kv down")

        config.delete_config = refuses
        config.get_config = unreadable
        with pytest.raises(ConnectionError):
            await pins.release("c", "o1")

    @pytest.mark.asyncio
    async def test_the_manifest_is_not_touched(self) -> None:
        guard, _, config = _guard()
        await _verify(guard, INT8)
        assert await config.get_config("/services/vectordb/collection_manifest", use_cache=False) is None
        assert all(k.startswith(PIN_DIRECTORY) for k in await config.list_keys_in_directory("/services/vectordb"))


class TestLeaks:
    @pytest.mark.asyncio
    async def test_only_the_leaked_points_of_the_record_are_found_and_removed(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=3, vrid="v1")
        db.add(FP32, n=2, vrid="v1")  # leaked into an int8 index
        db.add(IMAGE_TAG, n=2, vrid="v1")
        db.add(FP32, n=1, vrid="v2")  # another record's point is not this record's leak
        guard, _, _ = _guard(db)
        assert await guard.leaked("c", "o1", "v1", INT8) == 2
        await guard.delete_leaked("c", "o1", "v1", INT8)
        assert [p["embedPrecision"] for p in db.points if p["virtualRecordId"] == "v1"] == [INT8] * 3 + [IMAGE_TAG] * 2
        assert any(p["virtualRecordId"] == "v2" for p in db.points)

    @pytest.mark.asyncio
    async def test_the_leak_tag_is_the_other_precision_and_never_an_absence(self) -> None:
        assert PrecisionGuard.leak_tag(FP32) == INT8 and PrecisionGuard.leak_tag(INT8) == FP32


class TestRegistryDrop:
    @pytest.mark.asyncio
    async def test_dropping_a_collection_releases_its_pin_after_the_vectors(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.services.vector_db.collection_registry import CollectionRegistry

        config = _service()
        order: list[str] = []
        db = MagicMock()

        async def drop(name):  # noqa: ANN202
            order.append("vectors")

        db.delete_collection = drop
        manifest = MagicMock(config_service=config)
        manifest.forget = AsyncMock()
        registry = CollectionRegistry(db, MagicMock(), lambda n: None, manifest, log)
        pins = CollectionPinStore(config, log)
        await pins.claim("c", INT8)
        await pins.claim("c", INT8, "o1")
        await pins.claim("other", INT8)
        original = pins.release_collection

        await registry.delete_collection("c")
        assert order == ["vectors"]
        assert list(await pins.keys()) == [pins.key("other")]
        assert original is not None
