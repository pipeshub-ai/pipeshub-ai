"""Load user-attached chat files for chat and agent turns.

The frontend uploads each file via `POST /api/v1/document/upload`, then sends
storage `documentId`s on the stream payload (`ChatQuery.attachmentDocumentIds`).

For every attachment this module:

1. Fetches metadata and bytes via :class:`BlobStorage`.
2. Optionally creates a graph `FileRecord` plus permission edges when the
   graph user exists (same idea as artifacts, but `files` / `RecordType.FILE`).
   If the user vertex cannot be resolved, attachment bytes are still loaded for
   the prompt (Node already validated `documentId`s for the org).
3. Parses with :class:`FileContentParser` to blocks.
4. Returns **markdown meant for prompt templates** — not internal-search
   flattened records. Callers inject this string via
   ``app.modules.qna.prompt_templates`` / :func:`get_message_content` instead of
   merging fake retrieval rows into ``final_results``.

The first two tuple elements are always empty lists/dicts (legacy shape); only
the third string carries LLM-facing attachment text.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.models.entities import FileRecord, RecordType
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Generous but bounded — parsing a 50MB office document already pegs CPU,
# anything larger almost certainly belongs in the indexing pipeline, not in
# a single chat turn.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


def _normalize_extension(raw: str | None, fallback_filename: str | None = None) -> str:
    """Return a lowercase extension without a leading dot (`""` if unknown)."""
    if raw:
        ext = raw.strip().lower().lstrip(".")
        if ext:
            return ext
    if fallback_filename:
        idx = fallback_filename.rfind(".")
        if idx > 0 and idx < len(fallback_filename) - 1:
            return fallback_filename[idx + 1 :].strip().lower()
    return ""


def _build_file_record_for_chat_upload(
    *,
    org_id: str,
    document_id: str,
    file_name: str,
    mime_type: str,
    extension: str,
    size_in_bytes: int,
) -> FileRecord:
    """In-memory `FileRecord` for parsing chat uploads (may or may not be persisted)."""
    record_id = str(uuid4())
    now = get_epoch_timestamp_in_ms()
    file_record = FileRecord(
        id=record_id,
        org_id=org_id,
        record_name=file_name,
        record_type=RecordType.FILE,
        external_record_id=document_id,
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id=f"{Connectors.KNOWLEDGE_BASE.value.lower()}_{org_id}",
        mime_type=mime_type or "application/octet-stream",
        size_in_bytes=size_in_bytes,
        created_at=now,
        updated_at=now,
        indexing_status="NOT_STARTED",
        extraction_status="NOT_STARTED",
        is_file=True,
        extension=extension,
    )
    file_record.virtual_record_id = record_id
    return file_record


async def _create_attachment_file_record(
    *,
    graph_provider: IGraphDBProvider,
    user_key: str,
    org_id: str,
    document_id: str,
    file_name: str,
    mime_type: str,
    extension: str,
    size_in_bytes: int,
) -> FileRecord:
    """Persist a `FileRecord` + permission edges and return the populated model.

    Mirrors :func:`create_artifact_record` but targets the `files` collection
    (RecordType.FILE) instead of `artifacts`. We assign OWNER permission to the
    user that uploaded the file so subsequent record-level checks succeed.
    """
    file_record = _build_file_record_for_chat_upload(
        org_id=org_id,
        document_id=document_id,
        file_name=file_name,
        mime_type=mime_type,
        extension=extension,
        size_in_bytes=size_in_bytes,
    )
    record_id = file_record.id
    now = file_record.created_at

    record_data = file_record.to_arango_base_record()
    file_data = file_record.to_arango_record()

    permission_edge = {
        "from_id": user_key,
        "from_collection": CollectionNames.USERS.value,
        "to_id": record_id,
        "to_collection": CollectionNames.RECORDS.value,
        "type": "USER",
        "role": "OWNER",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }
    is_of_type_edge = {
        "from_id": record_id,
        "from_collection": CollectionNames.RECORDS.value,
        "to_id": record_id,
        "to_collection": CollectionNames.FILES.value,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }

    await graph_provider.batch_upsert_nodes(
        [record_data], CollectionNames.RECORDS.value,
    )
    await graph_provider.batch_upsert_nodes(
        [file_data], CollectionNames.FILES.value,
    )
    await graph_provider.batch_create_edges(
        [permission_edge], CollectionNames.PERMISSION.value,
    )
    await graph_provider.batch_create_edges(
        [is_of_type_edge], CollectionNames.IS_OF_TYPE.value,
    )
    return file_record


def _format_parsed_blocks_for_prompt_xml(
    *, display_name: str, container_dict: dict[str, Any],
) -> str:
    """Render parsed blocks in the same structural style used for KB records.

    Walks ``container_dict["blocks"]`` in document order and uses
    ``parent_index`` to detect block-group boundaries. Top-level text blocks
    are emitted with ``* Block Index / * Block Type / * Block Content``;
    children of a group are emitted as indented bullets after a group header
    (``* Block Group Index``, ``* Block Group Type``, optional
    ``* Table Summary``). The result is wrapped in an ``<attachment>``
    envelope so the LLM can parse attachments structurally and tell them
    apart from ``<record>`` sections coming from KB retrieval.

    Attachments deliberately have NO Citation IDs — they are not in the
    retrieval index and have no resolvable web URL.
    """
    blocks = container_dict.get("blocks") or []
    block_groups = container_dict.get("block_groups") or []

    groups_by_index: dict[int, dict[str, Any]] = {}
    for grp in block_groups:
        if not isinstance(grp, dict):
            continue
        idx = grp.get("index")
        if idx is not None:
            groups_by_index[idx] = grp

    def _enum_value(raw: Any) -> Any:
        return raw.value if hasattr(raw, "value") else raw

    body_sections: list[str] = []
    current_group_index: int | None = None

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_value = _enum_value(block.get("type"))
        if block_value not in ("text", "table_row"):
            # Skip IMAGE / TABLE_CELL / SQL / etc. — non-textual or already
            # represented through their parent group's natural-language rows.
            continue

        data = block.get("data")
        if block_value == "text":
            if not isinstance(data, str):
                continue
            content = data.strip()
        else:
            if not isinstance(data, dict):
                continue
            content = (data.get("row_natural_language_text") or "").strip()
        if not content:
            continue

        block_index = block.get("index")
        parent_index = block.get("parent_index")
        parent_group = groups_by_index.get(parent_index) if parent_index is not None else None
        # Fall back to top level if the referenced group is missing.
        effective_parent_index = parent_index if parent_group is not None else None

        if effective_parent_index != current_group_index:
            if parent_group is not None:
                grp_type = _enum_value(parent_group.get("type")) or "group"
                header_lines = [
                    f"* Block Group Index: {effective_parent_index}",
                    f"* Block Group Type: {grp_type}",
                ]
                if grp_type == "table":
                    description = (parent_group.get("description") or "").strip()
                    if description:
                        header_lines.append(f"* Table Summary: {description}")
                    header_lines.append("* Table Rows/Blocks:")
                else:
                    header_lines.append("* Block Group Content/Blocks:")
                body_sections.append("\n".join(header_lines))
            current_group_index = effective_parent_index

        if current_group_index is None:
            body_sections.append(
                f"* Block Index: {block_index}\n"
                f"* Block Type: {block_value}\n"
                f"* Block Content: {content}"
            )
        else:
            body_sections.append(
                f"  - Block Index: {block_index}\n"
                f"  - Block Content: {content}"
            )

    if not body_sections:
        return ""

    header = (
        "<attachment>\n"
        f"Name            : {display_name}\n"
        "Source          : User Attachment\n"
        "Type            : FILE\n\n"
        "Attachment blocks (sorted):\n"
    )
    return header + "\n\n".join(body_sections) + "\n</attachment>\n\n"


async def load_chat_attachments(
    *,
    document_ids: list[str],
    org_id: str,
    user_id: str,
    blob_store: BlobStorage,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Fetch each attachment; persist graph file rows; return prompt markdown.

    Returns ``([], {}, prompt_appendix)``. The first two values are unused
    placeholders for an older retrieval-merge API; only the third string should
    be passed into QnA / agent prompts.
    """
    flattened: list[dict[str, Any]] = []
    virtual_map: dict[str, dict[str, Any]] = {}
    prompt_sections: list[str] = []

    org_id = (org_id or "").strip()
    user_id = (user_id or "").strip()
    if not org_id:
        logger.warning("org_id missing; skipping chat attachments")
        return flattened, virtual_map, ""

    deduped: list[str] = []
    seen: set[str] = set()
    for did in document_ids or []:
        if did is None:
            continue
        clean = str(did).strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    if not deduped:
        return flattened, virtual_map, ""

    logger.info(
        "load_chat_attachments: orgId=%s userId=%s docIds=%s",
        org_id, user_id, deduped,
    )

    if not graph_provider:
        logger.warning("Graph provider unavailable; skipping %d attachment(s)", len(deduped))
        return flattened, virtual_map, ""

    user_key: str | None = None
    if user_id:
        user_doc = await graph_provider.get_user_by_user_id(user_id)
        if user_doc:
            raw_key = user_doc.get("_key") or user_doc.get("id")
            if isinstance(raw_key, str):
                user_key = raw_key.strip() or None
            elif raw_key is not None:
                user_key = str(raw_key).strip() or None
    if not user_key:
        logger.warning(
            "Graph user not found or missing _key for userId=%r; loading attachment bytes "
            "for the prompt only (skipping FileRecord persistence — Node already validated "
            "these documentIds for the org).",
            user_id,
        )

    # Imported lazily — the parser module pulls in the full document-parsing
    # stack (PDF/Office/Markdown/HTML), which we should not require at import
    # time for unrelated callers or tests of this loader.
    from app.agents.actions.util.parse_file import FileContentParser

    supported_extensions = FileContentParser.SUPPORTED_EXTENSIONS
    parser = FileContentParser(logger=logger, config_service=config_service)

    for document_id in deduped:
        try:
            metadata = await blob_store.get_raw_document_metadata(document_id, org_id)
            if not metadata:
                logger.warning(
                    "Skipping attachment %s: metadata not found (storage GET "
                    "/api/v1/document/internal/{id} returned None — check Node logs "
                    "for the matching documentId)",
                    document_id,
                )
                continue

            doc_name_raw = metadata.get("documentName") or "document"
            extension_raw = metadata.get("extension") or ""
            mime_type = metadata.get("mimeType") or "application/octet-stream"
            size_in_bytes = int(metadata.get("sizeInBytes") or 0)
            logger.info(
                "Attachment %s metadata: name=%r extension=%r mime=%r size=%d",
                document_id, doc_name_raw, extension_raw, mime_type, size_in_bytes,
            )
            if size_in_bytes and size_in_bytes > MAX_ATTACHMENT_BYTES:
                logger.warning(
                    "Skipping attachment %s: size %d exceeds cap %d",
                    document_id, size_in_bytes, MAX_ATTACHMENT_BYTES,
                )
                continue

            ext = _normalize_extension(extension_raw, doc_name_raw)
            if not ext:
                logger.warning("Skipping attachment %s: no file extension", document_id)
                continue
            if ext not in supported_extensions:
                logger.warning(
                    "Skipping attachment %s: extension .%s not supported by parser",
                    document_id, ext,
                )
                continue

            display_name = doc_name_raw
            if not display_name.lower().endswith(f".{ext}"):
                display_name = f"{doc_name_raw}.{ext}"

            raw_bytes = await blob_store.get_raw_document_bytes(document_id, org_id)
            if not raw_bytes:
                logger.warning("Skipping attachment %s: failed to download bytes", document_id)
                continue
            if len(raw_bytes) > MAX_ATTACHMENT_BYTES:
                logger.warning(
                    "Skipping attachment %s: download size %d exceeds cap %d",
                    document_id, len(raw_bytes), MAX_ATTACHMENT_BYTES,
                )
                continue

            if user_key:
                file_record = await _create_attachment_file_record(
                    graph_provider=graph_provider,
                    user_key=user_key,
                    org_id=org_id,
                    document_id=document_id,
                    file_name=display_name,
                    mime_type=mime_type,
                    extension=ext,
                    size_in_bytes=len(raw_bytes),
                )
            else:
                file_record = _build_file_record_for_chat_upload(
                    org_id=org_id,
                    document_id=document_id,
                    file_name=display_name,
                    mime_type=mime_type,
                    extension=ext,
                    size_in_bytes=len(raw_bytes),
                )

            container = await parser.parse_to_block_container(file_record, raw_bytes)
            container_dict = container.model_dump()

            section = _format_parsed_blocks_for_prompt_xml(
                display_name=display_name,
                container_dict=container_dict,
            )
            if not section:
                logger.info(
                    "Attachment %s parsed but produced no text blocks; skipping",
                    document_id,
                )
                continue

            prompt_sections.append(section)
            logger.info(
                "Loaded chat attachment %s -> recordId=%s (prompt markdown)",
                document_id, file_record.id,
            )
        except Exception as exc:
            logger.exception(
                "Failed to load chat attachment %s: %s", document_id, exc,
            )
            continue

    appendix = "".join(prompt_sections).strip()
    logger.info(
        "load_chat_attachments: %d/%d attachment(s) produced %d prompt char(s)",
        len(prompt_sections), len(deduped), len(appendix),
    )
    return flattened, virtual_map, appendix
