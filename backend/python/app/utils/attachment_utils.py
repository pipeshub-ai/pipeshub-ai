"""
Utilities for resolving user-uploaded attachments into LangChain multimodal
content blocks and injecting them into LLM messages.
"""
from __future__ import annotations

import logging
from typing import Any

from app.utils.chat_helpers import is_base64_image

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
) -> list[dict[str, Any]]:
    """Fetch user-uploaded attachments and return LangChain content blocks.

    For multimodal-capable LLMs each image attachment becomes an
    ``{"type": "image_url", ...}`` block.  For non-multimodal LLMs a plain
    text hint ``[Image attached: filename]`` is inserted so the model is at
    least aware that an image was provided.

    PDF attachments are resolved via ``record_to_message_content`` which
    handles both OCR-rasterised (image blocks) and text-extracted (text blocks)
    PDFs, forwarding the same ``is_multimodal_llm`` flag so image blocks are
    only emitted when the LLM supports vision.

    Other attachment types are skipped.

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

    blocks: list[dict[str, Any]] = []

    for att in attachments:
        mime_type: str = att.get("mimeType", "")
        record_name: str = att.get("recordName", "unknown")
        virtual_record_id: str | None = att.get("virtualRecordId")

        if not virtual_record_id:
            logger.warning("Attachment missing virtualRecordId: %s", record_name)
            continue

        is_image = mime_type.startswith("image/")
        is_pdf = mime_type.lower() == "application/pdf"

        if not is_image and not is_pdf:
            logger.debug(
                "Skipping non-image attachment: %s (%s)", record_name, mime_type
            )
            continue

        if is_image:
            if not is_multimodal_llm:
                # Model cannot process images; insert a text hint so it is at
                # least aware the user attached something.
                blocks.append(
                    {"type": "text", "text": f"[Image attached by user: {record_name}]\n"}
                )
                continue

            if blob_store is None:
                logger.warning(
                    "blob_store not available; cannot resolve image attachment %s",
                    record_name,
                )
                blocks.append(
                    {"type": "text", "text": f"[Image attached by user: {record_name}]\n"}
                )
                continue

            try:
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
                    continue

                if out_records is not None:
                    out_records[virtual_record_id] = record

                image_blocks = _extract_image_blocks(record, record_name, logger)
                if image_blocks:
                    blocks.extend(image_blocks)
                else:
                    logger.debug(
                        "No image blocks found in record for %s; skipping", record_name
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to resolve attachment %s (vrid=%s): %s",
                    record_name,
                    virtual_record_id,
                    exc,
                    exc_info=True,
                )

        else:  # is_pdf
            if blob_store is None:
                logger.warning(
                    "blob_store not available; cannot resolve PDF attachment %s",
                    record_name,
                )
                blocks.append(
                    {"type": "text", "text": f"[PDF attached by user: {record_name}]\n"}
                )
                continue

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
                    continue

                if out_records is not None:
                    out_records[virtual_record_id] = record

                pdf_content, ref_mapper = record_to_message_content(
                    record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm
                )
                if pdf_content:
                    blocks.extend(pdf_content)
                else:
                    logger.debug(
                        "No content blocks found in PDF record for %s; skipping",
                        record_name,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to resolve PDF attachment %s (vrid=%s): %s",
                    record_name,
                    virtual_record_id,
                    exc,
                    exc_info=True,
                )

    return blocks


def _extract_image_blocks(
    record: dict[str, Any],
    record_name: str,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """Extract ``image_url`` content blocks from a stored record dict.

    The record schema (mirrored from `chat_helpers.get_message_content`) is::

        record["block_containers"]["blocks"][i]["type"] == "image"
        record["block_containers"]["blocks"][i]["data"]["uri"] == "data:image/...;base64,..."

    Older records may store blocks directly under ``record["blocks"]``; both
    layouts are handled here.
    """
    result: list[dict[str, Any]] = []

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

        if uri.startswith(("https://", "http://")):
            result.append({"type": "image_url", "image_url": {"url": uri}})
        elif uri.startswith(_SUPPORTED_IMAGE_PREFIXES):
            result.append({"type": "image_url", "image_url": {"url": uri}})
        else:
            logger.debug(
                "Skipping image with unsupported URI format from %s: %.80s",
                record_name,
                uri,
            )

    return result


def resolve_pdf_blocks_simple(
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
        blocks = await resolve_attachments(
            attachments=raw_attachments,
            blob_store=blob_store,
            org_id=state.get("org_id", ""),
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            logger=logger,
            ref_mapper=ref_mapper,
            out_records=attachment_records,
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
