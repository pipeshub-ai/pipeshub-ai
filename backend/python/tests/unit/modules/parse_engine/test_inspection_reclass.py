"""Inspection corrects the class: a heavy-format record admitted as small on a prior is read on the inspect
pool, re-estimated from its page count and text layer, and moved to its class in place; small files then never
wait behind it. Also the small-parse bypass of the heavy start-rate limiter and the first-admission timestamp.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from types import SimpleNamespace

import pypdfium2
import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import inspection, wiring
from app.modules.parsers.pdf.pdf_inspection import quick_facts
from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor import StartRateLimiter
from app.services.resource_governor.models import ParseTier, Pool
from tests.support.settle import SETTLE_S
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

MIB = 1 << 20
S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE


@pytest.fixture(autouse=True)
def classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.delenv("PARSE_LANES", raising=False)
    wiring.reset_for_tests()


def host() -> SimpleNamespace:
    return SimpleNamespace(
        governor=make_test_governor(logger_name="test.insp"), logger=logging.getLogger("test.insp"),
        priority_lane=None, gate_waiters=cc.GateWaiters(),
    )


def ctx_for(ext: str = "pdf", org: str = "o") -> wiring.ClassContext:
    ctx = wiring.class_context({"extension": ext, "mimeType": "", "orgId": org})
    assert ctx is not None
    return ctx


def blank_pdf(pages: int) -> bytes:
    doc = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def text_pdf() -> bytes:
    stream = b"BT /F1 12 Tf 20 100 Td (hello inspection world, a line of text) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


# -- the inspection ------------------------------------------------------------------------------------------


def test_quick_facts_reads_the_page_count_and_the_text_layer() -> None:
    blank = quick_facts(blank_pdf(12))
    assert (blank.page_count, blank.scanned) == (12, True)  # no text layer on any sampled page
    text = quick_facts(text_pdf())
    assert (text.page_count, text.scanned) == (1, False)


def test_quick_facts_reads_a_thousand_pages_from_a_sample_only() -> None:
    started = time.monotonic()
    facts = quick_facts(blank_pdf(1000), sample_pages=8)
    assert facts.page_count == 1000
    # wall-clock-ok: a 1000-page sample read takes well under a second; reading every page is the failure, minutes
    assert time.monotonic() - started < 10


def test_an_unreadable_file_has_no_facts_and_keeps_its_prior() -> None:
    assert asyncio.run(inspection.inspect_facts(b"not a pdf at all")) == inspection.InspectedFacts()


def test_inspect_facts_runs_through_the_pool_and_reports_pages_and_scan() -> None:
    facts = asyncio.run(inspection.inspect_facts(blank_pdf(7)))
    assert facts == inspection.InspectedFacts(pages=7, scanned=True)


def test_only_a_pdf_or_office_document_with_its_bytes_in_hand_is_inspected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert inspection.wants_inspection("pdf", None, b"%PDF")
    assert not inspection.wants_inspection("pdf", None, None)  # no bytes
    assert not inspection.wants_inspection("txt", "text/plain", b"x")
    assert inspection.wants_inspection("xlsx", None, b"x")  # office packages are read for their page or cell counts
    assert not inspection.wants_inspection("png", None, b"x")
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert not inspection.wants_inspection("pdf", None, b"%PDF")  # off: no event, no read


# -- the class from the facts -----------------------------------------------------------------------------------


def test_a_thousand_page_pdf_under_100_mib_is_large_or_huge_once_inspected() -> None:
    ctx = ctx_for()
    size = 80 * MIB
    assert wiring.class_of(ctx, size) is S  # the prior: only a size above 100 MiB proves large
    assert wiring.class_of(ctx.inspected(1000, scanned=False), size) in (L, H)
    assert wiring.class_of(ctx.inspected(1000, scanned=False), size) is H
    assert wiring.class_of(ctx.inspected(1000, scanned=True), size) is H  # scanned
    assert wiring.class_of(ctx.inspected(5, scanned=False), size) is S  # a short one stays small
    assert ctx.inspected(None, scanned=None) is ctx


# -- moved in place -----------------------------------------------------------------------------------------------


def test_the_record_moves_in_place_holds_one_permit_and_small_files_do_not_wait_behind_it() -> None:
    async def run() -> dict[str, object]:
        h = host()
        gov_gate = h.governor.gate(Pool.INDEX_HEAVY)
        gate = wiring.index_gate(h.governor)
        ctx = ctx_for()
        big = await cc.acquire_index_slot(h, S, **cc.class_kwargs(ctx))  # no size at dispatch: small
        permit = big.ordered  # type: ignore[attr-defined]
        in_use_before = gov_gate.in_use
        ctx = ctx.inspected(1000, scanned=False)
        assert cc.reclass_index_slot(big, ctx, 80 * MIB) is H
        out: dict[str, object] = {
            "same_permit": big.ordered is permit,  # type: ignore[attr-defined]
            "cls": permit.cls,
            "running": (gate.running(S), gate.running(H)),
            "one_permit": gov_gate.in_use == in_use_before == 1,
        }
        # More of the same class queue behind the huge cap while small ones sail through.
        t0 = time.monotonic()
        smalls = [
            await asyncio.wait_for(cc.acquire_index_slot(h, S, **cc.class_kwargs(ctx_for())), SETTLE_S) for _ in range(2)
        ]
        out["small_wait"] = time.monotonic() - t0
        for a in smalls:
            cc.release_admission(a)
        cc.release_admission(big)
        out["after"] = (gate.running(S), gate.running(H), gov_gate.in_use)
        return out

    r = asyncio.run(run())
    assert r["same_permit"] and r["cls"] is H and r["running"] == (0, 1) and r["one_permit"]
    assert r["small_wait"] < 0.5  # type: ignore[operator]
    assert r["after"] == (0, 0, 0)


def test_a_reclassed_record_keeps_its_place_in_line() -> None:
    """Upgrading never moves the record behind later arrivals of its new class (first seen is kept)."""

    async def run() -> list[int]:
        h = host()
        gate = wiring.index_gate(h.governor)
        ctx = ctx_for()
        first = await cc.acquire_index_slot(h, S, **cc.class_kwargs(ctx))
        cc.reclass_index_slot(first, ctx.inspected(1000, scanned=False), 80 * MIB)
        assert first.ordered.cls is H  # type: ignore[attr-defined]
        assert gate.running(H) == 1
        cc.release_admission(first)
        return [gate.running(c) for c in (S, L, H)]

    assert asyncio.run(run()) == [0, 0, 0]


def test_the_parse_step_estimates_from_the_inspected_facts() -> None:
    """The parse slot is taken under the corrected class: 1000 pages at 80 MiB is a huge CPU parse."""

    async def run() -> CostClass:
        h = host()
        ctx = ctx_for().inspected(1000, scanned=False)
        adm = await asyncio.wait_for(cc.acquire_parsing_slot(h, ParseTier.HEAVY, 80 * MIB, timeout=2, ctx=ctx), 3)
        try:
            return adm.ordered.cls  # type: ignore[attr-defined]
        finally:
            cc.release_admission(adm)

    assert asyncio.run(run()) is H


# -- small CPU parses skip the heavy start-rate limiter ----------------------------------------------------------


def _limited_host() -> SimpleNamespace:
    h = host()
    h.governor._registry.set(Pool.HEAVY_PARSE, 8)
    # One start per 1000 s with room for one: the second start of any spaced class waits for the clock.
    h.governor.gate(Pool.HEAVY_PARSE)._rate_limiter = StartRateLimiter(1000.0, 1)
    return h


def test_small_cpu_parses_bypass_the_start_rate_limiter() -> None:
    async def run() -> int:
        h = _limited_host()
        held = [  # workbooks: small CPU parses
            await asyncio.wait_for(cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=1, ctx=ctx_for("xlsx")), 2)
            for _ in range(4)
        ]
        n = len(held)
        for a in held:
            cc.release_admission(a)
        return n

    assert asyncio.run(run()) == 4


def test_large_and_huge_parses_still_go_through_the_limiter() -> None:
    async def run() -> tuple[int, int]:
        h = _limited_host()
        ctx = ctx_for().inspected(1000, scanned=False)
        first = await asyncio.wait_for(cc.acquire_parsing_slot(h, ParseTier.HEAVY, 80 * MIB, timeout=1, ctx=ctx), 2)
        cc.release_admission(first)
        timed_out = 0
        try:
            await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 80 * MIB, timeout=0.3, ctx=ctx)
        except cc.ParseAdmissionTimeout:  # the class gate passed it; the governor's limiter held it
            timed_out = 1
        stats = h.governor.gate(Pool.HEAVY_PARSE).drain_demand()
        return timed_out, stats.rate_limited_acquires

    timed_out, limited = asyncio.run(run())
    assert timed_out == 1 and limited > 0


def test_a_small_parse_takes_none_of_the_limiters_tokens() -> None:
    async def run() -> bool:
        h = _limited_host()
        small = await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=1, ctx=ctx_for("xlsx"))
        cc.release_admission(small)
        # The one token is still there for the large one.
        big = await asyncio.wait_for(
            cc.acquire_parsing_slot(h, ParseTier.HEAVY, 80 * MIB, timeout=1, ctx=ctx_for().inspected(1000, scanned=False)), 2
        )
        cc.release_admission(big)
        return True

    assert asyncio.run(run())


# -- aging survives a requeue ------------------------------------------------------------------------------------


def test_first_admission_kwargs_only_for_a_big_timeout() -> None:
    assert cc.first_admission_kwargs(cc.BigAdmissionTimeout("p", 1.0, parse_enqueued_at_ms=123)) == {
        "parse_enqueued_at_ms": 123
    }
    assert cc.first_admission_kwargs(cc.ParseAdmissionTimeout("p", 1.0)) == {}
    assert cc.first_admission_kwargs(cc.BigAdmissionTimeout("p", 1.0)) == {}
    assert cc.first_admission_kwargs(RuntimeError("x")) == {}


def _kafka_requeue_payload(parse_enqueued_at_ms: int | None, existing: dict[str, object]) -> dict[str, object]:
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    sent: list[dict[str, object]] = []

    async def send(_message: object, _topic: str, _event: str, payload: dict[str, object]) -> None:
        sent.append(payload)

    async def on_main(coro: object) -> None:
        await coro  # type: ignore[misc]

    me = SimpleNamespace(
        producer=object(), logger=logging.getLogger("t"), _run_on_main_loop=on_main, _send_in_org=send,
    )
    msg = SimpleNamespace(payload=dict(existing), requestId="r", orgId="o", eventType="newRecord")
    kwargs = {} if parse_enqueued_at_ms is None else {"parse_enqueued_at_ms": parse_enqueued_at_ms}
    asyncio.run(IndexingKafkaConsumer._requeue_message(me, "topic", msg, "sid", retry_count=0, delay_s=1.0, **kwargs))  # type: ignore[arg-type]
    return sent[0]


def test_the_requeued_message_carries_its_first_admission_time() -> None:
    assert _kafka_requeue_payload(1000, {"x": 1})["parseEnqueuedAtMs"] == 1000


def test_a_later_requeue_keeps_the_first_time() -> None:
    assert _kafka_requeue_payload(2000, {"parseEnqueuedAtMs": 1000})["parseEnqueuedAtMs"] == 1000


def test_a_requeue_without_the_time_adds_nothing() -> None:
    assert "parseEnqueuedAtMs" not in _kafka_requeue_payload(None, {"x": 1})


def test_aging_is_measured_from_the_carried_time() -> None:
    now_ms = int(time.time() * 1000)
    ctx = wiring.class_context({"extension": "pdf", "orgId": "o", "parseEnqueuedAtMs": now_ms - 600_000})
    assert ctx is not None
    first = ctx.first_seen_s()
    # wall-clock-ok: two readings of the clock around a 600 s carried age; the 10 s window only absorbs the gap between them
    assert first is not None and 590 < time.monotonic() - first < 610


# -- /health parseEngine ---------------------------------------------------------------------------------------------


def test_the_health_block_reports_waiting_in_flight_and_the_oldest_waiter() -> None:
    async def run() -> dict[str, object]:
        h = host()
        gate = wiring.parse_gate(h.governor, None)
        cap = max(1, h.governor.limit(Pool.HEAVY_PARSE))

        def req(cls: CostClass) -> wiring.GateRequest:
            return wiring.GateRequest(cls=cls, org="o")

        held = [await gate.acquire(req(S), cancel=wiring.NEVER) for _ in range(cap)]
        waiter = asyncio.create_task(gate.acquire(req(S), cancel=wiring.NEVER))
        await asyncio.sleep(0.25)
        block = wiring.parse_engine_stats()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        for p in held:
            p.release()
        return block

    block = asyncio.run(run())
    small = block["classes"]["small"]  # type: ignore[index]
    assert small["waiting"] == 1 and small["in_flight"] >= 1 and small["oldest_wait_s"] >= 0.2
    assert block["classes"]["huge"] == {"waiting": 0, "in_flight": 0, "oldest_wait_s": 0.0}  # type: ignore[index]
    assert block["admission"] == "classes"


# -- indexing_main ------------------------------------------------------------------------------------------------


def test_the_live_config_service_is_attached_under_classes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.indexing_main import attach_parse_admission_config

    svc = object()
    assert attach_parse_admission_config(svc) is True
    assert wiring._Registry.config._svc is svc
    wiring.reset_for_tests()
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert attach_parse_admission_config(svc) is False
    assert wiring._Registry.config._svc is not svc


@pytest.mark.asyncio
async def test_health_reports_parse_engine_only_under_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from unittest.mock import MagicMock

    from app.indexing_main import health_check

    request = MagicMock()
    request.app.state = MagicMock()
    del request.app.state.governor
    del request.app.state.pipeline_runtime
    body = json.loads((await health_check(request)).body)
    assert set(body["parseEngine"]["classes"]) == {"small", "large", "huge"}
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert "parseEngine" not in json.loads((await health_check(request)).body)
