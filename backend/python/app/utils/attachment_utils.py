"""
Utilities for resolving user-uploaded attachments into LangChain multimodal
content blocks and injecting them into LLM messages.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.utils.attachment_mime_types import DOC_ATTACHMENT_MIME_TYPES
from app.utils.chat_helpers import ImageBudget, is_base64_image

# Base64 data-URI prefixes accepted by the multimodal LLM providers we support.
_SUPPORTED_IMAGE_PREFIXES: tuple[str, ...] = (
    "data:image/png",
    "data:image/jpeg",
    "data:image/jpg",
    "data:image/webp",
)


async def resolve_attachments(
    attachments: list[dict[str, Any]],
    blob_store: Any,
    org_id: str,
    is_multimodal_llm: bool,
    logger: logging.Logger,
    ref_mapper: Any = None,
    out_records: dict[str, dict[str, Any]] | None = None,
    image_budget: ImageBudget | None = None,
) -> list[dict[str, Any]]:
    """Fetch user-uploaded attachments and return LangChain content blocks.

    For multimodal-capable LLMs each image attachment becomes an
    ``{"type": "image_url", ...}`` block.  For non-multimodal LLMs a plain
    text hint ``[Image attached: filename]`` is inserted so the model is at
    least aware that an image was provided.

    PDF, TXT/MD/MDX, DOCX, XLSX, CSV, and TSV attachments are all resolved via
    ``record_to_message_content``, which handles both OCR-rasterised (image
    blocks) and text-extracted (text blocks) documents, forwarding the same
    ``is_multimodal_llm`` flag so image blocks are only emitted when the LLM
    supports vision.

    Any other attachment type is skipped.

    Args:
        attachments: List of attachment metadata dicts from the request.
        blob_store: BlobStorage instance (may be None; function handles it).
        org_id: Organisation ID used for scoped storage access.
        is_multimodal_llm: Whether the configured LLM supports image inputs.
        logger: Logger instance.

    Returns:
        List of LangChain content-dict blocks ready to be inserted into a
        ``HumanMessage(content=[...])`` list.
    """
    if not attachments:
        return []

    if image_budget is None:
        image_budget = ImageBudget()

    blocks: list[dict[str, Any]] = []

    for att in attachments:
        mime_type: str = att.get("mimeType", "")
        record_name: str = att.get("recordName", "unknown")
        virtual_record_id: str | None = att.get("virtualRecordId")

        if not virtual_record_id:
            logger.warning("Attachment missing virtualRecordId: %s", record_name)
            continue

        mime_lower = mime_type.lower()
        is_image = mime_lower.startswith("image/")
        is_document = mime_lower in DOC_ATTACHMENT_MIME_TYPES

        if not is_image and not is_document:
            logger.debug(
                "Skipping unsupported attachment type: %s (%s)", record_name, mime_type
            )
            continue

        if is_image:
            if not is_multimodal_llm:
                blocks.append(
                    {"type": "text", "text": f"[Image attached by user: {record_name}]\n"}
                )
                continue

            img_content, ref_mapper = await _resolve_attachment_content(
                blob_store=blob_store,
                virtual_record_id=virtual_record_id,
                org_id=org_id,
                record_name=record_name,
                is_multimodal_llm=True,
                ref_mapper=ref_mapper,
                out_records=out_records,
                logger=logger,
                unavailable_log_msg="blob_store not available; cannot resolve image attachment %s",
                fallback_block={"type": "text", "text": f"[Image attached by user: {record_name}]\n"},
                empty_content_fallback=lambda record: _extract_image_blocks(
                    record, record_name, logger, image_budget,
                ),
                image_budget=image_budget,
            )
            if img_content:
                blocks.extend(img_content)
            else:
                logger.debug("No image blocks found in record for %s; skipping", record_name)

        elif is_document:
            doc_content, ref_mapper = await _resolve_attachment_content(
                blob_store=blob_store,
                virtual_record_id=virtual_record_id,
                org_id=org_id,
                record_name=record_name,
                is_multimodal_llm=is_multimodal_llm,
                ref_mapper=ref_mapper,
                out_records=out_records,
                logger=logger,
                unavailable_log_msg="blob_store not available; cannot resolve attachment %s",
                fallback_block={"type": "text", "text": f"[Document attached by user: {record_name}]\n"},
                image_budget=image_budget,
            )
            if doc_content:
                blocks.extend(doc_content)
            else:
                logger.debug("No content blocks found in record for %s; skipping", record_name)

    return blocks


async def _resolve_attachment_content(
    *,
    blob_store: Any,
    virtual_record_id: str,
    org_id: str,
    record_name: str,
    is_multimodal_llm: bool,
    ref_mapper: Any,
    out_records: dict[str, dict[str, Any]] | None,
    logger: logging.Logger,
    unavailable_log_msg: str,
    fallback_block: dict[str, Any],
    empty_content_fallback: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    image_budget: ImageBudget | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Fetch a stored attachment record and convert it via ``record_to_message_content``.

    Shared by the image and document branches of ``resolve_attachments``: both
    fetch the record from ``blob_store``, cache it in ``out_records``, and
    convert it under an identical try/except + logging shape. Returns
    ``(blocks, ref_mapper)``; ``blocks`` is ``[fallback_block]`` when
    ``blob_store`` is unavailable, or ``[]`` if the record can't be fetched or
    resolution fails.
    """
    if blob_store is None:
        logger.warning(unavailable_log_msg, record_name)
        return [fallback_block], ref_mapper

    try:
        from app.utils.chat_helpers import record_to_message_content  # noqa: PLC0415

        record = await blob_store.get_record_from_storage(
            virtual_record_id=virtual_record_id,
            org_id=org_id,
        )
        if not record:
            logger.warning(
                "Could not fetch attachment record for virtualRecordId=%s (%s)",
                virtual_record_id,
                record_name,
            )
            return [], ref_mapper

        if out_records is not None:
            out_records[virtual_record_id] = record

        content, ref_mapper = record_to_message_content(
            record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm,
            image_budget=image_budget,
        )
        if not content and empty_content_fallback is not None:
            content = empty_content_fallback(record)
        return content, ref_mapper
    except Exception as exc:
        logger.warning(
            "Failed to resolve attachment %s (vrid=%s): %s",
            record_name,
            virtual_record_id,
            exc,
            exc_info=True,
        )
        return [], ref_mapper


def _extract_image_blocks(
    record: dict[str, Any],
    record_name: str,
    logger: logging.Logger,
    image_budget: ImageBudget | None = None,
) -> list[dict[str, Any]]:
    """Extract ``image_url`` content blocks from a stored record dict.

    The record schema (mirrored from `chat_helpers.get_message_content`) is::

        record["block_containers"]["blocks"][i]["type"] == "image"
        record["block_containers"]["blocks"][i]["data"]["uri"] == "data:image/...;base64,..."

    Older records may store blocks directly under ``record["blocks"]``; both
    layouts are handled here. Respects the shared conversation-wide
    ``image_budget`` (defaults to a fresh, effectively-unbounded one when not
    supplied) the same way ``record_to_message_content`` does, so this
    fallback path — only reached when that function returns zero content for
    an image record — cannot bypass the 50-image cap.
    """
    result: list[dict[str, Any]] = []
    if image_budget is None:
        image_budget = ImageBudget()

    block_containers = record.get("block_containers")
    if isinstance(block_containers, dict):
        raw_blocks = block_containers.get("blocks", []) or []
    else:
        # Legacy fallback: blocks at the top level of the record.
        raw_blocks = record.get("blocks", []) or []

    for block in raw_blocks:
        if not isinstance(block, dict):
            continue

        if block.get("type") != "image":
            continue

        data = block.get("data")
        if not isinstance(data, dict):
            continue

        uri: str | None = data.get("uri")
        if not uri:
            continue

        if not image_budget.can_add():
            logger.debug(
                "Skipping image from %s: conversation image budget exhausted",
                record_name,
            )
            continue

        if uri.startswith(("https://", "http://")):
            image_budget.try_consume(1)
            result.append({"type": "image_url", "image_url": {"url": uri}})
        elif uri.startswith(_SUPPORTED_IMAGE_PREFIXES):
            image_budget.try_consume(1)
            result.append({"type": "image_url", "image_url": {"url": uri}})
        else:
            logger.debug(
                "Skipping image with unsupported URI format from %s: %.80s",
                record_name,
                uri,
            )

    return result


def resolve_attachment_blocks_simple(
    record: dict[str, Any],
    is_multimodal_llm: bool,
) -> list[dict[str, Any]]:
    """Extract content blocks from a PDF record using simple block iteration.

    Unlike ``record_to_message_content`` (which wraps blocks with citation IDs,
    block indices, and Jinja templates), this produces plain text and image_url
    blocks suitable for sub-agent context where citation metadata is unnecessary.

    Handles ``text``, ``table_row``, and (when multimodal) ``image`` block types.
    """
    blocks: list[dict[str, Any]] = []
    raw_blocks = (record.get("block_containers") or {}).get("blocks") or []
    for blk in raw_blocks:
        blk_type = blk.get("type")
        data = blk.get("data", "")
        if blk_type == "text":
            if isinstance(data, str) and data.strip():
                blocks.append({"type": "text", "text": data})
        elif blk_type == "table_row":
            if isinstance(data, dict):
                row_text = data.get("row_natural_language_text", "")
                if row_text:
                    blocks.append({"type": "text", "text": row_text})
        elif blk_type == "image" and is_multimodal_llm:
            if isinstance(data, dict):
                image_uri = data.get("uri", "")
                if image_uri and is_base64_image(image_uri):
                    blocks.append({"type": "image_url", "image_url": {"url": image_uri}})
    return blocks


async def ensure_attachment_blocks(state: dict, logger: logging.Logger) -> list:
    """Lazily resolve user attachments into multimodal content blocks.

    Fetches image data for each attachment and caches the result on
    ``state["resolved_attachment_blocks"]`` so subsequent nodes can reuse
    the blocks without re-fetching.  Returns the list of blocks (may be empty).

    This function is safe to call from any agent node — it is a no-op when
    blocks have already been resolved or when there are no attachments.
    """
    if state.get("resolved_attachment_blocks") is not None:
        return state["resolved_attachment_blocks"]

    raw_attachments = state.get("attachments") or []
    if not raw_attachments:
        state["resolved_attachment_blocks"] = []
        return []

    try:
        blob_store = state.get("blob_store")
        if blob_store is None:
            from app.modules.transformers.blob_storage import BlobStorage  # noqa: PLC0415

            blob_store = BlobStorage(
                logger=logger,
                config_service=state.get("config_service"),
                graph_provider=state.get("graph_provider"),
            )
            state["blob_store"] = blob_store

        ref_mapper = state.get("citation_ref_mapper")
        if ref_mapper is None:
            from app.utils.chat_helpers import CitationRefMapper  # noqa: PLC0415
            ref_mapper = CitationRefMapper()
            state["citation_ref_mapper"] = ref_mapper

        attachment_records: dict[str, dict[str, Any]] = {}
        # Shared with retrieval/prefetch/citations/history-replay via the
        # SAME `state["image_budget"]` instance (see `context.py`'s
        # `_seed_tool_state`) so attachment images debit the one
        # conversation-wide 50-image cap instead of an independent budget.
        image_budget = state.setdefault("image_budget", ImageBudget())
        blocks = await resolve_attachments(
            attachments=raw_attachments,
            blob_store=blob_store,
            org_id=state.get("org_id", ""),
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            logger=logger,
            ref_mapper=ref_mapper,
            out_records=attachment_records,
            image_budget=image_budget,
        )

        if attachment_records:
            vrmap = state.get("virtual_record_id_to_result")
            if not isinstance(vrmap, dict):
                vrmap = {}
                state["virtual_record_id_to_result"] = vrmap
            for vrid, rec in attachment_records.items():
                # Don't clobber a richer pre-existing record (e.g. one already populated by retrieval).
                if vrid not in vrmap:
                    vrmap[vrid] = rec
    except Exception as exc:
        logger.warning("Failed to resolve attachments: %s", exc)
        blocks = []

    state["resolved_attachment_blocks"] = blocks
    return blocks


def inject_attachment_blocks(messages: list, attachment_blocks: list) -> None:
    """Mutate the last HumanMessage in *messages* to include attachment blocks.

    Handles both plain-text content (string) and already-multimodal content
    (list of dicts).  No-ops when *attachment_blocks* is empty or the last
    message is not a HumanMessage.
    """
    if not attachment_blocks or not messages:
        return

    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    last = messages[-1]
    if not isinstance(last, HumanMessage):
        return

    if isinstance(last.content, list):
        last.content.append(
            {"type": "text", "text": "\n\nAttached files from the user:\n"}
        )
        last.content.extend(attachment_blocks)
    else:
        text = last.content if isinstance(last.content, str) else str(last.content)
        messages[-1] = HumanMessage(content=build_multimodal_content(text, attachment_blocks))


def build_multimodal_content(
    text: str,
    attachment_blocks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | str:
    """Build multimodal content for a ``HumanMessage``.

    When *attachment_blocks* is non-empty the function returns a content
    list suitable for ``HumanMessage(content=<list>)``.  The attachment
    blocks are appended after the text so the model sees the query first
    and the images immediately after.

    When *attachment_blocks* is empty or ``None`` the original *text* string
    is returned unchanged, preserving existing behaviour for non-attachment
    requests.
    """
    if not attachment_blocks:
        return text

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content.append({"type": "text", "text": "\n\nAttached files from the user:\n"})
    content.extend(attachment_blocks)
    return content
