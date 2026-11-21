from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, override

from app.modules.retrieval.record_rerank.base import (
    RecordReranker,
    RecordRerankError,
    RerankerDeps,
    RerankRequest,
)
from app.modules.retrieval.record_rerank.factory import register_record_reranker

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

RECORD_RERANK_CROSS_ENCODER_MODEL_ENV_VAR = "RECORD_RERANK_CROSS_ENCODER_MODEL"
DEFAULT_MODEL = "syntriq/lupe-multilingual-reranker-small"
DEFAULT_MODEL_FILE = "onnx/model_qint8_avx512_vnni.onnx"
BATCH_SIZE = 32

logger = logging.getLogger(__name__)

_models: dict[str, asyncio.Future[TextCrossEncoder]] = {}


def build_model(name: str) -> TextCrossEncoder:
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    if name not in {model["model"] for model in TextCrossEncoder.list_supported_models()}:
        model_file = DEFAULT_MODEL_FILE if name == DEFAULT_MODEL else "onnx/model.onnx"
        TextCrossEncoder.add_custom_model(name, ModelSource(hf=name), model_file)
    try:
        return TextCrossEncoder(name, local_files_only=True)
    except Exception:
        if os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {"1", "true", "yes"}:
            raise
        logger.info("record_rerank: cross-encoder %r is not cached; downloading it", name)
        return TextCrossEncoder(name)


async def cross_encoder(name: str) -> TextCrossEncoder:
    loading = _models.get(name)
    if loading is None or (loading.done() and loading.exception() is not None):
        loading = _models[name] = asyncio.ensure_future(asyncio.to_thread(build_model, name))
    return await asyncio.shield(loading)


def candidate_text(title: str, text: str) -> str:
    return f"{title}\n{text}".strip()


@register_record_reranker("cross_encoder")
class CrossEncoderReranker(RecordReranker):
    def __init__(self, deps: RerankerDeps) -> None:
        del deps
        self._model_name = (os.getenv(RECORD_RERANK_CROSS_ENCODER_MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL

    @override
    async def rank(self, request: RerankRequest) -> list[str]:
        if not request.candidates:
            return []
        model = await cross_encoder(self._model_name)
        documents = [candidate_text(candidate.title, candidate.text) for candidate in request.candidates]
        scores = await asyncio.to_thread(lambda: list(model.rerank(request.question, documents, batch_size=BATCH_SIZE)))
        if len(scores) != len(documents):
            raise RecordRerankError(f"{len(scores)} scores for {len(documents)} candidates")
        order = sorted(range(len(scores)), key=lambda position: -scores[position])
        return [request.candidates[position].record_id for position in order[:request.top]]
