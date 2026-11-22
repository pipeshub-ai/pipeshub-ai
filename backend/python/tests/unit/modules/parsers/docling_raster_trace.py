"""Records what the rasterizer, the local Docling processor and the Docling service do with
``PARSE_LANES`` off, so a test can pin it.

``fixtures/docling_raster_off_trace.json`` was produced by running
``python -m tests.unit.modules.parsers.docling_raster_trace`` on the tree before the lanes
work reached these three modules. It holds a hash of every top-level statement and method
of each module (read from git, so it is the committed source and not the working tree) and
the traces below: executor creation and submission order for the rasterizer and for
Docling, the answers of the worker-count settings, and the status code, body and permit
calls of the service endpoint.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import threading
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tests.unit.modules.parsers.parse_pool_trace import module_hashes

FIXTURE = Path(__file__).parent / "fixtures" / "docling_raster_off_trace.json"
MODULES = {
    "pdf_rasterizer.py": "app/modules/parsers/pdf/pdf_rasterizer.py",
    "docling_processor.py": "app/modules/parsers/pdf/docling_processor.py",
    "docling_service.py": "app/services/docling/docling_service.py",
}
SETTING_VALUES = (None, "", "0", "1", "2", "7", "-3", "auto", "AUTO", "x", " 3 ")


class FakeExecutor:
    """Stands in for ``ProcessPoolExecutor`` and logs what is asked of it."""

    log: list[list] = []

    def __init__(self, **kwargs: object) -> None:
        context = kwargs.get("mp_context")
        init = kwargs.get("initializer")
        FakeExecutor.log.append(
            [
                "create",
                kwargs.get("max_workers"),
                getattr(context, "_name", None) or type(context).__name__,
                getattr(init, "__name__", None),
                len(kwargs.get("initargs") or ()),
            ]
        )

    def submit(self, fn: object, *args: object) -> Future:
        inner = args[0] if args else None
        FakeExecutor.log.append(["submit", getattr(fn, "__name__", "?"), getattr(inner, "__name__", "?"), len(args) - 1])
        future: Future = Future()
        broken = getattr(FakeExecutor, "broken", False)
        if broken:
            future.set_exception(BrokenProcessPool("killed"))
        else:
            future.set_result(FakeExecutor.result)
        return future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        FakeExecutor.log.append(["shutdown", wait, cancel_futures])

    result: object = {1: ("page", 1.0)}
    broken = False


def record_raster() -> dict:
    from app.modules.parsers.pdf import pdf_rasterizer as r

    FakeExecutor.log = []
    out: dict = {}
    r._get_pdf_raster_pool.cache_clear()
    with patch.object(r, "ProcessPoolExecutor", FakeExecutor):
        FakeExecutor.broken = False
        FakeExecutor.result = {1: ("page", 1.0)}
        out["sync"] = [
            repr(r.render_all_pages_from_path_sync("/x.pdf", 100)),
            repr(r.render_all_pages_from_bytes_sync(b"%PDF", 100)),
            repr(r.render_batch_from_path_sync("/x.pdf", [1, 2], 100)),
            repr(r.render_batch_from_bytes_sync(b"%PDF", [1], 100)),
        ]
        FakeExecutor.result = ("page", 2.0)
        out["pages"] = [
            repr(r.render_page_from_path_sync("/x.pdf", 1, 100)),
            repr(r.render_page_from_bytes_sync(b"%PDF", 2, 100)),
        ]
        FakeExecutor.result = {1: ("page", 1.0)}
        out["async"] = repr(asyncio.run(r.render_all_pages_from_bytes(b"%PDF", 100)))
        governor = MagicMock()
        r.set_resource_governor(governor)
        FakeExecutor.broken = True
        try:
            r.render_all_pages_from_path_sync("/x.pdf", 100)
            out["broken"] = "no error"
        except BrokenProcessPool:
            out["broken"] = "BrokenProcessPool"
        out["incident"] = [str(c.args[0]) for c in governor.report_memory_incident.call_args_list]
        out["cache_after_break"] = r._get_pdf_raster_pool.cache_info().currsize
        r.set_resource_governor(None)
        FakeExecutor.broken = False
        r.render_all_pages_from_path_sync("/x.pdf", 100)
        out["shutdown"] = r.shutdown_pdf_raster_pool()
        out["shutdown_again"] = r.shutdown_pdf_raster_pool()
    out["log"] = FakeExecutor.log
    r._get_pdf_raster_pool.cache_clear()
    return out


def worker_count_grid() -> dict:
    """What the two import-time worker settings make of every value."""
    from app.modules.parsers.pdf import docling_processor as d
    from app.modules.parsers.pdf import pdf_rasterizer as r

    out: dict = {}
    saved = {k: os.environ.get(k) for k in ("LOCAL_DOCLING_PARSE_WORKERS", "PDF_RASTER_WORKERS")}
    try:
        for value in SETTING_VALUES:
            for name in saved:
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            out[f"docling/{value!r}"] = d._get_local_parse_worker_count()
            out[f"raster/{value!r}"] = r._get_pdf_raster_worker_count() if value != "" else "default"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return out


def _fake_conversion(status: str = "success") -> MagicMock:
    result = MagicMock()
    result.status.value = status
    result.document = "DOC"
    return result


def record_docling() -> dict:
    from docling_core.types.doc.document import DoclingDocument

    from app.modules.parsers.pdf import docling_processor as d

    out: dict = {}
    log: list = []

    class Converter:
        def convert(self, source: object, **kwargs: object) -> MagicMock:
            log.append(["convert", source.name, sorted(kwargs), threading.current_thread() is threading.main_thread()])
            return _fake_conversion()

    processor = d.DoclingProcessor.__new__(d.DoclingProcessor)
    processor.logger = MagicMock()
    processor.config = None
    processor.converter = Converter()  # type: ignore[assignment]

    async def run(coro: object) -> object:
        try:
            return await coro  # type: ignore[misc]
        except Exception as exc:
            return f"{type(exc).__name__}"

    with patch.object(d, "LOCAL_DOCLING_PARSE_WORKERS", 1):
        out["thread"] = [
            asyncio.run(run(processor.parse_document("a.docx", b"PK"))),
            asyncio.run(run(processor.parse_document("a.pdf", BytesIO(b"%PDF"), page_range=(1, 3)))),
        ]
        bad = MagicMock()
        bad.convert.return_value = _fake_conversion("failure")
        processor.converter = bad
        out["thread_failure"] = asyncio.run(run(processor.parse_document("a.docx", b"PK")))
    out["thread_log"] = log

    serialized = DoclingDocument(name="t").model_dump_json()
    FakeExecutor.log = []
    FakeExecutor.result = serialized
    FakeExecutor.broken = False
    d._get_process_pool.cache_clear()
    governor = MagicMock()
    d.set_resource_governor(governor)
    with patch.object(d, "LOCAL_DOCLING_PARSE_WORKERS", 3), patch.object(d, "ProcessPoolExecutor", FakeExecutor):
        out["executor"] = [
            asyncio.run(run(processor.parse_document("a.docx", b"PK"))).name,
            asyncio.run(run(processor.parse_document("a.pdf", b"%PDF", page_range=(2, 4)))).name,
        ]
        FakeExecutor.broken = True
        out["executor_broken"] = asyncio.run(run(processor.parse_document("a.pdf", b"%PDF")))
        out["executor_cache_after_break"] = d._get_process_pool.cache_info().currsize
    out["executor_incident"] = [str(c.args[0]) for c in governor.report_memory_incident.call_args_list]
    out["executor_log"] = FakeExecutor.log
    d.set_resource_governor(None)
    d._get_process_pool.cache_clear()
    out["grid"] = worker_count_grid()
    return out


def record_service() -> dict:
    from fastapi import BackgroundTasks

    import app.services.docling.docling_service as mod

    out: dict = {}

    class Permits:
        def __init__(self, admit: bool = True) -> None:
            self.admit = admit
            self.events: list = []

        async def acquire(self, data: bytes, name: str) -> tuple[bool, int]:
            self.events.append(["acquire", len(data), name])
            return (True, 7) if self.admit else (False, 0)

        def release(self, cost: int) -> None:
            self.events.append(["release", cost])

    def doc(serialize: object | None = None) -> MagicMock:
        item = MagicMock()
        item.model_dump_json.side_effect = serialize or (lambda *a, **k: "{}")
        return item

    async def call(**form: object) -> object:
        upload = MagicMock(read=AsyncMock(return_value=b"data"))
        return await mod.parse_pdf_endpoint(
            background_tasks=BackgroundTasks(),
            file=upload,
            record_name="a.pdf",
            start_page=form.get("start_page"),
            end_page=form.get("end_page"),
        )

    def shape(response: object) -> list:
        if hasattr(response, "status_code"):
            body = json.loads(response.body)
            return [response.status_code, body, dict(response.headers).get("retry-after")]
        return [200, response.model_dump()]

    async def scenario(name: str, parse: object, *, admit: bool = True, timeout: float | None = None, hang: threading.Event | None = None) -> None:
        permits = Permits(admit)
        calls: list = []

        async def traced(*args: object, **kwargs: object) -> object:
            calls.append([len(args), sorted(kwargs), kwargs.get("page_range")])
            return await parse(*args, **kwargs)  # type: ignore[operator]

        svc = mod.DoclingService()
        svc.parse_pdf_only = traced  # type: ignore[method-assign]
        mod.docling_service = svc
        mod._parse_tasks.clear()
        mod._abandoned.clear()
        patches = [
            patch.object(mod, "_acquire_docling_gate", permits.acquire),
            patch.object(mod, "_release_docling_gate", permits.release),
        ]
        if timeout is not None:
            patches.append(patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", timeout))
        for p in patches:
            p.start()
        try:
            response = await call(start_page=2, end_page=5)
            record = {"response": shape(response), "calls": calls, "events_at_answer": list(permits.events)}
            if hang is not None:
                hang.set()
            for _ in range(100):
                if any(e[0] == "release" for e in permits.events):
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.1)
            record["events_final"] = list(permits.events)
            record["abandoned_after"] = len(mod._abandoned)
            record["tasks_after"] = len(mod._parse_tasks)
            out[name] = record
        finally:
            for p in patches:
                p.stop()

    async def ok(*a: object, **k: object) -> MagicMock:
        return doc()

    async def boom(*a: object, **k: object) -> MagicMock:
        raise RuntimeError("boom")

    stop = threading.Event()

    async def hanging(*a: object, **k: object) -> MagicMock:
        await asyncio.to_thread(stop.wait, 10)
        return doc()

    async def main() -> None:
        await scenario("success", ok)
        await scenario("parse_error", boom)
        await scenario("serialization_error", lambda *a, **k: _async_value(doc(MagicMock(side_effect=ValueError("ser")))))
        await scenario("backpressure", ok, admit=False)
        await scenario("timeout", hanging, timeout=0.3, hang=stop)

    saved = mod.docling_service
    try:
        asyncio.run(main())
    finally:
        mod.docling_service = saved

    out["holding_gate"] = asyncio.run(_holding_gate_trace(mod))
    return out


async def _async_value(value: object) -> object:
    return value


async def _holding_gate_trace(mod: object) -> dict:
    """``_parse_holding_gate``: the permit goes back once, whether the parse works or not,
    and a caller that stopped waiting does not cancel the task."""
    trace: dict = {}
    for name, parse in (
        ("ok", AsyncMock(return_value=MagicMock(model_dump_json=lambda: "{}"))),
        ("fail", AsyncMock(side_effect=RuntimeError("x"))),
    ):
        released: list = []
        service = MagicMock()
        service.parse_pdf_only = parse
        with patch.object(mod, "_release_docling_gate", released.append):
            try:
                result = await mod._parse_holding_gate(service, "a.pdf", b"x", (1, 2), 5)  # type: ignore[attr-defined]
            except RuntimeError as exc:
                result = f"RuntimeError:{exc}"
        trace[name] = [result, released, [c.args[:2] for c in parse.await_args_list], [sorted(c.kwargs) for c in parse.await_args_list]]
    released = []
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow(*a: object, **k: object) -> MagicMock:
        started.set()
        await finish.wait()
        return MagicMock(model_dump_json=lambda: "{}")

    service = MagicMock()
    service.parse_pdf_only = slow
    with patch.object(mod, "_release_docling_gate", released.append):
        task = asyncio.ensure_future(mod._parse_holding_gate(service, "a.pdf", b"x", None, 9))  # type: ignore[attr-defined]
        await started.wait()
        waiter = asyncio.ensure_future(asyncio.wait_for(asyncio.shield(task), 0.05))
        with contextlib.suppress(asyncio.TimeoutError):
            await waiter
        trace["after_caller_timeout"] = [task.done(), list(released)]
        finish.set()
        trace["result_after_finish"] = await task
        trace["released_at_end"] = list(released)
    return trace


def record() -> dict:
    return {
        "raster": record_raster(),
        "docling": record_docling(),
        "service": record_service(),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[4]
    hashes = {}
    for name, rel in MODULES.items():
        blob = subprocess.run(["git", "show", f"HEAD:backend/python/{rel}"], capture_output=True, text=True, check=True).stdout
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"hash_{name}"
        tmp.write_text(blob)
        hashes[name] = module_hashes(tmp)
        tmp.unlink()
    payload = record()
    payload["hashes"] = hashes
    payload["recorded_from"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root).stdout.strip()
    FIXTURE.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
