"""Parse benchmark driver: one run of one corpus mix through the service's parse path.

It drives what ``app/api/routes/parsing.py`` drives, without HTTP: the real
``ResourceGovernor`` (adaptive, its own sizing) and its gates admit each file,
then the real providers parse it, with the parse pool (``parse_pool``), the
PdfPlumber+OpenCV processor as the default PDF path, local Docling for DOCX
(and for PDFs with ``--pdf-parser docling``, the ``ENABLE_PDFPLUMBER_PROCESSOR=false``
setting), and the real VLM OCR strategy with only the model call stubbed.

Reported: files/s, event-loop lag p50/p99 (``LoopLagMonitor``), peak RSS of the
parent and of the parse workers, per-worker memory and threads, CPU seconds,
and, in a container, cgroup ``memory.peak`` and ``cpu.stat`` throttling.

Emulating a tier without a cgroup (``--cpus``, ``--mem-limit-gb``) pins the CPU
affinity, which ``effective_cpu_count()`` and the governor both read, and tells
the governor the memory limit and a working set taken from the process tree. A
watchdog stops the run above 85 % of that limit. It is not a cgroup run:
there is no kernel enforcement and no throttling.

Run from backend/python::

    python -m tests.perf.parse_bench.driver --corpus DIR --mix cpu-heavy --cpus 2 --out run.json
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import platform
import random
import resource
import socket
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tests.perf.parse_bench import golden, report
from tests.perf.parse_bench.corpus import Entry, load_manifest

MIXES: dict[str, dict[str, int]] = {
    # kind -> copies of each file in a pass
    "cpu-heavy": {"tables": 1, "figures": 1, "digital": 1, "docx": 1, "damaged": 1, "large": 1},
    "io-heavy": {"md": 4, "json": 4, "txt": 4, "scanned": 1},
    "laptop-typical": {"digital": 1, "docx": 1, "tables": 1, "scanned": 1, "md": 2, "json": 1, "txt": 2},
    "all": {"tables": 1, "figures": 1, "digital": 1, "docx": 1, "damaged": 1, "large": 1,
            "scanned": 1, "md": 1, "json": 1, "txt": 1},
}
HOST_LIMIT_PCT = 85.0
SAMPLE_SECONDS = 0.5
LAG_INTERVAL_SECONDS = 0.1
STALL_STACK_SECONDS = 0.25
RETRY_SECONDS = 1.0

log = logging.getLogger("parse_bench")


def select(entries: list[Entry], mix: str, passes: int, seed: int) -> list[Entry]:
    """The files of one run: *passes* of the mix's kinds, in a seeded order."""
    kinds = MIXES[mix]
    picked = [e for e in entries for _ in range(kinds.get(e.kind, 0))]
    out: list[Entry] = []
    for n in range(passes):
        batch = list(picked)
        random.Random(seed + n).shuffle(batch)
        out += batch
    return out


# -- process-tree sampler -----------------------------------------------------


class TreeSampler(threading.Thread):
    """Samples the parent and every descendant: RSS, threads, CPU, per-worker figures."""

    def __init__(self, *, mem_limit: int | None, host_limit_pct: float = HOST_LIMIT_PCT) -> None:
        super().__init__(name="tree-sampler", daemon=True)
        import psutil

        self._ps = psutil
        self._me = psutil.Process()
        self._stop_event = threading.Event()
        self.mem_limit = mem_limit
        self.host_limit_pct = host_limit_pct
        self.latest_tree_rss = 0
        self.abort_reason: str | None = None
        self.peak_tree_rss = 0
        self.peak_parent_rss = 0
        self.peak_tree_pss = 0
        self.peak_threads = 0
        self.peak_parse_workers = 0
        self.cpu_sampled = 0.0
        self.procs: dict[int, dict[str, Any]] = {}
        self._tick = 0

    def stop(self) -> None:
        self._stop_event.set()

    def parent_rss_now(self) -> int:
        return self._me.memory_info().rss

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sample()
            except Exception:  # a process exiting under us is normal
                log.debug("sample failed", exc_info=True)
            self._stop_event.wait(SAMPLE_SECONDS)

    def _role(self, proc: Any, parent_roles: dict[int, str]) -> str:  # noqa: ANN401
        if proc.pid == self._me.pid:
            return "parent"
        try:
            cmd = " ".join(proc.cmdline())
        except self._ps.Error:
            return "other"
        if "app.modules.parsers.parse_worker" in cmd:
            return "parse_worker"
        if parent_roles.get(proc.ppid()) == "parse_worker":
            return "raster"
        if "multiprocessing" in cmd:
            return "docling_or_pool"
        return "other"

    def sample(self) -> None:
        self._tick += 1
        want_pss = self._tick % 4 == 1
        procs = [self._me, *self._me.children(recursive=True)]
        roles: dict[int, str] = {}
        total_rss = total_pss = threads = cpu = 0
        workers = 0
        for proc in sorted(procs, key=lambda p: p.pid != self._me.pid):
            try:
                with proc.oneshot():
                    rss = proc.memory_info().rss
                    nthreads = proc.num_threads()
                    times = proc.cpu_times()
                    role = self._role(proc, roles)
                    pss = proc.memory_full_info().pss if want_pss else 0
            except self._ps.Error:
                continue
            roles[proc.pid] = role
            total_rss += rss
            total_pss += pss
            threads += nthreads
            cpu += times.user + times.system
            workers += role == "parse_worker"
            rec = self.procs.setdefault(proc.pid, {
                "role": role, "first_rss": rss, "peak_rss": 0, "peak_threads": 0, "first_seen": time.time(),
            })
            rec["peak_rss"] = max(rec["peak_rss"], rss)
            rec["peak_threads"] = max(rec["peak_threads"], nthreads)
            rec["last_seen"] = time.time()
        self.latest_tree_rss = total_rss
        self.peak_tree_rss = max(self.peak_tree_rss, total_rss)
        self.peak_tree_pss = max(self.peak_tree_pss, total_pss)
        self.peak_parent_rss = max(self.peak_parent_rss, self.procs[self._me.pid]["peak_rss"])
        self.peak_threads = max(self.peak_threads, threads)
        self.peak_parse_workers = max(self.peak_parse_workers, workers)
        self.cpu_sampled = max(self.cpu_sampled, cpu)
        self.check_limits(total_rss, self._ps.virtual_memory().percent)

    def check_limits(self, tree_rss: int, host_pct: float) -> None:
        """Set ``abort_reason`` when the run must stop: host memory, or 85 % of the emulated limit."""
        if host_pct >= self.host_limit_pct:
            self.abort_reason = f"host memory {host_pct:.0f} % >= {self.host_limit_pct:.0f} %"
        elif self.mem_limit and tree_rss >= report.GATE_MEM_FRACTION * self.mem_limit:
            self.abort_reason = (
                f"tree RSS {tree_rss / report.MIB:.0f} MiB >= 85 % of the emulated "
                f"{self.mem_limit / report.MIB:.0f} MiB limit"
            )

    def per_worker(self) -> list[dict[str, Any]]:
        return [
            {"pid": pid, **{k: v for k, v in rec.items() if k in ("role", "first_rss", "peak_rss", "peak_threads")}}
            for pid, rec in sorted(self.procs.items()) if rec["role"] != "parent"
        ]


class StackCollector(logging.Handler):
    """Keeps the stacks the loop-lag watchdog logs when the loop stops turning."""

    marker = "it is executing:\n"

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.stacks: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if self.marker in text:
            self.stacks.append(text.split(self.marker, 1)[1])


# -- the stubs: only the model call -------------------------------------------


def stub_markdown(content_len: int, page: int, latency: float) -> str:
    rng = random.Random(content_len * 1009 + page)
    words = ["alpha", "beta", "gamma", "delta", "kappa", "sigma", "omega", "theta"]
    lines = [f"# Page {page}", ""]
    lines += [" ".join(rng.choice(words) for _ in range(12)) + "." for _ in range(6)]
    return "\n".join(lines)


def _build_ocr(latency: float):  # noqa: ANN202
    from app.config.constants.ai_models import OCRProvider
    from app.modules.parsers.pdf.ocr_handler import OCRHandler
    from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy

    class StubVLM(VLMOCRStrategy):
        """The real strategy (render at RENDER_DPI, page workers, retries); only the model is fake."""

        async def _get_multimodal_llm(self):  # noqa: ANN202
            return object()

        async def _call_llm_for_markdown(self, image_base64: str, page_number: int) -> str:
            await asyncio.sleep(latency)
            return stub_markdown(len(image_base64), page_number, latency)

    class StubHandler(OCRHandler):
        def _create_strategy(self, strategy_type: str, **kwargs: object) -> VLMOCRStrategy:
            return StubVLM(self.logger, None)  # type: ignore[arg-type]

    return StubHandler(log, OCRProvider.VLM_OCR.value)


def build_registry(pdf_parser: str, ocr_latency: float) -> Any:  # noqa: ANN401
    from app.modules.parsers.json.json_parser import JSONParser
    from app.modules.parsers.markdown.docling_markdown_parser import (
        DoclingMarkdownParser,
    )
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
        PDFPlumberOpenCVProcessor,
    )
    from app.services.parsing.interface import ParserProvider
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
    from app.services.parsing.providers.ocr_parser import OCRParser
    from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
    from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
    from app.services.parsing.registry import ParserRegistry

    registry = ParserRegistry()
    md = MarkdownItParser()
    docling = LocalDoclingParser(DoclingProcessor(logger=log, config=None))
    plumber = PdfPlumberParser(PDFPlumberOpenCVProcessor(log, None))  # type: ignore[arg-type]
    # As parsing_main.py:151: each OCR page goes through the Docling markdown parser, not markdown-it.
    ocr = OCRParser(_build_ocr(ocr_latency), DoclingMarkdownParser(log, None))
    if pdf_parser == "pdfplumber":
        pdf = SmartPDFParser(plumber, ocr)
    else:
        pdf = SmartPDFParser(docling, ocr, large_pdf_parser=plumber)
    registry.register("pdf", ParserProvider.DEFAULT, pdf)
    registry.register("docx", ParserProvider.DEFAULT, docling)
    registry.register("md", ParserProvider.DEFAULT, md)
    registry.register("txt", ParserProvider.DEFAULT, md)
    registry.register("json", ParserProvider.DEFAULT, JSONParser())
    return registry


class TierProbe:
    """The real probe, with the memory a tier would show: limit and working set."""

    def __init__(self, inner: Any, cpus: int | None, mem_limit: int | None, sampler: TreeSampler) -> None:  # noqa: ANN401
        self._inner, self._cpus, self._mem_limit, self._sampler = inner, cpus, mem_limit, sampler

    def snapshot(self):  # noqa: ANN201
        snap = self._inner.snapshot()
        changes: dict[str, Any] = {}
        if self._cpus:
            changes["cpu_quota"] = float(self._cpus)
        if self._mem_limit:
            rss = self._sampler.latest_tree_rss or snap.mem_working_set_bytes
            changes.update(mem_limit_bytes=self._mem_limit, mem_working_set_bytes=rss,
                           mem_working_set_raw_bytes=rss, mem_baseline_bytes=None)
        return dataclasses.replace(snap, **changes) if changes else snap


# -- the run ------------------------------------------------------------------


def serialize(result: Any) -> Any:  # noqa: ANN401
    """What the route does with a result on the loop: dump a container to JSON. A Docling
    document stays the string the service would send."""
    if result.block_container is not None:
        return json.loads(result.block_container.model_dump_json())
    return result.raw_document or "{}"


def summarize_payload(payload: Any) -> dict[str, Any]:  # noqa: ANN401
    """Golden entry for a serialized result. Run after the timed window, off the loop."""
    if isinstance(payload, str):
        doc = json.loads(payload)
        return golden.summarize(doc, len(doc.get("texts") or []), len(doc.get("tables") or []))
    return golden.summarize(payload, len(payload.get("blocks") or []), len(payload.get("block_groups") or []))


async def run(args: argparse.Namespace, files: list[Entry], corpus: Path, sampler: TreeSampler) -> dict[str, Any]:
    from app.modules.parsers import parse_pool
    from app.modules.parsers.pdf import docling_processor, pdf_rasterizer
    from app.services.parsing.interface import ParseError, ParserProvider
    from app.services.resource_governor import (
        ResourceGovernor,
        acquire_gate_with_backpressure,
        build_probe,
        classify,
        gate_pool,
        parse_cost,
    )
    from app.utils.loop_lag_monitor import LoopLagMonitor

    class RecordingMonitor(LoopLagMonitor):
        """The production monitor, keeping every sample so p50/p99 can be read."""

        lags: list[float]

        def observe(self, lag: float, now: float | None = None) -> None:
            self.lags.append(lag)
            super().observe(lag, now)

    registry = build_registry(args.pdf_parser, args.ocr_latency)
    mem_limit = int(args.mem_limit_gb * 1024**3) if args.mem_limit_gb else None
    governor = ResourceGovernor(
        logger=log,
        env_parse=None,
        worker_count=1,
        probe=TierProbe(build_probe(), args.cpus, mem_limit, sampler),
    )
    parse_pool.set_resource_governor(governor)
    docling_processor.set_resource_governor(governor)
    pdf_rasterizer.set_resource_governor(governor)
    ceilings = governor.ceilings
    executor = ThreadPoolExecutor(max_workers=ceilings.heavy + ceilings.light + 4, thread_name_prefix="parsing-worker")
    loop = asyncio.get_running_loop()
    loop.set_default_executor(executor)

    # stack_after: the production watchdog logs what the loop thread is executing once the loop
    # has been silent that long. The handler keeps those stacks; they say which code held the loop.
    stacks = StackCollector()
    log.addHandler(stacks)
    log.setLevel(logging.WARNING)
    log.propagate = False
    monitor = RecordingMonitor(log, "bench", interval=LAG_INTERVAL_SECONDS, warn_after=1e9, stack_after=STALL_STACK_SECONDS)
    monitor.lags = []
    monitor.start()
    governor_task = asyncio.create_task(governor.run())
    # Services, models' libraries and the governor are up, nothing parsed yet: the idle footprint.
    idle_rss = sampler.parent_rss_now()
    concurrency = args.concurrency or max(2, 2 * (ceilings.heavy + ceilings.light))
    sem = asyncio.Semaphore(concurrency)
    outcomes: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    errors: dict[str, str | None] = {}
    stats = Counter()

    async def one(entry: Entry) -> None:
        async with sem:
            # Read once admitted, as a service holds only the files it is working on; the read is off the loop.
            data = await asyncio.to_thread((corpus / entry.path).read_bytes)
            started = time.monotonic()
            tier = classify(entry.ext, entry.mime)
            pool = gate_pool(tier)
            cost = parse_cost(tier, len(data))
            gate = governor.gate(pool)
            while not await acquire_gate_with_backpressure(
                gate, cost, tier, entry.path, logger=log, log_prefix="bench",
                queue_wait_warn_seconds=1e9, gate_timeout_seconds=args.gate_timeout,
            ):
                stats["backpressure_429"] += 1
                await asyncio.sleep(RETRY_SECONDS)
            admitted = time.monotonic()
            error = None
            try:
                parser = registry.resolve(entry.mime, entry.ext, ParserProvider.DEFAULT)
                result = await parser.parse(data, entry.path, {"extension": entry.ext, "skip_table_enrichment": True})
                payload = serialize(result)
            except ParseError as exc:
                error = exc.code.value
                payload = {"error": error}
            except Exception as exc:  # recorded, not fatal to the run
                error = type(exc).__name__
                payload = {"error": error}
            finally:
                gate.release(cost)
            done = time.monotonic()
        outcomes.append({"path": entry.path, "kind": entry.kind, "bytes": len(data), "error": error,
                         "latency_s": done - started, "gate_wait_s": admitted - started})
        payloads.setdefault(entry.path, payload if error is None else None)
        errors.setdefault(entry.path, error)

    cg_path = Path(args.cgroup) if args.cgroup else None
    cg_before = report.read_cgroup(cg_path) if cg_path else None
    peak = report.PeakReader(cg_path) if cg_path else None
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    rc0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    tasks = [asyncio.create_task(one(e)) for e in files]

    async def watchdog() -> None:
        while not sampler.abort_reason:  # noqa: ASYNC110 - the flag is set from the sampler thread
            await asyncio.sleep(0.5)
        for t in tasks:
            t.cancel()

    guard = asyncio.create_task(watchdog())
    await asyncio.gather(*tasks, return_exceptions=True)
    window_end = time.monotonic()
    wall = window_end - t0
    guard.cancel()
    cg_after = report.read_cgroup(cg_path) if cg_path else None
    if peak is not None and cg_after is not None:
        scoped = peak.read()
        cg_after["memory_peak"] = scoped if scoped is not None else cg_after["memory_peak"]
        cg_after["memory_peak_scope"] = "run" if peak.scoped else "container lifetime"
        peak.close()
    gov_stats = governor.stats()
    monitor.stop()
    governor.stop()
    governor_task.cancel()
    parse_pool.shutdown_parse_pool()
    governor.close()
    executor.shutdown(wait=False, cancel_futures=True)
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    rc1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = (ru1.ru_utime + ru1.ru_stime - ru0.ru_utime - ru0.ru_stime
                   + rc1.ru_utime + rc1.ru_stime - rc0.ru_utime - rc0.ru_stime)

    # Hashing is the harness's own cost, so it happens here, outside the measured window.
    golden_by_name = {
        path: ({"error": errors[path]} if payload is None else summarize_payload(payload))
        for path, payload in payloads.items()
    }
    ok = [o for o in outcomes if o["error"] is None]
    failed = [o for o in outcomes if o["error"] is not None and o["kind"] != "damaged"]
    return {
        "wall_s": wall,
        "window": (t0, window_end),
        "outcomes": outcomes,
        "ok": len(ok),
        "failed": len(failed),
        "failed_paths": sorted({o["path"] for o in failed}),
        "stats": dict(stats),
        "lags": monitor.lags,
        "idle_rss": idle_rss,
        "stall_stacks": stacks.stacks,
        "cpu_seconds": cpu_seconds,
        "ceilings": gov_stats.get("ceilings"),
        "limits": gov_stats.get("limits"),
        "cpu_quota": gov_stats.get("cpu_quota"),
        "mem_limit_bytes": gov_stats.get("mem_limit_bytes"),
        "concurrency": concurrency,
        "golden": golden_by_name,
        "cgroup": (
            {**report.cgroup_delta(cg_before, cg_after), "memory_peak_scope": cg_after.get("memory_peak_scope")}
            if cg_before and cg_after else None
        ),
        "n_files": len(files),
    }


def host_load() -> dict[str, float]:
    import psutil

    return {"loadavg_1m": os.getloadavg()[0], "cpu_pct": psutil.cpu_percent(interval=1.0),
            "mem_pct": psutil.virtual_memory().percent}


def assemble(args: argparse.Namespace, raw: dict[str, Any], sampler: TreeSampler, load: dict[str, float],
             manifest_id: str) -> dict[str, Any]:
    workers = [w for w in sampler.per_worker() if w["role"] == "parse_worker"]
    peaks = [w["peak_rss"] for w in workers]
    onnx_threads = int(os.environ.get("PDF_TABLE_MODEL_THREADS", "4"))
    return {
        "config": {
            "label": args.label, "mix": args.mix, "passes": args.passes, "cpus": args.cpus,
            "mem_limit_gb": args.mem_limit_gb, "pdf_parser": args.pdf_parser, "arm": report.ARM_LABELS[args.pdf_parser], "concurrency": raw["concurrency"],
            "ocr_latency_s": args.ocr_latency, "corpus_id": manifest_id, "seed": args.seed,
            "emulated": not raw["cgroup"], "table_model": os.environ.get("PDF_TABLE_MODEL", "heron"),
            "onnx_threads_per_worker": onnx_threads,
            "env": {k: os.environ[k] for k in sorted(os.environ) if k.startswith(
                ("PARSE_POOL", "PDF_", "LOCAL_DOCLING", "MAX_CONCURRENT", "GOVERNOR_", "ENABLE_PDFPLUMBER"))},
        },
        "host": {"python": platform.python_version(), "node": socket.gethostname(), "host_cpus": os.cpu_count(),
                 "affinity": len(os.sched_getaffinity(0)), **load},
        "status": "aborted: " + sampler.abort_reason if sampler.abort_reason else "ok",
        "files": {"submitted": raw["n_files"], "ok": raw["ok"], "failed": raw["failed"],
                  "failed_paths": raw["failed_paths"],
                  "damaged_errors": sum(1 for o in raw["outcomes"] if o["kind"] == "damaged" and o["error"])},
        "governor": {"ceilings": raw["ceilings"], "limits_at_end": raw["limits"], "cpu_quota": raw["cpu_quota"],
                     "mem_limit_bytes": raw["mem_limit_bytes"]},
        "metrics": {
            "wall_s": round(raw["wall_s"], 2),
            "files_per_s": round(raw["ok"] / raw["wall_s"], 4) if raw["wall_s"] else 0.0,
            "loop_lag": report.lag_summary(raw["lags"]),
            "top_stalls": report.top_stalls(raw["stall_stacks"]),
            "stall_count": len(raw["stall_stacks"]),
            "latency_s": report.latency_by_type((o["kind"], o["latency_s"]) for o in raw["outcomes"]),
            "gate_wait_mean_s": round(sum(o["gate_wait_s"] for o in raw["outcomes"]) / max(1, len(raw["outcomes"])), 3),
            "cpu_seconds": round(raw["cpu_seconds"], 1),
            "cpu_seconds_sampled_max": round(sampler.cpu_sampled, 1),
            "memory": {
                "parent_idle_rss_mib": round(raw["idle_rss"] / report.MIB, 1),
                "peak_parent_rss_mib": round(sampler.peak_parent_rss / report.MIB, 1),
                "peak_tree_rss_mib": round(sampler.peak_tree_rss / report.MIB, 1),
                "peak_tree_pss_mib": round(sampler.peak_tree_pss / report.MIB, 1),
            },
            "threads": {"peak_total": sampler.peak_threads, "onnx_threads_per_worker": onnx_threads,
                        "onnx_threads_total_peak": onnx_threads * sampler.peak_parse_workers},
            "workers": {
                "peak_parse_workers": sampler.peak_parse_workers,
                "parse_worker_peak_rss_mib": [round(p / report.MIB, 1) for p in peaks],
                "parse_worker_peak_rss_median_mib": round(report.median(peaks) / report.MIB, 1),
                "parse_worker_first_rss_mib": [round(w["first_rss"] / report.MIB, 1) for w in workers],
                "parse_worker_peak_threads": [w["peak_threads"] for w in workers],
            },
            "backpressure_429": raw["stats"].get("backpressure_429", 0),
        },
        "cgroup": raw["cgroup"],
        "golden": raw["golden"],
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--mix", choices=sorted(MIXES), default="cpu-heavy")
    ap.add_argument("--label", default="run")
    ap.add_argument("--passes", type=int, default=1, help="how many times the mix is submitted")
    ap.add_argument("--seed", type=int, default=1, help="submission order")
    ap.add_argument("--cpus", type=int, default=None, help="emulate N CPUs by pinning affinity")
    ap.add_argument("--mem-limit-gb", type=float, default=None, help="emulated memory limit (the governor sees it; a watchdog enforces 85 %%)")
    ap.add_argument("--concurrency", type=int, default=0, help="files in flight; default twice the governor's ceilings")
    ap.add_argument("--pdf-parser", choices=("pdfplumber", "docling"), default="pdfplumber")
    ap.add_argument("--ocr-latency", type=float, default=0.2, help="seconds the stubbed VLM takes per page")
    ap.add_argument("--gate-timeout", type=float, default=120.0)
    ap.add_argument("--cgroup", default="auto", help="cgroup v2 dir to read, 'auto' (only inside a container) or ''")
    ap.add_argument("--require-cgroup", action="store_true",
                    help="exit 6 unless the cgroup counters (memory.peak, cpu.stat, memory.events) can be read")
    ap.add_argument("--max-host-pct", type=float, default=HOST_LIMIT_PCT, help="refuse to start above this host CPU/memory %%")
    ap.add_argument("--verbose", action="store_true", help="show the services' own INFO logs")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--golden-write", type=Path, default=None)
    ap.add_argument("--golden-check", type=Path, default=None)
    return ap


def pin_cpus(count: int) -> None:
    allowed = sorted(os.sched_getaffinity(0))
    if count > len(allowed):
        raise SystemExit(f"--cpus {count} but only {len(allowed)} CPUs are allowed here")
    os.sched_setaffinity(0, set(allowed[:count]))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.CRITICAL)
    if args.cpus:
        pin_cpus(args.cpus)
    # The default of the code under test is "on"; make the setting explicit either way.
    os.environ["ENABLE_PDFPLUMBER_PROCESSOR"] = "true" if args.pdf_parser == "pdfplumber" else "false"
    os.environ.setdefault("SECRET_KEY", "bench-only-not-a-secret-0123456789abcdef")
    os.environ.setdefault("TELEMETRY_COLLECTION_FLAG", "false")
    if args.cgroup == "auto":
        own = report.in_own_cgroup(Path("/proc/self/cgroup").read_text())
        args.cgroup = "/sys/fs/cgroup" if own else ""
    if args.require_cgroup:
        missing = report.missing_cgroup_files(Path(args.cgroup)) if args.cgroup else list(report.REQUIRED_CGROUP_FILES)
        if missing:
            print(f"cgroup counters unreadable under {args.cgroup or '(no cgroup path)'}: {', '.join(missing)}; "
                  "refusing to produce a tier result without them (is this a cgroup v2 container with a private "
                  "cgroup namespace?)", file=sys.stderr)
            return 6
    load = host_load()
    if load["cpu_pct"] >= args.max_host_pct or load["mem_pct"] >= args.max_host_pct:
        print(f"host busy (cpu {load['cpu_pct']:.0f} %, mem {load['mem_pct']:.0f} %); not starting", file=sys.stderr)
        return 3
    manifest = load_manifest(args.corpus)
    files = select(manifest.entries, args.mix, args.passes, args.seed)
    if not files:
        print(f"mix {args.mix} selects no file from this corpus", file=sys.stderr)
        return 2
    mem_limit = int(args.mem_limit_gb * 1024**3) if args.mem_limit_gb else None
    sampler = TreeSampler(mem_limit=mem_limit, host_limit_pct=args.max_host_pct)
    sampler.start()
    raw = asyncio.run(run(args, files, args.corpus, sampler))
    sampler.stop()
    result = assemble(args, raw, sampler, load, manifest.corpus_id)
    # Recorded in the result, so a rerun over this file cannot pass a golden diff by skipping it.
    diffs = (
        golden.compare(json.loads(args.golden_check.read_text()), result["golden"], subset=args.mix != "all")
        if args.golden_check else None
    )
    result["golden_diffs"] = diffs
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text)
    print(report.markdown_report([result]))
    print(f"arm: {result['config']['arm']}")
    print(f"status: {result['status']}; failed {result['files']['failed']}; corpus {manifest.corpus_id}")
    code = 0 if result["status"] == "ok" else 4
    if args.golden_write:
        args.golden_write.write_text(json.dumps(result["golden"], indent=1, sort_keys=True))
        print(f"golden written: {args.golden_write} ({len(result['golden'])} files)")
    if args.golden_check:
        print("golden diff: " + ("empty" if not diffs else f"{len(diffs)} file(s) differ"))
        for line in diffs:
            print("  " + line)
        code = code or (5 if diffs else 0)
    return code


if __name__ == "__main__":
    sys.exit(main())
