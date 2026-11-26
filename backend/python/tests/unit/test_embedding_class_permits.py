"""The embedding server's encode gate under ``PARSE_ADMISSION=classes``: small ahead of large, one member permit per encode."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app import embedding_main as em
from app.capacity.budget_policy import AdmissionPolicy, Family, plan_floors
from app.capacity.member_budget import MemberBudget
from tests.support.settle import SETTLE_S

if TYPE_CHECKING:
    from pathlib import Path

BULK, QUERY = em.EncodePriority.BULK, em.EncodePriority.QUERY


def test_embed_class_of_a_request() -> None:
    assert em.embed_class(QUERY, "huge") == "small"  # a search query is small work whatever the header says
    assert em.embed_class(BULK, None) == "small"  # light-format records send no header
    assert em.embed_class(BULK, "large") == "large"
    assert em.embed_class(BULK, " Huge ") == "huge"
    assert em.embed_class(BULK, "bogus") is None  # never trusted


def test_embed_demand_family_and_weight() -> None:
    d = em.embed_demand(QUERY, "small")
    assert (d.family, d.cpu) == (Family.QUERY_EMBED, 2)
    d = em.embed_demand(BULK, "large")
    assert (d.family, d.cls, d.cpu) == (Family.EMBED, "large", 2)


def test_the_gate_serves_small_ahead_of_large_waiters() -> None:
    async def run() -> list[str]:
        gate = em.EncodeGate(1, reserved_for_queries=0)
        first = await gate.acquire(BULK)
        order: list[str] = []

        async def want(name: str, rank: int) -> None:
            token = await gate.acquire(BULK, rank=rank)
            order.append(name)
            gate.release(BULK, token)

        tasks = [asyncio.create_task(want("large1", 1)), asyncio.create_task(want("large2", 1))]
        await asyncio.sleep(0.02)
        tasks.append(asyncio.create_task(want("small1", 0)))
        await asyncio.sleep(0.02)
        tasks.append(asyncio.create_task(want("small2", 0)))
        await asyncio.sleep(0.02)
        gate.release(BULK, first)
        await asyncio.gather(*tasks)
        assert not gate._rank  # nothing left behind
        return order

    assert asyncio.run(run()) == ["small1", "small2", "large1", "large2"]


def test_without_ranks_the_queue_is_arrival_order() -> None:
    async def run() -> list[str]:
        gate = em.EncodeGate(1, reserved_for_queries=0)
        first = await gate.acquire(BULK)
        order: list[str] = []

        async def want(name: str) -> None:
            token = await gate.acquire(BULK)
            order.append(name)
            gate.release(BULK, token)

        tasks = []
        for n in ("a", "b", "c"):
            tasks.append(asyncio.create_task(want(n)))
            await asyncio.sleep(0.01)
        gate.release(BULK, first)
        await asyncio.gather(*tasks)
        return order

    assert asyncio.run(run()) == ["a", "b", "c"]


def test_a_cancelled_ranked_waiter_leaves_nothing_behind() -> None:
    async def run() -> int:
        gate = em.EncodeGate(1, reserved_for_queries=0)
        await gate.acquire(BULK)
        t = asyncio.create_task(gate.acquire(BULK, rank=1))
        await asyncio.sleep(0.02)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return len(gate._rank)

    assert asyncio.run(run()) == 0


@pytest.mark.asyncio
async def test_an_encode_holds_its_member_tokens_and_returns_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    budget = MemberBudget(d, cpu_tokens=8, mem_bytes=64 << 30, floors=plan_floors(8, 8, None, 1.0, AdmissionPolicy()), service="emb")
    monkeypatch.setattr("app.capacity.admission.member_budget", lambda *_a, **_k: budget)
    seen: list[int] = []

    class Model:
        def encode(self, texts: list[str], **_kw: object) -> object:
            seen.append(budget.snapshot()["families"]["embed"]["used"])

            class Out:
                def tolist(self) -> list[list[float]]:
                    return [[0.0]]

            return Out()

    mgr = em.ModelManager(max_concurrency=2, query_reserved_slots=0)

    class Lease:
        async def __aenter__(self) -> Model:
            return Model()

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(mgr, "_lease", lambda *_a, **_k: Lease())
    mgr._in_use["m"] = 1  # the lease the real manager would have counted
    mgr._last_used["m"] = 0.0
    await mgr.encode("m", ["x"], parse_class="large")
    await asyncio.sleep(0.05)
    assert seen == [2]  # the encode held the embed weight while it ran
    assert budget.snapshot()["families"]["embed"]["used"] == 0  # and gave it back
    assert budget.snapshot()["by_class"].get("embed/large", 0) == 0
    budget.close()


async def test_a_busy_node_answers_429_with_retry_after_and_frees_its_encode_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No member tokens come free in time: a 429 (backpressure), not a 500, and the encode slot is not kept."""
    from fastapi import HTTPException

    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    budget = MemberBudget(d, cpu_tokens=2, mem_bytes=64 << 30, floors=plan_floors(8, 2, None, 1.0, AdmissionPolicy()), service="emb")
    monkeypatch.setattr("app.capacity.admission.member_budget", lambda *_a, **_k: budget)
    monkeypatch.setattr(em, "EMBED_PERMIT_WAIT_S", 0.2)
    mgr = em.ModelManager(max_concurrency=1, query_reserved_slots=0)

    class Lease:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(mgr, "_lease", lambda *_a, **_k: Lease())
    monkeypatch.setattr(em, "model_manager", mgr)
    from app.capacity.budget_policy import Demand

    held = budget.try_reserve(Demand(Family.EMBED, 2, 0, "small"))  # the node's embed tokens are all taken
    assert held is not None
    request = em.EmbeddingRequest(model="m", input="x")
    started = asyncio.get_running_loop().time()
    with pytest.raises(HTTPException) as err:
        await em.create_embeddings(request, SimpleHttpRequest())
    assert err.value.status_code == 429
    assert err.value.headers == {"Retry-After": str(em.EMBED_BACKPRESSURE_RETRY_AFTER_SECONDS)}
    # wall-clock-ok: a hang guard: the refusal is immediate, the failure waits for a permit that never frees
    assert asyncio.get_running_loop().time() - started < 3.0
    held.release()
    # the encode slot went back: the next request is admitted at once
    token = await asyncio.wait_for(mgr._encode_gate.acquire(BULK), SETTLE_S)
    mgr._encode_gate.release(BULK, token)
    budget.close()


class SimpleHttpRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_the_wait_for_member_tokens_is_short_because_it_holds_an_encode_slot() -> None:
    """600 s let bulk requests queued for tokens take every encode slot; a busy node answers 429 within seconds."""
    assert 1.0 <= em.EMBED_PERMIT_WAIT_S <= 30.0
