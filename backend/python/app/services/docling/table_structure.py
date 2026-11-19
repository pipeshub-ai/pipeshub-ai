"""Table-structure model behind ``/table-structure``: a supervisor for one killable child process, and the
per-request runner that takes a gate permit for each table.

The child holds the model (see ``table_structure_child``). The supervisor starts it lazily, gives every table a
time bound, kills it when a table runs past that bound (that table fails, tables already answered keep their
grids) and starts a new one on the next table. One child per service process; tables run one at a time.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import struct
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.services.docling import table_structure_child as wire
from app.services.docling.table_structure_schema import (
    MODEL_REPO,
    MODEL_REVISION,
    ModelInfo,
    TableRequestMeta,
    TableResult,
    config_hash,
)

logger = logging.getLogger(__name__)

CHILD_MODULE = "app.services.docling.table_structure_child"
ARTIFACTS_PATH_ENV = "DOCLING_ARTIFACTS_PATH"  # one name for the artifacts folder; align with the image setting
DEFAULT_THREADS = 4
IDLE_EXIT_SECONDS = 600.0
KILL_GRACE_SECONDS = 5.0
START_TIMEOUT_SECONDS = 180.0
MEMORY_BAR_BYTES = 2 * 1024**3
TABLE_TIMEOUT_PER_STEP_S = 0.06  # about 8.6 times the measured seconds a step takes, so a host four times slower still fits
TABLE_TIMEOUT_FLOOR_S = 10.0
START_ESTIMATE_PER_STEP_S = 0.03  # a table is not started when less than this per step is left before the caller's deadline
_ENV_PREFIXES = (
    "PATH", "HOME", "LANG", "LC_", "PYTHON", "VIRTUAL_ENV", "HF_", "DOCLING_", "OMP_", "MKL_", "TMPDIR", "TORCH",
    "XDG_", "LD_", "DYLD_", "SSL_CERT", "REQUESTS_CA", "TRANSFORMERS_", "PIPESHUB_TABLE_",
)


def table_timeout_s(cap: int, ceiling: float) -> float:
    """The kill timeout of one table: ``min(ceiling, max(10, 0.06 * cap))`` seconds."""
    return min(float(ceiling), max(TABLE_TIMEOUT_FLOOR_S, TABLE_TIMEOUT_PER_STEP_S * cap))


class ChildUnavailable(Exception):
    """The child could not be started; ``reason`` is a short machine word (``memory``, ``weights``, ``start``)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Busy(Exception):
    """No permit within the wait the caller allows."""


def resolve_weights_dir(mode: str = "accurate") -> str:
    """The directory that holds the *mode* folder: the artifacts folder (``ARTIFACTS_PATH_ENV``,
    ``<path>/model_artifacts/tableformer``) when it is set and holds the files, else the pinned revision (a commit) from
    the model hub cache or, online, a download of that revision. Raises when neither has the files (offline included)."""
    root = os.environ.get(ARTIFACTS_PATH_ENV, "").strip()
    if root:
        candidate = os.path.join(root, "model_artifacts", "tableformer")
        if os.path.isfile(os.path.join(candidate, mode, "tm_config.json")):
            return candidate
    from huggingface_hub import constants as hub_constants
    from huggingface_hub import snapshot_download

    # A cached copy of the pinned revision is used as it lies: with a commit as revision the library asks the hub for the
    # file list even when the files are cached, which fails offline.
    cached = os.path.join(
        hub_constants.HF_HUB_CACHE, "models--" + MODEL_REPO.replace("/", "--"), "snapshots", MODEL_REVISION, "model_artifacts", "tableformer"
    )
    folder = os.path.join(cached, mode)
    if os.path.isfile(os.path.join(folder, "tm_config.json")) and any(n.endswith(".safetensors") for n in os.listdir(folder)):
        return cached
    path = snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, allow_patterns=[f"model_artifacts/tableformer/{mode}/*"])
    return os.path.join(path, "model_artifacts", "tableformer")


def default_memory_available() -> int | None:
    """Bytes left under the container's limit, or ``None`` when it cannot be read."""
    try:
        from app.services.resource_governor.probe import SystemResourceProbe

        snap = SystemResourceProbe().snapshot()
    except Exception:
        return None
    used = snap.mem_working_set_raw_bytes if snap.mem_working_set_raw_bytes is not None else snap.mem_working_set_bytes
    if snap.mem_limit_bytes is None or used is None:
        return None
    return max(0, snap.mem_limit_bytes - used)


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIXES)}


async def _aread_frame(reader: asyncio.StreamReader) -> tuple[dict[str, Any], bytes] | None:
    try:
        head = await reader.readexactly(wire._FRAME.size)
        hlen, blen = wire._FRAME.unpack(head)
        if hlen > wire.MAX_HEADER_BYTES or blen > wire.MAX_BLOB_BYTES:
            return None
        raw = await reader.readexactly(hlen)
        blob = await reader.readexactly(blen) if blen else b""
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    import json

    return json.loads(raw), blob


class TableSupervisor:
    def __init__(
        self,
        *,
        mode: str = "accurate",
        threads: int = DEFAULT_THREADS,
        idle_seconds: float = IDLE_EXIT_SECONDS,
        model_timeout_ceiling: float = 120.0,
        weights_dir: Callable[[str], str] = resolve_weights_dir,
        memory_available: Callable[[], int | None] = default_memory_available,
        memory_bar: int = MEMORY_BAR_BYTES,
        factory: str | None = None,
        kill_grace_s: float = KILL_GRACE_SECONDS,
        start_timeout_s: float = START_TIMEOUT_SECONDS,
    ) -> None:
        self.mode = mode
        self.threads = threads
        self.idle_seconds = idle_seconds
        self.model_timeout_ceiling = model_timeout_ceiling
        self._weights_dir = weights_dir
        self._memory_available = memory_available
        self._memory_bar = memory_bar
        self._factory = factory
        self._kill_grace_s = kill_grace_s
        self._start_timeout_s = start_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._info: ModelInfo | None = None
        self._last_done = 0.0
        self._ever_started = False
        self.stats = {"starts": 0, "respawns": 0, "kills": 0, "cap_hits": 0, "timeouts": 0}

    @property
    def model_info(self) -> ModelInfo | None:
        return self._info

    @property
    def child_pid(self) -> int | None:
        return self._proc.pid if self._proc is not None and self._proc.returncode is None else None

    async def _start(self) -> None:
        avail = self._memory_available()
        if avail is None or avail < self._memory_bar:
            raise ChildUnavailable("memory")
        try:
            weights = await asyncio.to_thread(self._weights_dir, self.mode)
        except Exception as exc:
            logger.warning("table model weights unavailable: %s", type(exc).__name__)
            raise ChildUnavailable("weights") from exc
        argv = [
            sys.executable, "-m", CHILD_MODULE, "--weights-dir", weights, "--mode", self.mode,
            "--threads", str(self.threads), "--idle-seconds", str(self.idle_seconds),
        ]
        if self._factory:
            argv += ["--factory", self._factory]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=_child_env(),
        )
        self._proc = proc
        try:
            assert proc.stdout is not None
            frame = await asyncio.wait_for(_aread_frame(proc.stdout), self._start_timeout_s)
        except TimeoutError:
            frame = None
        if frame is None or frame[0].get("op") != "ready":
            await self._kill()
            raise ChildUnavailable("start")
        info = frame[0]["info"]
        self._info = ModelInfo(
            docling_ibm_models=info["docling_ibm_models"], mode=info["mode"], weights_sha256=info["weights_sha256"],
            model_revision=MODEL_REVISION,
            config_sha256=config_hash(info["docling_ibm_models"], info["mode"], info["weights_sha256"], MODEL_REVISION),
        )
        self.stats["starts"] += 1
        if self._ever_started:
            self.stats["respawns"] += 1
        self._ever_started = True
        self._last_done = time.monotonic()

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        self.stats["kills"] += 1
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), self._kill_grace_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def _exchange(self, spec: dict[str, Any], png: bytes, timeout_s: float) -> dict[str, Any] | None:
        """One request and its answer; ``None`` when the child is gone."""
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None
        import json

        raw = json.dumps({"op": "predict", "spec": spec}, separators=(",", ":"), sort_keys=True).encode()
        try:
            proc.stdin.write(struct.pack(">II", len(raw), len(png)) + raw + png)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return None
        frame = await asyncio.wait_for(_aread_frame(proc.stdout), timeout_s)
        return None if frame is None else frame[0]

    async def run_table(self, spec: dict[str, Any], png: bytes) -> dict[str, Any]:
        """The result dict of one table (``status`` ok, cap_hit or error). Never raises for a model failure."""
        timeout_s = table_timeout_s(int(spec["cap"]), self.model_timeout_ceiling)
        async with self._lock:
            for attempt in (0, 1):
                if self._proc is not None and self._proc.returncode is not None:
                    self._proc = None
                if self._proc is None:
                    try:
                        await self._start()
                    except ChildUnavailable as exc:
                        return {"status": "error", "err": exc.reason}
                idle_for = time.monotonic() - self._last_done
                try:
                    result = await self._exchange(spec, png, timeout_s)
                except TimeoutError:
                    self.stats["timeouts"] += 1
                    await self._kill()
                    return {"status": "error", "err": "timeout"}
                if result is not None:
                    self._last_done = time.monotonic()
                    if result.get("status") == "cap_hit":
                        self.stats["cap_hits"] += 1
                    return result
                # The child ended: it exited after its idle time (ask once more), or it died.
                await self._kill()
                if attempt == 0 and idle_for >= 0.9 * self.idle_seconds:
                    continue
                return {"status": "error", "err": "child_exit"}
        return {"status": "error", "err": "child_exit"}

    async def shutdown(self) -> None:
        async with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or proc.returncode is not None:
                return
            with contextlib.suppress(Exception):
                assert proc.stdin is not None
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), self._kill_grace_s)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()


_holder: dict[str, TableSupervisor | None] = {"s": None}


def get_supervisor() -> TableSupervisor:
    if _holder["s"] is None:
        from app.utils.env_config import env_choice

        timeout = 120.0
        raw = os.environ.get("PDF_TABLE_MODEL_TIMEOUT_S", "").strip()
        if raw:
            with contextlib.suppress(ValueError):
                timeout = max(1.0, float(raw))
        mode = env_choice("PDF_DOCLING_TABLE_MODE", "accurate", ("accurate", "fast"))
        _holder["s"] = TableSupervisor(mode=mode, model_timeout_ceiling=timeout)
    return _holder["s"]


async def shutdown_table_supervisor() -> None:
    sup, _holder["s"] = _holder["s"], None
    if sup is not None:
        await sup.shutdown()


def set_supervisor(sup: TableSupervisor | None) -> None:
    """Replace the process's supervisor (tests)."""
    _holder["s"] = sup


Acquire = Callable[[float], contextlib.AbstractAsyncContextManager[None]]


async def run_tables(
    meta: TableRequestMeta,
    images: dict[str, bytes],
    supervisor: TableSupervisor,
    acquire: Acquire,
    *,
    deadline: float,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[list[TableResult], bool]:
    """Run the tables in order. A permit is taken for each table and given back before the next one starts.

    Returns the results and whether the first table was refused for want of a permit (the route answers 429 then).
    After the first table, a table that cannot get a permit, or cannot finish before *deadline* (a monotonic
    time), is answered ``error`` / ``service_busy`` and so are the rest.
    """
    results: list[TableResult] = []
    stop: str | None = None
    for i, spec in enumerate(meta.tables):
        if stop is None and disconnected is not None and await disconnected():
            stop = "client_gone"
        remaining = deadline - time.monotonic()
        if stop is None and remaining < spec.cap * START_ESTIMATE_PER_STEP_S:
            stop = "service_busy"
        if stop is None:
            try:
                async with acquire(remaining):
                    try:
                        raw = await supervisor.run_table(spec.model_dump(exclude={"image"}), images[spec.image])
                    except Exception as exc:  # one table fails; the request, and the other tables, go on
                        logger.warning("table structure failed for one table: %s", type(exc).__name__)
                        raw = {"status": "error", "err": "service_exception"}
            except Busy:
                stop = "service_busy"
            else:
                try:
                    results.append(TableResult(id=spec.id, **raw))
                except Exception:
                    results.append(TableResult(id=spec.id, status="error", err="bad_result"))
                continue
        if i == 0 and stop == "service_busy":
            return [], True
        results.append(TableResult(id=spec.id, status="error", err=stop))
    return results, False


@contextlib.asynccontextmanager
async def no_gate(_wait_s: float) -> AsyncIterator[None]:
    yield
