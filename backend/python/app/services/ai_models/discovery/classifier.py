"""Turn a provider row into a ``DiscoveredModel``.

Provider metadata wins. The catalog fills only fields the provider left
unset. ID rules run when neither source named a capability. What is still
unknown is ``other``, which the UI hides behind a toggle.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.services.ai_models.discovery.catalog import capability_from_mode, lookup_catalog
from app.services.ai_models.discovery.types import DiscoveredModel, RawModel

_EXCLUDE = re.compile(r"(moderation|guard|realtime|\bre-?rank\b|search)", re.IGNORECASE)


def _id_capabilities(model_id: str) -> list[str] | None:
    """Return ID-rule capabilities, an empty list to drop the model, or None."""
    lowered = model_id.lower()
    if _EXCLUDE.search(lowered):
        return []
    capabilities: list[str] = []
    if lowered.startswith("text-embedding") or "embed" in lowered:
        capabilities.append("embedding")
    if lowered.startswith("whisper") or "transcribe" in lowered:
        capabilities.append("stt")
    if lowered.startswith("tts-") or lowered.endswith("-tts") or "orpheus" in lowered:
        capabilities.append("tts")
    if (
        lowered.startswith("dall-e")
        or lowered.startswith("gpt-image")
        or lowered.startswith("imagen")
        or "-image" in lowered
    ):
        capabilities.append("imageGeneration")
    return capabilities or None


def _deprecated_from_catalog(entry: dict) -> bool | None:
    raw = entry.get("deprecation_date")
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp <= datetime.now(timezone.utc)


def classify(provider_id: str, raw: RawModel) -> DiscoveredModel | None:
    catalog = lookup_catalog(provider_id, raw.id)
    catalog_capability = capability_from_mode(catalog.get("mode")) if catalog else None
    if catalog and catalog.get("mode") in {"moderation", "rerank", "search"} and not raw.capabilities:
        return None

    capabilities = list(raw.capabilities) if raw.capabilities else None
    source: str = "provider_api"
    if capabilities is None and catalog_capability:
        capabilities = [catalog_capability]
        source = "catalog"
    if capabilities is None:
        from_id = _id_capabilities(raw.id)
        if from_id == []:
            return None
        if from_id:
            capabilities = from_id
            source = "heuristic"
    if capabilities is None:
        capabilities = ["other"]
        source = "heuristic"
    if not capabilities:
        return None

    context_length = raw.context_length
    is_multimodal = raw.is_multimodal
    is_reasoning = raw.is_reasoning
    supports_tools = raw.supports_tools
    deprecated = raw.deprecated
    if catalog:
        if context_length is None and isinstance(catalog.get("max_input_tokens"), int):
            context_length = catalog["max_input_tokens"]
        if is_multimodal is None and "supports_vision" in catalog:
            is_multimodal = bool(catalog["supports_vision"])
        if is_reasoning is None and "supports_reasoning" in catalog:
            is_reasoning = bool(catalog["supports_reasoning"])
        if supports_tools is None and "supports_function_calling" in catalog:
            supports_tools = bool(catalog["supports_function_calling"])
        if deprecated is None:
            deprecated = _deprecated_from_catalog(catalog)

    return DiscoveredModel(
        id=raw.id,
        display_name=raw.display_name or raw.id,
        capabilities=capabilities,
        context_length=context_length,
        is_multimodal=is_multimodal,
        is_reasoning=is_reasoning,
        supports_tools=supports_tools,
        deprecated=bool(deprecated),
        source=source,  # type: ignore[arg-type]
    )
