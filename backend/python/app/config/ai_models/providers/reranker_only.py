"""Reranker-only providers."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability
from app.config.constants.ai_models import DEFAULT_RERANKER_MODEL


@AIModelProviderBuilder("Default (System Provided)", "defaultReranker") \
    .with_description(
        "The multilingual reranking model bundled with PipesHub. Runs on the local "
        "model server; no additional configuration required."
    ) \
    .with_notice(
        "- Downloaded on first use (about 2.3 GB) unless it is already in the image.\n"
        "- Runs on CPU unless the model server has a GPU; reranking adds latency to each search.\n"
        "- Reranking is used only when it is turned on in Labs.",
        title="Before you enable it",
    ) \
    .with_capabilities([ModelCapability.RERANKING]) \
    .with_icon("/icons/ai-models/huggingface-color.svg") \
    .with_color("#FFD21E") \
    .with_model_name(DEFAULT_RERANKER_MODEL) \
    .build_decorator()
class DefaultRerankerProvider:
    """No fields — system-provided reranking model."""
