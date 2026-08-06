"""Configuration passed from ``VectorStore`` to ``MultimodalEmbeddingFactory``.

Kept as a plain dataclass (rather than passing the whole ``VectorStore``
instance) so provider classes depend only on the handful of fields they
actually need and can be unit-tested without constructing a VectorStore.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class MultimodalProviderConfig:
    provider: Optional[str]
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    region_name: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    base_url: Optional[str] = None
    # LangChain Embeddings instance for providers that already have a working
    # LangChain integration capable of embedding raw base64 image strings
    # (currently Voyage's ``embed_documents``/``aembed_documents``).
    dense_embeddings: Any = None
    # Injected image-normalisation callable (sync or async). Defaults to
    # ``app.utils.image_utils.normalize_image_to_base64`` inside each
    # provider when omitted; ``VectorStore`` injects its own instance method
    # here so existing test patches on ``VectorStore._normalize_image_to_base64``
    # keep working after this dispatch moved into provider classes.
    normalize_fn: Optional[Callable[[str], Any]] = None
    logger: Any = None
