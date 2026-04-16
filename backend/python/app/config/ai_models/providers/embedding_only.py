"""Embedding-only providers: Sentence Transformers, Jina AI, Voyage, HuggingFace (default)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, EMBEDDING_COMMON_TAIL, model_field


# ---------------------------------------------------------------------------
# Default (System Provided) — HuggingFace
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("Default (System Provided)", "default") \
    .with_description("Using the default embedding model provided by the system. No additional configuration required.") \
    .with_capabilities([ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/huggingface-color.svg") \
    .with_color("#FFD21E") \
    .build_decorator()
class DefaultEmbeddingProvider:
    """No fields — system-provided embedding model."""
    pass


# ---------------------------------------------------------------------------
# Sentence Transformers
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("Sentence Transformers", "sentenceTransformers") \
    .with_description("Sentence Transformers models") \
    .with_capabilities([ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/sentence-transformers.png") \
    .with_color("#0078D4") \
    .add_field(model_field("e.g., all-MiniLM-L6-v2")) \
    .add_field(EMBEDDING_COMMON_TAIL[0]) \
    .add_field(EMBEDDING_COMMON_TAIL[1]) \
    .build_decorator()
class SentenceTransformersProvider:
    pass


# ---------------------------------------------------------------------------
# Jina AI
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("Jina AI", "jinaAI") \
    .with_description("Jina AI models") \
    .with_capabilities([ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/jina.svg") \
    .with_color("#0078D4") \
    .add_field(model_field("e.g., jina-embeddings-v3")) \
    .add_field(API_KEY) \
    .add_field(EMBEDDING_COMMON_TAIL[0]) \
    .add_field(EMBEDDING_COMMON_TAIL[1]) \
    .build_decorator()
class JinaAIProvider:
    pass


# ---------------------------------------------------------------------------
# Voyage
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("Voyage", "voyage") \
    .with_description("Voyage models") \
    .with_capabilities([ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/voyage-color.svg") \
    .with_color("#0078D4") \
    .add_field(model_field("e.g., voyage-3.5")) \
    .add_field(API_KEY) \
    .add_field(EMBEDDING_COMMON_TAIL[0]) \
    .add_field(EMBEDDING_COMMON_TAIL[1]) \
    .build_decorator()
class VoyageProvider:
    pass


# ---------------------------------------------------------------------------
# HuggingFace (separate card from "default")
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("HuggingFace", "huggingFace") \
    .with_description("Open-source transformer models") \
    .with_capabilities([ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/huggingface-color.svg") \
    .with_color("#FFD21E") \
    .add_field(model_field("e.g., sentence-transformers/all-MiniLM-L6-v2")) \
    .add_field(EMBEDDING_COMMON_TAIL[0]) \
    .add_field(EMBEDDING_COMMON_TAIL[1]) \
    .build_decorator()
class HuggingFaceProvider:
    pass
