"""Reserve once, by the component that runs the work: lanes adopt, the Docling service reserves, indexing holds nothing."""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.capacity.admission import docling_demand, llm_slot, parse_class
from app.capacity.budget_policy import Demand, Family
from app.capacity.member_budget import AdmissionTimeout
from app.modules.parsers import parse_pool
from tests.support.member_budget_env import GIB, install_budget

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType


def _hold(seconds: float) -> int:
    time.sleep(seconds)
    return 1


@pytest.fixture
def no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_pool, "_pool", lambda **_kw: None)  # the unit runs in a thread: only the admission is under test


# ---- a permit passed in is adopted, never reserved twice ----------------------------------


async def test_the_lane_adopts_the_callers_permit_and_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None
) -> None:
    n = 4
    b = install_budget(monkeypatch, tmp_path / "l", c=n)  # C = N: a second permit per step could never be had
    seen: list[int] = []

    def work() -> int:
        seen.append(b.snapshot()["used"])
        return _hold(0.1)

    async def step() -> int:
        p = await b.reserve(Demand(Family.DOCLING, 1, 0, "small"), deadline_s=5)
        return await parse_pool.submit(work, timeout=2, permit=p)

    t = time.monotonic()
    assert await asyncio.gather(*(step() for _ in range(n))) == [1] * n
    assert time.monotonic() - t < 1.5, "a lane that reserves again waits for tokens its own caller holds"
    assert max(seen) == n  # exactly N tokens held, none for a second permit
    assert b.snapshot()["used"] == 0  # the lane released them at job end
    assert b.snapshot()["families"]["docling"]["used"] == 0


async def test_an_adopted_permit_keeps_its_owner_family(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    p = await b.reserve(Demand(Family.EMBED, 2, 0, "small"), deadline_s=5)
    seen = {}

    def work() -> int:
        seen.update(b.snapshot()["families"])
        return 1

    await parse_pool.submit(work, permit=p)
    assert seen["embed"]["used"] == 2 and seen["parse"]["used"] == 0


async def test_a_caller_without_a_permit_gets_one_from_the_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: list[int] = []

    def work() -> int:
        seen.append(b.snapshot()["families"]["parse"]["used"])
        return 1

    await parse_pool.submit(work)
    assert seen == [1] and b.snapshot()["used"] == 0


async def test_a_lane_wait_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    assert b.try_reserve(Demand(Family.PARSE, 2, 0, "small")) is not None
    with pytest.raises(AdmissionTimeout):
        await parse_pool.submit(lambda: 1, timeout=0.2)


def test_run_blocking_adopts_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=1)
    p = b.try_reserve(Demand(Family.PARSE, 1, 0, "small"))
    seen: list[int] = []
    monkeypatch.setattr(parse_pool, "_run_blocking", lambda *a: seen.append(b.snapshot()["used"]) or 1)
    assert parse_pool.run_blocking(_hold, 0, timeout=1, permit=p) == 1
    assert seen == [1] and b.snapshot()["used"] == 0  # C = 1 held by the caller's permit, nothing reserved again


async def test_with_the_flag_off_a_lane_holds_nothing_and_creates_no_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "never"))
    assert await parse_pool.submit(_hold, 0) == 1
    assert not (tmp_path / "never").exists()


# ---- the Docling conversion is reserved once, by the Docling service ------------------------------------


@pytest.fixture
def docling_route(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    import app.services.docling.docling_service as mod

    mod._parse_tasks.clear()
    original = mod.docling_service
    monkeypatch.setattr(mod, "_resource_governor", None)
    monkeypatch.setattr(mod, "serialize_docling_doc", lambda doc: "{}")
    yield mod
    mod.docling_service = original


async def _post(mod, cls: str | None) -> object:
    upload = MagicMock(read=AsyncMock(return_value=b"%PDF-data"))
    from fastapi import BackgroundTasks

    return await mod.parse_pdf_endpoint(
        BackgroundTasks(), file=upload, record_name="a.pdf", start_page=None, end_page=None, x_parse_class=cls
    )


async def test_docling_reserved_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    """The service holds the Docling tokens (class from ``X-Parse-Class``) for exactly its conversion."""
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: dict[str, object] = {}

    async def parse_pdf_only(name: str, pdf: bytes, page_range: object = None) -> MagicMock:
        seen["snap"] = b.snapshot()
        return MagicMock()

    docling_route.docling_service = MagicMock(parse_pdf_only=parse_pdf_only)
    resp = await _post(docling_route, "small")
    assert getattr(resp, "success", False) is True
    snap = seen["snap"]
    assert snap["by_class"] == {"docling/small": 2}  # one reservation, the class the caller sent
    assert snap["families"]["docling"]["used"] == 2
    await asyncio.sleep(0.1)
    assert b.snapshot()["used"] == 0  # released with the conversion


async def test_an_unknown_class_is_unclassified_not_trusted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: dict[str, object] = {}

    async def parse_pdf_only(name: str, pdf: bytes, page_range: object = None) -> MagicMock:
        seen["by_class"] = b.snapshot()["by_class"]
        return MagicMock()

    docling_route.docling_service = MagicMock(parse_pdf_only=parse_pdf_only)
    await _post(docling_route, "critical; drop table")
    assert seen["by_class"] == {"docling/none": 2}
    assert parse_class(" Small ") == "small" and parse_class(None) is None
    assert parse_class("critical; drop table") is None and parse_class(object()) is None


async def test_a_full_budget_answers_with_backpressure_and_holds_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    hold = b.try_reserve(Demand(Family.PARSE, 2, 0, "small"))
    monkeypatch.setattr(docling_route, "DOCLING_GATE_TIMEOUT_SECONDS", 0.2)
    docling_route.docling_service = MagicMock(parse_pdf_only=AsyncMock())
    resp = await _post(docling_route, "small")
    assert resp.status_code == 429
    hold.release()
    assert b.snapshot()["used"] == 0


async def test_the_indexing_client_holds_no_tokens_while_it_waits_on_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.docling.client import DoclingClient

    b = install_budget(monkeypatch, tmp_path / "l")
    during: list[int] = []
    client = DoclingClient(service_url="http://docling.invalid")

    async def post(path: str, **_kw: object) -> MagicMock:
        during.append(b.snapshot()["used"])
        return MagicMock(status_code=200, json=lambda: {"success": True, "parse_result": "{}"})

    with patch.object(client, "_post_multipart", post), patch.object(client, "_validate_pdf_binary", lambda _b: True):
        assert await client.parse_pdf("a.pdf", b"%PDF-1") == "{}"
    assert during == [0]


def test_docling_demand_is_the_service_weight() -> None:
    d = docling_demand("small")
    assert d.family is Family.DOCLING and d.cpu == 2 and d.cls == "small" and d.mem_bytes == int(1.5 * GIB)


def test_reserve_requires_deadline_or_cancel_at_the_call_sites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    with pytest.raises(ValueError, match="deadline or a cancel token"):
        b.reserve_blocking(Demand(Family.DOCLING, 2, 0, "small"))


# ---- the LLM member cap at the gateway ------------------------------------------------------------------


async def test_every_gateway_call_holds_a_member_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.llm_gateway.gateway import LLMGateway

    b = install_budget(monkeypatch, tmp_path / "l", llm=3)
    gw = LLMGateway(50)  # the per-process limiter is not what stops the fourth call
    inflight: list[int] = []
    gate = asyncio.Event()

    async def make_call() -> str:
        inflight.append(b.snapshot()["llm"]["used"])
        await gate.wait()
        return "ok"

    tasks = [asyncio.create_task(gw.call(make_call, provider="p")) for _ in range(8)]
    await asyncio.sleep(0.5)
    assert b.snapshot()["llm"]["used"] == 3 and len(inflight) == 3  # the other five wait for the container's cap
    gate.set()
    assert await asyncio.gather(*tasks) == ["ok"] * 8
    assert b.snapshot()["llm"]["used"] == 0


async def test_llm_slot_is_a_noop_with_the_flag_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "never"))
    async with llm_slot():
        pass
    assert not (tmp_path / "never").exists()
    assert not os.path.exists(tmp_path / "never")
