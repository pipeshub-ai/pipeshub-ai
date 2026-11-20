"""A run reusing an old stored result while the retention sweep runs, and a refresh purging, over every interleaving.

The real ``sweep_record`` and the real ``BumpQueue`` run against a store that versions objects the way S3 and MinIO
(content-hash ETag), Azure and local storage do, with a seeded scheduler choosing, at every store call, which of the
sweep, the reuse and the refresh goes next, and random gaps of store time between calls. The invariants:

* no refresh: the reused object is present at the end (the run never loses what it reused);
* a refresh: no pre-refresh bytes are left at the end, whatever the whole-second stamps do.

The control (a copy in place, which keeps the content-hash ETag, the first design) must lose objects on the S3 model:
that is what proves the model can see the bug.
"""

from __future__ import annotations

import asyncio
import contextvars
import random
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.bump import BUMP_AFTER_MS, BumpEntry, BumpQueue
from app.services.paid_cache.retention import GRACE_MS, sweep_record
from app.services.paid_cache.run import RunScope
from tests.support.versioned_store import VersionedStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ORG, REC = "org-1", "rec-1"
K = f"ocr_page/{1:064x}.json"
OLD = b'{"v":1,"kind":"ocr_page","value":"OLD"}'
MINUTE = 60_000
GAPS = (0, 1, 5, 40, 300, 700)
_ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("actor")


class Scheduler:
    """Runs the actors one store call at a time, in an order the seed picks."""

    def __init__(self, rng: random.Random, store: VersionedStore) -> None:
        self.rng, self.store = rng, store
        self.waiting: dict[str, asyncio.Future[None]] = {}

    async def point(self, _op: str) -> None:
        actor = _ACTOR.get(None)
        if actor is None:
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiting[actor] = fut
        await fut

    async def run(self, actors: dict[str, object]) -> None:
        tasks: dict[str, asyncio.Task[None]] = {}
        for name, fn in actors.items():
            ctx = contextvars.copy_context()
            ctx.run(_ACTOR.set, name)
            tasks[name] = asyncio.get_running_loop().create_task(fn(), context=ctx)  # type: ignore[operator]
        while True:
            for _ in range(200):
                await asyncio.sleep(0)
                if all(t.done() or n in self.waiting for n, t in tasks.items()):
                    break
            else:  # pragma: no cover - a model bug, not a product one
                raise AssertionError("an actor neither finished nor reached a store call")
            for t in tasks.values():
                if t.done() and t.exception() is not None:
                    raise t.exception()  # type: ignore[misc]
            if not self.waiting:
                return
            name = self.rng.choice(sorted(self.waiting))
            self.waiting.pop(name).set_result(None)
            self.store.now_ms += self.rng.choice(GAPS)


async def _trial(
    provider: str, seed: str, *, refresh: bool, copy_bump: bool = False, gone_412: bool = False, post_check_delay: bool = True,
) -> tuple[bool, bool]:
    """(lost: the reused object is missing at the end, stale: pre-refresh bytes are left at the end)."""
    rng = random.Random(seed)
    store = VersionedStore(
        etag_mode="md5" if provider in ("s3", "minio", "minio-no-conditional-delete") else "unique", stamp_ms=1 if provider == "local" else 1000,
        now_ms=1_700_000_000_000 + rng.randint(0, 999), copy_in_place_bump=copy_bump, gone_is_412=gone_412,
        conditional_delete=provider != "minio-no-conditional-delete",
    )
    sched = Scheduler(rng, store)
    # Two newer completed revisions that do not list K: it is a delete candidate once it is old enough.
    age = rng.choice([31 * MINUTE, 45 * MINUTE, 61 * MINUTE, 2 * 60 * MINUTE, 24 * 60 * MINUTE])
    store.write(ORG, REC, K, OLD, age_ms=age)
    for rev in ("r2", "r3"):
        run = RunScope(ORG, REC, rev, "parse", clock=lambda: store.now_ms / 1000)
        other = f"ocr_page/{int(rev[1:]):064x}.json"
        store.write(ORG, REC, other, b"{}")
        await run.note(store, other, opens=True)  # type: ignore[arg-type]
        await run.finish(store)  # type: ignore[arg-type]
        store.now_ms += 1000
    store.now_ms += 2 * 60 * MINUTE                       # the objects of r2 and r3 are older than the grace too
    store.before_op = sched.point
    seen: dict[str, bool] = {}

    async def sweeper() -> None:
        await sweep_record(store, ORG, REC, now_ms=store.now_ms)  # type: ignore[arg-type]

    async def reuse() -> None:
        got = await store.get_versioned(ORG, REC, K)
        if got is None or got.etag is None or got.last_modified_ms is None:
            return
        seen["held"] = True
        queue = BumpQueue(store, sleep=_advance(store) if post_check_delay else _no_wait, autostart=False)  # type: ignore[arg-type]
        queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, got.last_modified_ms, None))
        await queue.drain_once()

    async def refresher() -> None:
        await store._op("refresh_start")
        store.begin_refresh(ORG, REC)
        await store._op("marker_set")
        cutoff = store.cutoff()
        await store._op("cutoff")
        await store.purge_before(ORG, REC, cutoff)

    actors: dict[str, object] = {"sweep": sweeper, "reuse": reuse}
    if refresh:
        actors["refresh"] = refresher
    await sched.run(actors)
    stored = store.files.get((ORG, REC, K))
    return (stored is None and not refresh and seen.get("held", False)), (stored is not None and b"OLD" in stored[0] and refresh)


async def _no_wait(_seconds: float) -> None:
    return None


def _advance(store: VersionedStore) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        store.now_ms += int(seconds * 1000)
    return sleep


TRIALS = 150
SEEDS = (5150, 77731)


@pytest.mark.parametrize("provider", ["s3", "minio", "azure", "local"])
@pytest.mark.parametrize("seed", SEEDS)
async def test_a_reused_object_is_never_lost(provider: str, seed: int) -> None:
    lost = [i for i in range(TRIALS) if (await _trial(provider, f"{seed}-{provider}-lost-{i}", refresh=False))[0]]
    assert lost == [], f"{len(lost)} of {TRIALS} reused objects lost on {provider}; first schedule seed {lost[:3]}"


@pytest.mark.parametrize("provider", ["s3", "azure", "local"])
@pytest.mark.parametrize("seed", SEEDS)
async def test_no_pre_refresh_bytes_survive_a_refresh_purge(provider: str, seed: int) -> None:
    stale = [i for i in range(TRIALS) if (await _trial(provider, f"{seed}-{provider}-refresh-{i}", refresh=True))[1]]
    assert stale == [], f"{len(stale)} of {TRIALS} pre-refresh objects survived the purge on {provider}"


@pytest.mark.parametrize("provider", ["s3", "azure"])
async def test_a_store_answering_412_for_a_missing_key_still_restores(provider: str) -> None:
    lost = [i for i in range(TRIALS) if (await _trial(provider, f"412-{provider}-{i}", refresh=False, gone_412=True))[0]]
    assert lost == []


async def test_control_a_copy_in_place_bump_loses_objects_on_the_content_hash_store() -> None:
    """The first design: the ETag does not move, so the sweep's conditional delete still removes the reused object."""
    lost = [i for i in range(TRIALS * 2) if (await _trial("s3", f"control-{i}", refresh=False, copy_bump=True))[0]]
    assert len(lost) > 0, "the model no longer sees the copy-in-place loss: it has lost its teeth"


async def test_control_a_post_check_with_no_wait_leaves_pre_refresh_bytes_on_whole_second_stores() -> None:
    """The whole-second stamp: a write and a marker set right after it can share the second the cutoff carries."""
    n = 600
    stale = [i for i in range(n) if (await _trial("s3", f"nowait-{i}", refresh=True, post_check_delay=False))[1]]
    assert len(stale) > 0, "the model no longer sees the whole-second window: it has lost its teeth"


def test_the_bump_age_is_inside_the_sweep_grace() -> None:
    # bump age + the per-process repeat window (10 min) + a skew allowance (10 min) stays under the sweep's grace
    assert BUMP_AFTER_MS + 10 * MINUTE + 10 * MINUTE <= GRACE_MS


async def test_a_store_that_ignores_the_delete_condition_loses_only_what_lands_after_the_last_look() -> None:
    """MinIO (the CI build) deletes on a wrong ETag. The sweep then looks once more right before the delete, so only a
    reuse landing in the one request between that look and the delete is lost (cost only). The bound is recorded here:
    an alternative that did no better than no protection at all would fail it."""
    n = 400
    lost = [i for i in range(n) if (await _trial("minio-no-conditional-delete", f"nocond-{i}", refresh=False))[0]]
    assert len(lost) <= n // 8, f"{len(lost)} of {n} lost: the pre-delete look is not narrowing the window"
    # with the bump alone and no look, it would lose about a fifth (the control above); the look makes the loss rarer
