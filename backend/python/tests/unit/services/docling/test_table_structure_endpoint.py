"""``/table-structure`` of the Docling service: permit per table, reservation, deadline, limits, modes."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import cv2
import httpx
import numpy as np
import pytest

import app.services.docling.docling_service as mod
from app.services.docling import table_structure as ts
from tests.support.table_shadow import CountingGate, FakeGovernor, FakeSupervisor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

PNG = cv2.imencode(".png", np.zeros((8, 6, 3), np.uint8))[1].tobytes()


def table(i: int, cap: int = 100) -> dict:
    return {"id": f"p1:b{i}", "image": f"c{i}", "scale_factor": 0.5, "page_w": 1224.0, "page_h": 1584.0, "table_bbox": [1, 2, 3, 4], "cap": cap,
            "tokens": [{"id": 0, "bbox": [0, 0, 1, 1], "text": "secret words"}]}


def body(n: int, cap: int = 100) -> tuple[dict, dict]:
    return {"meta": json.dumps({"v": 1, "tables": [table(i, cap) for i in range(n)]})}, {f"c{i}": (f"c{i}.png", PNG, "image/png") for i in range(n)}


@pytest.fixture
def wired() -> Iterator[tuple[CountingGate, FakeSupervisor, list[str]]]:
    gate = CountingGate(1)
    sup = FakeSupervisor()
    reservations: list[str] = []

    async def fake_reserve(permit, demand, *, deadline_s=None, cancel=None) -> object:
        reservations.append("reserve")

        class P:
            def release(self_inner) -> None:
                reservations.append("release")

        return P()

    with (
        patch.object(mod, "_resource_governor", FakeGovernor(gate)),
        patch.dict(ts._holder, {"s": sup}),
        patch.object(mod, "reserve_or_adopt", fake_reserve),
    ):
        yield gate, sup, reservations


async def post(n: int = 2, *, headers: dict | None = None, cap: int = 100, data=None, files=None) -> httpx.Response:
    d, f = body(n, cap)
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/table-structure", data=data if data is not None else d, files=files if files is not None else f, headers=headers or {})


async def test_one_permit_of_cost_one_and_one_reservation_per_table(wired) -> None:
    gate, sup, reservations = wired
    r = await post(3)
    assert r.status_code == 200
    assert [t["status"] for t in r.json()["tables"]] == ["ok", "ok", "ok"]
    assert gate.acquires == [1, 1, 1] and gate.in_use == 0
    assert reservations == ["reserve", "release"] * 3
    assert r.json()["model"]["config_sha256"] == sup.model_info.config_sha256 and set(r.json()["model"]) == {"docling_ibm_models", "mode", "weights_sha256", "model_revision", "token_definition", "config_sha256"}


async def test_the_response_carries_no_text(wired) -> None:
    r = await post(1)
    assert "secret" not in r.text and "text" not in json.dumps(r.json()["tables"])


async def test_a_busy_gate_answers_429_before_any_table_runs(wired) -> None:
    gate, sup, _ = wired
    await gate.acquire(1)  # a /parse-pdf holds the only permit
    r = await post(2, headers={"X-Table-Deadline-Ms": "200"})
    assert r.status_code == 429 and r.headers["Retry-After"] and sup.calls == []


async def test_the_wait_for_a_permit_is_bounded_by_the_callers_deadline(wired) -> None:
    gate, sup, _ = wired
    await gate.acquire(1)
    t0 = time.monotonic()
    r = await post(1, headers={"X-Table-Deadline-Ms": "300"})
    assert r.status_code == 429 and time.monotonic() - t0 < 2.0


async def test_a_table_that_cannot_finish_before_the_deadline_is_not_started(wired) -> None:
    gate, sup, _ = wired
    r = await post(1, headers={"X-Table-Deadline-Ms": "1"}, cap=1000)  # 1000 steps need more than 1 ms
    assert r.status_code == 429 and sup.calls == []


async def test_later_tables_that_cannot_get_a_permit_are_service_busy_and_earlier_ones_keep_their_grids(wired) -> None:
    gate, sup, _ = wired
    sup.delay = 0.0
    real_acquire = gate.acquire
    state = {"n": 0}

    async def flaky(cost=1, timeout=None, **kw) -> bool:
        state["n"] += 1
        return False if state["n"] == 2 else await real_acquire(cost=cost, timeout=timeout, **kw)

    gate.acquire = flaky  # type: ignore[method-assign]
    r = await post(3)
    statuses = [(t["status"], t["err"]) for t in r.json()["tables"]]
    assert r.status_code == 200 and statuses == [("ok", None), ("error", "service_busy"), ("error", "service_busy")]


async def test_a_permit_is_given_back_between_tables_so_a_conversion_is_never_held_up_for_a_batch(wired) -> None:
    """The saturated-gate case: a competing acquire gets in between two tables of one request."""
    gate, sup, _ = wired
    sup.delay = 0.3
    competitor_waits: list[bool] = []

    async def competitor() -> None:
        await asyncio.sleep(0.05)
        ok = await gate.acquire(1, timeout=0.5)  # /parse-pdf's wait is far longer than one table
        competitor_waits.append(ok)
        if ok:
            gate.release(1)

    results = await asyncio.gather(post(4), competitor())
    assert results[0].status_code == 200 and competitor_waits == [True]


async def test_negative_control_a_permit_held_for_the_whole_request_starves_the_competitor(wired) -> None:
    gate, sup, _ = wired
    sup.delay = 0.3

    @asynccontextmanager
    async def no_permit_per_table(_wait) -> AsyncIterator[None]:
        yield

    meta = ts.TableRequestMeta.model_validate({"v": 1, "tables": [table(i) for i in range(4)]})

    async def whole_batch() -> None:
        await gate.acquire(1)  # the broken variant: one permit for the request
        try:
            await ts.run_tables(meta, {f"c{i}": PNG for i in range(4)}, sup, no_permit_per_table, deadline=time.monotonic() + 30)
        finally:
            gate.release(1)

    async def competitor() -> bool:
        await asyncio.sleep(0.05)
        return await gate.acquire(1, timeout=0.5)

    _, ok = await asyncio.gather(whole_batch(), competitor())
    assert ok is False


@pytest.mark.parametrize(
    "bad_meta",
    [
        "not json",
        json.dumps({"v": 1, "tables": []}),
        json.dumps({"v": 1, "tables": [table(i, 600) for i in range(2)]}),  # sum of caps 1,200 over the 1,024 limit
        json.dumps({"v": 1, "tables": [table(0, 2000)]}),
    ],
)
async def test_bad_requests_are_422_and_do_not_echo_the_input(wired, bad_meta) -> None:
    _, sup, _ = wired
    _, files = body(2)
    r = await post(data={"meta": bad_meta}, files=files)
    assert r.status_code == 422 and "secret" not in r.text and sup.calls == []


async def test_a_missing_image_part_is_422(wired) -> None:
    d, _ = body(2)
    r = await post(data=d, files={"c0": ("c0.png", PNG, "image/png")})
    assert r.status_code == 422


async def test_images_over_the_byte_limit_are_422(wired) -> None:
    d, _ = body(1)
    big = b"\0" * (8 * 1024 * 1024 + 1)
    r = await post(data=d, files={"c0": ("c0.png", big, "image/png")})
    assert r.status_code == 422


async def test_fast_mode_is_409(wired, monkeypatch) -> None:
    monkeypatch.setenv("PDF_DOCLING_TABLE_MODE", "fast")
    r = await post(1)
    assert r.status_code == 409 and r.json()["errorCode"] == "mode_not_supported"


async def test_a_table_error_is_a_200_with_a_status(wired) -> None:
    _, sup, _ = wired
    sup.fail = {"p1:b1"}
    r = await post(3)
    assert r.status_code == 200 and [t["status"] for t in r.json()["tables"]] == ["ok", "error", "ok"]


async def test_a_request_queued_for_the_model_holds_no_permit(tmp_path) -> None:
    """Gate limit 2, two concurrent one-table requests, the real supervisor with a child that takes 0.8 s a table: at most
    one permit is held at any moment, and a conversion's acquire is admitted while one table runs."""
    gate = CountingGate(2)
    sup = ts.TableSupervisor(memory_available=lambda: 10**12, weights_dir=lambda _m: "unused", factory="tests.unit.services.docling.fake_table_model:build")
    peak = {"v": 0}
    stop = asyncio.Event()

    async def sample() -> None:
        while not stop.is_set():
            peak["v"] = max(peak["v"], gate.in_use)
            await asyncio.sleep(0.01)

    async def post_slow(tag: str) -> httpx.Response:
        meta = {"v": 1, "tables": [{**table(0), "id": "sleep-0.8", "image": "c0"}]}
        transport = httpx.ASGITransport(app=mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/table-structure", data={"meta": json.dumps(meta)}, files={"c0": ("c0.png", PNG, "image/png")}, headers={"X-Table-Deadline-Ms": "20000"})

    async def conversion() -> bool:
        await asyncio.sleep(0.3)  # both requests are in; one runs, one waits for its turn
        ok = await gate.acquire(1, timeout=0.3)
        if ok:
            gate.release(1)
        return ok

    reserve = None

    async def no_reserve(permit, demand, *, deadline_s=None, cancel=None) -> None:
        return reserve

    with patch.object(mod, "_resource_governor", FakeGovernor(gate)), patch.dict(ts._holder, {"s": sup}), patch.object(mod, "reserve_or_adopt", no_reserve):
        sampler = asyncio.create_task(sample())
        try:
            r1, r2, got = await asyncio.gather(post_slow("a"), post_slow("b"), conversion())
        finally:
            stop.set()
            await sampler
            await sup.shutdown()
    assert r1.status_code == 200 and r2.status_code == 200
    assert peak["v"] <= 1, f"{peak['v']} permits held while one table can run"
    assert got is True  # the conversion's acquire was admitted while a table ran


async def test_an_image_part_is_read_no_further_than_the_byte_limit(wired) -> None:
    from starlette.datastructures import UploadFile

    sizes: list[int] = []
    real_read = UploadFile.read

    async def read(self, size: int = -1) -> bytes:
        sizes.append(size)
        return await real_read(self, size)

    big = b"\x00" * (mod.MAX_IMAGE_BYTES + 10)
    with patch.object(UploadFile, "read", read):
        r = await post(1, files={"c0": ("c0.png", big, "image/png")})
    assert r.status_code == 422
    assert sizes and all(0 < s <= mod.MAX_IMAGE_BYTES + 1 for s in sizes)
