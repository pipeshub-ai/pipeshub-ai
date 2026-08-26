import asyncio
from collections.abc import AsyncGenerator
import base64
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from io import BytesIO

import pdfplumber
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, field_validator

from app.config.constants.ai_models import validate_reasoning_effort
from app.modules.parsers.pdf.pdf_rasterizer import render_all_pages_as_pil_from_bytes_sync
from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor
from app.agents.agent_loop.protocol import AGUIEventType, frame, resolve_protocol
from app.agents.chat_modes import resolve_chat_mode_policy, run_chat_stream
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes, config_node_constants
from app.config.constants.arangodb import CollectionNames, Connectors
from app.containers.query import QueryAppContainer
from app.events.processor import convert_record_dict_to_record
from app.models.blocks import Block, BlockType, BlocksContainer, CitationMetadata, DataFormat
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.modules.retrieval.retrieval_service import RetrievalService
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import get_generator_model_async
from app.utils.attachment_mime_types import (
    DELIMITED_MIME_TYPES,
    DOCX_MIME_TYPES,
    SPREADSHEET_MIME_TYPES,
    SUPPORTED_ATTACHMENT_MIME_TYPES,
    TEXT_ATTACHMENT_MIME_TYPES,
)
from app.utils.streaming import create_sse_event
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DEFAULT_CONTEXT_LENGTH = 128000
logger = logging.getLogger(__name__)
OCR_IMAGE_PAGE_CAP = 30

router = APIRouter()

# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: int | None = 50
    previousConversations: list[dict] = []
    filters: dict[str, Any] | None = None
    retrievalMode: str | None = "HYBRID"
    quickMode: bool | None = False
    # New fields for multi-model support
    modelKey: str | None = None  # e.g., "uuid-of-the-model"
    modelName: str | None = None  # e.g., "gpt-4o-mini", "claude-3-5-sonnet", "llama3.2"
    chatMode: str | None = "internal_search"  # "quick", "analysis", "deep_research", "creative", "precise"
    # "none" | "low" | "medium" | "high" | "max" — forwarded to the LLM factory's
    # reasoning_effort param; absent/None means no explicit override (the LLM
    # factory applies DEFAULT_REASONING_EFFORT for reasoning-capable models).
    reasoningEffort: str | None = None
    mode: str | None = "json"  # "json" for full metadata, "simple" for answer only
    timezone: str | None = None  # IANA timezone id from the client (e.g., "America/New_York")
    currentTime: str | None = None  # ISO 8601 datetime string from the client
    conversationId: str | None = None  # Passed by Node.js layer for background task tracking
    attachments: list[dict[str, Any]] = []
    # AG-UI is the only supported SSE wire protocol. This field is
    # accepted but ignored — `resolve_protocol` always returns "agui".
    protocol: str | None = None
    # Per-request capability toggles for agent mode (both paths).
    # When absent defaults to both enabled, preserving existing behavior.
    agentCapabilities: dict[str, Any] | None = None
    # TEMPORARY token-savings experiment — see `RecordIdShortener` in
    # `utils/chat_helpers.py`. Opt-in and disabled by default: short "R<n>"
    # labels are only valid for the request that minted them, so callers
    # that rely on record ids surviving across turns should leave this off.
    enableRecordIdShortening: bool = False

    _validate_reasoning_effort = field_validator("reasoningEffort")(validate_reasoning_effort)


class AttachmentUploadItem(BaseModel):
    fileName: str
    mimeType: str
    size: int
    contentBase64: str


class AttachmentUploadRequest(BaseModel):
    conversationId: str | None = None
    attachments: list[AttachmentUploadItem]


def _pdf_has_any_ocr_page(file_content: bytes) -> bool:
    with pdfplumber.open(BytesIO(file_content)) as pdf:
        total = len(pdf.pages)
        if total == 0:
            return False
        ocr_count = sum(1 for page in pdf.pages if OCRStrategy.needs_ocr(page, logger))
    return ocr_count / total >= 0.5


def _build_pdf_image_blocks(file_content: bytes) -> BlocksContainer:
    blocks: list[Block] = []
    images = render_all_pages_as_pil_from_bytes_sync(file_content, resolution=144)
    for idx, image in enumerate(images):
        buf = BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('utf-8')}"
        blocks.append(
            Block(
                index=idx,
                type=BlockType.IMAGE,
                format=DataFormat.BASE64,
                data={"uri": data_uri},
                citation_metadata=CitationMetadata(page_number=idx + 1),
            )
        )
    return BlocksContainer(blocks=blocks, block_groups=[])


def _pdf_page_count(file_content: bytes) -> int:
    with pdfplumber.open(BytesIO(file_content)) as pdf:
        return len(pdf.pages)


def _build_image_blocks(file_content: bytes, mime_type: str) -> BlocksContainer:
    mime_lower = mime_type.lower()
    if mime_lower in ("image/jpeg", "image/jpg"):
        media_type = "image/jpeg"
    else:
        media_type = "image/png"
    data_uri = f"data:{media_type};base64,{base64.b64encode(file_content).decode('utf-8')}"
    block = Block(
        index=0,
        type=BlockType.IMAGE,
        format=DataFormat.BASE64,
        data={"uri": data_uri},
        citation_metadata=CitationMetadata(page_number=1),
    )
    return BlocksContainer(blocks=[block], block_groups=[])


async def _build_text_blocks(file_content: bytes) -> BlocksContainer:
    """Parse a plain-text or markdown file into a BlocksContainer using the default MarkdownParser."""
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
    try:
        text = file_content.decode("utf-8")
    except UnicodeDecodeError:
        text = file_content.decode("latin-1")
    parser = MarkdownItParser()
    return await parser.parse_to_blocks(text.strip())


async def _build_docx_blocks(
    file_content: bytes,
    file_name: str,
    config_service: ConfigurationService,
) -> BlocksContainer:
    """Parse a DOCX file into blocks via in-process Docling (same path as FileContentParser)."""
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor

    processor = DoclingProcessor(logger=logger, config=config_service)
    stem = Path(file_name).stem if file_name else "document"
    doc_name = file_name if file_name.lower().endswith(".docx") else f"{stem}.docx"
    doc = await processor.parse_document(doc_name, file_content)
    return await processor.create_blocks(doc)


async def _build_excel_blocks(
    file_content: bytes,
    file_name: str,
    config_service: ConfigurationService,
) -> BlocksContainer:
    """Parse an XLSX file into blocks without LLM enrichment (chat upload path)."""
    from app.modules.parsers.excel.excel_parser import ExcelParser

    parser = ExcelParser(logger=logger, config_service=config_service)
    await asyncio.to_thread(parser.load_workbook_from_binary, file_content)
    return await parser.create_blocks_lightweight(max_rows=_CHAT_ATTACHMENT_MAX_TABLE_ROWS)


async def _build_csv_blocks(
    file_content: bytes,
    file_name: str,
    config_service: ConfigurationService,
    mime_type: str,
) -> BlocksContainer:
    """Parse a CSV/TSV file into blocks without LLM enrichment, capped at max rows."""
    from app.modules.parsers.csv.csv_parser import CSVParser

    delimiter = "\t" if mime_type.lower() == "text/tab-separated-values" else ","
    # Extension-based fallback when MIME is generic but filename says .tsv.
    if delimiter == "," and Path(file_name).suffix.strip().lower() == ".tsv":
        delimiter = "\t"
    parser = CSVParser(config_service=config_service, delimiter=delimiter)
    return await parser.parse_to_blocks_lightweight(
        file_content, max_rows=_CHAT_ATTACHMENT_MAX_TABLE_ROWS
    )


# Dependency injection functions
async def get_retrieval_service(request: Request) -> RetrievalService:
    container: QueryAppContainer = request.app.container
    return await container.retrieval_service()


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    """Get graph provider from app.state or container"""
    if hasattr(request.app.state, 'graph_provider'):
        return request.app.state.graph_provider
    container: QueryAppContainer = request.app.container
    return await container.graph_provider()


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    return container.config_service()


async def get_model_config(config_service: ConfigurationService, model_key: str | None = None, model_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Get model configuration based on user selection or fallback to default

    Returns:
        Tuple of (model_config, ai_models_config) where:
        - model_config: The specific LLM configuration for the selected model
        - ai_models_config: The full AI models configuration object
    """

    def _find_config_by_default(configs: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find config marked as default"""
        return next((config for config in configs if config.get("isDefault", False)), None)

    def _find_config_by_model_name(configs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        """Find config by model name in configuration.model field"""
        for config in configs:
            model_string = config.get("configuration", {}).get("model", "")
            model_names = [n.strip() for n in model_string.split(",") if n.strip()]
            if name in model_names:
                return config
        return None

    def _find_config_by_key(configs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        """Find config by modelKey"""
        return next((config for config in configs if config.get("modelKey") == key), None)

    # Get initial config
    ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value)
    llm_configs = ai_models["llm"]

    # Search based on provided parameters
    if model_key is None and model_name is None:
        # Return default config
        if default_config := _find_config_by_default(llm_configs):
            return default_config, ai_models
    elif model_key is None and model_name is not None:
        # Search by model name
        if name_config := _find_config_by_model_name(llm_configs, model_name):
            return name_config, ai_models
    elif model_key is not None:
        # Search by model key
        if key_config := _find_config_by_key(llm_configs, model_key):
            return key_config, ai_models

    # Try fresh config if not found (only for model_key searches)
    if model_key is not None:
        new_ai_models = await config_service.get_config(
            config_node_constants.AI_MODELS.value,
            use_cache=False
        )
        llm_configs = new_ai_models["llm"]
        if key_config := _find_config_by_key(llm_configs, model_key):
            return key_config, new_ai_models

    if not llm_configs:
        raise ValueError("No LLM configurations found")

    return llm_configs, ai_models

async def get_llm_for_chat(
    config_service: ConfigurationService,
    model_key: str = None,
    model_name: str = None,
    chat_mode: str = "internal_search",
    reasoning_effort: str | None = None,
) -> tuple[BaseChatModel, dict, dict]:
    """Get LLM instance based on user selection or fallback to default

    Returns:
        Tuple of (llm, model_config, ai_models_config) where:
        - llm: The initialized LLM instance
        - model_config: The specific LLM configuration for the selected model
        - ai_models_config: The full AI models configuration object
    """
    try:
        llm_config, ai_models_config = await get_model_config(config_service, model_key, model_name)
        if not llm_config:
            raise ValueError("No LLM configurations found")

        # Handle list of configs - extract first one if we got a list
        if isinstance(llm_config, list):
            llm_config = llm_config[0]

        # If user specified a model, try to find it
        if model_key and model_name:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            if (llm_config.get("modelKey") == model_key and model_name in model_names):
                model_provider = llm_config.get("provider")
                llm = await get_generator_model_async(
                    model_provider, llm_config, model_name, reasoning_effort
                )
                return llm, llm_config, ai_models_config

        # If user specified only provider, find first matching model
        if model_key:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            default_model_name = model_names[0]
            model_provider = llm_config.get("provider")
            llm = await get_generator_model_async(
                model_provider, llm_config, default_model_name, reasoning_effort
            )
            return llm, llm_config, ai_models_config

        # Fallback to first available model
        model_string = llm_config.get("configuration", {}).get("model")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]
        default_model_name = model_names[0]
        model_provider = llm_config.get("provider")
        llm = await get_generator_model_async(
            model_provider, llm_config, default_model_name, reasoning_effort
        )
        return llm, llm_config, ai_models_config
    except Exception as e:
        raise ValueError(f"Failed to initialize LLM: {str(e)}")


# Cap tabular chat-attachment context so large CSV/XLSX files don't blow the LLM window.
_CHAT_ATTACHMENT_MAX_TABLE_ROWS = 500


def _is_supported_attachment_mime(mime_type: str) -> bool:
    return mime_type.lower() in SUPPORTED_ATTACHMENT_MIME_TYPES


def _is_image_attachment(mime_type: str) -> bool:
    return mime_type.lower().startswith("image/")


def _is_text_attachment(mime_type: str) -> bool:
    return mime_type.lower() in TEXT_ATTACHMENT_MIME_TYPES


def _is_docx_attachment(mime_type: str) -> bool:
    return mime_type.lower() in DOCX_MIME_TYPES


def _is_spreadsheet_attachment(mime_type: str) -> bool:
    return mime_type.lower() in SPREADSHEET_MIME_TYPES


def _is_delimited_attachment(mime_type: str) -> bool:
    return mime_type.lower() in DELIMITED_MIME_TYPES


def _attachment_extension(file_name: str, mime_type: str) -> str:
    suffix = Path(file_name).suffix.strip().lower()
    if suffix:
        return suffix.lstrip(".")
    mime_lower = mime_type.lower()
    if mime_lower == "application/pdf":
        return "pdf"
    if mime_lower in ("image/jpeg", "image/jpg"):
        return "jpg"
    if mime_lower == "image/png":
        return "png"
    if mime_lower == "text/plain":
        return "txt"
    if mime_lower == "text/markdown":
        return "md"
    if mime_lower == "text/mdx":
        return "mdx"
    if mime_lower in DOCX_MIME_TYPES:
        return "docx"
    if mime_lower in SPREADSHEET_MIME_TYPES:
        return "xlsx"
    if mime_lower == "text/csv":
        return "csv"
    if mime_lower == "text/tab-separated-values":
        return "tsv"
    return "bin"







class _AttachmentSinkNoopVectorStore:
    """Vector store shim for attachment upload sink-only pipeline (skipped by SinkOrchestrator)."""

    async def apply(self, ctx: TransformContext) -> bool:
        return True


@router.post("/chat/attachments/upload", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def upload_chat_attachments(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        payload = AttachmentUploadRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid attachment upload payload: {str(e)}")

    user = request.state.user or {}
    org_id = user.get("orgId")
    user_id = user.get("userId")
    is_service_account = user.get("isServiceAccount")

    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org context for attachment upload")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user context for attachment upload")

    if not payload.attachments:
        raise HTTPException(status_code=400, detail="No attachments provided")

    # Resolve the auth `userId` to the User node's internal key so that
    # permission edges MATCH the User node (graph providers key User nodes
    # by `_key`/`id`, not by the auth `userId`). Service-account callers
    # have no User node and skip permission edges, so the lookup is skipped.
    user_key: str | None = None
    if not is_service_account:
        user_doc = await graph_provider.get_user_by_user_id(user_id)
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found for attachment upload")
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            raise HTTPException(status_code=500, detail="Resolved user is missing key/id")

    now = get_epoch_timestamp_in_ms()
    uploaded_refs: list[dict[str, Any]] = []
    record_docs: list[dict[str, Any]] = []
    file_docs: list[dict[str, Any]] = []
    parsed_blocks_by_record: dict[str, BlocksContainer] = {}
    ocr_image_pages_used = 0

    container: QueryAppContainer = request.app.container
    service_logger = container.logger()
    graphdb = GraphDBTransformer(graph_provider=graph_provider, logger=service_logger)
    blob_storage = BlobStorage(logger=service_logger, config_service=config_service, graph_provider=graph_provider)
    pdf_processor = PDFPlumberOpenCVProcessor(logger=logger, config=config_service)

    for item in payload.attachments:
        if not _is_supported_attachment_mime(item.mimeType):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported attachment type '{item.mimeType}': {item.fileName}. "
                    "Supported: PDF, JPEG, PNG, TXT, MD, MDX, DOCX, XLSX, CSV, TSV."
                ),
            )
        if item.size <= 0:
            raise HTTPException(status_code=400, detail=f"Attachment size must be positive: {item.fileName}")

        record_id = str(uuid4())
        virtual_record_id = str(uuid4())
        extension = _attachment_extension(item.fileName, item.mimeType)
        is_image = _is_image_attachment(item.mimeType)
        is_text = _is_text_attachment(item.mimeType)
        is_docx = _is_docx_attachment(item.mimeType)
        is_spreadsheet = _is_spreadsheet_attachment(item.mimeType)
        is_delimited = _is_delimited_attachment(item.mimeType)

        try:
            file_binary = base64.b64decode(item.contentBase64, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content for attachment: {item.fileName}")

        storage_doc_id, _ = await blob_storage.save_binary_to_storage(
            org_id=org_id,
            record_id=record_id,
            file_name=item.fileName,
            extension=extension,
            content_type=item.mimeType,
            binary_data=file_binary,
        )
        external_record_id = storage_doc_id if storage_doc_id else record_id

        record_doc = {
            "_key": record_id,
            "id": record_id,
            "orgId": org_id,
            "recordName": item.fileName,
            "externalRecordId": external_record_id,
            "recordType": "FILE",
            "origin": "UPLOAD",
            "connectorId": f"attachments_{org_id}",
            "connectorName": Connectors.ATTACHMENTS.value,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
            "sourceCreatedAtTimestamp": now,
            "sourceLastModifiedTimestamp": now,
            "isDeleted": False,
            "isArchived": False,
            "indexingStatus": "NOT_STARTED",
            "extractionStatus": "NOT_STARTED",
            "version": 1,
            "mimeType": item.mimeType,
            "sizeInBytes": item.size,
            "virtualRecordId": virtual_record_id,
        }

        needs_ocr = False
        parse_mode = "pdfplumber"
        if is_image:
            try:
                block_containers = _build_image_blocks(file_binary, item.mimeType)
                parsed_blocks_by_record[record_id] = block_containers
                parse_mode = "image_direct"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to process image attachment {item.fileName}: {str(e)}")
        elif is_text:
            try:
                block_containers = await _build_text_blocks(file_binary)
                parsed_blocks_by_record[record_id] = block_containers
                parse_mode = "text"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse text attachment {item.fileName}: {str(e)}")
        elif is_docx:
            try:
                block_containers = await _build_docx_blocks(file_binary, item.fileName, config_service)
                parsed_blocks_by_record[record_id] = block_containers
                parse_mode = "docling"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse DOCX attachment {item.fileName}: {str(e)}")
        elif is_spreadsheet:
            try:
                block_containers = await _build_excel_blocks(file_binary, item.fileName, config_service)
                parsed_blocks_by_record[record_id] = block_containers
                parse_mode = "excel_lightweight"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse Excel attachment {item.fileName}: {str(e)}")
        elif is_delimited:
            try:
                block_containers = await _build_csv_blocks(
                    file_binary, item.fileName, config_service, item.mimeType
                )
                parsed_blocks_by_record[record_id] = block_containers
                parse_mode = "csv_lightweight"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse CSV attachment {item.fileName}: {str(e)}")
        else:
            try:
                needs_ocr = await asyncio.to_thread(_pdf_has_any_ocr_page, file_binary)
                if needs_ocr:
                    page_count = await asyncio.to_thread(_pdf_page_count, file_binary)
                    if ocr_image_pages_used + page_count > OCR_IMAGE_PAGE_CAP:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Scanned attachment page cap exceeded. "
                                f"Maximum allowed combined scanned pages is {OCR_IMAGE_PAGE_CAP}."
                            ),
                        )
                    block_containers = await asyncio.to_thread(
                        _build_pdf_image_blocks, file_binary
                    )
                    ocr_image_pages_used += page_count
                    parse_mode = "image_direct"
                else:
                    parsed_data = await pdf_processor.parse_document(item.fileName, file_binary)
                    block_containers = await pdf_processor.create_blocks(parsed_data, skip_llm_enrichment=True)
                    parse_mode = "pdfplumber"
                parsed_blocks_by_record[record_id] = block_containers
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse attachment {item.fileName}: {str(e)}")
        record_doc["isVLMOcrProcessed"] = needs_ocr
        file_doc = {
            "_key": record_id,
            "id": record_id,
            "orgId": org_id,
            "name": item.fileName,
            "isFile": True,
            "extension": extension,
            "mimeType": item.mimeType,
            "sizeInBytes": item.size,
        }
        record_docs.append(record_doc)
        file_docs.append(file_doc)
        uploaded_refs.append(
            {
                "recordId": record_id,
                "recordName": item.fileName,
                "mimeType": item.mimeType,
                "extension": extension,
                "virtualRecordId": record_doc.get("virtualRecordId", virtual_record_id),
                "parseMode": parse_mode,
                # Deprecated: kept for backward compatibility with clients still
                # reading `ocrMode`. `parse_mode` covers non-OCR parse paths too
                # (e.g. csv_lightweight, docling) so `parseMode` is the canonical field.
                "ocrMode": parse_mode,
            }
        )

    await graph_provider.batch_upsert_nodes(record_docs, CollectionNames.RECORDS.value)
    await graph_provider.batch_upsert_nodes(file_docs, CollectionNames.FILES.value)

    ts = get_epoch_timestamp_in_ms()
    is_of_type_edges = [
        {
            "_from": f"{CollectionNames.RECORDS.value}/{rd['_key']}",
            "_to": f"{CollectionNames.FILES.value}/{rd['_key']}",
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        for rd in record_docs
    ]
    await graph_provider.batch_create_edges(is_of_type_edges, CollectionNames.IS_OF_TYPE.value)
    if is_service_account:
        # Service accounts have no user graph node, so no USER -> RECORD edge
        # can be created. Grant an org-scoped permission edge instead so the
        # uploaded file is readable org-wide through the standard ACL path
        # (orgAccessPermissionEdge in check_record_access_with_details).
        permission_edges = [
            {
                "from_id": org_id,
                "from_collection": CollectionNames.ORGS.value,
                "to_id": rd["_key"],
                "to_collection": CollectionNames.RECORDS.value,
                "type": "ORGANIZATION",
                "role": "READER",
                "createdAtTimestamp": ts,
                "updatedAtTimestamp": ts,
            }
            for rd in record_docs
        ]
        await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value)
    else:
        permission_edges = [
            {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": rd["_key"],
                "to_collection": CollectionNames.RECORDS.value,
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": ts,
                "updatedAtTimestamp": ts,
            }
            for rd in record_docs
        ]
        await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value)

    sink_orchestrator = SinkOrchestrator(
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=_AttachmentSinkNoopVectorStore(),
        graph_provider=graph_provider,
        logger=service_logger,
    )

    for record_doc in record_docs:
        record_id = record_doc.get("_key") or record_doc.get("id")
        block_containers = parsed_blocks_by_record.get(record_id)
        if block_containers is None:
            continue

        record = convert_record_dict_to_record(record_doc)
        record.block_containers = block_containers
        record.virtual_record_id = record_doc.get("virtualRecordId")
        ctx = TransformContext(
            record=record,
            settings={"sink_only": True, "skip_vector_store": True},
        )
        await sink_orchestrator.index(ctx)

    return {
        "conversationId": payload.conversationId,
        "attachments": uploaded_refs,
    }


class AttachmentPermissionRequest(BaseModel):
    userIds: list[str]
    recordIds: list[str]


@router.post("/chat/attachments/permissions", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def grant_attachment_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """Grant READER permission edges on chat attachment records to the specified users."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        payload = AttachmentPermissionRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request payload: {str(e)}")

    if not payload.userIds or not payload.recordIds:
        return {"granted": 0}

    ts = get_epoch_timestamp_in_ms()
    edges: list[dict[str, Any]] = []

    for user_id in payload.userIds:
        user_doc = await graph_provider.get_user_by_user_id(user_id)
        if not user_doc:
            logger.warning("User not found for permission grant, skipping: %s", user_id)
            continue
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            logger.warning("Resolved user missing _key/id, skipping: %s", user_id)
            continue
        for record_id in payload.recordIds:
            edges.append(
                {
                    "from_id": user_key,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": record_id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "type": "USER",
                    "role": "READER",
                    "createdAtTimestamp": ts,
                    "updatedAtTimestamp": ts,
                }
            )

    if edges:
        await graph_provider.batch_create_edges(edges, CollectionNames.PERMISSION.value)

    return {"granted": len(edges)}


@router.delete("/chat/attachments/permissions", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def revoke_attachment_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """Remove READER permission edges on chat attachment records for the specified users."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        payload = AttachmentPermissionRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request payload: {str(e)}")

    if not payload.userIds or not payload.recordIds:
        return {"revoked": 0}

    edges: list[dict[str, Any]] = []

    for user_id in payload.userIds:
        user_doc = await graph_provider.get_user_by_user_id(user_id)
        if not user_doc:
            logger.warning("User not found for permission revoke, skipping: %s", user_id)
            continue
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            logger.warning("Resolved user missing _key/id, skipping: %s", user_id)
            continue
        for record_id in payload.recordIds:
            edges.append(
                {
                    "from_id": user_key,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": record_id,
                    "to_collection": CollectionNames.RECORDS.value,
                }
            )

    if edges:
        await graph_provider.batch_delete_edges(edges, CollectionNames.PERMISSION.value)

    return {"revoked": len(edges)}


@router.delete(
    "/chat/attachments/{record_id}",
    status_code=204,
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))],
)
@inject
async def delete_chat_attachment(
    record_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> None:
    """Delete a previously uploaded chat attachment.

    Called fire-and-forget from the frontend when the user removes an attachment
    chip after its upload has completed.  Errors are intentionally swallowed on
    the client side so the UI is never blocked, but we still clean up graph
    nodes and edges here on a best-effort basis.

    The attachment created by ``upload_chat_attachments`` consists of:
    - A RECORDS node  (key == record_id)
    - A FILES node    (key == record_id, same key by convention)
    - IS_OF_TYPE edge  RECORDS/record_id -> FILES/record_id
    - PERMISSION edge(s)  USERS/… -> RECORDS/record_id

    ``delete_nodes_and_edges`` removes the RECORDS node plus all edges that
    reference it; a separate ``delete_nodes`` call removes the FILES node.
    """
    user = request.state.user or {}
    org_id = user.get("orgId")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org context")

    # Verify the record belongs to this org before deleting.
    record = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value)
    if not record:
        # Already gone — treat as success so the client stays consistent.
        return
    if record.get("orgId") != org_id:
        raise HTTPException(status_code=403, detail="Attachment does not belong to this organisation")

    # Remove the RECORDS node and all its incident edges
    # (IS_OF_TYPE to FILES, PERMISSION edges to the record).
    await graph_provider.delete_nodes_and_edges(
        [record_id], CollectionNames.RECORDS.value
    )

    # Remove the associated FILES node (same key, independent collection).
    await graph_provider.delete_nodes(
        [record_id], CollectionNames.FILES.value
    )


async def _generate_chat_stream_via_agent_loop(
    request: Request,
    query_info: "ChatQuery",
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
) -> AsyncGenerator[str, None]:
    """Adapts a validated `ChatQuery` + the authenticated request into the
    plain-dict `query_info`/`user_info` contract `chat_modes.run_chat_stream()`
    expects (see that module's docstring for why it never sees `ChatQuery`
    or `Request` directly -- avoids both a circular import and coupling the
    agents layer to FastAPI), then streams straight from it.

    Tool calling is left on for every provider, including Ollama: without it,
    `internal_search` can never reach the `fetch_full_record` tool the agent
    loop registers mid-run, so full-record retrieval would silently degrade
    to snippet-only answers. Models that genuinely can't bind tools surface
    that as a normal LLM-call error from within the agent loop, same as any
    other provider misconfiguration."""
    container = request.app.container
    logger_ = container.logger()
    user = getattr(request.state, "user", {}) or {}
    org_id = user.get("orgId")
    user_id = user.get("userId")
    protocol = resolve_protocol(query_info.protocol, request)

    try:
        llm_bundle = await get_llm_for_chat(
            config_service, query_info.modelKey, query_info.modelName, query_info.chatMode,
            reasoning_effort=query_info.reasoningEffort,
        )
        if not llm_bundle or llm_bundle[0] is None:
            raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")
        llm, model_config, ai_models_config = llm_bundle
    except Exception as exc:
        logger_.error(f"Error initializing LLM for chat: {exc}", exc_info=True)
        if protocol == "agui":
            evt = frame(AGUIEventType.RUN_ERROR, message=str(exc), code="llm_initialization_failed")
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        else:
            yield create_sse_event("error", {"error": str(exc)})
        return

    # Fetch system prompts from dedicated key; fall back to legacy aiModels blob for OSS
    # deployments that haven't migrated yet.
    system_prompts_config: dict[str, Any] = {}
    try:
        sp_raw = await config_service.get_config(config_node_constants.SYSTEM_PROMPTS.value)
        if sp_raw:
            system_prompts_config = sp_raw
        else:
            ai_raw = await config_service.get_config(config_node_constants.AI_MODELS.value)
            if ai_raw:
                system_prompts_config = {
                    k: ai_raw[k] for k in ("customSystemPrompt", "customSystemPromptWebSearch", "customSystemPromptAgent")
                    if k in ai_raw
                }
    except Exception:
        logger_.debug("Could not load system prompts config", exc_info=True)

    policy = resolve_chat_mode_policy(query_info.chatMode)
    is_multimodal_llm = bool(model_config.get("isMultimodal"))
    context_length = model_config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

    # When the request carries agentCapabilities and we're in agent mode,
    # build a capabilities-aware policy instead of the static AGENT_POLICY.
    chat_mode_str = (query_info.chatMode or "").strip().lower()
    if chat_mode_str == "agent" or chat_mode_str.startswith("agent:"):
        raw_caps = query_info.agentCapabilities
        if raw_caps and isinstance(raw_caps, dict):
            caps = AgentCapabilities(
                internal_search=bool(raw_caps.get("internalSearch", True)),
                web_search=bool(raw_caps.get("webSearch", True)),
                deep_search=bool(raw_caps.get("deepSearch", False)),
            )
            policy = resolve_agent_policy(caps)

    query_dict = {
        "query": query_info.query,
        "limit": query_info.limit,
        "previous_conversations": query_info.previousConversations,
        "filters": query_info.filters,
        "retrievalMode": query_info.retrievalMode,
        "quickMode": query_info.quickMode,
        "chatMode": query_info.chatMode,
        "timezone": query_info.timezone,
        "currentTime": query_info.currentTime,
        "conversationId": query_info.conversationId,
        "attachments": query_info.attachments,
        "enableRecordIdShortening": query_info.enableRecordIdShortening,
    }
    user_info = {
        "userId": user_id,
        "orgId": org_id,
        "userEmail": user.get("email") or "",
        "sendUserInfo": request.query_params.get("sendUserInfo", True),
    }

    org_info: dict[str, Any] | None = None
    try:
        user_doc = await graph_provider.get_user_by_user_id(user_id) if user_id else None
        if user_doc and isinstance(user_doc, dict):
            for field in ("fullName", "firstName", "lastName", "displayName"):
                if user_doc.get(field):
                    user_info[field] = user_doc[field]
        if org_id:
            org_doc = await graph_provider.get_document(org_id, CollectionNames.ORGS.value)
            if org_doc and isinstance(org_doc, dict):
                raw_account_type = str(org_doc.get("accountType", "")).lower()
                org_info = {
                    "orgId": org_id,
                    "accountType": raw_account_type if raw_account_type in ("enterprise", "individual") else "",
                    "name": org_doc.get("name") or "",
                }
    except Exception:
        logger_.debug("Failed to enrich user/org context for prompt", exc_info=True)

    client_name = request.headers.get("client-name")

    async for event in run_chat_stream(
        query_dict, user_info, llm, policy, logger_,
        retrieval_service=retrieval_service, graph_provider=graph_provider,
        reranker_service=None, config_service=config_service,
        org_info=org_info,
        model_name=query_info.modelName, model_key=query_info.modelKey,
        is_multimodal_llm=is_multimodal_llm, context_length=context_length,
        llm_provider=model_config.get("provider") or "",
        system_prompts_config=system_prompts_config, protocol=protocol,
        client_name=client_name,
    ):
        yield event


@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support.

    Every mode (`internal_search`, `web_search`, `agent`) routes through the
    agent loop (`app.agents.chat_modes.run_chat_stream`) — see that package
    for the mode → tool/prefetch behavior.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        query_info = ChatQuery(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request parameters: {str(e)}")


    _chat_user = getattr(request.state, "user", {}) or {}
    _chat_email = _chat_user.get("email")
    _search_type = "web_search" if query_info.chatMode == "web_search" else "internal_search"
    record_event("chat_session_started", {
        "orgId": _chat_user.get("orgId"),
        "userId": _chat_user.get("userId"),
        "email": _chat_email,
        "domain": domain_from_email(_chat_email),
        "mode": query_info.chatMode,
        "search_type": _search_type,
    })

    stream = _generate_chat_stream_via_agent_loop(
        request=request,
        query_info=query_info,
        retrieval_service=retrieval_service,
        graph_provider=graph_provider,
        config_service=config_service,
    )

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control",
        },
    )
