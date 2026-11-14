"""Embeds text the way production does, for each weight precision.

Requests go through the real ``embedding_main`` ASGI app, in process (no port),
and the real ``EmbeddingServerEmbeddings`` client. Documents are packed by the
indexer's own batcher and sent by four concurrent workers; queries carry the
query-priority header. No offline ``model.encode`` loop is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import numpy as np
import openai
from langchain_core.documents import Document
from langchain_openai.embeddings import OpenAIEmbeddings

from app.config.constants.ai_models import (
    EMBEDDING_PRIORITY_HEADER,
    EMBEDDING_PRIORITY_QUERY,
)
from app.utils.embedding_precision import QUANTIZER_VERSION, PrecisionSelector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    import torch

WORKERS = 4
_BASE_URL = "http://embedding.invalid"


class ServerPath:
    """One embedding server (a fresh model manager and precision decision) and a client wired to it."""

    def __init__(self, model: str, precision: str, *, bfloat16: bool = False, state_dir: Path | None = None) -> None:
        self.model = model
        self.precision = precision
        self.bfloat16 = bfloat16
        self._state_dir = state_dir
        self.client = None
        self.info: dict = {}

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[ServerPath]:
        import app.embedding_main as server
        from app.utils.embedding_server_client import EmbeddingServerEmbeddings

        with tempfile.TemporaryDirectory() as scratch:
            state = (self._state_dir or Path(scratch)) / "embedding_precision.json"
            selector = PrecisionSelector(state_path=state, quantizer_version=QUANTIZER_VERSION, request=self.precision)
            manager = server.ModelManager(device="cpu")
            patches = [
                patch.object(server, "model_manager", manager),
                patch.object(server, "precision_selector", selector),
            ]
            if self.bfloat16:
                patches.append(patch.object(server.ModelManager, "_load_with_precision", _bfloat16_loader))
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                http = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url=_BASE_URL, timeout=1800)
                client = EmbeddingServerEmbeddings(model=self.model)
                client._inner = _openai(self.model, http)
                client._query_inner = _openai(self.model, http, {EMBEDDING_PRIORITY_HEADER: EMBEDDING_PRIORITY_QUERY})
                self.client = client
                try:
                    await client.aembed_query("warm up")  # loads the model and takes the precision decision
                    # read_precision builds and closes its own client; give it a throwaway one on the in-process app.
                    real_async_openai = openai.AsyncOpenAI
                    with patch.object(
                        openai, "AsyncOpenAI",
                        lambda **kw: real_async_openai(**{**kw, "http_client": httpx.AsyncClient(
                            transport=http._transport, base_url=_BASE_URL, timeout=60)}),
                    ):
                        await client.read_precision()
                    health = (await http.get("/health")).json()
                    self.info = {
                        "reported_precision": client.precision,
                        "reason": health.get("precision_reason"),
                        "quantizer_version": health.get("quantizer_version"),
                    }
                    yield self
                finally:
                    await http.aclose()
                    manager._models.clear()

    async def documents(self, texts: Sequence[str], *, workers: int = WORKERS) -> np.ndarray:
        """Embed *texts* in the indexer's batches (up to 20 texts / 40,000 characters), *workers* at a time."""
        from app.modules.transformers.vectorstore import (
            _LOCAL_CPU_DOCUMENT_BATCH_CHARS,
            _LOCAL_CPU_DOCUMENT_BATCH_SIZE,
            _batch_documents_by_size,
        )

        batches = _batch_documents_by_size(
            [Document(page_content=t) for t in texts], _LOCAL_CPU_DOCUMENT_BATCH_SIZE, _LOCAL_CPU_DOCUMENT_BATCH_CHARS,
        )
        out: list[list[float] | None] = [None] * len(texts)
        gate = asyncio.Semaphore(workers)

        async def run(start: int, batch: list[Document]) -> None:
            async with gate:
                vectors = await self.client.aembed_documents([d.page_content for d in batch])
            out[start:start + len(batch)] = vectors

        await asyncio.gather(*(run(s, b) for s, b in batches))
        return np.asarray(out, dtype=np.float64)

    async def queries(self, texts: Sequence[str], *, workers: int = WORKERS) -> np.ndarray:
        gate = asyncio.Semaphore(workers)

        async def one(text: str) -> list[float]:
            async with gate:
                return await self.client.aembed_query(text)

        return np.asarray(await asyncio.gather(*(one(t) for t in texts)), dtype=np.float64)


def _openai(model: str, http: httpx.AsyncClient, headers: dict[str, str] | None = None) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=model, api_key="not-needed", base_url=f"{_BASE_URL}/v1", check_embedding_ctx_length=False,
        max_retries=0, http_async_client=http, default_headers=headers,
    )


def _bfloat16_loader(self, load, model_name=None):  # noqa: ANN202
    """Calibration only: the same load with the weights cast to bfloat16. Not a production precision."""
    import torch

    from app.utils.embedding_precision import FP32, PrecisionDecision

    model = load().to(torch.bfloat16)
    return model, PrecisionDecision(FP32, "calibration: bfloat16 weights", QUANTIZER_VERSION)


@contextlib.contextmanager
def dynamic_activation_variant():  # noqa: ANN201
    """The control: int8 layers that also quantise activations per batch, so a text's vector depends on its neighbours."""

    from app.utils import int8_linear

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        flat = x.reshape(-1, self.in_features).float()
        step = flat.abs().max() / 127
        flat = (flat / step).round().clamp(-127, 127) * step
        out = (flat @ self.wi.float().T) * self.scale
        if self.bias is not None:
            out = out + self.bias
        return out.reshape(*x.shape[:-1], self.out_features).to(x.dtype)

    with patch.object(int8_linear.Int8WeightLinear, "forward", forward):
        yield


def batch_dependence_texts(neighbour_words: int = 380) -> tuple[str, list[str], list[str]]:
    """One probe text, a 512-token neighbour batch, and a 512-plus-seven-300-token batch."""
    probe = "The scheduler retries a failed batch after a short delay and records the attempt."
    long = " ".join(f"word{i % 97}" for i in range(neighbour_words * 2))
    medium = " ".join(f"item{i % 53}" for i in range(230))
    return probe, [long], [long, *[medium] * 7]


async def batch_dependence(path: ServerPath) -> tuple[float, float]:
    """Cosine of one text alone against the same text beside one long neighbour and beside eight."""
    probe, one_neighbour, many_neighbours = batch_dependence_texts()
    alone = (await path.documents([probe]))[0]
    beside_one = (await path.documents([probe, *one_neighbour]))[0]
    beside_many = (await path.documents([probe, *many_neighbours]))[0]

    def cos(a, b) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    return cos(alone, beside_one), cos(alone, beside_many)


Encoded = dict[str, np.ndarray]


async def encode_everything(
    model: str, precision: str, docs: Sequence[str], queries: Sequence[str], *, bfloat16: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[Encoded, dict]:
    async with ServerPath(model, precision, bfloat16=bfloat16).running() as path:
        if progress:
            progress(f"{precision}{' bf16' if bfloat16 else ''}: {path.info}")
        return {"docs": await path.documents(docs), "queries": await path.queries(queries)}, path.info
