"""`prefetch_retrieval()`: upfront internal-knowledge search for
`internal_search` mode, run BEFORE the agent's first turn so a simple
query answers in one turn with zero tool calls (today's chatbot latency
profile) instead of paying a full ReAct round-trip (model call -> tool
call -> model call) just to fetch the same context the model could have
been handed immediately.

Mirrors the "Standard path: upfront retrieval" branch `chatbot.py`'s
`_generate_internal_search_stream()` ran today (`search_with_filters` ->
`get_flattened_results` -> `enrich_virtual_record_id_to_result_with_fk_
children` -> `enrich_records_with_graph_context` -> sort), and formats
results through `build_message_content_array`. Multimodal prefetch uses
`from_tool=False` so image blocks are emitted alongside the same citation
markers used by text results. Citation numbering stays on one
`CitationRefMapper` across prefetch and later tool calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    enrich_records_with_graph_context,
    enrich_virtual_record_id_to_result_with_fk_children,
    flattened_result_sort_key,
    get_flattened_results,
    is_base64_image,
)

if TYPE_CHECKING:
    import logging

    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["PrefetchResult", "prefetch_retrieval"]

_MAX_PREFETCH_IMAGES = 4
_MAX_PREFETCH_IMAGE_BYTES = 16 * 1024 * 1024
_IMAGE_MARKER_RE = re.compile(r"(?:^|\n)\[[^\]\n]*\|[^\]\n]+\]\s*$")

# Statuses `RetrievalService.search_with_filters()` uses for "the backend
# itself failed/is unavailable" -- as opposed to "ran fine, found nothing"
# (which returns 200 with empty `searchResults`, handled as `is_empty`
# below, not an error). Mirrors the check `_generate_internal_search_
# stream()` ran on the same call.
_RETRIEVAL_ERROR_STATUS_CODES = frozenset({202, 404, 500, 503})


@dataclass
class PrefetchResult:
    """Pure data produced by one upfront retrieval call. Never mutates
    caller state -- `chat_modes/bridge.py` is solely responsible for
    applying this onto `AgentContext.tool_state`."""

    formatted_context: str
    final_results: list[dict[str, Any]]
    virtual_record_id_to_result: dict[str, Any]
    tool_records: list[dict[str, Any]]
    citation_ref_mapper: CitationRefMapper
    is_empty: bool
    image_blocks: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = field(default=None)


def _select_prefetch_content(
    parts: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Apply image limits while keeping accepted images paired with their citation markers."""
    selected_images = []
    selected_marker_indexes = set()
    image_marker_indexes = set()
    current_marker_index = None
    total_bytes = 0
    for index, part in enumerate(parts):
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and _IMAGE_MARKER_RE.search(text):
                current_marker_index = index
            continue
        if part.get("type") != "image_url":
            continue
        if current_marker_index is not None:
            image_marker_indexes.add(current_marker_index)
        image_url = part.get("image_url") or {}
        uri = image_url.get("url") if isinstance(image_url, dict) else None
        if not isinstance(uri, str) or "," not in uri or not is_base64_image(uri):
            continue
        payload = uri.split(",", 1)[1]
        padding = len(payload) - len(payload.rstrip("="))
        decoded_bytes = max(0, (len(payload) * 3) // 4 - padding)
        if (
            len(selected_images) >= _MAX_PREFETCH_IMAGES
            or decoded_bytes > _MAX_PREFETCH_IMAGE_BYTES - total_bytes
        ):
            continue
        selected_images.append(part)
        if current_marker_index is not None:
            selected_marker_indexes.add(current_marker_index)
        total_bytes += decoded_bytes

    text_parts = []
    for index, part in enumerate(parts):
        text = part.get("text") if part.get("type") == "text" else None
        if not isinstance(text, str) or not text:
            continue
        if index in image_marker_indexes and index not in selected_marker_indexes:
            text = _IMAGE_MARKER_RE.sub("", text).strip()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts), selected_images


def _is_followup(previous_conversations: list[dict[str, Any]] | None) -> bool:
    """Identical signal `chatbot.py` used (`len(previousConversations) > 0`)
    -- not a smarter heuristic, deliberately, so prefetch skip/run behavior
    doesn't silently diverge from what shipped before this migration."""
    return bool(previous_conversations)


async def prefetch_retrieval(
    *,
    query: str,
    org_id: str,
    user_id: str,
    retrieval_service: Any,
    graph_provider: "IGraphDBProvider",
    blob_store: "BlobStorage",
    filters: dict[str, Any] | None,
    limit: int | None,
    is_multimodal_llm: bool,
    previous_conversations: list[dict[str, Any]] | None,
    logger: "logging.Logger",
    force: bool = False,
    ref_mapper: CitationRefMapper | None = None,
) -> PrefetchResult | None:
    """Runs upfront retrieval for the current query.

    Returns `None` when prefetch should be skipped -- a detected follow-up
    turn (the raw follow-up utterance, e.g. "what about last week?", is a
    poor standalone retrieval query; the registered `search_internal_
    knowledge` tool stays available for the agent to issue a proper
    follow-up search itself). `force=True` (Ollama/no-tool-calls
    degradation) always runs prefetch regardless of follow-up detection,
    since there is no tool for the agent to fall back on in that case.

    `ref_mapper`, when given, is the SAME `CitationRefMapper` the caller
    already handed to the request's dynamic tools (e.g. `fetch_url`/
    `web_search` via `tool_loader.py`) -- reusing it here (rather than the
    default fresh one) keeps every citation on this request numbered
    through one mapper, so a mapper swap after prefetch resolves doesn't
    orphan refs those tools already registered. See `bridge.py`'s
    `_produce()` for the call site this matters for.
    """
    if not force and _is_followup(previous_conversations):
        return None

    ref_mapper = ref_mapper if ref_mapper is not None else CitationRefMapper()
    try:
        result = await retrieval_service.search_with_filters(
            queries=[query],
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            filter_groups=filters,
            include_image_content=is_multimodal_llm,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a graceful empty prefetch
        logger.error("prefetch_retrieval: search_with_filters failed: %s", exc, exc_info=True)
        return PrefetchResult(
            formatted_context="",
            final_results=[],
            virtual_record_id_to_result={},
            tool_records=[],
            citation_ref_mapper=ref_mapper,
            is_empty=True,
            error_message=str(exc),
        )

    if not isinstance(result, dict):
        return PrefetchResult(
            formatted_context="",
            final_results=[],
            virtual_record_id_to_result={},
            tool_records=[],
            citation_ref_mapper=ref_mapper,
            is_empty=True,
            error_message="Invalid search response",
        )

    status_code = result.get("status_code", 500)
    if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
        return PrefetchResult(
            formatted_context="",
            final_results=[],
            virtual_record_id_to_result={},
            tool_records=[],
            citation_ref_mapper=ref_mapper,
            is_empty=True,
            error_message=result.get("message", "Search failed"),
        )

    raw_search_results = result.get("searchResults", [])
    search_results = (
        [
            item for item in raw_search_results
            if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
        ]
        if isinstance(raw_search_results, list)
        else []
    )
    raw_virtual_to_record_map = result.get("virtual_to_record_map", {})
    virtual_to_record_map = (
        {
            key: value for key, value in raw_virtual_to_record_map.items()
            if isinstance(value, dict)
        }
        if isinstance(raw_virtual_to_record_map, dict)
        else {}
    )
    if is_multimodal_llm:
        hydrated_image_records = []
        seen_image_records = set()
        for item in search_results:
            metadata = item.get("metadata")
            virtual_record_id = metadata.get("virtualRecordId")
            record = virtual_to_record_map.get(virtual_record_id) or {}
            mime_type = str(metadata.get("mimeType") or record.get("mimeType") or "")
            if (
                virtual_record_id
                and virtual_record_id not in seen_image_records
                and metadata.get("blockIndex") is None
                and metadata.get("isRecordSummary") is True
                and mime_type.lower().startswith("image/")
                and not is_base64_image(item.get("content"))
            ):
                hydrated_item = dict(item)
                hydrated_item["metadata"] = {
                    **metadata,
                    "blockIndex": 0,
                    "isBlockGroup": False,
                    "isRecordSummary": False,
                }
                hydrated_image_records.append(hydrated_item)
                seen_image_records.add(virtual_record_id)
                if len(hydrated_image_records) == _MAX_PREFETCH_IMAGES:
                    break
        search_results = [*search_results, *hydrated_image_records]

    virtual_record_id_to_result: dict[str, Any] = {}
    flattened_results = await get_flattened_results(
        search_results, blob_store, org_id, is_multimodal_llm,
        virtual_record_id_to_result, virtual_to_record_map,
        graph_provider=graph_provider,
    )
    await enrich_virtual_record_id_to_result_with_fk_children(
        virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results,
    )
    if flattened_results and graph_provider:
        await enrich_records_with_graph_context(
            virtual_record_id_to_result,
            graph_provider,
            flattened_results,
            virtual_to_record_map,
            blob_store=blob_store,
            org_id=org_id,
            config_service=getattr(blob_store, "config_service", None),
        )

    final_results = sorted(flattened_results, key=flattened_result_sort_key)
    if not final_results:
        return PrefetchResult(
            formatted_context="",
            final_results=[],
            virtual_record_id_to_result=virtual_record_id_to_result,
            tool_records=[],
            citation_ref_mapper=ref_mapper,
            is_empty=True,
        )

    message_content_array, ref_mapper = build_message_content_array(
        final_results, virtual_record_id_to_result,
        is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper, from_tool=False,
    )
    flat_parts = [item for sublist in message_content_array for item in sublist]
    formatted_context, image_blocks = _select_prefetch_content(flat_parts)
    logger.info(
        "prefetch_retrieval: prepared %d text part(s) and %d image part(s)",
        sum(1 for part in flat_parts if part.get("type") == "text"),
        len(image_blocks),
    )

    return PrefetchResult(
        formatted_context=formatted_context,
        final_results=final_results,
        virtual_record_id_to_result=virtual_record_id_to_result,
        tool_records=list(virtual_record_id_to_result.values()),
        citation_ref_mapper=ref_mapper,
        is_empty=not formatted_context.strip() and not image_blocks,
        image_blocks=image_blocks,
    )
