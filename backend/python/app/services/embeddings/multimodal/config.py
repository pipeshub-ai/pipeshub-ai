"""Configuration passed from ``VectorStore`` to ``MultimodalEmbeddingFactory``.

Kept as a plain dataclass (rather than passing the whole ``VectorStore``
instance) so provider classes depend only on the handful of fields they
actually need and can be unit-tested without constructing a VectorStore.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class MultimodalProviderConfig:
    provider: str | None
    api_key: str | None = None
    model_name: str | None = None
    region_name: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    base_url: str | None = None
    # Dimension the target collection was created with. Providers that let
    # the caller choose an output length (Bedrock Titan) must request this
    # one, or every point they return is rejected by
    # ``VectorStore._build_image_points``'s dimension check.
    embedding_size: int | None = None
    # LangChain Embeddings instance for providers that already have a working
    # LangChain integration capable of embedding raw base64 image strings
    # (currently Voyage's ``embed_documents``/``aembed_documents``).
    dense_embeddings: Any = None
    # Injected image-normalisation callable (sync or async). Defaults to
    # ``app.utils.image_utils.normalize_image_to_base64`` inside each
    # provider when omitted; ``VectorStore`` injects its own instance method
    # here so existing test patches on ``VectorStore._normalize_image_to_base64``
    # keep working after this dispatch moved into provider classes.
    normalize_fn: Callable[[str], Any] | None = None
    logger: Any = None
