"""Retention by reference: the two newest completed revisions' objects stay, an in-flight revision's are never swept."""

from __future__ import annotations

import json
from typing import Any

from app.services.paid_cache.retention import GRACE_MS, sweep_record
from app.services.paid_cache.run import OPEN_PROTECTS_MS, RunScope
from tests.support.paid_cache import MemoryResultStore

ORG, REC = "org-1", "rec-1"
HOUR = 3600 * 1000
DAY = 24 * HOUR


def _obj(n: int) -> str:
    return f"ocr_page/{n:064x}.json"


class Clock:
    """The storage service's write clock and the sweep's now, moved together by a test."""

    def __init__(self, store: MemoryResultStore) -> None:
        self.store = store

    def advance(self, ms: int) -> None:
        self.store.now_ms += ms

    @property
    def now(self) -> int:
        return self.store.now_ms


async def _revision(store: MemoryResultStore, rev: str, objects: list[int], *, writer: str = "parse") -> None:
    """One completed revision: its objects written, then its final manifest."""
    for n in objects:
        store.files[(ORG, REC, _obj(n))] = (b"{}", store.now_ms)
    run = RunScope(ORG, REC, rev, writer, clock=lambda: store.now_ms / 1000)
    for n in objects:
        await run.note(store, _obj(n), opens=True)
    await run.finish(store)


async def test_two_newest_revisions_kept() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    # R1..R4: each keeps pages {1, 2} (unchanged content) and adds one page of its own.
    for i, rev in enumerate(("r1", "r2", "r3", "r4"), start=1):
        await _revision(store, rev, [1, 2, 10 + i])
        clock.advance(2 * HOUR)

    result = await sweep_record(store, ORG, REC, now_ms=clock.now)

    kept = store.results(ORG, REC)
    # Only objects the manifests of R4 and R3 reference: the shared pages and R3's and R4's own.
    assert kept == sorted([_obj(1), _obj(2), _obj(13), _obj(14)])
    manifests = [p for p in store.paths(ORG, REC) if p.startswith("manifests/")]
    assert manifests == ["manifests/r3/parse.json", "manifests/r4/parse.json"]
    assert result.deleted == 4  # R1's and R2's own pages, and their two manifests


async def test_storage_does_not_grow_with_the_number_of_revisions() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    sizes = []
    for i in range(1, 9):
        await _revision(store, f"r{i}", [100 * i, 100 * i + 1])
        clock.advance(2 * HOUR)
        await sweep_record(store, ORG, REC, now_ms=clock.now)
        sizes.append(len(store.results(ORG, REC)))
    assert max(sizes) <= 4  # two revisions of two objects each


async def test_a_failed_run_keeps_its_objects_until_two_newer_revisions_complete() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    await _revision(store, "r3", [3])
    clock.advance(2 * HOUR)
    await _revision(store, "r4", [4])
    clock.advance(2 * HOUR)
    # R5 wrote two pages, flushed its open manifest, and then its run failed (no final manifest).
    failed = RunScope(ORG, REC, "r5", "parse", clock=lambda: store.now_ms / 1000)
    for n in (50, 51):
        store.files[(ORG, REC, _obj(n))] = (b"{}", store.now_ms)
        await failed.note(store, _obj(n), opens=True)
    await failed.flush(store)
    clock.advance(2 * HOUR)

    await sweep_record(store, ORG, REC, now_ms=clock.now)
    assert {_obj(50), _obj(51)} <= set(store.results(ORG, REC))      # protected by the open manifest

    clock.advance(OPEN_PROTECTS_MS)                                   # the open manifest is stale now
    await sweep_record(store, ORG, REC, now_ms=clock.now)
    assert {_obj(50), _obj(51)} <= set(store.results(ORG, REC))      # one newer completed revision is not enough? none yet

    await _revision(store, "r6", [6])
    clock.advance(2 * HOUR)
    await _revision(store, "r7", [7])
    clock.advance(2 * HOUR)
    await sweep_record(store, ORG, REC, now_ms=clock.now)
    assert store.results(ORG, REC) == sorted([_obj(6), _obj(7)])      # two newer revisions completed


async def test_a_retry_of_the_failed_run_reuses_its_objects() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    await _revision(store, "r1", [1])
    clock.advance(2 * HOUR)
    failed = RunScope(ORG, REC, "r2", "parse", clock=lambda: store.now_ms / 1000)
    store.files[(ORG, REC, _obj(20))] = (b"{}", store.now_ms)
    await failed.note(store, _obj(20), opens=True)
    await failed.flush(store)
    clock.advance(3 * HOUR)
    # Another revision completes and sweeps while R2 is still failed.
    await _revision(store, "r1b", [1])
    clock.advance(2 * HOUR)

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert _obj(20) in store.results(ORG, REC)


async def test_sweep_spares_an_in_flight_revision() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    # R_n completed long ago and R_{n-1} before it.
    await _revision(store, "r1", [1])
    clock.advance(2 * HOUR)
    await _revision(store, "r2", [2])
    clock.advance(2 * HOUR)
    await _revision(store, "r3", [3])
    clock.advance(2 * HOUR)
    # R_{n+1} is mid-parse: 200 of 500 pages stored, the open manifest flushed at 150.
    run = RunScope(ORG, REC, "r4", "parse", clock=lambda: store.now_ms / 1000)
    for n in range(1000, 1200):
        store.files[(ORG, REC, _obj(n))] = (b"{}", store.now_ms)
        if n < 1150:
            await run.note(store, _obj(n), opens=True)
    await run.flush(store)
    clock.advance(10 * 60 * 1000)   # ten minutes later R_n's sweep runs

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    survivors = set(store.results(ORG, REC))
    assert {_obj(n) for n in range(1000, 1200)} <= survivors      # 150 by the open manifest, 50 by the 1 h grace
    # R_{n+1} aborts and retries: every page is still there to hit.
    assert all((ORG, REC, _obj(n)) in store.files for n in range(1000, 1200))

    # Two hours on the grace has passed: the 150 the open manifest lists are still protected (it is under 24 h
    # old), and R1, beyond the two newest revisions, goes. The 50 that were never flushed are no longer spared,
    # which is why the manifest is flushed every 50 keys or 30 s.
    clock.advance(2 * HOUR)
    await sweep_record(store, ORG, REC, now_ms=clock.now)
    survivors = set(store.results(ORG, REC))
    assert {_obj(n) for n in range(1000, 1150)} <= survivors
    assert not {_obj(n) for n in range(1150, 1200)} & survivors
    assert _obj(1) not in survivors


async def test_an_object_written_within_the_grace_is_never_swept() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    await _revision(store, "r1", [1])
    clock.advance(2 * HOUR)
    await _revision(store, "r2", [2])
    clock.advance(2 * HOUR)
    store.files[(ORG, REC, _obj(99))] = (b"{}", clock.now - GRACE_MS + 1000)   # written 59 minutes ago, in no manifest
    store.files[(ORG, REC, _obj(98))] = (b"{}", clock.now - GRACE_MS - 1000)   # written 61 minutes ago, in no manifest

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert _obj(99) in store.results(ORG, REC)
    assert _obj(98) not in store.results(ORG, REC)


async def test_with_no_completed_revision_nothing_is_deleted() -> None:
    store = MemoryResultStore()
    for n in (1, 2, 3):
        store.files[(ORG, REC, _obj(n))] = (b"{}", 1)

    result = await sweep_record(store, ORG, REC, now_ms=store.now_ms + 10 * DAY)

    assert result.skipped_reason == "no completed revision"
    assert len(store.results(ORG, REC)) == 3
    assert store.ops["delete"] == 0


async def test_a_sweep_never_touches_another_record_or_org() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    for rev in ("r1", "r2", "r3"):
        await _revision(store, rev, [int(rev[1:])])
        clock.advance(2 * HOUR)
    store.files[("org-2", REC, _obj(1))] = (b"{}", 1)
    store.files[(ORG, "rec-2", _obj(1))] = (b"{}", 1)

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert ("org-2", REC, _obj(1)) in store.files
    assert (ORG, "rec-2", _obj(1)) in store.files
    assert store.touched == {(ORG, REC)}


async def test_two_writers_of_one_revision_both_count() -> None:
    store = MemoryResultStore()
    clock = Clock(store)
    await _revision(store, "r1", [1], writer="parse")
    await _revision(store, "r1", [2], writer="index")
    clock.advance(2 * HOUR)
    await _revision(store, "r2", [3], writer="parse")
    clock.advance(2 * HOUR)
    await _revision(store, "r3", [4], writer="parse")
    clock.advance(2 * HOUR)

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    # r1 is beyond the two newest (r2, r3), whatever its writers.
    assert store.results(ORG, REC) == sorted([_obj(3), _obj(4)])


async def test_the_manifest_lists_every_key_the_run_used_once() -> None:
    store = MemoryResultStore()
    run = RunScope(ORG, REC, "r1", "parse", clock=lambda: store.now_ms / 1000)
    for n in (1, 2, 1, 3, 2):
        await run.note(store, _obj(n), opens=False)
    await run.finish(store)

    body = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])
    assert body["keys"] == [_obj(1), _obj(2), _obj(3)]
    assert (ORG, REC, "manifests/r1/parse.open.json") not in store.files   # the open one is gone


async def test_the_open_manifest_exists_before_the_first_object_and_is_flushed_every_fifty_keys() -> None:
    store = MemoryResultStore()
    run = RunScope(ORG, REC, "r1", "parse", clock=lambda: store.now_ms / 1000)

    await run.note(store, _obj(0), opens=True)
    opened = json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])
    assert opened["keys"] == [_obj(0)] and opened["startedMs"] == store.now_ms

    for n in range(1, 50):
        await run.note(store, _obj(n), opens=True)
    assert len(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == 1   # 49 not yet

    await run.note(store, _obj(50), opens=True)                                                     # the 50th since the last write
    assert len(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == 51

    for n in range(51, 60):
        await run.note(store, _obj(n), opens=True)
    assert len(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == 51   # not yet

    store.now_ms += 31_000
    await run.note(store, _obj(60), opens=True)
    assert len(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == 61  # 30 s passed


# -- QA-S7a-1: revisions are ordered by what the writer wrote in the manifest, not by the storage write time ----------

class _WholeSecondStore(MemoryResultStore):
    """Azure and AWS S3 report a listing's ``LastModified`` in whole seconds."""

    async def list_files(self, org_id: str, record_id: str, prefix: str = "") -> list:
        from dataclasses import replace

        return [replace(f, last_modified_ms=f.last_modified_ms // 1000 * 1000)
                for f in await super().list_files(org_id, record_id, prefix)]


async def _revision_at(store: MemoryResultStore, rev: str, objects: list[int], at_ms: int) -> None:
    """A revision completed at ``at_ms`` by the writer's clock while the storage clock stays where it is."""
    for n in objects:
        store.files[(ORG, REC, _obj(n))] = (b"{}", store.now_ms)
    run = RunScope(ORG, REC, rev, "parse", clock=lambda: at_ms / 1000)
    for n in objects:
        await run.note(store, _obj(n), opens=True)
    await run.finish(store)


async def test_revisions_that_share_one_storage_second_are_ordered_by_their_manifests() -> None:
    store = _WholeSecondStore()
    base = store.now_ms
    for i in range(1, 6):                                          # five revisions written within one second
        await _revision_at(store, f"r{i}", [i], base + i * 10)

    result = await sweep_record(store, ORG, REC, now_ms=base + 2 * DAY)

    assert store.results(ORG, REC) == sorted([_obj(4), _obj(5)])    # the two newest, not an arbitrary two
    assert [p for p in store.paths(ORG, REC) if p.startswith("manifests/")] == ["manifests/r4/parse.json", "manifests/r5/parse.json"]
    assert result.deleted == 6


async def test_revisions_that_complete_in_the_same_millisecond_are_ordered_by_the_writers_sequence() -> None:
    store = _WholeSecondStore()
    base = store.now_ms
    for i in range(1, 6):
        # One writer, one millisecond, five revisions; the names sort against the order they completed in.
        await _revision_at(store, f"r{6 - i}", [i], base + 5)

    await sweep_record(store, ORG, REC, now_ms=base + 2 * DAY)

    assert store.results(ORG, REC) == sorted([_obj(4), _obj(5)])


async def test_a_failed_run_is_kept_until_two_newer_revisions_complete_within_one_storage_second() -> None:
    store = _WholeSecondStore()
    base = store.now_ms
    await _revision_at(store, "r1", [1], base + 10)
    # r2 fails: its open manifest only, started after r1 completed.
    store.files[(ORG, REC, _obj(20))] = (b"{}", store.now_ms)
    failed = RunScope(ORG, REC, "r2", "parse", clock=lambda: (base + 20) / 1000)
    await failed.note(store, _obj(20), opens=True)
    await _revision_at(store, "r3", [3], base + 30)
    await sweep_record(store, ORG, REC, now_ms=base + 2 * DAY)
    assert _obj(20) in store.results(ORG, REC)                      # one newer completed revision: still kept

    await _revision_at(store, "r4", [4], base + 40)
    await sweep_record(store, ORG, REC, now_ms=base + 2 * DAY)
    assert _obj(20) not in store.results(ORG, REC)                  # two newer: swept
    assert store.results(ORG, REC) == sorted([_obj(3), _obj(4)])


async def test_a_manifest_carries_its_completion_time_and_sequence() -> None:
    store = MemoryResultStore()
    await _revision_at(store, "r1", [1], store.now_ms + 7)
    await _revision_at(store, "r2", [2], store.now_ms + 7)
    one, two = (json.loads(store.files[(ORG, REC, f"manifests/{r}/parse.json")][0]) for r in ("r1", "r2"))
    assert one["completedMs"] == two["completedMs"] and two["seq"] > one["seq"]
    assert one["completedMs"] >= one["startedMs"]


async def test_an_object_a_run_reuses_while_the_sweep_runs_is_not_deleted() -> None:
    """A run notes an old object in its manifest after the sweep listed the folder: the second read protects it."""
    store = MemoryResultStore()
    clock = Clock(store)
    for i, rev in enumerate(("r1", "r2", "r3"), start=1):
        await _revision(store, rev, [10 + i])
        clock.advance(2 * HOUR)
    store.files[(ORG, REC, _obj(99))] = (b"{}", store.now_ms - 3 * DAY)      # old, referenced by no kept manifest

    real_list = store.list_files
    calls = 0

    async def list_files(org: str, rec: str, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        nonlocal calls
        calls += 1
        result = await real_list(org, rec, *a, **k)
        if calls == 1:                                                      # after the first listing: a run reuses it
            run = RunScope(ORG, REC, "r4", "parse", clock=lambda: store.now_ms / 1000)
            await run.note(store, _obj(99), opens=True)
        return result

    store.list_files = list_files  # type: ignore[method-assign]
    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert _obj(99) in store.results(ORG, REC), "the sweep deleted an object a concurrent run had just listed"


async def test_a_failed_writers_references_survive_while_its_revision_is_retained() -> None:
    """R1: the parser completed, the image writer failed. R1 is still one of the two retained revisions."""
    store = MemoryResultStore()
    clock = Clock(store)
    await _revision(store, "r1", [1], writer="parse")
    store.files[(ORG, REC, _obj(77))] = (b"{}", store.now_ms)            # an image the failed writer paid for
    failed = RunScope(ORG, REC, "r1", "index", clock=lambda: store.now_ms / 1000)
    await failed.note(store, _obj(77), opens=True)                      # its open manifest stays
    clock.advance(2 * DAY)                                              # long past OPEN_PROTECTS_MS? no: past the grace
    clock.advance(OPEN_PROTECTS_MS)

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert _obj(77) in store.results(ORG, REC), "an object a retained revision's failed writer paid for was deleted"


async def test_a_failed_writer_of_a_retained_revision_is_kept_even_when_two_revisions_completed_after_it_started() -> None:
    """Only ``rev in kept_revs`` protects here: the writer's start lies before both completions."""
    store = MemoryResultStore()
    clock = Clock(store)
    failed = RunScope(ORG, REC, "r1", "index", clock=lambda: store.now_ms / 1000)   # starts first
    store.files[(ORG, REC, _obj(55))] = (b"{}", store.now_ms)
    await failed.note(store, _obj(55), opens=True)
    clock.advance(2 * HOUR)
    await _revision(store, "r1", [1], writer="parse")                   # r1 completes: retained
    clock.advance(2 * HOUR)
    await _revision(store, "r2", [2], writer="parse")                   # the newest
    clock.advance(OPEN_PROTECTS_MS + HOUR)                              # the open manifest is no longer young

    await sweep_record(store, ORG, REC, now_ms=clock.now)

    assert _obj(55) in store.results(ORG, REC)
