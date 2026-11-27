import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator, Mapping
from concurrent.futures.process import BrokenProcessPool
from contextlib import aclosing
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from uuid import uuid4

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.agents.agent_loop.cancellation.registry import (
    RunCancellationRegistry,
    RunOwner,
)
from app.agents.agent_loop.cancellation.validation import validate_run_id
from app.agents.agent_loop.error_classification import classify_exception
from app.agents.agent_loop.protocol import AGUIEventType, frame, resolve_protocol
from app.agents.agent_loop.protocol.stream_collector import collect_stream_outcome
from app.agents.agent_loop.skills.graph_store import (
    erase_session_learned_skills,
    erase_session_skill_candidates,
)
from app.agents.agent_loop.stream_bridge import run_chat_stream
from app.agents.agent_loop.turn_log import log_turn_end
from app.agents.chat_modes import resolve_chat_mode_policy
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.agents.chat_modes.setup import enrich_chat_identity
from app.api.middlewares.auth import (
    connection_limit,
    require_scopes,
    require_service_token,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import validate_reasoning_effort
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.service import OAuthScopes, TokenScopes, config_node_constants
from app.containers.query import QueryAppContainer
from app.core.org_context import bound_org_id
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.harness.legacy.errors import ResolvedTurnRefused
from app.harness.legacy.resolved_request import resolved_turn_for, with_resolved_stages
from app.harness.legacy.resolved_turn import ResolvedTurn
from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
)
from app.models.record_convert import convert_record_dict_to_record
from app.modules.parsers import parse_pool
from app.modules.parsers.parse_pool import ParseWorkerError
from app.modules.parsers.parse_pool_lanes import interactive_parsing
from app.modules.parsers.pdf.pdf_inspection import needs_ocr_and_page_count
from app.modules.parsers.pdf.pdf_rasterizer import (
    render_all_pages_as_png,
)
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.modules.retrieval.filter_schema import validate_request_filters
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import (
    IMAGE_DESCRIPTION_BUDGET_S,
    TransformContext,
)
from app.schedules.firing import scheduled_turn
from app.services.artifact_registry.access import is_direct_share, reader_edge
from app.services.artifact_registry.copies import (
    COPY_LIMIT,
    ArtifactCopier,
    CopyRequest,
)
from app.services.artifact_registry.erasure import ArtifactEraser
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.artifact_registry.viewer_access import artifact_viewer_access
from app.services.cache.invalidation_hooks import notify_principals_changed
from app.services.chat_attachments.erasure import ERASE_PAGE as ATTACHMENT_ERASE_PAGE
from app.services.chat_attachments.erasure import (
    AttachmentEraser,
    AttachmentRef,
    list_unsent,
)
from app.services.chat_attachments.uploads import (
    AttachmentFile,
    Uploader,
    UploaderKind,
    attachment_documents,
    attachment_record_doc,  # noqa: F401 - the upload route's record, read by its tests here
    mark_sent,
    uploads_tracked,
    write_attachment_graph,
)
from app.services.featureflag.platform_settings import is_user_context_enabled
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.model_identity.boundary import (
    UNATTRIBUTED_SERVICE,
    principal_from_request_user,
)
from app.services.model_identity.principal import Principal, service_principal
from app.services.model_identity.runtime import assert_identity_allowed, call_scope
from app.services.model_identity.workload import Workload
from app.services.opik_traces import SAFE_ID_PATTERN as OPIK_SAFE_ID_PATTERN
from app.services.opik_traces import erase_conversation_traces, opik_trace_store
from app.services.org_erasure import ORG_ERASE_PAGE, erase_org_page
from app.services.parsing.interface import ParseError
from app.services.resource_governor.kinds import ParseKind
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.attachment_mime_types import (
    DELIMITED_MIME_TYPES,
    DOCX_MIME_TYPES,
    SPREADSHEET_MIME_TYPES,
    SUPPORTED_ATTACHMENT_MIME_TYPES,
    TEXT_ATTACHMENT_MIME_TYPES,
)
from app.utils.attachment_utils import is_chat_attachment, keep_accessible_attachments
from app.utils.chat_helpers import accessible_node_ids
from app.utils.concurrency import gather_with_concurrency
from app.utils.entity_vector_store import (  # noqa: F401 - warm_up is imported from here by query_main
    load_entity_vector_store,
    warm_up_entity_vector_store,
)
from app.utils.image_utils import downscale_to_limits
from app.utils.llm import (
    LLM_MISSING_FOR_CHAT,
    LLMNotConfiguredError,
    LLMUnavailableError,
    build_chat_model,
)
from app.utils.streaming import create_sse_event
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_errors import EXCEL_TOO_LARGE
from app.utils.user_lookup import (
    await_or_503,
    get_caller_user,
    get_document_or_503,
    get_user_or_503,
)
from app.utils.validation_messages import validation_error_text

DEFAULT_CONTEXT_LENGTH = 128000
logger = logging.getLogger(__name__)
OCR_IMAGE_PAGE_CAP = 30
# Pages of one PDF attached to a chat message. This bounds the memory one upload can use:
# the layout parse holds every page it reads. It is not a promise about time: on a busy
# host a file near the cap can still run past _CHAT_ATTACHMENT_UPLOAD_BUDGET_S, and the
# user is then told it took too long (_attachment_too_slow).
CHAT_PDF_PAGE_CAP = 50

router = APIRouter()

# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: int | None = 50
    previousConversations: list[dict] = []
    # What the conversation so far read (FG2): its messages' labels joined by Node; absent, nobody vouches for it.
    historyLabel: dict[str, Any] | None = None
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
    # Author-set instructions from the Project this conversation is linked
    # to (Node `ProjectService.buildContext`). Additive — rendered as its
    # own prompt section, never merged into system_prompt/instructions.
    projectInstructions: str | None = Field(default=None, max_length=8000)
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
    # Opt-in measurement surface for evaluation clients: streams CUSTOM
    # `retrieval_context` frames naming the records and blocks that reached
    # the model, plus a `run_usage` frame. No effect on the answer.
    includeRetrievalContext: bool = False
    # "short": a search waits at most `KEYWORD_SEARCH_SHORT_WAIT_SECS` for the
    # keyword (grep) pattern match once its semantic results are in, and keeps
    # what it found by then. For callers that would rather answer sooner, such
    # as agents calling chat over MCP. Ignored under HIGH_ACCURACY_MODE.
    # Absent or "full": waits for all of it, as before.
    keywordSearchBudget: Literal["short", "full"] | None = None
    # Stop Generation: client-generated UUID identifying this run, so a
    # later `POST /chat/cancel {runId}` can target it. Absent for callers
    # that predate this field or don't need cancellation (the agent loop
    # generates one itself — see `stream_bridge.py`).
    runId: str | None = None
    # Set by Node for a project-scoped chat (see `applyProjectScope`,
    # project-context.ts). When true and the effective `filters` carry no
    # apps/kb, `get_accessible_virtual_record_ids` returns no records instead
    # of falling back to "search everything the user can access" — an empty
    # project scope must stay empty, never widen. Threaded into `filters`
    # below rather than passed as a separate retrieval parameter.
    strictScope: bool = False
    # Set by Node for a project-scoped chat whose turn selects below app level:
    # the project's own sources (`{"apps": [...]}`), which the selected record
    # groups, folders and records must lie in. Node builds it from the project and
    # never copies it from the client's request. It only narrows the turn: a body
    # that names other sources, or leaves it out, reaches no record beyond the
    # caller's own access, as in a chat outside any project.
    allowedFilters: dict[str, Any] | None = None

    _validate_reasoning_effort = field_validator("reasoningEffort")(validate_reasoning_effort)
    _validate_filters = field_validator("filters")(validate_request_filters)
    _validate_run_id = field_validator("runId")(validate_run_id)


class CancelRunRequest(BaseModel):
    """Body of `POST /chat/cancel`. One endpoint for both assistant
    (`/chat/stream`) and agent (`/{agent_id}/chat/stream`) runs — the
    registry is keyed by `runId` alone, not by which route created it.

    `conversationId` is Node's already-ownership-checked path param,
    forwarded so the registry can reject a `runId` that is real and owned
    by this same user/org but was registered under a DIFFERENT
    conversation (see `RunOwner.conversation_id`). Optional only so an
    older/rolling-deploy Node build without this field still gets the
    pre-existing user/org check rather than a hard 400.
    """

    runId: str
    conversationId: str | None = None

    _validate_run_id = field_validator("runId")(validate_run_id)


class AttachmentUploadItem(BaseModel):
    fileName: str
    mimeType: str
    size: int
    contentBase64: str


class AttachmentUploadRequest(BaseModel):
    conversationId: str | None = None
    attachments: list[AttachmentUploadItem]


_BodyT = TypeVar("_BodyT", bound=BaseModel)


async def _parse_body(request: Request, model: type[_BodyT], problem: str) -> _BodyT:
    """The JSON body as ``model``, or a 400 naming each refused field and rule, never the value sent."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"{problem}: {validation_error_text(e)}") from e


def _build_pdf_image_blocks(png_pages: list[bytes]) -> BlocksContainer:
    blocks: list[Block] = []
    for idx, png_bytes in enumerate(png_pages):
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


# Limits for the copy of an uploaded image kept in its parsed record, which is
# what the model reads and what every later turn replays. The stored file stays
# as uploaded; the model's own per-image limits still apply when it is sent.
_RECORD_IMAGE_MAX_LONG_EDGE_PX = 2048
_RECORD_IMAGE_MAX_BYTES = 1024 * 1024


def _build_image_blocks(file_content: bytes, mime_type: str) -> BlocksContainer:
    mime_lower = mime_type.lower()
    if mime_lower in ("image/jpeg", "image/jpg"):
        media_type = "image/jpeg"
    else:
        media_type = "image/png"
    data_uri = downscale_to_limits(
        f"data:{media_type};base64,{base64.b64encode(file_content).decode('utf-8')}",
        max_long_edge_px=_RECORD_IMAGE_MAX_LONG_EDGE_PX,
        max_bytes=_RECORD_IMAGE_MAX_BYTES,
    )
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
    from app.modules.parsers.text_decoding import decode_text
    parser = MarkdownItParser()
    return await parser.parse_to_blocks(decode_text(file_content).strip())


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


async def get_run_cancellation_registry(request: Request) -> RunCancellationRegistry:
    container: QueryAppContainer = request.app.container
    return container.run_cancellation_registry()


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
    ai_models = await config_service.get_config(
        config_node_constants.AI_MODELS.value, use_cache=True,
    ) or {}
    llm_configs = ai_models.get("llm") or []

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
        ) or {}
        llm_configs = new_ai_models.get("llm") or []
        if key_config := _find_config_by_key(llm_configs, model_key):
            return key_config, new_ai_models

    if not llm_configs:
        raise LLMNotConfiguredError(LLM_MISSING_FOR_CHAT)

    return llm_configs, ai_models

def chat_model_name(llm_config: dict[str, Any], model_key: str | None, model_name: str | None) -> str:
    """The model name a chat request runs on: the one asked for when the selected entry is that
    key and lists it, otherwise the entry's first name."""
    model_string = llm_config.get("configuration", {}).get("model")
    model_names = [name.strip() for name in model_string.split(",") if name.strip()]
    if model_key and model_name and llm_config.get("modelKey") == model_key and model_name in model_names:
        return model_name
    return model_names[0]


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
            raise LLMNotConfiguredError(LLM_MISSING_FOR_CHAT)

        # Handle list of configs - extract first one if we got a list
        if isinstance(llm_config, list):
            llm_config = llm_config[0]

        model_provider = llm_config.get("provider")
        llm = await build_chat_model(
            config_service, model_provider, llm_config, chat_model_name(llm_config, model_key, model_name),
            reasoning_effort,
        )
        return llm, llm_config, ai_models_config
    except (LLMNotConfiguredError, LLMUnavailableError):
        # Already says what to do; the "Failed to initialize" prefix would only bury it.
        raise
    except Exception as e:
        raise ValueError(f"Failed to initialize LLM: {str(e)}")


# Shown when the chat model fails to start for a reason the classifier doesn't recognise.
CHAT_MODEL_START_FAILED = (
    "The selected AI model couldn't be started. Try another model, or ask a "
    "workspace admin to check it in Workspace → AI Models."
)


_ATTACHMENT_UNREADABLE_HINTS = {
    "image": "The image may be damaged or in a format we can't open. Save it as PNG or JPEG and attach it again.",
    "text": "Make sure it's a plain-text file, then attach it again.",
    "docx": "It may be damaged or password-protected. Save it again, or attach it as a PDF.",
    "spreadsheet": "It may be damaged or password-protected. Save it again, or attach it as a CSV.",
    "csv": "Check that it's a valid CSV or TSV file, then attach it again.",
    "pdf": "It may be damaged or password-protected. Save it again, or attach a different copy.",
    "upload": "Please attach it again.",
}


def _attachment_unreadable(file_name: str, kind: str) -> str:
    """The message a user sees when a chat attachment can't be read; the cause goes to the log."""
    return f"Couldn't read {file_name}. {_ATTACHMENT_UNREADABLE_HINTS[kind]}"


def _attachment_too_heavy(file_name: str) -> str:
    return (
        f"{file_name} is too large or complex to read right now, so it wasn't attached. "
        "Try again, or split it into smaller files and attach those."
    )


def _attachment_too_long(file_name: str, page_count: int) -> str:
    return (
        f"{file_name} has {page_count} pages, more than the {CHAT_PDF_PAGE_CAP} pages a PDF can have "
        "when attached to a message. Attach a part of it, or split it into smaller files."
    )


def _attachment_too_slow(file_name: str) -> str:
    return (
        f"{file_name} took too long to read, so it wasn't attached. "
        "Try again, attach fewer pages, or add it to a knowledge base."
    )


# Cap tabular chat-attachment context so large CSV/XLSX files don't blow the LLM window.
_CHAT_ATTACHMENT_MAX_TABLE_ROWS = 500

# The browser gives an attachment upload 90s, and one stalled vision call can run
# for minutes. An image left undescribed still reaches a multimodal chat model.
_CHAT_ATTACHMENT_IMAGE_DESCRIPTION_BUDGET_S = 40

# Reading the PDFs and describing images share this, so the upload answers
# before a proxy in front of the app gives up on it (60s on a load balancer,
# 100s on Cloudflare) and drops it without a status. A PDF still being read
# then fails with a message, its parse worker killed rather than left running.
_CHAT_ATTACHMENT_UPLOAD_BUDGET_S = 50


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


async def _purge_stored_attachments(
    blob_storage: BlobStorage, org_id: str, stored: list[tuple[str, str]],
) -> None:
    """A failed upload's stored file and parsed-block documents. Each upload has its own new virtual record, so
    no other record reads them. Best-effort: a failure here must not replace the error that triggered it."""
    for document_id, virtual_record_id in stored:
        try:
            if document_id:
                await blob_storage.purge_document(org_id, document_id)
            await blob_storage.purge_virtual_record_documents(org_id, virtual_record_id)
        except Exception:
            logger.warning("attachments.upload_purge_failed", exc_info=True)


async def _rollback_attachment_records(
    graph_provider: IGraphDBProvider,
    record_ids: list[str],
) -> None:
    """Undo the graph half of an attachment upload that failed partway through.

    Mirrors `delete_chat_attachment`: dropping the RECORDS node takes its
    IS_OF_TYPE and PERMISSION edges with it, and the FILES node shares the key.
    The stored bytes are purged by the caller (`_purge_stored_attachments`).

    Each collection is attempted independently so a failure on one still cleans
    the other. Both go through `delete_nodes_and_edges` rather than the cheaper
    `delete_nodes` the delete route uses for FILES: that route only reaches FILES
    once RECORDS cleanup has already taken the isOfType edge, whereas here FILES
    can be deleted while the edge is still live, and on Arango `delete_nodes`
    leaves edges behind (on Neo4j both are the same DETACH DELETE).

    Best-effort: a failure here must not replace the error that triggered it.
    """
    if not record_ids:
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value):
        try:
            await graph_provider.delete_nodes_and_edges(record_ids, collection)
        except Exception:
            logger.exception(
                "Failed to roll back partially uploaded attachments from %s; "
                "records may be orphaned: %s",
                collection,
                record_ids,
            )


# Node's conversation routes (conversation:chat) and agent conversation routes
# (agent:execute) both forward here with the caller's token.
@router.post(
    "/chat/attachments/upload",
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.CONVERSATION_CHAT,
                OAuthScopes.AGENT_EXECUTE,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        )
    ],
)
@inject
async def upload_chat_attachments(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> dict[str, Any]:
    # A person is waiting: every parse below, however deep in a parser, runs on
    # the chat attachment pool, never behind background indexing.
    with interactive_parsing():
        return await _upload_chat_attachments(request, graph_provider, config_service)



async def _upload_chat_attachments(
    request: Request,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
) -> dict[str, Any]:
    payload = await _parse_body(request, AttachmentUploadRequest, "Invalid attachment upload payload")

    user = request.state.user or {}
    org_id = user.get("orgId")
    user_id = user.get("userId")

    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org context for attachment upload")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user context for attachment upload")
    uploader = Uploader.from_claims(user)
    is_service_account = uploader.kind is UploaderKind.SERVICE_ACCOUNT

    if not payload.attachments:
        raise HTTPException(status_code=400, detail="No attachments provided")

    # Resolve the auth `userId` to the User node's internal key so that
    # permission edges MATCH the User node (graph providers key User nodes
    # by `_key`/`id`, not by the auth `userId`). Service-account callers
    # have no User node and skip permission edges, so the lookup is skipped.
    user_key: str | None = None
    if not is_service_account:
        user_doc = await get_user_or_503(graph_provider, user_id)
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found for attachment upload")
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            raise HTTPException(status_code=500, detail="Resolved user is missing key/id")

    now = get_epoch_timestamp_in_ms()
    tracked = await uploads_tracked(config_service)
    uploaded_refs: list[dict[str, Any]] = []
    record_docs: list[dict[str, Any]] = []
    file_docs: list[dict[str, Any]] = []
    parsed_blocks_by_record: dict[str, BlocksContainer] = {}
    ocr_image_pages_used = 0
    loop = asyncio.get_running_loop()
    upload_deadline = loop.time() + _CHAT_ATTACHMENT_UPLOAD_BUDGET_S

    container: QueryAppContainer = request.app.container
    service_logger = container.logger()
    graphdb = GraphDBTransformer(graph_provider=graph_provider, logger=service_logger)
    blob_storage = BlobStorage(logger=service_logger, config_service=config_service, graph_provider=graph_provider)
    pdf_processor = PDFPlumberOpenCVProcessor(logger=logger, config=config_service)

    # Bytes are stored before the request can fail (an unreadable file, a graph write): a failed upload purges
    # what it stored, or nothing would ever name those documents again (CLEANUP-G5).
    stored: list[tuple[str, str]] = []
    try:
        for item in payload.attachments:
            if not _is_supported_attachment_mime(item.mimeType):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{item.fileName} can't be attached because that file type isn't supported. "
                        "You can attach PDF, Word (DOCX), Excel (XLSX), CSV, TSV, text, Markdown, "
                        "JPEG and PNG files."
                    ),
                )
            if item.size <= 0:
                raise HTTPException(status_code=400, detail=f"{item.fileName} is empty. Attach a file that has content.")

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
                raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "upload"))

            storage_doc_id, _ = await blob_storage.save_binary_to_storage(
                org_id=org_id,
                record_id=record_id,
                file_name=item.fileName,
                extension=extension,
                content_type=item.mimeType,
                binary_data=file_binary,
            )
            stored.append((storage_doc_id or "", virtual_record_id))
            external_record_id = storage_doc_id if storage_doc_id else record_id

            record_doc, file_doc = attachment_documents(
                org_id,
                AttachmentFile(
                    record_id=record_id,
                    virtual_record_id=virtual_record_id,
                    file_name=item.fileName,
                    extension=extension,
                    mime_type=item.mimeType,
                    size=item.size,
                    external_record_id=external_record_id,
                ),
                uploader,
                now,
                tracked=tracked,
                conversation_id=payload.conversationId,
            )

            needs_ocr = False
            parse_mode = "pdfplumber"
            if is_image:
                try:
                    block_containers = await asyncio.to_thread(_build_image_blocks, file_binary, item.mimeType)
                    parsed_blocks_by_record[record_id] = block_containers
                    parse_mode = "image_direct"
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "image"))
            elif is_text:
                try:
                    block_containers = await _build_text_blocks(file_binary)
                    parsed_blocks_by_record[record_id] = block_containers
                    parse_mode = "text"
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "text"))
            elif is_docx:
                try:
                    block_containers = await _build_docx_blocks(file_binary, item.fileName, config_service)
                    parsed_blocks_by_record[record_id] = block_containers
                    parse_mode = "docling"
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "docx"))
            elif is_spreadsheet:
                try:
                    block_containers = await _build_excel_blocks(file_binary, item.fileName, config_service)
                    parsed_blocks_by_record[record_id] = block_containers
                    parse_mode = "excel_lightweight"
                except (ParseWorkerError, TimeoutError, BrokenProcessPool, MemoryError) as e:
                    # Same as the PDF branch: a worker that overran or died is most likely out of
                    # memory, not a damaged file. ParseWorkerError is a ParseError, so it goes first.
                    logger.warning("Chat attachment %s could not be read in time: %r", item.fileName, e)
                    raise HTTPException(status_code=422, detail=_attachment_too_heavy(item.fileName)) from e
                except ParseError as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    if e.message == EXCEL_TOO_LARGE:
                        raise HTTPException(status_code=400, detail=EXCEL_TOO_LARGE) from e
                    raise HTTPException(
                        status_code=400, detail=_attachment_unreadable(item.fileName, "spreadsheet"),
                    ) from e
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "spreadsheet"))
            elif is_delimited:
                try:
                    block_containers = await _build_csv_blocks(
                        file_binary, item.fileName, config_service, item.mimeType
                    )
                    parsed_blocks_by_record[record_id] = block_containers
                    parse_mode = "csv_lightweight"
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "csv"))
            else:
                try:
                    # pdfplumber and the layout parser are pure Python and hold the GIL,
                    # so they run in parse workers: a slow or hostile file then costs
                    # its own upload only, never this loop or this process's memory.
                    async with asyncio.timeout_at(upload_deadline):
                        needs_ocr, page_count = await parse_pool.submit(
                            needs_ocr_and_page_count,
                            file_binary,
                            CHAT_PDF_PAGE_CAP,
                            label=item.fileName,
                            size=len(file_binary),
                            kind=ParseKind.INSPECT,
                        )
                        if page_count > CHAT_PDF_PAGE_CAP:
                            raise HTTPException(status_code=400, detail=_attachment_too_long(item.fileName, page_count))
                        if needs_ocr:
                            if ocr_image_pages_used + page_count > OCR_IMAGE_PAGE_CAP:
                                raise HTTPException(
                                    status_code=400,
                                    detail=(
                                        f"{item.fileName} has too many scanned pages to attach: the limit is "
                                        f"{OCR_IMAGE_PAGE_CAP} scanned pages per message. Attach fewer pages "
                                        "or split the document."
                                    ),
                                )
                            png_pages = await parse_pool.submit(
                                render_all_pages_as_png,
                                file_binary,
                                144,
                                label=item.fileName,
                                size=len(file_binary),
                                kind=ParseKind.RENDER,
                            )
                            block_containers = _build_pdf_image_blocks(png_pages)
                            ocr_image_pages_used += page_count
                            parse_mode = "image_direct"
                        else:
                            parsed_data = await pdf_processor.parse_document(item.fileName, file_binary)
                            block_containers = await pdf_processor.create_blocks(parsed_data, skip_llm_enrichment=True)
                            parse_mode = "pdfplumber"
                    parsed_blocks_by_record[record_id] = block_containers
                except HTTPException:
                    raise
                except TimeoutError as e:
                    # The upload deadline fired: the file was being read, just not fast enough.
                    logger.warning("Chat attachment %s could not be read in time: %r", item.fileName, e)
                    raise HTTPException(status_code=422, detail=_attachment_too_slow(item.fileName)) from e
                except (ParseWorkerError, BrokenProcessPool, MemoryError) as e:
                    # A worker that died, or a renderer it started that
                    # died: most likely out of memory, not a damaged file.
                    logger.warning("Chat attachment %s could not be read in time: %r", item.fileName, e)
                    raise HTTPException(status_code=422, detail=_attachment_too_heavy(item.fileName)) from e
                except Exception as e:
                    logger.warning("Chat attachment %s could not be read: %s", item.fileName, e)
                    raise HTTPException(status_code=400, detail=_attachment_unreadable(item.fileName, "pdf"))
            record_doc["isVLMOcrProcessed"] = needs_ocr
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

        # Records, files, edges and the sink's blob write are five separate round
        # trips with no shared transaction, so a failure partway leaves a record the
        # user can neither see nor delete. Undo the graph writes before surfacing it.
        try:
            await write_attachment_graph(
                graph_provider, org_id, record_docs, file_docs, uploader, user_key, get_epoch_timestamp_in_ms()
            )

            sink_orchestrator = SinkOrchestrator(
                graphdb=graphdb,
                blob_storage=blob_storage,
                vector_store=_AttachmentSinkNoopVectorStore(),
                graph_provider=graph_provider,
                logger=service_logger,
                config_service=config_service,
            )

            # One budget for the whole request: its records are indexed one after
            # another, and the Slack bot and API send up to 10 files in one upload.
            describe_deadline = min(
                loop.time() + _CHAT_ATTACHMENT_IMAGE_DESCRIPTION_BUDGET_S, upload_deadline
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
                    settings={
                        "sink_only": True,
                        "skip_vector_store": True,
                        IMAGE_DESCRIPTION_BUDGET_S: max(describe_deadline - loop.time(), 0.0),
                    },
                )
                await sink_orchestrator.index(ctx)
        except Exception:
            await _rollback_attachment_records(
                graph_provider, [rd["_key"] for rd in record_docs]
            )
            raise

    except BaseException:
        await _purge_stored_attachments(blob_storage, org_id, stored)
        raise

    return {
        "conversationId": payload.conversationId,
        "attachments": uploaded_refs,
    }


class AttachmentPermissionRequest(BaseModel):
    userIds: list[str]
    recordIds: list[str]


class ArtifactPermissionRequest(BaseModel):
    conversationId: str
    userIds: list[str]


_PERMISSION_EDGE_LOOKUP_CONCURRENCY = 16


def _permission_grantor(claims: Mapping[str, Any]) -> tuple[str, str]:
    """Node signs the conversation owner's `userId`/`orgId` into the service token
    only after checking ownership or sharing in Mongo; the body is never trusted
    for either."""
    org_id = claims.get("orgId")
    grantor_user_id = claims.get("userId")
    if not org_id or not grantor_user_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId and userId")
    return org_id, grantor_user_id


async def _resolve_user_keys(
    graph_provider: IGraphDBProvider, user_ids: list[str]
) -> list[str]:
    """Resolve auth `userId`s to the User node's internal `_key`/`id` — permission
    edges MATCH the User node, not the auth `userId`. Unresolvable users are logged
    and skipped rather than failing the whole batch."""
    user_keys: list[str] = []
    for user_id in user_ids:
        user_doc = await _user_doc(graph_provider, user_id)
        if not user_doc:
            logger.warning("User not found for permission grant/revoke, skipping: %s", user_id)
            continue
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            logger.warning("Resolved user missing _key/id, skipping: %s", user_id)
            continue
        user_keys.append(user_key)
    return user_keys


async def _user_doc(graph_provider: IGraphDBProvider, user_id: str) -> dict[str, Any] | None:
    """Another user's graph record (not the caller's, so no ensurer). An outage is a 503, not "not found": a revoke
    that skipped the user would answer 0 and leave their READER edges."""
    return await await_or_503(graph_provider.get_user_by_user_id(user_id, raise_on_error=True))


async def _permission_edge(
    graph_provider: IGraphDBProvider, user_key: str, record_id: str
) -> dict[str, Any] | None:
    """The `USER -> RECORD` permission edge, or None. An outage is a 503, not "no edge"."""
    return await await_or_503(
        graph_provider.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=record_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
            raise_on_error=True,
        )
    )


async def _get_permission_edges(
    graph_provider: IGraphDBProvider, pairs: list[tuple[str, str]]
) -> list[dict[str, Any] | None]:
    """`USER -> RECORD` permission edge (or None) for each `(user_key, record_id)`, in order."""
    return await gather_with_concurrency(
        _PERMISSION_EDGE_LOOKUP_CONCURRENCY,
        *(_permission_edge(graph_provider, user_key, record_id) for user_key, record_id in pairs),
    )


async def _records_owned_by(
    graph_provider: IGraphDBProvider, grantor_user_id: str, record_ids: list[str]
) -> set[str]:
    """Only records the grantor OWNS may be shared onward. Attachment recordIds come
    from the client's message body, so without this a sharer could hand out (or
    strip) access to records they cannot see themselves."""
    grantor_keys = await _resolve_user_keys(graph_provider, [grantor_user_id])
    if not grantor_keys or not record_ids:
        return set()
    edges = await _get_permission_edges(
        graph_provider, [(grantor_keys[0], record_id) for record_id in record_ids]
    )
    return {
        record_id
        for record_id, edge in zip(record_ids, edges)
        if edge and edge.get("role") == "OWNER"
    }


async def _user_record_pairs(
    graph_provider: IGraphDBProvider, org_id: str, user_ids: list[str], record_ids: list[str]
) -> tuple[list[tuple[str, str]], list[dict[str, Any] | None]]:
    user_keys = await _org_user_keys(graph_provider, org_id, user_ids)
    pairs = [
        (user_key, record_id)
        for user_key in dict.fromkeys(user_keys)
        for record_id in dict.fromkeys(record_ids)
    ]
    return pairs, await _get_permission_edges(graph_provider, pairs)


async def _grant_reader_permissions(
    graph_provider: IGraphDBProvider,
    org_id: str,
    grantor_user_id: str,
    user_ids: list[str],
    record_ids: list[str],
) -> int:
    """Create READER edges for pairs with no permission edge yet, on records the
    grantor owns. Other edges are left alone: `batch_create_edges` overwrites
    every property, so re-granting would downgrade an OWNER edge and rewrite edges
    on every shared conversation view. The exception is an artifact the owner also
    shared directly: its edge records the conversation grant once, so taking the
    direct share away later leaves the chat's access."""
    if not user_ids or not record_ids:
        return 0

    pairs, existing = await _user_record_pairs(graph_provider, org_id, user_ids, record_ids)
    missing = [pair for pair, edge in zip(pairs, existing) if edge is None]
    direct_only = [
        (pair, edge)
        for pair, edge in zip(pairs, existing)
        if is_direct_share(edge) and not edge.get("chatGranted")
    ]
    if not missing and not direct_only:
        return 0

    owned = await _records_owned_by(
        graph_provider,
        grantor_user_id,
        list(dict.fromkeys([rid for _, rid in missing] + [rid for (_, rid), _ in direct_only])),
    )
    ts = get_epoch_timestamp_in_ms()
    edges: list[dict[str, Any]] = [
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
        for user_key, record_id in missing
        if record_id in owned
    ]
    edges += [
        reader_edge(user_key, record_id, direct=True, chat=True, now=ts, created_at=edge.get("createdAtTimestamp"))
        for (user_key, record_id), edge in direct_only
        if record_id in owned
    ]
    if edges:
        await graph_provider.batch_create_edges(edges, CollectionNames.PERMISSION.value)
        await notify_principals_changed(org_id)  # a new READER grant
    return len(edges)


async def _revoke_reader_permissions(
    graph_provider: IGraphDBProvider,
    org_id: str,
    grantor_user_id: str,
    user_ids: list[str],
    record_ids: list[str],
) -> int:
    """Remove READER edges on records the grantor owns. `batch_delete_edges` matches
    `(_from, _to)` regardless of role, so without the READER filter an unshare that
    names the owner would delete the owner's own OWNER edges. An artifact the
    owner also shared directly keeps its edge and only loses the chat's grant."""
    if not user_ids or not record_ids:
        return 0

    pairs, existing = await _user_record_pairs(graph_provider, org_id, user_ids, record_ids)
    readers = [
        pair
        for pair, edge in zip(pairs, existing)
        if edge and edge.get("role") == "READER" and not is_direct_share(edge)
    ]
    also_direct = [
        (pair, edge)
        for pair, edge in zip(pairs, existing)
        if is_direct_share(edge) and edge.get("chatGranted")
    ]
    if not readers and not also_direct:
        return 0

    owned = await _records_owned_by(
        graph_provider,
        grantor_user_id,
        list(dict.fromkeys([rid for _, rid in readers] + [rid for (_, rid), _ in also_direct])),
    )
    ts = get_epoch_timestamp_in_ms()
    direct_kept = [
        reader_edge(user_key, record_id, direct=True, chat=False, now=ts, created_at=edge.get("createdAtTimestamp"))
        for (user_key, record_id), edge in also_direct
        if record_id in owned
    ]
    if direct_kept:
        await graph_provider.batch_create_edges(direct_kept, CollectionNames.PERMISSION.value)
    edges: list[dict[str, Any]] = [
        {
            "from_id": user_key,
            "from_collection": CollectionNames.USERS.value,
            "to_id": record_id,
            "to_collection": CollectionNames.RECORDS.value,
        }
        for user_key, record_id in readers
        if record_id in owned
    ]
    if edges:
        await graph_provider.batch_delete_edges(edges, CollectionNames.PERMISSION.value)
    if edges or direct_kept:
        await notify_principals_changed(org_id)  # a narrowing PERMISSION write
    return len(edges) + len(direct_kept)


async def _get_artifact_record_ids_for_conversation(
    graph_provider: IGraphDBProvider, org_id: str, conversation_id: str
) -> list[str]:
    """Every artifact record id (`_key`, shared with its `records` doc) in a
    conversation. Sorted so SKIP/LIMIT pages don't overlap or skip, and raises on
    graph errors (an outage as a 503) rather than reporting them as "no artifacts"."""
    record_ids: list[str] = []
    skip = 0
    page_size = 200
    while True:
        docs = await await_or_503(
            graph_provider.get_documents_paginated(
                CollectionNames.ARTIFACTS.value,
                skip=skip,
                limit=page_size,
                filters={"orgId": org_id, "conversationId": conversation_id},
                sort_field="_key",
                raise_on_error=True,
            )
        )
        if not docs:
            break
        for doc in docs:
            record_id = doc.get("_key") or doc.get("id")
            if record_id:
                record_ids.append(record_id)
        if len(docs) < page_size:
            break
        skip += page_size
    return record_ids


async def _attachment_caller(
    request: Request, graph_provider: IGraphDBProvider,
) -> tuple[str, str | None, bool]:
    """(org id, the caller's User node key, is a service account). A service
    account has no User node, so it owns no attachment."""
    user = request.state.user or {}
    org_id = user.get("orgId")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org context")
    if user.get("isServiceAccount"):
        return org_id, None, True
    user_id = user.get("userId")
    user_doc = await get_caller_user(graph_provider, user_id) if user_id else None
    return org_id, (user_doc or {}).get("_key") or (user_doc or {}).get("id"), False


# The ids come from the client: bound the graph reads they cause.
_OWNERSHIP_CHECKS_AT_ONCE = 8


async def _owned_chat_attachments(
    graph_provider: IGraphDBProvider, org_id: str, user_key: str | None, record_ids: list[str],
) -> list[str]:
    """The ids that are chat attachments of this org uploaded by this user. The
    ids come from the client and a READER edge written here is honoured by every
    permission check, so only the uploader may share, unshare or delete one."""
    if not user_key:
        return []

    async def owned(record_id: str) -> bool:
        record = await get_document_or_503(graph_provider, record_id, CollectionNames.RECORDS.value)
        if not is_chat_attachment(record, org_id):
            return False
        edge = await _permission_edge(graph_provider, user_key, record_id)
        return (edge or {}).get("role") == "OWNER"

    ids = list(dict.fromkeys(r for r in record_ids if r))
    flags = await gather_with_concurrency(_OWNERSHIP_CHECKS_AT_ONCE, *(owned(r) for r in ids))
    return [r for r, ok in zip(ids, flags) if ok]


async def _grantor_chat_attachments(
    graph_provider: IGraphDBProvider, org_id: str, grantor_user_id: str, record_ids: list[str],
) -> list[str]:
    """The attachment routes take record ids from the client; only this org's chat
    attachments the grantor uploaded may be shared or unshared through them, never
    a collection file or connector record the grantor happens to own."""
    grantor_keys = await _resolve_user_keys(graph_provider, [grantor_user_id])
    return await _owned_chat_attachments(
        graph_provider, org_id, grantor_keys[0] if grantor_keys else None, record_ids,
    )


async def _org_user_keys(
    graph_provider: IGraphDBProvider, org_id: str, user_ids: list[str],
) -> list[str]:
    """User node keys for the ids that are users of this org; others are skipped."""
    keys: list[str] = []
    for user_id in dict.fromkeys(user_ids):
        user_doc = await _user_doc(graph_provider, user_id)
        user_key = (user_doc or {}).get("_key") or (user_doc or {}).get("id")
        if not user_key or user_doc.get("orgId") != org_id:
            logger.warning("Not a user of this org, skipping: %s", user_id)
            continue
        keys.append(user_key)
    return keys


# Records read per address; an address that fills it is treated as unreadable.
_RECORDS_PER_URL_LIMIT = 50


class RecordAccessRequest(BaseModel):
    recordIds: list[str] = Field(default_factory=list, max_length=500)
    # Addresses of records known only by link (a tool's reference data): readable
    # when every live record stored at the address is readable.
    webUrls: list[str] = Field(default_factory=list, max_length=500)


@router.post("/chat/records/access")
@inject
async def check_viewer_record_access(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_PERMISSIONS)),
) -> dict[str, Any]:
    """Which of these records the viewer named in the service token may read, by
    the same `check_access` as search and `fetch_record`. Node signs the viewer's
    own ids after authenticating them, so a shared chat is rendered for the person
    looking at it. An access check that cannot answer is a 503, never a guess."""
    org_id, viewer_id = _permission_grantor(claims)
    payload = await _parse_body(request, RecordAccessRequest, "Invalid request payload")
    ids = list(dict.fromkeys(r for r in payload.recordIds if r))
    urls = list(dict.fromkeys(u for u in payload.webUrls if u))
    by_url: dict[str, list[str]] = {}
    if urls:
        try:
            # Deleted records too: a deleted record's address is that record's, as its id is.
            # Known limit: a record removed outright leaves no row, so its address reads as unmatched
            # (a web page). Answers that cite it by record id are still hidden; only one that names it
            # by address alone is not.
            rows = await graph_provider.get_records_by_weburls(
                urls, org_id, per_address=_RECORDS_PER_URL_LIMIT, include_deleted=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Access check unavailable") from exc
        for row in rows:
            by_url.setdefault(row.get("webUrl"), []).append(row["id"])
    try:
        readable = await accessible_node_ids(
            graph_provider, set(ids) | {r for rs in by_url.values() for r in rs}, viewer_id, org_id,
        )
    except PermissionVerificationUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Access check unavailable") from exc
    return {
        "accessible": [r for r in ids if r in readable],
        "accessibleUrls": [
            u for u in urls
            if by_url.get(u)
            # A full page means records may be cut off, so the address is not trusted.
            and len(by_url[u]) < _RECORDS_PER_URL_LIMIT
            and all(r in readable for r in by_url[u])
        ],
        # An address no record is stored at (a public web page): not a record to lose.
        "unmatchedUrls": [u for u in urls if not by_url.get(u)],
    }


@router.post("/chat/attachments/permissions")
@inject
async def grant_attachment_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_PERMISSIONS)),
) -> dict[str, Any]:
    """Grant READER on chat attachment records to the users a conversation was shared with."""
    org_id, grantor_user_id = _permission_grantor(claims)
    payload = await _parse_body(request, AttachmentPermissionRequest, "Invalid request payload")
    record_ids = await _grantor_chat_attachments(graph_provider, org_id, grantor_user_id, payload.recordIds)
    granted = await _grant_reader_permissions(
        graph_provider, org_id, grantor_user_id, payload.userIds, record_ids
    )
    return {"granted": granted}


@router.delete("/chat/attachments/permissions")
@inject
async def revoke_attachment_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_PERMISSIONS)),
) -> dict[str, Any]:
    """Revoke READER on chat attachment records from users a conversation was unshared from."""
    org_id, grantor_user_id = _permission_grantor(claims)
    payload = await _parse_body(request, AttachmentPermissionRequest, "Invalid request payload")
    record_ids = await _grantor_chat_attachments(graph_provider, org_id, grantor_user_id, payload.recordIds)
    revoked = await _revoke_reader_permissions(
        graph_provider, org_id, grantor_user_id, payload.userIds, record_ids
    )
    return {"revoked": revoked}


@router.post("/chat/artifacts/permissions")
@inject
async def grant_artifact_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_PERMISSIONS)),
) -> dict[str, Any]:
    """Reconcile READER on a conversation's artifacts for the users it is shared
    with. Node calls this on share, and on each non-owner's first-page load so
    artifacts created after the share are caught up. A user gets READER only on
    the artifacts `ArtifactViewerAccess` finds readable for them (SS0); READER
    on every other artifact is revoked, which also removes edges granted before
    the check existed. OWNER edges are never touched."""
    org_id, grantor_user_id = _permission_grantor(claims)
    payload = await _parse_body(request, ArtifactPermissionRequest, "Invalid request payload")
    if not payload.userIds:
        return {"granted": 0, "revoked": 0}

    record_ids = await _get_artifact_record_ids_for_conversation(
        graph_provider, org_id, payload.conversationId
    )
    if not record_ids:
        return {"granted": 0, "revoked": 0}
    viewer_access = artifact_viewer_access(graph_provider)
    granted = revoked = 0
    for user_id in dict.fromkeys(payload.userIds):
        statuses = await viewer_access.decide(Actor(org_id=org_id, user_id=user_id), record_ids)
        readable = [rid for rid in record_ids if statuses.get(rid) == "readable"]
        hidden = [rid for rid in record_ids if statuses.get(rid) != "readable"]
        granted += await _grant_reader_permissions(graph_provider, org_id, grantor_user_id, [user_id], readable)
        revoked += await _revoke_reader_permissions(graph_provider, org_id, grantor_user_id, [user_id], hidden)
    logger.info(
        "artifacts.permissions_reconciled users=%d artifacts=%d granted=%d revoked=%d",
        len(dict.fromkeys(payload.userIds)),
        len(record_ids),
        granted,
        revoked,
    )
    return {"granted": granted, "revoked": revoked}


@router.delete("/chat/artifacts/permissions")
@inject
async def revoke_artifact_permissions(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_PERMISSIONS)),
) -> dict[str, Any]:
    """Revoke READER on a conversation's artifacts from users it was unshared from."""
    org_id, grantor_user_id = _permission_grantor(claims)
    payload = await _parse_body(request, ArtifactPermissionRequest, "Invalid request payload")
    if not payload.userIds:
        return {"revoked": 0}

    record_ids = await _get_artifact_record_ids_for_conversation(
        graph_provider, org_id, payload.conversationId
    )
    revoked = await _revoke_reader_permissions(
        graph_provider, org_id, grantor_user_id, payload.userIds, record_ids
    )
    return {"revoked": revoked}


class ArtifactEraseRequest(BaseModel):
    """What to erase: one conversation's artifacts, or drafts discarded before a time. Never the org."""

    model_config = ConfigDict(extra="forbid")

    conversationId: str | None = Field(default=None, min_length=1, max_length=128)
    discardedBefore: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_selector(self) -> "ArtifactEraseRequest":
        if (self.conversationId is None) == (self.discardedBefore is None):
            raise ValueError("exactly one of conversationId and discardedBefore")
        return self


@router.post("/chat/artifacts/erase")
@inject
async def erase_artifacts(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Erase a deleted conversation's artifacts, or collect discarded drafts (E7, T5).

    Node signs the org into the service token after the conversation's delete
    committed; the body names only what to erase. Repeatable: Node calls again
    while `remaining`, and after a crash.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, ArtifactEraseRequest, "Invalid request payload")
    container: QueryAppContainer = request.app.container
    eraser = ArtifactEraser(
        graph_provider,
        BlobStorage(logger=container.logger(), config_service=config_service, graph_provider=graph_provider),
    )
    uploads = candidates = learned = 0
    if payload.conversationId is not None:
        report = await eraser.erase_conversation(org_id, payload.conversationId)
        remaining = report.remaining
        if not remaining:
            # Uploads into the conversation no message carried (and those it did), and skill candidates learned
            # from it (CLEANUP-G5, CLEANUP-G7): Node's artifacts step repeats this call while `remaining`.
            blob_storage = BlobStorage(
                logger=container.logger(), config_service=config_service, graph_provider=graph_provider,
            )
            swept = await AttachmentEraser(
                graph_provider,
                blob_storage,
                lambda user_id, record_ids: _records_owned_by(graph_provider, user_id, record_ids),
            ).erase_conversation_uploads(org_id, payload.conversationId)
            candidates, more_candidates = await erase_session_skill_candidates(
                graph_provider, org_id, payload.conversationId,
            )
            # Skills its runs wrote go with it, as its memories do (FOLLOWUPS1-1).
            learned, more_learned = await erase_session_learned_skills(
                graph_provider, org_id, payload.conversationId,
            )
            uploads = swept.attachments
            remaining = swept.remaining or more_candidates or more_learned
    else:
        report = await eraser.collect_discarded(org_id, payload.discardedBefore or 0)
        remaining = report.remaining
    logger.info(
        "artifacts.erased selector=%s artifacts=%d blobs=%d uploads=%d skillCandidates=%d learnedSkills=%d "
        "remaining=%s",
        "conversation" if payload.conversationId is not None else "discarded",
        report.artifacts,
        report.blobs,
        uploads,
        candidates,
        learned,
        remaining,
    )
    return {
        "artifacts": report.artifacts,
        "blobs": report.blobs,
        "uploads": uploads,
        "skillCandidates": candidates,
        "learnedSkills": learned,
        "remaining": remaining,
    }


class OrgEraseRequest(BaseModel):
    """One page of a deleted org's erasure (CLEANUP-G1). Never the org: that is the token's."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pageLimit: int = Field(default=ORG_ERASE_PAGE, ge=1, le=200)


@router.post("/chat/org/erase")
@inject
async def erase_org(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Erase one page of what the query service holds for a deleted org (CLEANUP-G1).

    Node calls it when the org is deleted and from the org's erasure job once every session's erasure completed, again while
    `remaining` and after a crash: artifacts, then chat attachments (sent or not), then skill candidates,
    then agent versions.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, OrgEraseRequest, "Invalid request payload")
    container: QueryAppContainer = request.app.container
    report = await erase_org_page(
        graph_provider,
        BlobStorage(logger=container.logger(), config_service=config_service, graph_provider=graph_provider),
        org_id,
        page=payload.pageLimit,
    )
    logger.info(
        "org.erased_page artifacts=%d attachments=%d blobs=%d skillCandidates=%d agentVersions=%d remaining=%s",
        report.artifacts, report.attachments, report.blobs, report.skill_candidates, report.agent_versions,
        report.remaining,
    )
    return {
        "attachments": report.attachments,
        "artifacts": report.artifacts,
        "blobs": report.blobs,
        "skillCandidates": report.skill_candidates,
        "agentVersions": report.agent_versions,
        "remaining": report.remaining,
    }


class TraceEraseRequest(BaseModel):
    """A conversation being erased and its runs, whose Opik traces go (cleanup audit, Opik item)."""

    model_config = ConfigDict(extra="forbid")

    conversationId: str = Field(pattern=OPIK_SAFE_ID_PATTERN)
    runIds: list[Annotated[str, Field(pattern=OPIK_SAFE_ID_PATTERN)]] = Field(default_factory=list, max_length=50)


@router.post("/chat/traces/erase")
@inject
async def erase_traces(
    request: Request,
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Delete a conversation's Opik traces, by its thread and its runs. Repeatable.

    Opik off: nothing was sent, nothing to ask. Opik failing: 503, and Node's erasure step retries.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, TraceEraseRequest, "Invalid request payload")
    store = opik_trace_store()
    if store is None:
        return {"traces": 0, "configured": False}
    try:
        traces = await erase_conversation_traces(store, org_id, payload.conversationId, payload.runIds)
    except Exception as exc:
        logger.warning("traces.erase_failed errorType=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Opik could not be asked to delete the traces") from exc
    logger.info("traces.erased traces=%d", traces)
    return {"traces": traces, "configured": True}


class AttachmentSentRequest(BaseModel):
    """Uploads a committed message carries, and its conversation (CLEANUP-G5)."""

    model_config = ConfigDict(extra="forbid")

    conversationId: str | None = Field(default=None, min_length=1, max_length=128)
    recordIds: list[str] = Field(min_length=1, max_length=ATTACHMENT_ERASE_PAGE)


@router.post("/chat/attachments/sent")
@inject
async def mark_attachments_sent(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Node's unsent-upload collector marks the listed uploads it found a message naming, so they are never listed
    again or erased with a conversation as unsent (CLEANUP-G5).

    Repeatable; ids that are not this org's chat attachments are ignored.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, AttachmentSentRequest, "Invalid request payload")
    marked = await mark_sent(graph_provider, org_id, payload.recordIds, payload.conversationId)
    return {"marked": marked}


class UnsentUploadsRequest(BaseModel):
    """A page of the token's org's uploads no message has been found carrying, uploaded before ``before``."""

    model_config = ConfigDict(extra="forbid")

    before: int = Field(ge=0)
    limit: int = Field(ge=1, le=ATTACHMENT_ERASE_PAGE)
    after: str | None = Field(default=None, min_length=1, max_length=128)


@router.post("/chat/attachments/unsent")
@inject
async def list_unsent_attachments(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Node's unsent-upload collector (CLEANUP-G5) reads uploads still awaiting a message, decides which no message
    names, and erases those through ``/chat/attachments/erase`` with the ``ownerId`` given here. ``next`` is the
    cursor to pass as ``after`` while ``remaining``.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, UnsentUploadsRequest, "Invalid request payload")
    page = await list_unsent(graph_provider, org_id, payload.before, payload.limit, after=payload.after)
    return {
        "uploads": [
            {"recordId": u.record_id, "ownerId": u.owner_id, "conversationId": u.conversation_id} for u in page.uploads
        ],
        "remaining": page.remaining,
        "next": page.next,
    }


class AttachmentEraseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordId: str = Field(min_length=1, max_length=128)
    ownerId: str = Field(min_length=1, max_length=128)


class AttachmentEraseRequest(BaseModel):
    """The chat attachments a deleted conversation's messages carried, each with the message's sender. Never the org."""

    model_config = ConfigDict(extra="forbid")

    attachments: list[AttachmentEraseItem] = Field(min_length=1, max_length=ATTACHMENT_ERASE_PAGE)


@router.post("/chat/attachments/erase")
@inject
async def erase_attachments(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_ERASE)),
) -> dict[str, Any]:
    """Erase a deleted conversation's chat attachments (QA15-F4): records, files, edges and stored bytes.

    Node's conversation erasure step calls it before it deletes the messages
    that name them, again after a crash. An attachment another user uploaded,
    or a record that is not an upload, is refused and counted, never deleted.
    ``removed`` names what is gone, so Node can mark it in other conversations.
    """
    org_id = bound_org_id()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    payload = await _parse_body(request, AttachmentEraseRequest, "Invalid request payload")
    container: QueryAppContainer = request.app.container
    eraser = AttachmentEraser(
        graph_provider,
        BlobStorage(logger=container.logger(), config_service=config_service, graph_provider=graph_provider),
        lambda user_id, record_ids: _records_owned_by(graph_provider, user_id, record_ids),
    )
    report = await eraser.erase(
        org_id, [AttachmentRef(record_id=item.recordId, owner_id=item.ownerId) for item in payload.attachments]
    )
    logger.info(
        "attachments.erased attachments=%d blobs=%d refused=%d unrecorded=%d",
        report.attachments, report.blobs, report.refused, report.unrecorded,
    )
    return {
        "attachments": report.attachments,
        "blobs": report.blobs,
        "refused": report.refused,
        "unrecorded": report.unrecorded,
        "removed": list(report.removed),
    }


class ArtifactCopyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifactId: str = Field(min_length=1, max_length=128)
    version: int | None = Field(default=None, ge=1)


class ArtifactCopyBody(BaseModel):
    """Which artifact versions of one conversation to copy into another. Never the org or the user."""

    model_config = ConfigDict(extra="forbid")

    fromConversationId: str = Field(min_length=1, max_length=128)
    toConversationId: str = Field(min_length=1, max_length=128)
    artifacts: list[ArtifactCopyItem] = Field(min_length=1, max_length=COPY_LIMIT)

    @model_validator(mode="after")
    def _distinct_conversations(self) -> "ArtifactCopyBody":
        if self.fromConversationId == self.toConversationId:
            raise ValueError("an artifact is copied into another conversation")
        return self


@router.post("/chat/artifacts/copy")
@inject
async def copy_artifacts(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CONVERSATION_FORK)),
) -> dict[str, Any]:
    """Copy artifact versions into a private fork of their conversation (SS3).

    Node signs the forker's `userId` and `orgId` into the service token after
    deciding which versions the forker may see; the registry still copies only
    what that user may read. Repeatable: a retry answers the copies already made.
    """
    org_id, user_id = _permission_grantor(claims)
    payload = await _parse_body(request, ArtifactCopyBody, "Invalid request payload")
    container: QueryAppContainer = request.app.container
    blob_store = BlobStorage(logger=container.logger(), config_service=config_service, graph_provider=graph_provider)
    copier = ArtifactCopier(ArtifactRegistryService(graph_provider, blob_store), graph_provider)
    outcomes = await copier.copy(
        actor=Actor(org_id=org_id, user_id=user_id),
        from_conversation_id=payload.fromConversationId,
        to_conversation_id=payload.toConversationId,
        refs=[CopyRequest(item.artifactId, item.version) for item in payload.artifacts],
    )
    return {
        "copies": [
            {
                "artifactId": outcome.artifact_id,
                "version": outcome.version,
                "copy": (
                    {"artifactId": outcome.copy_id, "documentId": outcome.document_id, "version": outcome.copy_version}
                    if outcome.copy_id is not None
                    else None
                ),
                **({"reason": outcome.reason} if outcome.reason is not None else {}),
            }
            for outcome in outcomes
        ]
    }


# Node's conversation routes (conversation:chat) and agent conversation routes
# (agent:execute) both forward here with the caller's token.
@router.delete(
    "/chat/attachments/{record_id}",
    status_code=204,
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT, OAuthScopes.AGENT_EXECUTE))],
)
@inject
async def delete_chat_attachment(
    record_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
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

    The stored file and parsed-block documents go first (CLEANUP-G5), then the
    FILES node and the RECORDS node with every edge touching it.
    """
    org_id, caller_key, _ = await _attachment_caller(request, graph_provider)

    record = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value)
    # record_id comes from the client: only a chat attachment, and only by its
    # uploader, or any member could delete any record in the org (KB and
    # connector records included). One answer for a missing id, another org's
    # record and one that is not the caller's, so the id is never confirmed.
    if (
        not record
        or record.get("connectorName") != Connectors.ATTACHMENTS.value
        or not is_chat_attachment(record, org_id)
        or not await _owned_chat_attachments(graph_provider, org_id, caller_key, [record_id])
    ):
        raise HTTPException(status_code=404, detail="Attachment not found")

    container: QueryAppContainer = request.app.container
    eraser = AttachmentEraser(
        graph_provider,
        BlobStorage(logger=container.logger(), config_service=config_service, graph_provider=graph_provider),
        lambda user_id, record_ids: _records_owned_by(graph_provider, user_id, record_ids),
    )
    await eraser.erase_record(org_id, record_id, record)


async def _load_user_doc(graph_provider: IGraphDBProvider, user_id: str | None) -> dict | None:
    """Deferred so the graph call happens inside the coroutine, not when the
    task is created — enrichment is best-effort and its failures are handled
    by the caller's `gather(..., return_exceptions=True)`."""
    if not user_id:
        return None
    return await get_caller_user(graph_provider, user_id)


async def _load_org_doc(graph_provider: IGraphDBProvider, org_id: str | None) -> dict | None:
    """See :func:`_load_user_doc`."""
    if not org_id:
        return None
    return await graph_provider.get_document(org_id, CollectionNames.ORGS.value)


async def load_system_prompts(
    config_service: ConfigurationService, logger_: Any,
) -> dict[str, Any]:
    """System prompts from the dedicated key, falling back to the legacy
    aiModels blob for OSS deployments that haven't migrated yet."""
    try:
        sp_raw = await config_service.get_config(
            config_node_constants.SYSTEM_PROMPTS.value, use_cache=True,
        )
        if sp_raw:
            return sp_raw
        ai_raw = await config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=True,
        )
        if ai_raw:
            return {
                k: ai_raw[k]
                for k in ("customSystemPrompt", "customSystemPromptWebSearch", "customSystemPromptAgent")
                if k in ai_raw
            }
    except Exception:
        logger_.debug("Could not load system prompts config", exc_info=True)
    return {}


def _chat_principal(request: Request) -> Principal:
    try:
        return principal_from_request_user(getattr(request.state, "user", None))
    except Exception:
        logger.warning("could not derive the caller's identity for the model gateway", exc_info=True)
        return service_principal(UNATTRIBUTED_SERVICE)


async def _generate_chat_stream_via_agent_loop(
    request: Request,
    query_info: "ChatQuery",
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    cancellation_registry: RunCancellationRegistry | None = None,
    resolved: ResolvedTurn | None = None,
) -> AsyncGenerator[str, None]:
    """Run the chat stream with the caller bound as the identity for model calls.

    Bound here, inside the generator, because the binding has to live in the
    task that iterates it: ``StreamingResponse`` and ``collect_stream_outcome``
    both drive it from one task, and work the stream spawns (tools, sub-agents)
    copies the context at creation.
    """
    async with call_scope(
        _chat_principal(request),
        Workload.CHAT,
        config_service=config_service,
        container=getattr(request.app, "container", None),
        conversation_id=query_info.conversationId,
    ):
        try:
            async with aclosing(
                _stream_chat_via_agent_loop(
                    request=request,
                    query_info=query_info,
                    retrieval_service=retrieval_service,
                    graph_provider=graph_provider,
                    config_service=config_service,
                    cancellation_registry=cancellation_registry,
                    resolved=resolved,
                )
            ) as inner:
                async for event in inner:
                    yield event
        except Exception as exc:
            # A turn that failed outside the agent loop (attachment check, prompts, the
            # bridge's own setup) still gets its end line; the error propagates as before.
            log_turn_end(logger, "error", exc=exc, code=classify_exception(exc)[0], run_id=query_info.runId)
            raise


async def _stream_chat_via_agent_loop(
    request: Request,
    query_info: "ChatQuery",
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    cancellation_registry: RunCancellationRegistry | None = None,
    resolved: ResolvedTurn | None = None,
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

    # LLM init, system prompts, user/org enrichment, and the entity vector
    # store are independent of each other and all sit before the first
    # streamed byte, so they run as one wave instead of serial round trips.
    llm_task = asyncio.ensure_future(
        get_llm_for_chat(
            config_service, query_info.modelKey, query_info.modelName, query_info.chatMode,
            reasoning_effort=query_info.reasoningEffort,
        )
    )
    prompts_task = asyncio.ensure_future(load_system_prompts(config_service, logger_))
    user_doc_task = asyncio.ensure_future(_load_user_doc(graph_provider, user_id))
    org_doc_task = asyncio.ensure_future(_load_org_doc(graph_provider, org_id))
    user_context_flag_task = asyncio.ensure_future(is_user_context_enabled(config_service))
    # Optional — backs the knowledgegraph search_entities /
    # find_records_by_entity tools. When unavailable those tools are simply
    # not granted, rather than blocking chat.
    entity_vector_store_task = asyncio.ensure_future(
        load_entity_vector_store(container, logger_, org_id)
    )
    background_tasks = [
        prompts_task, user_doc_task, org_doc_task,
        user_context_flag_task, entity_vector_store_task,
    ]

    try:
        llm_bundle = await llm_task
        if not llm_bundle or llm_bundle[0] is None:
            raise LLMNotConfiguredError(LLM_MISSING_FOR_CHAT)
        llm, model_config, ai_models_config = llm_bundle
    except Exception as exc:
        for pending in background_tasks:
            pending.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        log_turn_end(logger_, "error", exc=exc, code="llm_initialization_failed", run_id=query_info.runId)
        error_code, user_message = classify_exception(exc)
        if error_code == "unknown":
            user_message = CHAT_MODEL_START_FAILED
        if protocol == "agui":
            evt = frame(AGUIEventType.RUN_ERROR, message=user_message, code="llm_initialization_failed")
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        else:
            yield create_sse_event("error", {"error": user_message})
        return

    system_prompts_config: dict[str, Any] = await prompts_task

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

    # strictScope rides along inside `filters` (not a separate top-level key)
    # so every downstream consumer that already forwards `filters` straight
    # into `get_accessible_virtual_record_ids` picks it up for free.
    effective_filters: dict[str, Any] = dict(query_info.filters or {})
    if query_info.strictScope:
        effective_filters["strictScope"] = True

    attachments, previous_conversations = await keep_accessible_attachments(
        graph_provider,
        org_id=org_id,
        user_id=user_id,
        is_service_account=bool(user.get("isServiceAccount")),
        attachments=query_info.attachments,
        previous_conversations=query_info.previousConversations,
        logger=logger_,
        connection_sources=connection_limit(user),
    )
    query_dict = {
        "query": query_info.query,
        "limit": query_info.limit,
        "previous_conversations": previous_conversations,
        "historyLabel": query_info.historyLabel,
        "filters": effective_filters,
        "allowedFilters": query_info.allowedFilters,
        "connectionLimit": connection_limit(user),
        "retrievalMode": query_info.retrievalMode,
        "quickMode": query_info.quickMode,
        "chatMode": query_info.chatMode,
        "timezone": query_info.timezone,
        "currentTime": query_info.currentTime,
        "conversationId": query_info.conversationId,
        "projectInstructions": query_info.projectInstructions,
        "attachments": attachments,
        "enableRecordIdShortening": query_info.enableRecordIdShortening,
        "includeRetrievalContext": query_info.includeRetrievalContext,
        "keywordSearchBudget": query_info.keywordSearchBudget,
        "runId": query_info.runId,
        "is_service_account": bool(user.get("isServiceAccount")),
        "resolvedFlowRules": resolved.flow if resolved is not None else None,
        "scheduleFiring": scheduled_turn(request.headers),
        "nodeAuthorization": request.headers.get("authorization"),
    }
    user_info = {
        "userId": user_id,
        "orgId": org_id,
        "userEmail": user.get("email") or "",
        "sendUserInfo": request.query_params.get("sendUserInfo", True),
        "isServiceAccount": bool(user.get("isServiceAccount")),
    }

    user_doc, org_doc, user_context_flag = await asyncio.gather(
        user_doc_task, org_doc_task, user_context_flag_task, return_exceptions=True,
    )
    org_info = enrich_chat_identity(
        user_info, org_id, user_doc, org_doc, user_context_flag, logger_,
    )

    entity_vector_store = await entity_vector_store_task

    client_name = request.headers.get("client-name")

    # `aclosing`: closing this generator (client gone, collector done) must close
    # the bridge now so its producer task is cancelled, not whenever it is GC'd.
    async with aclosing(run_chat_stream(
        query_dict, user_info, llm, policy, logger_,
        retrieval_service=retrieval_service, graph_provider=graph_provider,
        reranker_service=None, config_service=config_service,
        org_info=org_info,
        model_name=query_info.modelName, model_key=query_info.modelKey,
        is_multimodal_llm=is_multimodal_llm, context_length=context_length,
        configured_context_window=model_config.get("contextLength"),
        llm_provider=model_config.get("provider") or "",
        system_prompts_config=system_prompts_config, protocol=protocol,
        client_name=client_name,
        cancellation_registry=cancellation_registry,
        entity_vector_store=entity_vector_store,
    )) as events:
        async for event in events:
            yield event


async def resolved_turn_or_401(
    request: Request, *, agent_id: str | None, conversation_id: str | None
) -> ResolvedTurn | None:
    """The turn Node resolved for this legacy chat request (`X-PipesHub-Resolved`), or None when it sent none
    and today's reads apply. A header that does not verify, or none while the policy store is `on`, is 401
    `RESOLVED_TURN_REFUSED` with its reason; the request is never served from the reads instead."""
    try:
        return await resolved_turn_for(request, agent_id=agent_id, conversation_id=conversation_id)
    except ResolvedTurnRefused as refused:
        raise HTTPException(status_code=refused.http_status, detail=refused.detail()) from None


async def _parse_chat_query(
    request: Request,
    cancellation_registry: RunCancellationRegistry,
    *,
    streaming: bool,
) -> tuple["ChatQuery", ResolvedTurn | None]:
    """Request prelude shared by `/chat` and `/chat/stream`: parse, validate,
    verify the turn Node resolved, reject a `runId` that is already running,
    and record the session."""
    query_info = await _parse_body(request, ChatQuery, "Invalid request parameters")

    resolved = await resolved_turn_or_401(request, agent_id=None, conversation_id=query_info.conversationId)

    # A real HTTP 409 is only possible HERE, before `StreamingResponse` is
    # returned — once the SSE generator below starts, Starlette has already
    # committed the response to 200. See `RunCancellationRegistry.is_active`.
    if query_info.runId and await cancellation_registry.is_active(query_info.runId):
        raise HTTPException(status_code=409, detail=f"runId '{query_info.runId}' is already active")

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
        "streaming": streaming,
    })
    return query_info, resolved


@router.post("/chat", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAI(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    cancellation_registry: RunCancellationRegistry = Depends(get_run_cancellation_registry),
) -> JSONResponse:
    """Non-streaming twin of `/chat/stream`: runs the same agent-loop stream
    and returns its final `completion_data` as JSON (see `stream_collector`).

    Called by Node's `POST /api/v1/conversations/create` and
    `POST /api/v1/conversations/:id/messages`.
    """
    query_info, resolved = await _parse_chat_query(request, cancellation_registry, streaming=False)
    await assert_identity_allowed(
        _chat_principal(request),
        Workload.CHAT,
        config_service=config_service,
        container=getattr(request.app, "container", None),
    )
    stream = with_resolved_stages(resolved, _generate_chat_stream_via_agent_loop(
        request=request,
        query_info=query_info,
        retrieval_service=retrieval_service,
        graph_provider=graph_provider,
        config_service=config_service,
        cancellation_registry=cancellation_registry,
        resolved=resolved,
    ))
    outcome = await collect_stream_outcome(stream, request.is_disconnected)
    return outcome.to_response()


@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
    cancellation_registry: RunCancellationRegistry = Depends(get_run_cancellation_registry),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support.

    Every mode (`internal_search`, `web_search`, `agent`) routes through the
    agent loop (`app.agents.chat_modes.run_chat_stream`) — see that package
    for the mode → tool/prefetch behavior.
    """
    query_info, resolved = await _parse_chat_query(request, cancellation_registry, streaming=True)
    await assert_identity_allowed(
        _chat_principal(request),
        Workload.CHAT,
        config_service=config_service,
        container=getattr(request.app, "container", None),
    )

    stream = with_resolved_stages(resolved, _generate_chat_stream_via_agent_loop(
        request=request,
        query_info=query_info,
        retrieval_service=retrieval_service,
        graph_provider=graph_provider,
        config_service=config_service,
        cancellation_registry=cancellation_registry,
        resolved=resolved,
    ))

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


# Node's conversation routes (conversation:chat) and agent conversation routes
# (agent:execute) both forward here with the caller's token.
@router.post(
    "/chat/cancel",
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT, OAuthScopes.AGENT_EXECUTE))],
)
async def cancel_chat_stream(
    request: Request,
    cancellation_registry: RunCancellationRegistry = Depends(get_run_cancellation_registry),
) -> dict[str, bool]:
    """Cooperatively cancel an in-flight run — one endpoint for both
    assistant (`/chat/stream`) and agent (`/{agent_id}/chat/stream`) runs,
    since the registry is keyed by `runId` alone. Node's own
    `/conversations/:id/cancel` and `/agents/:key/conversations/:id/cancel`
    routes (owner-filtered against Mongo) forward here after their own
    ownership check; this is the second, independent check against the
    `RunOwner` the run was actually registered with.

    Never a 4xx for "already finished"/"unknown" — `{cancelled: false}` —
    only for a genuine mismatch (403) or a malformed `runId` (400, via
    `CancelRunRequest`'s field validator).
    """
    cancel_request = await _parse_body(request, CancelRunRequest, "Invalid request parameters")

    user = getattr(request.state, "user", {}) or {}
    requester = RunOwner(
        user_id=user.get("userId", ""),
        org_id=user.get("orgId", ""),
        conversation_id=cancel_request.conversationId,
    )

    outcome = await cancellation_registry.cancel(cancel_request.runId, requester)
    if outcome == "forbidden":
        raise HTTPException(status_code=403, detail="You do not own this run")
    return {"cancelled": outcome == "cancelled"}
