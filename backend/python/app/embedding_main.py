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
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_SERVER_PORT,
)
from app.utils.logger import create_logger
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = create_logger("embedding_service")

DEFAULT_DEVICE = os.getenv("EMBEDDING_SERVER_DEVICE", "cpu")
DEFAULT_NORMALIZE = os.getenv("EMBEDDING_SERVER_NORMALIZE", "true").lower() in (
    "1",
    "true",
    "yes",
)
MAX_CONCURRENT_EMBEDDINGS = int(os.getenv("EMBEDDING_SERVER_MAX_CONCURRENCY", "2"))

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


class ModelManager:
    """Thread-safe cache of loaded SentenceTransformer models."""

    def __init__(
        self,
        *,
        device: str = DEFAULT_DEVICE,
        normalize_embeddings: bool = DEFAULT_NORMALIZE,
        max_concurrency: int = MAX_CONCURRENT_EMBEDDINGS,
    ) -> None:
        self._device = device
        self._normalize = normalize_embeddings
        self._models: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._encode_semaphore = asyncio.Semaphore(max_concurrency)

    @staticmethod
    def _cache_key(model_name: str, *, trust_remote_code: bool) -> str:
        return f"{model_name}::trust_remote_code={trust_remote_code}"

    def list_loaded_models(self) -> list[str]:
        return list({key.split("::", 1)[0] for key in self._models})

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
                    return SentenceTransformer(
                        model_name,
                        local_files_only=True,
                        **load_kwargs,
                    )
                except Exception:
                    logger.info(
                        "Model '%s' not found in local cache; downloading from Hub",
                        model_name,
                    )
                    return SentenceTransformer(
                        model_name,
                        local_files_only=False,
                        **load_kwargs,
                    )

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

        async with self._encode_semaphore:
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
    app.state.model_manager = model_manager
    logger.info(
        "Starting embedding server warmup for %s (max_concurrency=%d)",
        DEFAULT_EMBEDDING_MODEL,
        MAX_CONCURRENT_EMBEDDINGS,
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
    yield
    logger.info("Shutting down embedding server")


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
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "unhealthy",
                "error": str(e),
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


@app.post("/v1/embeddings")
async def create_embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
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
