"""Standalone embedding server for local HuggingFace / SentenceTransformer models.

Exposes OpenAI-compatible ``POST /v1/embeddings`` and ``GET /v1/models`` so
indexing and query services can share a single in-process model load.
"""

from __future__ import annotations

import app.utils.runtime_threads  # noqa: E402 - must precede all ML library imports

import asyncio
import base64
import os
import struct
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_PRIORITY_QUERY,
    EMBEDDING_SERVER_PORT,
)
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.telemetry.setup import setup_telemetry
from app.utils.logger import create_logger
from app.utils.process_hardening import mark_process_non_dumpable
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = create_logger("embedding_service")

DEFAULT_DEVICE = os.getenv("EMBEDDING_SERVER_DEVICE", "cpu")
DEFAULT_NORMALIZE = os.getenv("EMBEDDING_SERVER_NORMALIZE", "true").lower() in (
    "1",
    "true",
    "yes",
)
_ENV_MAX_CONCURRENT_EMBEDDINGS = (os.getenv("EMBEDDING_SERVER_MAX_CONCURRENCY") or "").strip()
MAX_CONCURRENT_EMBEDDINGS = (
    int(_ENV_MAX_CONCURRENT_EMBEDDINGS)
    if _ENV_MAX_CONCURRENT_EMBEDDINGS
    else max(2, os.cpu_count() or 2)
)
if MAX_CONCURRENT_EMBEDDINGS < 1:
    raise ValueError(
        f"EMBEDDING_SERVER_MAX_CONCURRENCY must be at least 1, got {MAX_CONCURRENT_EMBEDDINGS}"
    )
# Encode slots bulk (indexing) requests may never take, kept for search
# queries so a query does not wait for an indexing batch to finish. Capped
# below the total so bulk always keeps at least one slot; 0 keeps only the
# ordering (queries ahead of queued bulk requests).
_ENV_QUERY_RESERVED_SLOTS = (os.getenv("EMBEDDING_SERVER_QUERY_RESERVED_SLOTS") or "").strip()
QUERY_RESERVED_SLOTS = int(_ENV_QUERY_RESERVED_SLOTS) if _ENV_QUERY_RESERVED_SLOTS else 1
if QUERY_RESERVED_SLOTS < 0:
    raise ValueError(
        f"EMBEDDING_SERVER_QUERY_RESERVED_SLOTS must be at least 0, got {QUERY_RESERVED_SLOTS}"
    )

# Bound any Hub HTTP call so a slow/blocked network can never hang model load
# indefinitely. huggingface_hub reads this on import, so set it before the
# library is imported inside ``_load``.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

# ---------------------------------------------------------------------------
# Server-side security policy (evaluated once at startup)
# ---------------------------------------------------------------------------

# EMBEDDING_SERVER_ALLOW_REMOTE_CODE=true must be explicitly opt-in.
# When false (the default), any request that sets trust_remote_code=true is
# rejected with 403 — a compromised internal caller cannot trigger RCE via a
# malicious model repo.
ALLOW_REMOTE_CODE: bool = os.getenv(
    "EMBEDDING_SERVER_ALLOW_REMOTE_CODE", "false"
).lower() in ("1", "true", "yes")

# EMBEDDING_SERVER_ALLOWED_MODELS is an optional comma-separated allowlist.
# When set, only listed model names are accepted; requests for any other model
# are rejected with 403, preventing disk-exhaustion DoS via arbitrary Hub
# downloads. When unset, any model name is allowed (original behaviour).
_allowed_models_raw = os.getenv("EMBEDDING_SERVER_ALLOWED_MODELS", "").strip()
ALLOWED_MODELS: frozenset[str] | None = (
    frozenset(m.strip() for m in _allowed_models_raw.split(",") if m.strip())
    if _allowed_models_raw
    else None
)

# Formats sentence-transformers never needs at inference time on CPU/GPU
# (alternate export formats for other runtimes). Skipping them keeps the
# tracked download size close to what actually gets used and avoids paying
# for multi-format repos when only the PyTorch weights are needed.
MODEL_DOWNLOAD_IGNORE_PATTERNS: list[str] = [
    "*.onnx",
    "*.msgpack",
    "*.h5",
    "*.tflite",
    "*.ot",
    "onnx/**",
    "openvino/**",
    "coreml/**",
]

# Progress is measured by polling on-disk blob size rather than hooking
# huggingface_hub's internal tqdm bars (whose class is bound by reference
# inside huggingface_hub.file_download at import time, so patching the
# public `huggingface_hub.utils.tqdm.tqdm` class does not affect per-file
# download bars). Polling the cache directory is version-stable and works
# for both regular HTTP and hf_transfer/xet download backends since all of
# them stream into the same on-disk blob (as a growing `.incomplete` file).
DOWNLOAD_PROGRESS_POLL_INTERVAL_SECONDS = 1.0


@dataclass
class DownloadStatus:
    """Snapshot of a single model's download/load lifecycle.

    status transitions: checking -> downloading -> loading -> ready
                                                            \\-> failed
    """

    status: str = "checking"
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "progress": round(self.progress, 1),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


def _model_cache_blobs_dir(model_name: str) -> str:
    from huggingface_hub.constants import HF_HUB_CACHE

    repo_folder = f"models--{model_name.replace('/', '--')}"
    return os.path.join(HF_HUB_CACHE, repo_folder, "blobs")


def _measure_blobs_dir_bytes(blobs_dir: str) -> int:
    """Sum bytes on disk for a repo's blob store, including in-progress
    ``.incomplete`` files, so progress reflects partial downloads."""
    if not os.path.isdir(blobs_dir):
        return 0
    total = 0
    try:
        with os.scandir(blobs_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _compute_expected_total_bytes(model_name: str) -> int:
    """Best-effort lookup of the repo's total download size via the Hub
    metadata API. Returns 0 if the lookup fails (e.g. offline, private repo
    without credentials); progress then falls back to byte-count-only."""
    try:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import filter_repo_objects

        api = HfApi()
        info = api.model_info(model_name, files_metadata=True)
        siblings = info.siblings or []
        filtered = filter_repo_objects(
            siblings,
            ignore_patterns=MODEL_DOWNLOAD_IGNORE_PATTERNS,
            key=lambda f: f.rfilename,
        )
        return sum(f.size or 0 for f in filtered)
    except Exception:
        logger.info(
            "Could not fetch expected size for '%s'; progress will show bytes downloaded only",
            model_name,
        )
        return 0


def _download_with_progress(
    model_name: str,
    status: "DownloadStatus",
    status_lock: threading.Lock,
) -> None:
    """Download a repo snapshot while polling on-disk size for progress.

    Runs on a worker thread (called from within ``ModelManager._load``, which
    is itself already off the event loop via ``asyncio.to_thread``).
    """
    from huggingface_hub import snapshot_download

    total_bytes = _compute_expected_total_bytes(model_name)
    with status_lock:
        status.total_bytes = total_bytes
        status.downloaded_bytes = 0

    blobs_dir = _model_cache_blobs_dir(model_name)
    stop_event = threading.Event()

    def _poll() -> None:
        while not stop_event.wait(DOWNLOAD_PROGRESS_POLL_INTERVAL_SECONDS):
            downloaded = _measure_blobs_dir_bytes(blobs_dir)
            with status_lock:
                status.downloaded_bytes = downloaded
                if status.total_bytes > 0:
                    status.progress = min(
                        99.0, downloaded / status.total_bytes * 100
                    )
                status.updated_at = time.time()

    poll_thread = threading.Thread(
        target=_poll, name=f"embedding-download-poll-{model_name}", daemon=True
    )
    poll_thread.start()
    try:
        snapshot_download(
            repo_id=model_name,
            ignore_patterns=MODEL_DOWNLOAD_IGNORE_PATTERNS,
        )
    finally:
        stop_event.set()
        poll_thread.join(timeout=5)

    # Final measurement so the reported total matches what's on disk even
    # if the Hub metadata lookup under-/over-estimated it.
    downloaded = _measure_blobs_dir_bytes(blobs_dir)
    with status_lock:
        status.downloaded_bytes = downloaded
        status.total_bytes = max(status.total_bytes, downloaded)
        status.progress = 100.0
        status.updated_at = time.time()


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str = "float"
    user: str | None = None
    trust_remote_code: bool = False


class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: list[float] | str
    index: int


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage


class ModelListItem(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "pipeshub"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelListItem]


class PrepareModelRequest(BaseModel):
    model: str
    trust_remote_code: bool = False


class EncodePriority(StrEnum):
    """Who is waiting on an encode. A search query has a person waiting on
    it; a bulk request is an indexing batch, which can wait."""

    QUERY = "query"
    BULK = "bulk"

    @classmethod
    def from_header(cls, value: str | None) -> "EncodePriority":
        if value is not None and value.strip().lower() == EMBEDDING_PRIORITY_QUERY:
            return cls.QUERY
        return cls.BULK


class EncodeGate:
    """Encode slots shared by search queries and bulk indexing batches.

    A plain semaphore serves requests in arrival order, so while an indexing
    run keeps every slot busy a search query waits behind every batch queued
    before it, each one seconds of CPU on a local model. Here a query is
    admitted ahead of any waiting bulk request, and bulk requests may hold at
    most ``capacity - reserved_for_queries`` slots, so a query normally starts
    at once instead of waiting for a batch to finish. Within each class,
    first come first served.

    Bulk is never shut out. While a bulk request is waiting and no bulk
    request holds a slot, queries may not take the last free slot, and the
    next slot a query frees goes to bulk; so a steady stream of queries
    slows indexing to one encode at a time but cannot stop it. A slot a
    batch frees goes to a waiting query first, so a steady stream of
    batches cannot keep queries waiting either. With a single slot the two
    take turns. Event-loop only; no locking.
    """

    def __init__(self, capacity: int, reserved_for_queries: int = 1) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {capacity}")
        self._capacity = capacity
        self._bulk_limit = max(1, capacity - max(0, reserved_for_queries))
        self._in_use = 0
        self._bulk_in_use = 0
        self._waiting: dict[EncodePriority, deque[asyncio.Future[None]]] = {
            EncodePriority.QUERY: deque(),
            EncodePriority.BULK: deque(),
        }

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def bulk_limit(self) -> int:
        return self._bulk_limit

    def waiting(self, priority: EncodePriority) -> int:
        return sum(1 for f in self._waiting[priority] if not f.done())

    def _bulk_shut_out(self) -> bool:
        """A bulk request is waiting and no bulk request holds a slot."""
        return self._bulk_in_use == 0 and self.waiting(EncodePriority.BULK) > 0

    def _has_room(self, priority: EncodePriority) -> bool:
        if self._in_use >= self._capacity:
            return False
        if priority is EncodePriority.QUERY:
            # Leave the last free slot to bulk work that has none.
            return not (self._bulk_shut_out() and self._in_use >= self._capacity - 1)
        if self._bulk_in_use == 0:
            return True
        return self._in_use < self._bulk_limit and not self.waiting(EncodePriority.QUERY)

    def _take(self, priority: EncodePriority) -> None:
        self._in_use += 1
        if priority is EncodePriority.BULK:
            self._bulk_in_use += 1

    def _grant_next(self, priority: EncodePriority, *, any_free_slot: bool = False) -> bool:
        queue = self._waiting[priority]
        while queue and queue[0].done():
            queue.popleft()
        if not queue:
            return False
        room = self._in_use < self._capacity if any_free_slot else self._has_room(priority)
        if not room:
            return False
        # The slot is taken on the waiter's behalf, so nothing that runs
        # before it resumes can take it first.
        self._take(priority)
        queue.popleft().set_result(None)
        return True

    def _wake(self, *, bulk_released: bool = False) -> None:
        if bulk_released:
            # A finished batch hands its slot to a waiting query first, even
            # the last free one: otherwise, with batches always queued, the
            # slot would pass from batch to batch and a query would wait for
            # the whole queue. Bulk gets the next slot a query frees.
            self._grant_next(EncodePriority.QUERY, any_free_slot=True)
        while True:
            # Bulk first only when it holds nothing; otherwise queries first.
            order = (
                (EncodePriority.BULK, EncodePriority.QUERY)
                if self._bulk_shut_out()
                else (EncodePriority.QUERY, EncodePriority.BULK)
            )
            if not any(self._grant_next(priority) for priority in order):
                return

    async def acquire(self, priority: EncodePriority) -> None:
        if not self.waiting(priority) and self._has_room(priority):
            self._take(priority)
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting[priority].append(waiter)
        # Settles the queue at once if there is room after all (its head
        # was only waiters that have since given up).
        self._wake()
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # Granted, then cancelled before it could run: hand it back.
                self.release(priority)
            raise

    def release(self, priority: EncodePriority) -> None:
        self._in_use = max(0, self._in_use - 1)
        if priority is EncodePriority.BULK:
            self._bulk_in_use = max(0, self._bulk_in_use - 1)
        self._wake(bulk_released=priority is EncodePriority.BULK)

    @asynccontextmanager
    async def slot(self, priority: EncodePriority) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release(priority)


class ModelManager:
    """Thread-safe cache of loaded SentenceTransformer models."""

    def __init__(
        self,
        *,
        device: str = DEFAULT_DEVICE,
        normalize_embeddings: bool = DEFAULT_NORMALIZE,
        max_concurrency: int = MAX_CONCURRENT_EMBEDDINGS,
        query_reserved_slots: int = QUERY_RESERVED_SLOTS,
    ) -> None:
        self._device = device
        self._normalize = normalize_embeddings
        self._models: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._encode_gate = EncodeGate(max_concurrency, query_reserved_slots)
        self._download_status: dict[str, DownloadStatus] = {}
        self._download_status_lock = threading.Lock()
        self._prepare_tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _cache_key(model_name: str, *, trust_remote_code: bool) -> str:
        return f"{model_name}::trust_remote_code={trust_remote_code}"

    def list_loaded_models(self) -> list[str]:
        return list({key.split("::", 1)[0] for key in self._models})

    def is_ready(self, model_name: str) -> bool:
        return model_name in self.list_loaded_models()

    def _get_or_create_status(self, model_name: str) -> DownloadStatus:
        with self._download_status_lock:
            status = self._download_status.get(model_name)
            if status is None:
                status = DownloadStatus()
                self._download_status[model_name] = status
            return status

    def get_download_status(self, model_name: str) -> DownloadStatus | None:
        with self._download_status_lock:
            return self._download_status.get(model_name)

    def start_prepare(
        self,
        model_name: str,
        *,
        trust_remote_code: bool = False,
    ) -> DownloadStatus:
        """Kick off (or reuse) a background load/download for ``model_name``.

        Returns immediately with the current status; the caller polls
        ``get_download_status`` for progress instead of blocking on the
        request that triggered it.
        """
        if self.is_ready(model_name):
            status = self._get_or_create_status(model_name)
            status.status = "ready"
            status.progress = 100.0
            status.updated_at = time.time()
            return status

        existing_task = self._prepare_tasks.get(model_name)
        if existing_task is not None and not existing_task.done():
            return self._get_or_create_status(model_name)

        status = self._get_or_create_status(model_name)
        status.status = "checking"
        status.error = None
        status.updated_at = time.time()

        async def _run() -> None:
            try:
                await self.get_model(model_name, trust_remote_code=trust_remote_code)
            except Exception as exc:
                logger.exception(
                    "Background model preparation failed for '%s'", model_name
                )
                with self._download_status_lock:
                    status.status = "failed"
                    status.error = str(exc)
                    status.updated_at = time.time()
            finally:
                self._prepare_tasks.pop(model_name, None)

        self._prepare_tasks[model_name] = asyncio.create_task(_run())
        return status

    async def warmup(self, model_name: str) -> None:
        await self.get_model(model_name)

    async def get_model(
        self,
        model_name: str,
        *,
        trust_remote_code: bool = False,
    ) -> Any:
        cache_key = self._cache_key(model_name, trust_remote_code=trust_remote_code)
        if cache_key in self._models:
            return self._models[cache_key]

        status = self._get_or_create_status(model_name)

        async with self._global_lock:
            if cache_key not in self._locks:
                self._locks[cache_key] = asyncio.Lock()

        async with self._locks[cache_key]:
            if cache_key in self._models:
                return self._models[cache_key]

            logger.info(
                "Loading embedding model '%s' on device '%s' (trust_remote_code=%s)",
                model_name,
                self._device,
                trust_remote_code,
            )
            status.status = "checking"
            status.error = None
            status.updated_at = time.time()

            def _load() -> Any:
                from sentence_transformers import SentenceTransformer

                load_kwargs: dict[str, Any] = {"device": self._device}
                if trust_remote_code:
                    load_kwargs["trust_remote_code"] = True

                # Prefer cached weights: loading with local_files_only avoids the
                # Hub round-trip that can hang for a long time on a slow/blocked
                # network even when the model is already in the HF cache. Only
                # reach out to the network when the model is not cached yet.
                try:
                    model = SentenceTransformer(
                        model_name,
                        local_files_only=True,
                        **load_kwargs,
                    )
                    with self._download_status_lock:
                        status.status = "ready"
                        status.progress = 100.0
                        status.updated_at = time.time()
                    return model
                except Exception:
                    logger.info(
                        "Model '%s' not found in local cache; downloading from Hub",
                        model_name,
                    )
                    with self._download_status_lock:
                        status.status = "downloading"
                        status.started_at = time.time()
                        status.updated_at = status.started_at
                    try:
                        _download_with_progress(
                            model_name, status, self._download_status_lock
                        )
                    except Exception as download_exc:
                        offline = os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {
                            "1",
                            "true",
                            "yes",
                        }
                        if offline:
                            err = RuntimeError(
                                "HuggingFace offline mode is on (HF_HUB_OFFLINE=1), so "
                                f"'{model_name}' cannot be downloaded. Set HF_HUB_OFFLINE=0 "
                                "to allow the download, or use a model already in the image."
                            )
                            err.__cause__ = download_exc
                            download_exc = err
                        with self._download_status_lock:
                            status.status = "failed"
                            status.error = str(download_exc)
                            status.updated_at = time.time()
                        raise download_exc

                    with self._download_status_lock:
                        status.status = "loading"
                        status.updated_at = time.time()
                    model = SentenceTransformer(
                        model_name,
                        local_files_only=True,
                        **load_kwargs,
                    )
                    with self._download_status_lock:
                        status.status = "ready"
                        status.progress = 100.0
                        status.updated_at = time.time()
                    return model

            model = await asyncio.to_thread(_load)
            self._models[cache_key] = model
            logger.info("Embedding model '%s' loaded", model_name)
            return model

    async def encode(
        self,
        model_name: str,
        texts: list[str],
        *,
        trust_remote_code: bool = False,
        priority: EncodePriority = EncodePriority.BULK,
    ) -> list[list[float]]:
        model = await self.get_model(
            model_name,
            trust_remote_code=trust_remote_code,
        )

        def _encode() -> list[list[float]]:
            embeddings = model.encode(
                texts,
                normalize_embeddings=self._normalize,
                convert_to_numpy=True,
            )
            return embeddings.tolist()

        async with self._encode_gate.slot(priority):
            return await asyncio.to_thread(_encode)


model_manager = ModelManager()


def _normalize_input(raw: str | list[str]) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if not raw:
        raise HTTPException(status_code=400, detail="'input' must not be empty")
    return list(raw)


def _format_embedding_vector(
    vector: list[float],
    encoding_format: str,
) -> list[float] | str:
    """Format a single embedding per OpenAI encoding_format semantics."""
    if encoding_format == "base64":
        packed = struct.pack(f"<{len(vector)}f", *vector)
        return base64.b64encode(packed).decode("ascii")
    if encoding_format == "float":
        return vector
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported encoding_format: {encoding_format}",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    mark_process_non_dumpable()
    app.state.model_manager = model_manager
    logger.info(
        "Starting embedding server warmup for %s (max_concurrency=%d, "
        "slots reserved for search queries=%d)",
        DEFAULT_EMBEDDING_MODEL,
        MAX_CONCURRENT_EMBEDDINGS,
        min(QUERY_RESERVED_SLOTS, MAX_CONCURRENT_EMBEDDINGS - 1),
    )
    logger.info(
        "Security policy: allow_remote_code=%s, allowed_models=%s",
        ALLOW_REMOTE_CODE,
        sorted(ALLOWED_MODELS) if ALLOWED_MODELS is not None else "unrestricted",
    )
    try:
        await model_manager.warmup(DEFAULT_EMBEDDING_MODEL)
        logger.info("Default embedding model ready")
    except Exception as exc:
        logger.error(
            "Failed to warm up default embedding model (will retry on first request): %s",
            exc,
        )

    config_service: ConfigurationService | None = None
    try:
        config_service = ConfigurationService(
            logger=logger,
            key_value_store=EncryptedKeyValueStore(logger=logger),
        )
        await telemetry.bind(config_service, logger).start()
    except Exception as exc:
        logger.warning("Failed to start telemetry pusher: %s", exc)

    yield

    logger.info("Shutting down embedding server")
    if telemetry.pusher is not None:
        await telemetry.pusher.stop()
    if config_service is not None:
        try:
            await config_service.close()
        except Exception as exc:
            logger.error("Error closing configuration service: %s", exc)


from app.api.middlewares.request_context import RequestContextMiddleware
from app.utils.request_context import set_service_suffix

set_service_suffix("-es")

app = FastAPI(
    title="Embedding Server API",
    description="OpenAI-compatible API for local dense embedding models",
    version="1.0.0",
    lifespan=lifespan,
)

# Trace context — outermost.
app.add_middleware(RequestContextMiddleware)

# Telemetry: metrics middleware + pusher (started/stopped in lifespan).
telemetry = setup_telemetry(app, service_name="embedding_service")


@app.get("/health")
async def health_check() -> JSONResponse:
    try:
        loaded = model_manager.list_loaded_models()
        status = "healthy" if DEFAULT_EMBEDDING_MODEL in loaded else "starting"
        return JSONResponse(
            status_code=200,
            content={
                "status": status,
                "loaded_models": loaded,
                "default_model": DEFAULT_EMBEDDING_MODEL,
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except Exception:
        logger.exception("Health check failed")
        return JSONResponse(
            status_code=500,
            content={
                "status": "unhealthy",
                "error": "Health check failed",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


@app.get("/v1/models")
async def list_models() -> ModelListResponse:
    created = int(time.time())
    models = [
        ModelListItem(id=name, created=created)
        for name in model_manager.list_loaded_models()
    ]
    if not models:
        models.append(
            ModelListItem(id=DEFAULT_EMBEDDING_MODEL, created=created)
        )
    return ModelListResponse(data=models)


@app.post("/prepare-model")
async def prepare_model(request: PrepareModelRequest) -> JSONResponse:
    """Kick off a non-blocking model load/download and return its status.

    Callers (the Node.js config manager) poll ``GET /download-progress`` for
    progress instead of blocking a single request on a multi-GB download,
    which is what previously tripped health-check and axios timeouts.
    """
    if ALLOWED_MODELS is not None and request.model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=403,
            detail=f"Model '{request.model}' is not in the server's allowed model list.",
        )
    if request.trust_remote_code and not ALLOW_REMOTE_CODE:
        raise HTTPException(
            status_code=403,
            detail=(
                "trust_remote_code is disabled on this server. "
                "Set environment variable EMBEDDING_SERVER_ALLOW_REMOTE_CODE=true to enable it."
            ),
        )

    status = model_manager.start_prepare(
        request.model, trust_remote_code=request.trust_remote_code
    )
    return JSONResponse(
        status_code=202,
        content={"model": request.model, **status.to_dict()},
    )


@app.get("/download-progress/{model_name:path}")
async def download_progress(model_name: str) -> JSONResponse:
    import urllib.parse

    decoded_name = urllib.parse.unquote(model_name)
    status = model_manager.get_download_status(decoded_name)
    if status is None:
        return JSONResponse(
            status_code=200,
            content={
                "model": decoded_name,
                "status": "ready" if model_manager.is_ready(decoded_name) else "not_found",
                "progress": 100.0 if model_manager.is_ready(decoded_name) else 0.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "error": None,
            },
        )
    return JSONResponse(
        status_code=200,
        content={"model": decoded_name, **status.to_dict()},
    )


@app.post("/v1/embeddings")
async def create_embeddings(
    request: EmbeddingRequest,
    x_embedding_priority: Annotated[str | None, Header()] = None,
) -> EmbeddingResponse:
    # --- server-side policy checks (fail fast, before any I/O) ---
    if ALLOWED_MODELS is not None and request.model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=403,
            detail=f"Model '{request.model}' is not in the server's allowed model list.",
        )
    if request.trust_remote_code and not ALLOW_REMOTE_CODE:
        raise HTTPException(
            status_code=403,
            detail=(
                "trust_remote_code is disabled on this server. "
                "Set environment variable EMBEDDING_SERVER_ALLOW_REMOTE_CODE=true to enable it."
            ),
        )

    texts = _normalize_input(request.input)
    encoding_format = request.encoding_format or "float"

    try:
        vectors = await model_manager.encode(
            request.model,
            texts,
            trust_remote_code=request.trust_remote_code,
            priority=EncodePriority.from_header(x_embedding_priority),
        )
    except Exception as exc:
        logger.exception("Embedding failed for model=%s", request.model)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate embeddings: {exc}",
        ) from exc

    token_estimate = sum(max(1, len(t.split()) * 4 // 3) for t in texts)
    data = [
        EmbeddingData(
            embedding=_format_embedding_vector(vec, encoding_format),
            index=i,
        )
        for i, vec in enumerate(vectors)
    ]
    return EmbeddingResponse(
        data=data,
        model=request.model,
        usage=EmbeddingUsage(
            prompt_tokens=token_estimate,
            total_tokens=token_estimate,
        ),
    )


def run(
    host: str = "0.0.0.0",
    port: int | None = None,
    reload: bool = False,
) -> None:
    resolved_port = port or int(
        os.getenv("EMBEDDING_SERVER_PORT", str(EMBEDDING_SERVER_PORT))
    )
    uvicorn.run(
        "app.embedding_main:app",
        host=host,
        port=resolved_port,
        log_level="info",
        reload=reload,
    )


if __name__ == "__main__":
    run(reload=False)
