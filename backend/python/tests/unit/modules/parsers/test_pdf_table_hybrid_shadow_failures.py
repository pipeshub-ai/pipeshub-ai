"""Failure cases of the shadow run, through the real ``/table-structure`` route with a scripted model.

After each case the default downstream feedback holds no event: a failed or busy table service must not shrink the
index pools. The reason is recorded for every table, and the document line carries the denominator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Never
from unittest.mock import patch

import httpx
import pytest

import app.services.docling.docling_service as mod
from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf import table_hybrid_flow as flow
from app.modules.parsers.pdf.table_hybrid_client import TableStructureClient
from app.services.docling import table_structure as ts
from app.services.docling.table_structure_schema import ModelInfo
from app.services.resource_governor import (
    get_default_downstream_feedback,
    set_default_downstream_feedback,
)
from app.services.resource_governor.feedback import DownstreamFeedback
from tests.support.table_shadow import (
    CountingGate,
    FakeGovernor,
    ScriptedSupervisor,
    measured_model_info,
    parse_logs,
    spool_tables,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, caplog) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_TABLE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "shadow")
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")
    monkeypatch.delenv("PDF_DOCLING_TABLE_MODE", raising=False)
    caplog.set_level(logging.INFO, logger="app.modules.parsers.pdf.table_hybrid_flow")
    fb = DownstreamFeedback()
    set_default_downstream_feedback(fb)
    yield
    assert get_default_downstream_feedback().drain().is_empty  # no downstream event after any failure case
    set_default_downstream_feedback(None)


class Service:
    """The route with a scripted model, reached over ASGI, counting requests."""

    def __init__(self, script=None, **kw) -> None:
        self.sup = ScriptedSupervisor(script, **kw)
        self.gate = CountingGate(4)
        self.requests = 0
        self._transport = httpx.ASGITransport(app=mod.app)

    def transport(self) -> httpx.AsyncBaseTransport:
        outer = self

        class Counting(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                outer.requests += 1
                return await outer._transport.handle_async_request(request)

        return Counting()

    def patched(self):  # noqa: ANN201
        async def reserve(permit, demand, *, deadline_s=None, cancel=None) -> None:
            return None

        return (
            patch.object(mod, "_resource_governor", FakeGovernor(self.gate)),
            patch.dict(ts._holder, {"s": self.sup}),
            patch.object(mod, "reserve_or_adopt", reserve),
        )


async def run_doc(service: Service, n: int, *, transport=None, doc_s: float = 300.0, clock=None, coordinator=None):  # noqa: ANN201
    coord = coordinator or flow.ShadowCoordinator(
        TableStructureClient("http://svc", transport or service.transport()), doc_s=lambda: doc_s, **({"clock": clock} if clock else {})
    )
    adm = coord.admit("rec-1", "req-1")
    assert adm is not None
    spool_tables(adm.spool_dir, n)
    p1, p2, p3 = service.patched()
    with p1, p2, p3:
        await coord.start(adm, "rec-1", "req-1")
    return coord


def reasons(tables):  # noqa: ANN201
    return [t["reason"] for t in sorted(tables, key=lambda t: t["key"])]


async def test_a_clean_run_routes_every_table_to_the_model_and_writes_both_lines(caplog) -> None:
    svc = Service()
    await run_doc(svc, 3)
    tables, docs = parse_logs(caplog)
    assert reasons(tables) == ["none"] * 3 and all(t["would_route"] == "model" for t in tables)
    assert docs[0]["record_id"] == "rec-1" and docs[0]["tables_seen"] == 3 and docs[0]["sent"] == 3
    assert svc.requests == 1


async def test_f1_a_table_timeout_keeps_the_earlier_grids(caplog) -> None:
    svc = Service({"p1:b2": "err:timeout"})
    await run_doc(svc, 4)
    tables, docs = parse_logs(caplog)
    by = {t["key"]: t for t in tables}
    assert [by[f"p1:b{i}"]["reason"] for i in range(4)] == ["none", "none", "timeout", "none"]
    assert docs[0]["fallback_by_reason"] == {"timeout": 1} and docs[0]["docs_with_load_fallback"] is True


async def test_f2_an_exception_in_the_service_fails_one_table_not_the_request(caplog) -> None:
    svc = Service({"p1:b1": "raise"})
    await run_doc(svc, 3)
    tables, _ = parse_logs(caplog)
    by = {t["key"]: t for t in tables}
    assert by["p1:b1"]["reason"] == "error" and by["p1:b0"]["reason"] == "none" and by["p1:b2"]["reason"] == "none"


async def test_f3_a_service_that_is_down_is_one_attempt_and_the_next_document_is_not_started(caplog, tmp_path) -> None:
    seen = []

    def refuse(request) -> Never:
        seen.append(request)
        raise httpx.ConnectError("refused", request=request)

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(refuse)))
    await run_doc(svc, 40, coordinator=coord)  # three batches
    tables, docs = parse_logs(caplog)
    assert len(seen) == 1 and set(reasons(tables)) == {"service_down"} and len(tables) == 40
    assert coord.admit("rec-2", "req-2") is None  # latched: no spool, no worker work
    _, docs = parse_logs(caplog)
    assert docs[-1]["outcome"] == "shadow_busy:service_down"


async def test_f4_a_malformed_grid_is_recorded_as_malformed(caplog) -> None:
    svc = Service({"p1:b0": "malformed"})
    await run_doc(svc, 2)
    tables, _ = parse_logs(caplog)
    by = {t["key"]: t for t in tables}
    assert by["p1:b0"]["reason"] == "malformed" and by["p1:b0"]["malformed_why"] == "index_range" and by["p1:b1"]["reason"] == "none"


async def test_f5_the_deadline_marks_the_remaining_tables_and_nothing_fails(caplog) -> None:
    """A fake clock that moves 10 s per request: batches 1 and 2 run, batch 3 starts past the deadline."""
    now = {"t": 0.0}
    svc = Service()
    inner = svc.transport()

    class Ticking(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            out = await inner.handle_async_request(request)
            now["t"] += 10.0
            return out

    await run_doc(svc, 40, transport=Ticking(), doc_s=15.0, clock=lambda: now["t"])
    tables, docs = parse_logs(caplog)
    assert svc.requests == 2
    assert reasons(tables).count("deadline") == 8 and reasons(tables).count("none") == 32
    assert docs[0]["deadline_hit"] is True and docs[0]["tables_seen"] == 40


async def test_f6_a_429_stops_the_document_with_no_loop(caplog, monkeypatch) -> None:
    monkeypatch.setattr(ts, "START_ESTIMATE_PER_STEP_S", 0.0001)  # the wait for the permit, not the deadline, is what ends it
    svc = Service()
    await svc.gate.acquire(cost=4)  # the heavy gate is full of conversions
    await run_doc(svc, 40, doc_s=0.4)
    tables, docs = parse_logs(caplog)
    assert svc.requests == 1  # one attempt, no retry, no wait
    assert set(reasons(tables)) == {"service_busy"} and len(tables) == 40
    assert docs[0]["tables_seen"] == 40 and docs[0]["docs_with_load_fallback"] is True


async def test_f7_an_old_service_is_latched_unsupported(caplog) -> None:
    def old(request) -> httpx.Response:
        return httpx.Response(404, json={})

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(old)))
    await run_doc(svc, 3, coordinator=coord)
    tables, _ = parse_logs(caplog)
    assert set(reasons(tables)) == {"unsupported"}
    assert coord.admit("rec-2", "req-2") is None
    _, docs = parse_logs(caplog)
    assert docs[-1]["outcome"] == "shadow_busy:unsupported"


async def test_the_detached_run_does_not_carry_the_callers_context(caplog) -> None:
    import contextvars

    marker: contextvars.ContextVar[str | None] = contextvars.ContextVar("marker", default=None)
    marker.set("the finished record's context")
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(marker.get())
        return httpx.Response(500, json={})

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(handler)))
    await run_doc(svc, 2, coordinator=coord)
    assert seen == [None]


async def test_f8_a_version_mismatch_names_the_part_that_moved(caplog) -> None:
    good = measured_model_info()
    moved = ModelInfo(docling_ibm_models="9.9.9", mode=good.mode, weights_sha256=good.weights_sha256, config_sha256="0" * 64)
    svc = Service(model_info=moved)
    await run_doc(svc, 3)
    tables, _ = parse_logs(caplog)
    assert set(reasons(tables)) == {"version_mismatch"}
    warned = [r.getMessage() for r in caplog.records if "identity differs" in r.getMessage()]
    assert len(warned) == 1 and "library" in warned[0] and "config" in warned[0] and "weights" not in warned[0]


async def test_f8b_a_changed_weights_hash_alone_is_a_mismatch(caplog) -> None:
    from app.services.docling.table_structure_schema import config_hash

    good = measured_model_info()
    moved = ModelInfo(docling_ibm_models=good.docling_ibm_models, mode=good.mode, weights_sha256="f" * 64, config_sha256=config_hash(good.docling_ibm_models, good.mode, "f" * 64))
    svc = Service(model_info=moved)
    await run_doc(svc, 2)
    tables, _ = parse_logs(caplog)
    assert set(reasons(tables)) == {"version_mismatch"}
    assert "weights" in next(r.getMessage() for r in caplog.records if "identity differs" in r.getMessage())


async def test_f9_fast_mode_is_recorded_and_latched(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PDF_DOCLING_TABLE_MODE", "fast")
    svc = Service()
    coord = await run_doc(svc, 3)
    tables, _ = parse_logs(caplog)
    assert set(reasons(tables)) == {"mode_fast"} and svc.requests == 1
    assert coord.admit("r", "q") is None


async def test_a_cap_hit_is_recorded_and_routes_to_evidence(caplog) -> None:
    svc = Service({"p1:b0": "cap_hit"})
    await run_doc(svc, 2)
    tables, _ = parse_logs(caplog)
    assert {t["key"]: (t["reason"], t["would_route"]) for t in tables}["p1:b0"] == ("cap_hit", "evidence")


async def test_every_table_leaves_phase_two_with_a_route_and_a_reason_and_no_line_has_word_text(caplog) -> None:
    svc = Service({"p1:b1": "malformed", "p1:b2": "cap_hit"})
    await run_doc(svc, 4)
    tables, docs = parse_logs(caplog)
    assert len(tables) == 4 and all(t["route"] and t["reason"] and t["would_route"] and t["would_reason"] for t in tables)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "w00" not in logged and "w12" not in logged  # the grid words of the fixture boxes never reach a log line


async def test_a_second_document_while_one_runs_is_shadow_busy_and_still_writes_its_line(caplog) -> None:
    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", svc.transport()))
    first = coord.admit("rec-1", "req-1")
    assert first is not None
    assert coord.admit("rec-2", "req-2") is None
    _, docs = parse_logs(caplog)
    assert docs[-1]["record_id"] == "rec-2" and docs[-1]["outcome"] == "shadow_busy:queue_full" and docs[-1]["tables_seen"] is None
    coord.abandon(first)


async def test_an_unknown_record_id_is_logged_as_null(caplog) -> None:
    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", svc.transport()))
    adm = coord.admit(None, "req-9")
    spool_tables(adm.spool_dir, 2)
    p1, p2, p3 = svc.patched()
    with p1, p2, p3:
        await coord.start(adm, None, "req-9")
    _, docs = parse_logs(caplog)
    assert docs[0]["record_id"] is None and docs[0]["request_id"] == "req-9"


def test_the_measured_constants_are_what_the_clean_service_reports() -> None:
    info = measured_model_info()
    assert (info.docling_ibm_models, info.mode) == (th.MODEL_LIBRARY_VERSION, th.MODEL_MODE)


async def test_f8c_a_different_token_definition_is_a_mismatch_and_the_document_line_names_the_definition(caplog) -> None:
    from app.services.docling.table_structure_schema import config_hash

    good = measured_model_info()
    moved = ModelInfo(
        docling_ibm_models=good.docling_ibm_models, mode=good.mode, weights_sha256=good.weights_sha256, model_revision=good.model_revision,
        token_definition="text-layer-v1", config_sha256=config_hash(good.docling_ibm_models, good.mode, good.weights_sha256, good.model_revision, "text-layer-v1"),
    )
    await run_doc(Service(model_info=moved), 2)
    tables, docs = parse_logs(caplog)
    assert set(reasons(tables)) == {"version_mismatch"}
    assert "token_definition" in next(r.getMessage() for r in caplog.records if "identity differs" in r.getMessage())
    assert docs[0]["token_definition"] == th.TOKEN_DEFINITION


def test_the_rule_crossing_count_is_reported_and_changes_no_route() -> None:
    from app.services.docling.table_structure_schema import TableResult

    rec = {
        "words": [["a", 10.0, 0.0, 20.0, 10.0], ["b", 10.0, 30.0, 20.0, 40.0], ["c", 60.0, 0.0, 70.0, 10.0], ["d", 60.0, 30.0, 70.0, 40.0]],
        "rules": {"h": [[25.0, 0.0, 100.0]], "v": []}, "evidence_shape": [2, 2], "synthetic": [[1.0, 1.0, 2.0, 2.0]],
    }
    cells = [{"r0": r, "r1": r + 1, "c0": c, "c1": c + 1, "tokens": [r * 2 + c]} for r in range(2) for c in range(2)]
    clean = flow.analyse_table(rec, TableResult(id="t", status="ok", n_rows=2, n_cols=2, cells=cells), None)
    assert clean["rule_crossings"] == 0 and clean["would_route"] == "model" and clean["synthetic_excluded"] == 1
    merged = [{"r0": 0, "r1": 2, "c0": 0, "c1": 1, "tokens": [0, 1]}, {"r0": 0, "r1": 2, "c0": 1, "c1": 2, "tokens": [2, 3]}]
    crossed = flow.analyse_table(rec, TableResult(id="t", status="ok", n_rows=2, n_cols=2, cells=merged), None)
    assert crossed["rule_crossings"] == 2 and crossed["would_route"] == clean["would_route"] == "model"  # reported only
    unknown = flow.analyse_table({**rec, "rules": None}, TableResult(id="t", status="ok", n_rows=2, n_cols=2, cells=cells), None)
    assert unknown["rule_crossings"] is None


async def test_one_table_the_endpoint_would_refuse_fails_alone_as_invalid_input(caplog, monkeypatch) -> None:
    """A table whose own description is over the limit is not sent; the rest of its batch goes through."""
    monkeypatch.setattr(flow, "MAX_META_BYTES", 6000)  # the small fixture tables are far under it; one inflated table is not
    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", svc.transport()))
    adm = coord.admit("rec-1", "req-1")
    spool_tables(adm.spool_dir, 4)
    import json
    import os

    path = os.path.join(adm.spool_dir, flow.INDEX_FILE)
    lines = [json.loads(line) for line in open(path)]
    lines[2]["scale_factor"] = -1.0  # a description the schema refuses
    lines[1]["words"] = [["w", 1.0 + i, 2.0, 3.0 + i, 4.0] for i in range(400)]  # a description over the limit
    with open(path, "w") as f:
        f.write("".join(json.dumps(x) + "\n" for x in lines))
    p1, p2, p3 = svc.patched()
    with p1, p2, p3:
        await coord.start(adm, "rec-1", "req-1")
    tables, docs = parse_logs(caplog)
    by = {t["key"]: t["reason"] for t in tables}
    assert by == {"p1:b0": "none", "p1:b1": "invalid_input", "p1:b2": "invalid_input", "p1:b3": "none"}
    assert docs[0]["docs_with_load_fallback"] is False  # a bad input is not a load fallback


async def test_a_422_from_the_service_is_invalid_input_and_not_a_load_reason(caplog) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"success": False})

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(refuse)))
    await run_doc(svc, 3, coordinator=coord)
    tables, docs = parse_logs(caplog)
    assert set(reasons(tables)) == {"invalid_input"} and docs[0]["docs_with_load_fallback"] is False
    assert "invalid_input" in th.REASONS and "invalid_input" not in th.LOAD_REASONS


async def test_a_long_word_is_cut_and_counted_and_the_table_is_still_sent(caplog) -> None:
    import json
    import os

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", svc.transport()))
    adm = coord.admit("rec-1", "req-1")
    spool_tables(adm.spool_dir, 2)
    path = os.path.join(adm.spool_dir, flow.INDEX_FILE)
    lines = [json.loads(line) for line in open(path)]
    lines[0]["words"][0][0] = "." * 400
    lines[0]["long_words"] = 1
    with open(path, "w") as f:
        f.write("".join(json.dumps(x) + "\n" for x in lines))
    p1, p2, p3 = svc.patched()
    with p1, p2, p3:
        await coord.start(adm, "rec-1", "req-1")
    tables, docs = parse_logs(caplog)
    assert set(reasons(tables)) == {"none"} and docs[0]["long_words_cut"] == 1


async def test_a_deadline_shortfall_is_reported_as_deadline_not_service_busy(caplog) -> None:
    """An idle service, a document deadline too short for the first table: 429 with the deadline as its reason."""
    svc = Service()
    await run_doc(svc, 3, doc_s=0.5)
    tables, docs = parse_logs(caplog)
    assert set(reasons(tables)) == {"deadline"} and docs[0]["deadline_hit"] is True


async def test_a_deadline_that_runs_out_after_the_first_table_is_deadline_too(caplog, monkeypatch) -> None:
    svc = Service()
    original = svc.sup.run_table

    async def slow_first(spec: dict, png: bytes) -> dict:
        await asyncio.sleep(0.4)
        return await original(spec, png)

    svc.sup.run_table = slow_first  # type: ignore[method-assign]
    monkeypatch.setattr(ts, "START_ESTIMATE_PER_STEP_S", 0.02)  # a 56-step table needs 1.12 s: the first fits in 1.2 s, the second (0.8 s left) does not
    await run_doc(svc, 2, doc_s=1.2)
    tables, _ = parse_logs(caplog)
    by = {t["key"]: t["reason"] for t in tables}
    assert by["p1:b0"] == "none" and by["p1:b1"] == "deadline"


async def test_the_clients_timeout_outlasts_the_deadline_it_sends(caplog) -> None:
    """The service may answer 429 as late as the deadline it was given. The request's own timeout is longer by a margin, so
    that answer arrives first: tables the service never started are service_busy, not a model timeout."""
    seen: list[tuple[float, float]] = []

    def busy(request: httpx.Request) -> httpx.Response:
        seen.append((int(request.headers["X-Table-Deadline-Ms"]) / 1000, request.extensions["timeout"]["read"]))
        return httpx.Response(429, json={"errorCode": "service_busy"})

    svc = Service()
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(busy)), doc_s=lambda: 30.0)
    await run_doc(svc, 2, coordinator=coord)
    tables, _ = parse_logs(caplog)
    assert set(reasons(tables)) == {"service_busy"}
    deadline_s, read_timeout = seen[0]
    assert read_timeout >= deadline_s + flow.DEADLINE_MARGIN_S - 0.01


def test_a_strict_word_loss_is_reported_beside_the_one_percent_figure() -> None:
    from app.services.docling.table_structure_schema import TableResult

    n = 200
    rec = {"words": [[f"w{i}", float(i), 0.0, float(i) + 0.5, 5.0] for i in range(n)], "rules": None, "content_excess": 3}
    cells = [{"r0": r, "r1": r + 1, "c0": 0, "c1": 2, "tokens": list(range(r * 100 + (1 if r == 0 else 0), r * 100 + 100))} for r in range(2)]
    cells = [{**cells[0], "c1": 1}, {**cells[1], "r0": 1, "r1": 2, "c0": 1, "c1": 2}, {"r0": 0, "r1": 1, "c0": 1, "c1": 2, "tokens": []}, {"r0": 1, "r1": 2, "c0": 0, "c1": 1, "tokens": []}]
    one_lost = flow.analyse_table(rec, TableResult(id="t", status="ok", n_rows=2, n_cols=2, cells=cells), None)
    assert one_lost["content_words_lost"] == 1 and one_lost["strict_word_loss"] is True
    assert one_lost["would_route"] == "model" and one_lost["content_excess"] == 3  # the 1 % rule still passes it
    full = [{**cells[0], "tokens": list(range(0, 100))}, *cells[1:]]
    none_lost = flow.analyse_table(rec, TableResult(id="t", status="ok", n_rows=2, n_cols=2, cells=full), None)
    assert none_lost["content_words_lost"] == 0 and none_lost["strict_word_loss"] is False


async def test_a_document_without_a_parse_wall_logs_null_and_counts_its_table_pages(caplog) -> None:
    await run_doc(Service(), 8)  # six boxes to a page: two pages
    _, docs = parse_logs(caplog)
    assert docs[0]["layout_parse_wall_s"] is None and docs[0]["table_pages"] == 2
