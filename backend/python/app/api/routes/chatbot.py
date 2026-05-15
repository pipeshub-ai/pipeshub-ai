import asyncio
from collections.abc import AsyncGenerator
import base64
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from jinja2 import Template
import fitz
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes, config_node_constants
from app.config.constants.arangodb import CollectionNames, Connectors
from app.containers.query import QueryAppContainer
from app.events.processor import convert_record_dict_to_record
from app.models.blocks import Block, BlockType, BlocksContainer, CitationMetadata, DataFormat
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
from app.modules.qna.prompt_templates import (
    qna_prompt_with_retrieval_tool,
    qna_prompt_instructions_2,
    qna_prompt_with_retrieval_tool_second_part,
    web_search_system_prompt,
    web_search_user_prompt,
)
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import get_generator_model
from app.utils.cache_helpers import get_cached_user_info
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    enrich_virtual_record_id_to_result_with_fk_children,
    get_flattened_results,
    get_message_content,
    record_to_message_content,
)
from app.utils.fetch_full_record import create_fetch_full_record_tool
from app.utils.execute_query import create_execute_query_tool, has_sql_connector_configured
from app.utils.query_decompose import QueryDecompositionExpansionService
from app.utils.fetch_url_tool import create_fetch_url_tool
from app.utils.streaming import (
    create_sse_event,
    stream_llm_response_with_tools,
)
from app.utils.time_conversion import build_llm_time_context, get_epoch_timestamp_in_ms
from app.utils.web_search_tool import create_web_search_tool

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
    mode: str | None = "json"  # "json" for full metadata, "simple" for answer only
    timezone: str | None = None  # IANA timezone id from the client (e.g., "America/New_York")
    currentTime: str | None = None  # ISO 8601 datetime string from the client
    conversationId: str | None = None  # Passed by Node.js layer for background task tracking
    attachments: list[dict[str, Any]] = []


class AttachmentUploadItem(BaseModel):
    fileName: str
    mimeType: str
    size: int
    contentBase64: str


class AttachmentUploadRequest(BaseModel):
    conversationId: str | None = None
    attachments: list[AttachmentUploadItem]


class InternalSearchToolArgs(BaseModel):
    query: str = Field(description="Search query for internal knowledge retrieval")
    reason: str = Field(
        default="Retrieve relevant internal records for the user question",
        description="Why this retrieval is needed",
    )


def create_internal_search_tool(
    retrieval_service: RetrievalService,
    org_id: str,
    user_id: str,
    limit: int | None,
    filter_groups: dict[str, Any] | None,
    blob_store: "BlobStorage",
    is_multimodal_llm: bool,
    virtual_record_id_to_result: dict[str, Any],
    graph_provider: IGraphDBProvider,
    ref_mapper: CitationRefMapper,
    final_results: list[dict[str, Any]],
):
    """Factory that creates a LangChain tool wrapping retrieval search."""

    @tool("search_internal_knowledge", args_schema=InternalSearchToolArgs)
    async def search_internal_knowledge_tool(
        query: str,
        reason: str = "Retrieve relevant internal records for the user question",
    ) -> dict[str, Any]:
        """Search the company's internal knowledge base for relevant records matching the query.

        Use this tool when you need context from internal documents, messages, emails,
        files, or other knowledge sources to answer the user's question.

        Args:
            query: The search query to find relevant internal records
            reason: Brief explanation of why this search is needed

        Returns: Retrieved record blocks with metadata for citation, or {"ok": false, "error": "..."}.
        """
        try:
            result = await retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=limit,
                filter_groups=filter_groups,
            )

            search_results = result.get("searchResults", [])
            virtual_to_record_map = result.get("virtual_to_record_map", {})
            status_code = result.get("status_code", 500)

            if status_code in [202, 500, 503, 404]:
                return {"ok": False, "error": result.get("message", "Search failed"), "result_type": "internal_search"}

            flattened_results = await get_flattened_results(
                search_results, blob_store, org_id, is_multimodal_llm,
                virtual_record_id_to_result, virtual_to_record_map,
                graph_provider=graph_provider,
            )
            await enrich_virtual_record_id_to_result_with_fk_children(
                virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results
            )

            existing_keys = {
                (r["virtual_record_id"], r["block_index"]) for r in final_results
            }
            temp_final_results = sorted(flattened_results, key=lambda x: (x["virtual_record_id"], x["block_index"]))
            
            for r in flattened_results:
                key = (r["virtual_record_id"], r["block_index"])
                if key not in existing_keys:
                    final_results.append(r)
                    existing_keys.add(key)
            final_results.sort(key=lambda x: (x["virtual_record_id"], x["block_index"]))

            message_content_array, _ = build_message_content_array(temp_final_results, virtual_record_id_to_result,is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper,from_tool=True)

            message_content_array = [item for sublist in message_content_array for item in sublist]
            return message_content_array
        except Exception as e:
            return {"ok": False, "error": f"Internal search failed: {str(e)}", "result_type": "internal_search"}

    return search_internal_knowledge_tool


def _pdf_has_any_ocr_page(file_content: bytes) -> bool:
    with fitz.open(stream=file_content, filetype="pdf") as temp_doc:
        total = len(temp_doc)
        if total == 0:
            return False
        ocr_count = sum(1 for page in temp_doc if OCRStrategy.needs_ocr(page, logger))
    return ocr_count / total >= 0.5


def _build_pdf_image_blocks(file_content: bytes) -> BlocksContainer:
    blocks: list[Block] = []
    with fitz.open(stream=file_content, filetype="pdf") as pdf_doc:
        for idx, page in enumerate(pdf_doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            png_bytes = pix.tobytes("png")
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
    with fitz.open(stream=file_content, filetype="pdf") as pdf_doc:
        return len(pdf_doc)


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


async def _build_llm_user_context_string(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    send_user_info: Any,
) -> str:
    """Build user/org context for the chat LLM user message when sendUserInfo is enabled."""
    if not send_user_info:
        return ""
    user_info, org_info = await get_cached_user_info(graph_provider, user_id, org_id)
    user_info = user_info or {}
    org_name = (org_info or {}).get("name")
    if org_name:
        return (
            "I am the user of the organization. "
            f"My name is {user_info.get('fullName', 'a user')} "
            f"({user_info.get('designation', '')}) "
            f"from {org_name}. "
            "Please provide accurate and relevant information based on the available context."
        )
    return (
        "I am the user. "
        f"My name is {user_info.get('fullName', 'a user')} "
        f"({user_info.get('designation', '')}) "
        "Please provide accurate and relevant information based on the available context."
    )


def get_model_config_for_mode(chat_mode: str) -> dict[str, Any]:
    """Get model configuration based on chat mode and user selection"""
    mode_configs = {
        "analysis": {
            "temperature": 0.3,
            "max_tokens": 8192,
            "system_prompt": "You are an analytical assistant. Provide detailed analysis with insights and patterns."
        },
        "deep_research": {
            "temperature": 0.2,
            "max_tokens": 16384,
            "system_prompt": "You are a research assistant. Provide comprehensive, well-sourced answers with detailed explanations."
        },
        "creative": {
            "temperature": 0.7,
            "max_tokens": 16384,
            "system_prompt": "You are a creative assistant. Provide innovative and imaginative responses while staying relevant."
        },
        "precise": {
            "temperature": 0.05,
            "max_tokens": 16384,
            "system_prompt": "You are a precise assistant. Provide accurate, factual answers with high attention to detail."
        },
        "standard": {
            "temperature": 0.2,
            "max_tokens": 16384,
            "system_prompt": "You are an enterprise questions answering expert"
        },
        "internal_search": {
            "temperature": 0.1,
            "max_tokens": 4096,
            "system_prompt": (
                "You are an assistant. Answer queries in a professional, enterprise-appropriate format. "
                "You MUST ONLY answer based on the provided internal knowledge base documents/attachments. "
                "Do NOT use your own training knowledge. "
                "If the answer is not present in the provided context blocks, respond with: "
                "'This information is not available in the internal knowledge base.'"
            )
        },
        "web_search": {
            "temperature": 0.1,
            "max_tokens": 4096,
            "system_prompt": web_search_system_prompt,
        }
    }
    return mode_configs.get(chat_mode, mode_configs["internal_search"])


_CITATION_SYSTEM_RULES = (
    "\n\n## Citation Rules\n"
    "When the message contains context blocks with Citation IDs (e.g., ref1, ref2), follow these rules:\n"
    "- **Limit citations to the most relevant blocks.** Do NOT cite every sentence — only cite the most important, non-obvious, or specific factual claims.\n"
    "- Cite by embedding the block's Citation ID as a markdown link: [source](ref1).\n"
    "- Use EXACTLY the Citation ID shown in the context. Do NOT invent or modify Citation IDs.\n"
    "- Do NOT manually assign citation numbers — the system numbers them automatically.\n"
    "- If you cannot find the Citation ID for a fact, omit the citation rather than guessing.\n"
)


def _collapse_single_text_user_content(parts: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Use a plain string for a single text block; otherwise return OpenAI-style parts."""
    if not parts:
        return ""
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0].get("text", "")
    return parts


async def _append_conversation_history(
    messages: list[dict[str, Any]],
    previous_conversations: list[dict],
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    ref_mapper: CitationRefMapper | None = None,
    virtual_record_id_to_result: dict[str, Any] | None = None,
    logger: Any = None,
) -> None:
    """Append prior user/assistant turns to the message list (mutates in place).

    When *is_multimodal_llm* is True, image attachments on previous user_query
    messages are fetched from blob storage and included as multimodal content
    blocks alongside the text.

    When *ref_mapper* and *virtual_record_id_to_result* are provided, PDF
    attachments on previous user_query turns are resolved from blob storage
    and appended in the same ``record_to_message_content`` format as current
    query PDFs (Citation IDs + ``virtual_record_id_to_result`` for resolution).
    """
    has_previous_attachments = False
    for conversation in previous_conversations:
        if conversation.get("role") == "user_query":
            text_content = conversation.get("content", "")
            attachments = conversation.get("attachments") or []
            content: str | list[dict[str, Any]] = text_content
            if is_multimodal_llm and attachments and blob_store and org_id:
                from app.utils.chat_helpers import build_multimodal_user_content
                content = await build_multimodal_user_content(
                    text_content, attachments, blob_store, org_id,
                )
                if isinstance(content, list):
                    has_previous_attachments = True

            pdf_history_blocks: list[dict[str, Any]] = []
            if (
                ref_mapper is not None
                and virtual_record_id_to_result is not None
                and blob_store
                and org_id
            ):
                pdf_attachments = [
                    att
                    for att in attachments
                    if isinstance(att, dict)
                    and (att.get("mimeType") or "").lower() == "application/pdf"
                ]
                for att in pdf_attachments:
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        virtual_record_id_to_result[vrid] = record
                        blocks, ref_mapper = record_to_message_content(
                            record,
                            ref_mapper=ref_mapper,
                            is_multimodal_llm=is_multimodal_llm,
                        )
                        pdf_history_blocks.extend(blocks)
                    except Exception as exc:
                        if logger is not None:
                            logger.warning(
                                "Failed to resolve historical PDF attachment vrid=%s: %s",
                                vrid,
                                exc,
                            )

            if pdf_history_blocks:
                parts: list[dict[str, Any]] = []
                if isinstance(content, list):
                    parts.extend(content)
                else:
                    if content:
                        parts.append({"type": "text", "text": content})
                parts.append({"type": "text", "text": "Attached PDF documents:"})
                parts.extend(pdf_history_blocks)
                has_previous_attachments = True
                content = _collapse_single_text_user_content(parts)

            messages.append({"role": "user", "content": content})
        elif conversation.get("role") == "bot_response":
            messages.append({"role": "assistant", "content": conversation.get("content")})
    
    return has_previous_attachments
        


def _build_system_prompt(
    chat_mode: str,
    ai_models_config: dict[str, Any],
    current_time: str | None,
    timezone: str | None,
    custom_prompt_key: str = "customSystemPrompt",
    append_citation_rules: bool = False,
) -> str:
    """Build the system prompt with optional custom override, time context, and citation rules."""
    mode_config = get_model_config_for_mode(chat_mode)
    custom_system_prompt = ai_models_config.get(custom_prompt_key, "")
    if custom_system_prompt:
        mode_config["system_prompt"] +=  f"\n\n{custom_system_prompt}"

    system_prompt = mode_config["system_prompt"]
    time_context = build_llm_time_context(
        current_time=current_time,
        time_zone=timezone,
    )
    if time_context:
        system_prompt += f"\n\n{time_context}"
    if append_citation_rules:
        system_prompt += _CITATION_SYSTEM_RULES

    return system_prompt


async def _build_chat_llm_messages(
    query_info: ChatQuery,
    ai_models_config: dict[str, Any],
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
    user_data: str,
    logger: Any,
    is_multimodal_llm: bool=False,
    has_sql_connector: bool=False,
    blob_store: Any = None,
    org_id: str = "",
) -> tuple[list[dict[str, Any]], CitationRefMapper]:
    """System prompt (with optional custom override), prior turns, then user message with retrieval context."""
    system_prompt = _build_system_prompt(
        chat_mode=query_info.chatMode,
        ai_models_config=ai_models_config,
        current_time=query_info.currentTime,
        timezone=query_info.timezone,
        append_citation_rules=bool(final_results),
    )
    if ai_models_config.get("customSystemPrompt"):
        logger.debug(f"Custom system prompt: {ai_models_config['customSystemPrompt']}")

    ref_mapper = CitationRefMapper()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    await _append_conversation_history(
        messages, query_info.previousConversations,
        is_multimodal_llm=is_multimodal_llm,
        blob_store=blob_store,
        org_id=org_id,
        ref_mapper=ref_mapper,
        virtual_record_id_to_result=virtual_record_id_to_result,
        logger=logger,
    )

    image_blocks: list[dict[str, Any]] = []
    if is_multimodal_llm and blob_store and org_id and query_info.attachments:
        from app.utils.chat_helpers import build_multimodal_user_content
        image_parts = await build_multimodal_user_content(
            "", query_info.attachments, blob_store, org_id,
        )
        if isinstance(image_parts, list):
            image_blocks = [p for p in image_parts if p.get("type") == "image_url"]

    content, ref_mapper = get_message_content(
        final_results, virtual_record_id_to_result, user_data, query_info.query, query_info.mode,
        is_multimodal_llm=is_multimodal_llm, from_tool=False, has_sql_connector=has_sql_connector,
        image_blocks=image_blocks or None,
        ref_mapper=ref_mapper,
    )

    messages.append({"role": "user", "content": content})
    return messages, ref_mapper

async def _build_attachment_llm_messages(
    query_info: ChatQuery,
    ai_models_config: dict[str, Any],
    user_data: str,
    logger: Any,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    has_attachments: bool = True,
    virtual_record_id_to_result: dict[str, Any] = {},
) -> tuple[list[dict[str, Any]], CitationRefMapper]:
    """Build messages for the tool-based retrieval path.

    No retrieval context is pre-populated; the LLM decides whether to call
    ``search_internal_knowledge`` based on the query, conversation history,
    and any attached images.
    """
    system_prompt = _build_system_prompt(
        chat_mode=query_info.chatMode,
        ai_models_config=ai_models_config,
        current_time=query_info.currentTime,
        timezone=query_info.timezone,
        append_citation_rules=False,
    )
    if ai_models_config.get("customSystemPrompt"):
        logger.debug(f"Custom system prompt: {ai_models_config['customSystemPrompt']}")

    ref_mapper = CitationRefMapper()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    has_previous_attachments = await _append_conversation_history(
        messages, query_info.previousConversations,
        is_multimodal_llm=is_multimodal_llm,
        blob_store=blob_store,
        org_id=org_id,
        ref_mapper=ref_mapper,
        virtual_record_id_to_result=virtual_record_id_to_result,
        logger=logger,
    )

    content: list[dict[str, Any]] = []

    rendered_prompt = Template(qna_prompt_with_retrieval_tool).render(
        user_data=user_data,
        query=query_info.query,
        has_attachments=has_attachments,
        has_previous_attachments=has_previous_attachments,
    )
    content.append({"type": "text", "text": rendered_prompt})

    if is_multimodal_llm and blob_store and org_id and query_info.attachments:
        from app.utils.chat_helpers import build_multimodal_user_content
        image_parts = await build_multimodal_user_content(
            "", query_info.attachments, blob_store, org_id,
        )
        if isinstance(image_parts, list):
            image_blocks = [p for p in image_parts if p.get("type") == "image_url"]
            if image_blocks:
                content.append({"type": "text", "text": "Attachments/Image queries (IMPORTANT: If any image below contains a question, you can call search_internal_knowledge for it — treat it exactly as if the user typed that question):"})
                content.extend(image_blocks)

    pdf_attachments = [
        att for att in query_info.attachments
        if isinstance(att, dict)
        and (att.get("mimeType") or "").lower() == "application/pdf"
    ]
    pdf_content_blocks: list[dict[str, Any]] = []
    if pdf_attachments and blob_store and org_id:
        for att in pdf_attachments:
            vrid = att.get("virtualRecordId") or ""
            if not vrid:
                continue
            try:
                record = await blob_store.get_record_from_storage(vrid, org_id)
                if not record:
                    continue
                virtual_record_id_to_result[vrid] = record
                pdf_blocks, ref_mapper = record_to_message_content(
                    record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm
                )
                pdf_content_blocks.extend(pdf_blocks)
            except Exception as exc:
                logger.warning("Failed to resolve PDF attachment vrid=%s: %s", vrid, exc)

    if pdf_content_blocks:
        content.append({"type": "text", "text": "Attached PDF documents:"})
        content.extend(pdf_content_blocks)

    content.append({"type": "text", "text": "</queries>"})
    content.append({"type": "text", "text": qna_prompt_with_retrieval_tool_second_part})

    messages.append({"role": "user", "content": content})
    return messages, ref_mapper


async def _build_web_search_messages(
    query_info: ChatQuery,
    ai_models_config: dict[str, Any],
    original_query: str,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
) -> list[dict[str, Any]]:
    """Build LLM messages for web search mode."""
    system_prompt = _build_system_prompt(
        chat_mode="web_search",
        ai_models_config=ai_models_config,
        current_time=query_info.currentTime,
        timezone=query_info.timezone,
        custom_prompt_key="customSystemPromptWebSearch",
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    await _append_conversation_history(
        messages, query_info.previousConversations,
        is_multimodal_llm=is_multimodal_llm,
        blob_store=blob_store,
        org_id=org_id,
    )

    messages.append({
        "role": "user",
        "content": Template(web_search_user_prompt).render(
            query=original_query,
        )
    })
    return messages


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

async def get_llm_for_chat(config_service: ConfigurationService, model_key: str = None, model_name: str = None, chat_mode: str = "internal_search") -> tuple[BaseChatModel, dict, dict]:
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
                llm = await asyncio.to_thread(get_generator_model, model_provider, llm_config, model_name)
                return llm, llm_config, ai_models_config

        # If user specified only provider, find first matching model
        if model_key:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            default_model_name = model_names[0]
            model_provider = llm_config.get("provider")
            llm = await asyncio.to_thread(get_generator_model, model_provider, llm_config, default_model_name)
            return llm, llm_config, ai_models_config

        # Fallback to first available model
        model_string = llm_config.get("configuration", {}).get("model")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]
        default_model_name = model_names[0]
        model_provider = llm_config.get("provider")
        llm = await asyncio.to_thread(get_generator_model, model_provider, llm_config, default_model_name)
        return llm, llm_config, ai_models_config
    except Exception as e:
        raise ValueError(f"Failed to initialize LLM: {str(e)}")



async def _generate_internal_search_stream(
    request: Request,
    query_info: ChatQuery,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
) -> AsyncGenerator[str, None]:
    """Stream generator for internal knowledge-base search mode."""
    try:
        container = request.app.container
        logger = container.logger()

        yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})

        has_attachments = bool(query_info.attachments)
        is_followup = len(query_info.previousConversations) > 0

        try:
            llm, config, ai_models_config = await get_llm_for_chat(
                config_service,
                query_info.modelKey,
                query_info.modelName,
                query_info.chatMode,
            )
            is_multimodal_llm = config.get("isMultimodal")
            context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

            if llm is None:
                raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

            if config.get("provider").lower() == "ollama":
                query_info.mode = "no_tools"
            else:
                query_info.mode = "simple"

            all_queries = [query_info.query]

            org_id = request.state.user.get("orgId")
            user_id = request.state.user.get("userId")

            blob_store = BlobStorage(logger=logger, config_service=config_service, graph_provider=graph_provider)
            virtual_record_id_to_result: dict[str, Any] = {}

            send_user_info = request.query_params.get("sendUserInfo", True)
            user_data = await _build_llm_user_context_string(
                graph_provider, user_id, org_id, send_user_info,
            )

            use_retrieval_tool = (has_attachments or is_followup) and query_info.mode != "no_tools"
            final_results: list[dict[str, Any]] = []
            
            if use_retrieval_tool:
                # --- Tool path: retrieval exposed as a tool ---
                # Used for attachment queries AND follow-up queries so the LLM
                # decides whether it needs to search the knowledge base.
                logger.info("Tool retrieval path: exposing retrieval as search_internal_knowledge tool (attachments=%s, followup=%s)", has_attachments, is_followup)
                
                messages, ref_mapper = await _build_attachment_llm_messages(
                    query_info,
                    ai_models_config,
                    user_data,
                    logger,
                    is_multimodal_llm=is_multimodal_llm,
                    blob_store=blob_store,
                    org_id=org_id,
                    has_attachments=has_attachments,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                )

                search_tool = create_internal_search_tool(
                    retrieval_service=retrieval_service,
                    org_id=org_id,
                    user_id=user_id,
                    limit=query_info.limit,
                    filter_groups=query_info.filters,
                    blob_store=blob_store,
                    is_multimodal_llm=is_multimodal_llm,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    graph_provider=graph_provider,
                    ref_mapper=ref_mapper,
                    final_results=final_results,
                )

                tools = [search_tool]

                has_sql_connector = await has_sql_connector_configured(graph_provider, user_id, org_id)

                fetch_tool = create_fetch_full_record_tool(virtual_record_id_to_result, org_id, graph_provider)
                deferred_tools = [fetch_tool]
                if has_sql_connector:
                    deferred_tools.append(create_execute_query_tool(
                        config_service=config_service,
                        graph_provider=graph_provider,
                        org_id=org_id,
                        conversation_id=query_info.conversationId,
                        blob_store=blob_store,
                    ))

                

                tool_runtime_kwargs = {
                    "blob_store": blob_store,
                    "graph_provider": graph_provider,
                    "org_id": org_id,
                    "has_sql_connector": has_sql_connector,
                }

            else:
                # --- Standard path: upfront retrieval (first query, no attachments) ---
                yield create_sse_event("status", {"status": "searching", "message": "Searching knowledge base..."})

                result = await retrieval_service.search_with_filters(
                    queries=all_queries,
                    org_id=org_id,
                    user_id=user_id,
                    limit=query_info.limit,
                    filter_groups=query_info.filters,
                )

                search_results = result.get("searchResults", [])
                virtual_to_record_map = result.get("virtual_to_record_map", {})
                status_code = result.get("status_code", 500)

                if status_code in [202, 500, 503, 404]:
                    raise HTTPException(status_code=status_code, detail=result)

                yield create_sse_event("status", {"status": "processing", "message": "Processing search results..."})

                flattened_results = await get_flattened_results(
                    search_results, blob_store, org_id, is_multimodal_llm,
                    virtual_record_id_to_result, virtual_to_record_map,
                    graph_provider=graph_provider,
                )
                await enrich_virtual_record_id_to_result_with_fk_children(
                    virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results
                )

                final_results = sorted(flattened_results, key=lambda x: (x["virtual_record_id"], x["block_index"]))

                has_sql_connector = await has_sql_connector_configured(graph_provider, user_id, org_id)
                tools = []
                if has_sql_connector:
                    tools.append(create_execute_query_tool(
                        config_service=config_service,
                        graph_provider=graph_provider,
                        org_id=org_id,
                        conversation_id=query_info.conversationId,
                        blob_store=blob_store,
                    ))

                messages, ref_mapper = await _build_chat_llm_messages(
                    query_info,
                    ai_models_config,
                    final_results,
                    virtual_record_id_to_result,
                    user_data,
                    logger,
                    is_multimodal_llm=is_multimodal_llm,
                    has_sql_connector=has_sql_connector,
                    blob_store=blob_store,
                    org_id=org_id,
                )

                fetch_tool = create_fetch_full_record_tool(virtual_record_id_to_result, org_id, graph_provider)
                tools.append(fetch_tool)
                tool_runtime_kwargs = {
                    "blob_store": blob_store,
                    "graph_provider": graph_provider,
                    "org_id": org_id,
                    "has_sql_connector": has_sql_connector,
                }
                deferred_tools = []

        except HTTPException as e:
            logger.error(f"HTTPException: {str(e)}", exc_info=True)
            detail = e.detail
            if isinstance(detail, dict):
                yield create_sse_event("error", {
                    "status": detail.get("status", "error"),
                    "message": detail.get("message", "No results found"),
                })
            else:
                yield create_sse_event("error", {
                    "status": "error",
                    "message": str(detail) if detail else f"HTTP {e.status_code} error",
                })
            return
        except Exception as e:
            logger.error(f"Error processing internal search query: {str(e)}", exc_info=True)
            yield create_sse_event("error", {"error": str(e)})
            return

        try:
            async for stream_event in stream_llm_response_with_tools(
                llm=llm,
                messages=messages,
                final_results=final_results,
                all_queries=all_queries,
                retrieval_service=retrieval_service,
                user_id=user_id,
                org_id=org_id,
                virtual_record_id_to_result=virtual_record_id_to_result,
                blob_store=blob_store,
                is_multimodal_llm=is_multimodal_llm,
                context_length=context_length,
                tools=tools,
                tool_runtime_kwargs=tool_runtime_kwargs,
                target_words_per_chunk=1,
                mode=query_info.mode,
                ref_mapper=ref_mapper,
                max_hops=3 if has_attachments else 2,
                conversation_id=query_info.conversationId,
                defer_tool_until_called_name="search_internal_knowledge" if deferred_tools else None,
                deferred_tool=deferred_tools if deferred_tools else None,
            ):
                yield create_sse_event(stream_event["event"], stream_event["data"])
        except Exception as stream_error:
            logger.error(f"Error during LLM streaming: {str(stream_error)}", exc_info=True)
            yield create_sse_event("error", {"error": f"Stream error: {str(stream_error)}"})

    except Exception as e:
        logger.error(f"Error in internal search stream: {str(e)}", exc_info=True)
        yield create_sse_event("error", {"error": str(e)})


async def _generate_web_search_stream(
    request: Request,
    query_info: ChatQuery,
    config_service: ConfigurationService,
) -> AsyncGenerator[str, None]:
    """Stream generator for web search mode."""
    try:
        container = request.app.container
        logger = container.logger()

        yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})

        try:
            original_query = query_info.query

            llm, config, ai_models_config = await get_llm_for_chat(
                config_service,
                query_info.modelKey,
                query_info.modelName,
                query_info.chatMode,
            )
            is_multimodal_llm = config.get("isMultimodal")
            context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

            if llm is None:
                raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

            if config.get("provider").lower() == "ollama":
                query_info.mode = "no_tools"
            else:
                query_info.mode = "simple"

            # Load web search provider configuration
            web_search_config = await config_service.get_config(
                config_node_constants.WEB_SEARCH.value,
                default={},
                use_cache=False,
            )
            web_search_provider_config = None
            if web_search_config and web_search_config.get("providers"):
                providers = web_search_config.get("providers", [])
                default_provider = next(
                    (p for p in providers if p.get("isDefault")), None,
                )
                if default_provider:
                    web_search_provider_config = {
                        "provider": default_provider.get("provider"),
                        "configuration": default_provider.get("configuration", {}),
                    }
                    logger.info(
                        "Web search provider selected",
                        extra={"provider": web_search_provider_config["provider"]},
                    )
            else:
                logger.warning("No web search config found; proceeding without a configured provider")

            # Build messages for web search
            messages = await _build_web_search_messages(
                query_info=query_info,
                ai_models_config=ai_models_config,
                original_query=original_query,
            )

            # Prepare web search tools. Share a single CitationRefMapper across tools so
            # tiny web-ref URLs minted by one tool can be resolved by another (fetch_url
            # may receive a ref minted by web_search).
            ref_mapper = CitationRefMapper()
            tools = [
                create_web_search_tool(web_search_provider_config),
                create_fetch_url_tool(
                    ref_mapper=ref_mapper,
                ),
            ]
            tool_runtime_kwargs = {
                "config_service": config_service,
            }

        except Exception as e:
            logger.error(f"Error setting up web search: {str(e)}", exc_info=True)
            yield create_sse_event("error", {"error": str(e)})
            return

        org_id = request.state.user.get("orgId")
        user_id = request.state.user.get("userId")

        try:
            async for stream_event in stream_llm_response_with_tools(
                llm=llm,
                messages=messages,
                final_results=[],
                all_queries=[query_info.query],
                retrieval_service=None,
                user_id=user_id,
                org_id=org_id,
                virtual_record_id_to_result={},
                blob_store=None,
                is_multimodal_llm=is_multimodal_llm,
                context_length=context_length,
                tools=tools,
                tool_runtime_kwargs=tool_runtime_kwargs,
                target_words_per_chunk=1,
                mode=query_info.mode,
                ref_mapper=ref_mapper,
                chat_mode="web_search",
            ):
                yield create_sse_event(stream_event["event"], stream_event["data"])
        except Exception as stream_error:
            logger.error(f"Error during web search LLM streaming: {str(stream_error)}", exc_info=True)
            yield create_sse_event("error", {"error": f"Stream error: {str(stream_error)}"})

    except Exception as e:
        logger.error(f"Error in web search stream: {str(e)}", exc_info=True)
        yield create_sse_event("error", {"error": str(e)})


_SUPPORTED_ATTACHMENT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
}


def _is_supported_attachment_mime(mime_type: str) -> bool:
    return mime_type.lower() in _SUPPORTED_ATTACHMENT_MIME_TYPES


def _is_image_attachment(mime_type: str) -> bool:
    return mime_type.lower().startswith("image/")


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
    return "bin"

def _collect_effective_attachments(query_info: ChatQuery) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for att in query_info.attachments or []:
        if not isinstance(att, dict):
            continue
        key = str(att.get("recordId") or att.get("virtualRecordId") or "").strip()
        if key:
            merged[key] = att

    for conv in query_info.previousConversations or []:
        if conv.get("role") != "user_query":
            continue
        for att in conv.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            key = str(att.get("recordId") or att.get("virtualRecordId") or "").strip()
            if key and key not in merged:
                merged[key] = att

    return list(merged.values())


def _estimate_record_tokens(record: dict[str, Any]) -> int:
    block_containers = record.get("block_containers", {}) if isinstance(record, dict) else {}
    blocks = block_containers.get("blocks", []) if isinstance(block_containers, dict) else []
    char_count = 0
    for block in blocks:
        data = block.get("data") if isinstance(block, dict) else None
        if isinstance(data, dict):
            if isinstance(data.get("uri"), str):
                char_count += len(data.get("uri", ""))
        elif isinstance(data, str):
            char_count += len(data)
        elif data is not None:
            char_count += len(str(data))
    # Heuristic fallback (~4 chars/token)
    return max(1, char_count // 4) if char_count > 0 else 1


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
    pdf_processor = PyMuPDFOpenCVProcessor(logger=logger, config=config_service)

    for item in payload.attachments:
        if not _is_supported_attachment_mime(item.mimeType):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported attachment type '{item.mimeType}': {item.fileName}. Supported: PDF, JPEG, PNG.",
            )
        if item.size <= 0:
            raise HTTPException(status_code=400, detail=f"Attachment size must be positive: {item.fileName}")

        record_id = str(uuid4())
        virtual_record_id = str(uuid4())
        extension = _attachment_extension(item.fileName, item.mimeType)
        is_image = _is_image_attachment(item.mimeType)

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
        if is_image:
            try:
                block_containers = _build_image_blocks(file_binary, item.mimeType)
                parsed_blocks_by_record[record_id] = block_containers
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to process image attachment {item.fileName}: {str(e)}")
        else:
            try:
                needs_ocr = _pdf_has_any_ocr_page(file_binary)
                if needs_ocr:
                    page_count = _pdf_page_count(file_binary)
                    if ocr_image_pages_used + page_count > OCR_IMAGE_PAGE_CAP:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Scanned attachment page cap exceeded. "
                                f"Maximum allowed combined scanned pages is {OCR_IMAGE_PAGE_CAP}."
                            ),
                        )
                    block_containers = _build_pdf_image_blocks(file_binary)
                    ocr_image_pages_used += page_count
                else:
                    parsed_data = await pdf_processor.parse_document(item.fileName, file_binary)
                    block_containers = await pdf_processor.create_blocks(parsed_data, skip_llm_enrichment=True)
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
                "ocrMode": "image_direct" if needs_ocr else "pymupdf",
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
    if not is_service_account:
        permission_edges = [
            {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": rd['_key'],
                "to_collection": CollectionNames.RECORDS.value,
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": ts,
                "updatedAtTimestamp": ts,
            }
            for rd in record_docs
        ]
        await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value)

    class _NoopVectorStore:
        async def apply(self, ctx: TransformContext) -> bool:
            return True

    sink_orchestrator = SinkOrchestrator(
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=_NoopVectorStore(),
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
        await sink_orchestrator.apply(ctx)

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


@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        query_info = ChatQuery(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request parameters: {str(e)}")

    if query_info.chatMode == "web_search":
        stream = _generate_web_search_stream(
            request=request,
            query_info=query_info,
            config_service=config_service,
        )
    else:
        stream = _generate_internal_search_stream(
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
