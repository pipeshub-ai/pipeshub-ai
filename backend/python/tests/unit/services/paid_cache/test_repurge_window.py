"""The window of a coalesced re-purge: claim, append, seal, repair, on a Redis that refuses cross-slot scripts."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.paid_cache import repurge, repurge_window
from app.services.paid_cache.repurge_window import (
    APPENDED,
    BUSY,
    CLAIMED,
    CLOSED,
    FULL,
    SEAL_DONE,
    SEAL_HANDBACK,
    SEAL_LOST_ABSENT,
    SEAL_LOST_EPOCH,
    SEAL_OK,
    decide_open,
    decide_seal,
)
from tests.support.repurge_window import (
    Broker,
    CrossSlot,
    SlotCheckedRedis,
    first_hop,
    redis_store,
)

ORG = "org-1"
NOW = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_RESULT_REPURGE_CHAIN", "true")
    monkeypatch.setenv("PAID_RESULT_REPURGE_COALESCE", "true")


async def _open_window(store: Any, window: int = 5, token: str = "a" * 8, epoch: str = "e1", now: int = NOW) -> None:  # noqa: ANN401
    got = await store.open_or_append(ORG, window, token, epoch, "seed", now)
    assert got.state == CLAIMED
    assert await store.mark_published(ORG, window, token, epoch, now + 330_000, now)


async def test_exactly_one_of_200_concurrent_deleters_claims_and_every_id_lands_once_after_publish() -> None:
    store = redis_store()
    first = await asyncio.gather(*(store.open_or_append(ORG, 5, f"t{i:04x}", f"e{i}", f"r{i}", NOW) for i in range(200)))
    assert sum(r.state == CLAIMED for r in first) == 1 and sum(r.state == BUSY for r in first) == 199
    winner = next(i for i, r in enumerate(first) if r.state == CLAIMED)
    assert await store.mark_published(ORG, 5, f"t{winner:04x}", f"e{winner}", NOW + 330_000, NOW)
    again = await asyncio.gather(*(store.open_or_append(ORG, 5, f"t{i:04x}", f"e{i}", f"r{i}", NOW) for i in range(200)))
    assert {r.state for r in again} == {APPENDED}
    read = await store.seal_read(ORG, 5, f"e{winner}", NOW + 400_000)
    assert read.state == SEAL_OK and sorted(read.ids) == sorted(f"r{i}" for i in range(200))


async def test_a_deleter_never_trusts_another_deleters_live_claim() -> None:
    store = redis_store()
    assert (await store.open_or_append(ORG, 5, "aaaa", "e1", "r1", NOW)).state == CLAIMED
    assert (await store.open_or_append(ORG, 5, "bbbb", "e2", "r2", NOW + 1)).state == BUSY
    # its own redelivery recognises the claim (same token) and keeps the epoch
    again = await store.open_or_append(ORG, 5, "aaaa", "e9", "r1", NOW + 2)
    assert (again.state, again.epoch) == (CLAIMED, "e1")
    # a lapsed claim is taken over, with the new deleter's epoch
    lapsed = await store.open_or_append(ORG, 5, "bbbb", "e2", "r2", NOW + repurge_window.CLAIM_TTL_MS + 5)
    assert (lapsed.state, lapsed.epoch) == (CLAIMED, "e2")
    assert not await store.mark_published(ORG, 5, "aaaa", "e1", NOW + 1, NOW + 1)      # the old claimant lost it


async def test_a_sealed_or_full_window_answers_closed_or_full_and_never_moves_on(monkeypatch: pytest.MonkeyPatch) -> None:
    store = redis_store()
    await _open_window(store)
    assert (await store.open_or_append(ORG, 5, "zz", "e", "r1", NOW)).state == APPENDED
    assert (await store.seal_read(ORG, 5, "e1", NOW + 400_000)).state == SEAL_OK
    assert (await store.open_or_append(ORG, 5, "zz", "e", "r2", NOW)).state == CLOSED
    monkeypatch.setattr(repurge_window, "WINDOW_CAP", 2)
    await _open_window(store, window=6)
    assert [(await store.open_or_append(ORG, 6, "zz", "e", f"r{i}", NOW)).state for i in range(3)] == [APPENDED, APPENDED, FULL]


async def test_a_seal_hands_back_while_its_own_epoch_is_still_claimed_then_runs() -> None:
    store = redis_store()
    assert (await store.open_or_append(ORG, 5, "aaaa", "e1", "r1", NOW)).state == CLAIMED
    assert (await store.seal_read(ORG, 5, "e1", NOW + 1_000)).state == SEAL_HANDBACK       # published before mark_published
    assert (await store.seal_read(ORG, 5, "other", NOW + 1_000)).state == SEAL_LOST_EPOCH
    await store.mark_published(ORG, 5, "aaaa", "e1", NOW + 2_000, NOW + 1_000)
    assert (await store.open_or_append(ORG, 5, "aaaa", "e1", "r1", NOW)).state == APPENDED
    read = await store.seal_read(ORG, 5, "e1", NOW + 3_000)
    assert (read.state, read.ids) == (SEAL_OK, ("r1",))
    await store.mark_done(ORG, 5, "e1")
    assert (await store.seal_read(ORG, 5, "e1", NOW + 4_000)).state == SEAL_DONE           # a duplicate seal is a no-op


async def test_a_seal_of_a_flushed_window_counts_a_loss_with_what_it_can_still_see() -> None:
    store = redis_store()
    await _open_window(store)
    await store.open_or_append(ORG, 5, "zz", "e", "r1", NOW)
    await store._client.inner.delete(store._keys(ORG, 5)[0])                               # own lost, list left
    read = await store.seal_read(ORG, 5, "e1", NOW + 400_000)
    assert (read.state, read.seen) == (SEAL_LOST_ABSENT, 1)
    other = redis_store()
    await _open_window(other)
    await other._client.inner.delete(other._keys(ORG, 5)[0])
    await other.open_or_append(ORG, 5, "new", "e2", "r9", NOW + 1)                          # a new epoch after the flush
    assert (await other.seal_read(ORG, 5, "e1", NOW + 400_000)).state == SEAL_LOST_EPOCH


async def test_the_seal_reads_a_large_list_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repurge_window, "SEAL_READ_CHUNK", 7)
    client = SlotCheckedRedis()
    ranges: list[tuple[int, int]] = []
    real = client.inner.lrange

    async def spy(key: str, start: int, stop: int) -> Any:  # noqa: ANN401
        ranges.append((start, stop))
        return await real(key, start, stop)

    client.inner.lrange = spy  # type: ignore[method-assign]
    store = redis_store(client)
    await _open_window(store)
    for i in range(50):
        await store.open_or_append(ORG, 5, "zz", "e", f"r{i}", NOW)
    read = await store.seal_read(ORG, 5, "e1", NOW + 400_000)
    assert read.ids == tuple(f"r{i}" for i in range(50))
    # one LRANGE of 50 ids would be [(0, -1)]: the reads are ceil(50 / 7) contiguous ranges of at most 7
    assert ranges == [(i, i + 6) for i in range(0, 50, 7)]


async def test_repair_lists_overdue_open_windows_of_the_org_and_prunes_what_is_older_than_the_ids_life() -> None:
    store = redis_store()
    await _open_window(store, window=5, epoch="e5", now=NOW)
    await _open_window(store, window=6, epoch="e6", now=NOW + 400_000)
    horizon = NOW + 330_000 + 700_000
    assert await store.overdue(ORG, horizon - 700_000 + 10) == [(5, "e5")]
    assert sorted(await store.overdue(ORG, NOW + 10**7)) == [(5, "e5"), (6, "e6")]
    # a done window leaves the set at once (before any prune could remove it): else every claimer republishes it
    await store.mark_done(ORG, 5, "e5")
    assert await store.overdue(ORG, NOW + 10**7) == [(6, "e6")]
    assert await store._client.inner.zrange(store._open_key(ORG), 0, -1) == ["6:e6"]
    # what is older than the ids' life is pruned (it could only count a loss)
    assert await store.overdue(ORG, NOW + (repurge_window.KEY_TTL_AFTER_S + 10_000) * 1000) == []
    assert await store._client.inner.zrange(store._open_key(ORG), 0, -1) == []


async def test_every_script_declares_every_key_it_touches_and_stays_on_one_slot() -> None:
    client = SlotCheckedRedis()
    store = redis_store(client)
    await _open_window(store)
    await store.open_or_append(ORG, 5, "zz", "e", "r1", NOW)
    await store.seal_read(ORG, 5, "e1", NOW + 400_000)
    await store.mark_done(ORG, 5, "e1")
    await store.release_claim(ORG, 9, "x")
    await store.overdue(ORG, NOW)
    assert client.evals >= 6
    # a key of another org's hash tag in one script is what the stub refuses (the check is live)
    with pytest.raises(CrossSlot):
        await client.eval("return 1", 2, "pc:rpw:{a}:1:own", "pc:rpw:{b}:1:own")
    # undeclared keys: the first argument of every redis.call of every script is a KEYS[n] (never a built name)
    for script in (repurge_window._OPEN_OR_APPEND, repurge_window._MARK_PUBLISHED, repurge_window._SEAL,
                   repurge_window._MARK_DONE, repurge_window._RELEASE_CLAIM):
        firsts = re.findall(r"redis\.call\('[A-Z]+',\s*([^,)]+)", script)
        assert firsts and all(re.fullmatch(r"KEYS\[\d\]", f.strip()) for f in firsts), firsts


def test_the_pure_decisions_match_the_scripts_contract() -> None:
    assert decide_open(None, "t", "e", NOW) == ("claim", "e")
    assert decide_open(f"claim:t:e0:{NOW + 5}", "t", "e", NOW) == ("claim", "e0")
    assert decide_open(f"claim:o:e0:{NOW + 5}", "t", "e", NOW) == ("busy", None)
    assert decide_open(f"claim:o:e0:{NOW - 5}", "t", "e", NOW) == ("claim", "e")
    assert decide_open("published:e0:9", "t", "e", NOW) == ("append", None)
    assert decide_open("garbage", "t", "e", NOW) == ("claim", "e")
    assert decide_seal(None, "e", NOW) == SEAL_LOST_ABSENT
    assert decide_seal(f"claim:t:e:{NOW + 5}", "e", NOW) == SEAL_HANDBACK
    assert decide_seal(f"claim:t:e:{NOW - 5}", "e", NOW) == SEAL_LOST_EPOCH
    assert decide_seal("published:e:9", "x", NOW) == SEAL_LOST_EPOCH
    assert decide_seal("published:e:9", "e", NOW) == SEAL_OK


async def test_a_repeat_of_the_seal_after_a_failed_publish_reads_the_same_list_again() -> None:
    clock = [1000.0]
    broker = Broker(clock)
    store = redis_store()
    await _open_window(store, token="a" * 8)
    for i in range(3):
        await store.open_or_append(ORG, 5, "zz", "e", f"r{i}", NOW)
    seal = repurge_window.seal_payload(ORG, 5, "e1", due_s=0.0)
    broker.fail_next = 1
    with pytest.raises(ConnectionError):
        await repurge_window.handle_seal(broker, ORG, seal, store=store, now=1000.0)
    await repurge_window.handle_seal(broker, ORG, seal, store=store, now=1001.0)
    pages = broker.of("purgePaidResultsBatch")
    assert [m.payload["recordIds"] for m in pages] == [["r0", "r1", "r2"]]
    await repurge_window.handle_seal(broker, ORG, seal, store=store, now=1002.0)       # done: nothing more
    assert len(broker.of("purgePaidResultsBatch")) == 1


async def test_the_first_hop_with_no_shared_store_or_an_invalid_id_keeps_its_own_chain() -> None:
    broker = Broker()
    hop = first_hop(ORG, "rec-1", 1000.0)
    assert await repurge_window.coalesce_successor(broker, ORG, hop, store=None, now=1100.0) is False
    assert await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, "bad id!", 1000.0), store=redis_store(), now=1100.0) is False
    assert await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, "org", 1000.0), store=redis_store(), now=1100.0) is False
    assert broker.sent == []


def test_the_chain_off_or_coalesce_off_never_coalesces(monkeypatch: pytest.MonkeyPatch) -> None:
    assert repurge_window.coalesce_enabled()
    monkeypatch.delenv("PAID_RESULT_REPURGE_COALESCE")
    assert not repurge_window.coalesce_enabled()
    monkeypatch.setenv("PAID_RESULT_REPURGE_COALESCE", "true")
    monkeypatch.delenv("PAID_RESULT_REPURGE_CHAIN")
    assert not repurge_window.coalesce_enabled()


_ = (SimpleNamespace, repurge)
