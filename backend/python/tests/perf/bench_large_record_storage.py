"""Store and read records through real HTTP, before and after a storage change.

Drives the real ``BlobStorage`` (from ``--checkout``) against the stand-in
storage gateway in ``tests/support/fake_storage_gateway.py`` (always from this
checkout, so an older checkout can be measured too). One scenario per process,
so peak memory is that scenario's alone. Prints one JSON line.

    python tests/perf/bench_large_record_storage.py --checkout <repo>/backend/python \\
        --scenario read --storage s3 --record deck.json --concurrency 4

Scenarios:
  write   save_record_to_storage once (the indexing worker's storage step).
  read    store once, then N concurrent get_record_from_storage (query/reindex reads).
  mixed   one large record plus --small-count small ones read at once (a chat turn).

Reports wall time, the longest event-loop stall (a 5 ms ticker's worst lateness),
peak RSS above the start of the measured phase, stored bytes, and the outcome.
Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


def _status(key: str) -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1]) * 1024
    return 0


def _reset_peak() -> int:
    with open("/proc/self/clear_refs", "w") as f:
        f.write("5")
    return _status("VmRSS")


class _LoopProbe:
    def __init__(self) -> None:
        self.worst = 0.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            t = time.perf_counter()
            await asyncio.sleep(0.005)
            self.worst = max(self.worst, time.perf_counter() - t - 0.005)

    async def __aenter__(self) -> _LoopProbe:
        self._task = asyncio.get_running_loop().create_task(self._run())
        # Let the ticker start before the measured work: an AsyncMock await never
        # yields, so synchronous work right after would run before the first tick.
        await asyncio.sleep(0.01)
        return self

    async def stop(self) -> float:
        self._stop.set()
        assert self._task is not None
        await self._task
        return self.worst

    async def __aexit__(self, *_exc: object) -> None:
        self._stop.set()


def _load_gateway() -> type:
    path = Path(__file__).resolve().parents[1] / "support" / "fake_storage_gateway.py"
    spec = importlib.util.spec_from_file_location("fake_storage_gateway", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeStorageGateway


async def _main(args: argparse.Namespace) -> dict:
    from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

    gateway_cls = _load_gateway()
    logger = logging.getLogger("bench")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    record = json.loads(Path(args.record).read_bytes())
    small = json.loads(Path(args.small_record).read_bytes()) if args.small_record else None
    out: dict = {"scenario": args.scenario, "storage": args.storage, "record": Path(args.record).name,
                 "concurrency": args.concurrency, "upload_limit_mb": args.upload_limit_mb}

    with gateway_cls(args.storage, upload_limit_bytes=args.upload_limit_mb * 1024 * 1024) as gateway:
        storage = BlobStorage(logger=logger, config_service=MagicMock(), graph_provider=None)
        storage._get_auth_and_config = AsyncMock(return_value=({}, gateway.base_url, args.storage))

        async def store(rec: dict, vrid: str) -> dict:
            doc_id, size = await storage.save_record_to_storage("org", f"rec-{vrid}", vrid, rec)
            return {"record_doc_id": doc_id, "fileSizeBytes": size}

        try:
            if args.scenario == "write":
                base = _reset_peak()
                probe = _LoopProbe()
                async with probe:
                    t = time.perf_counter()
                    try:
                        lookup = await store(record, "vr-big")
                        out.update(outcome="stored", stored_bytes=lookup["fileSizeBytes"])
                    except Exception as e:
                        out.update(outcome=f"failed: {type(e).__name__}: {str(e)[:120]}")
                    out["seconds"] = time.perf_counter() - t
                    out["loop_stall_ms"] = await probe.stop() * 1000
                out["peak_extra_mib"] = (_status("VmHWM") - base) / 2**20
                out["rejected_uploads"] = gateway.rejected_uploads
                return out

            lookup = await store(record, "vr-big")
            out["stored_bytes"] = lookup["fileSizeBytes"]
            small_lookups = []
            if args.scenario == "mixed":
                assert small is not None
                small_lookups = [await store(small, f"vr-s{i}") for i in range(args.small_count)]
            del record

            async def timed(vrid: str, lk: dict) -> tuple[str, float, str]:
                t0 = time.perf_counter()
                try:
                    got = await storage.get_record_from_storage(vrid, "org", lk)
                    status = "ok" if got else "empty"
                except Exception as e:
                    status = type(e).__name__
                return vrid, time.perf_counter() - t0, status

            jobs = [("vr-big", lookup)] * args.concurrency
            jobs += [(f"vr-s{i}", lk) for i, lk in enumerate(small_lookups)]
            base = _reset_peak()
            probe = _LoopProbe()
            async with probe:
                t = time.perf_counter()
                results = await asyncio.gather(*(timed(v, lk) for v, lk in jobs))
                out["seconds"] = time.perf_counter() - t
                out["loop_stall_ms"] = await probe.stop() * 1000
            out["peak_extra_mib"] = (_status("VmHWM") - base) / 2**20
            big = [r for r in results if r[0] == "vr-big"]
            if big:
                out["big_ok"] = sum(r[2] == "ok" for r in big)
                out["big_errors"] = sorted({r[2] for r in big if r[2] != "ok"})
                out["big_latency_ms_max"] = max(r[1] for r in big) * 1000
            smalls = [r[1] * 1000 for r in results if r[0] != "vr-big"]
            if smalls:
                out["small_ok"] = sum(r[2] == "ok" for r in results if r[0] != "vr-big")
                out["small_latency_ms_p50"] = statistics.median(smalls)
                out["small_latency_ms_p95"] = sorted(smalls)[int(0.95 * (len(smalls) - 1))]
                out["small_latency_ms_max"] = max(smalls)
            out["downloads"] = gateway.downloads
            return out
        finally:
            await close_shared_session()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkout", required=True, help="backend/python directory whose app/ to measure")
    parser.add_argument("--scenario", choices=["write", "read", "mixed"], required=True)
    parser.add_argument("--storage", choices=["local", "s3"], default="s3")
    parser.add_argument("--record", required=True, help="record JSON file (the large record)")
    parser.add_argument("--small-record", help="record JSON for the small records of 'mixed'")
    parser.add_argument("--small-count", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=1, help="concurrent reads of the large record")
    parser.add_argument("--upload-limit-mb", type=int, default=1024, help="local storage upload limit")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.checkout).resolve()))
    result = asyncio.run(_main(args))
    result["label"] = args.label
    print(json.dumps(result))


if __name__ == "__main__":
    main()
