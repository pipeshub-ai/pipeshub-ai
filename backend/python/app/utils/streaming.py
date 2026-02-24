import asyncio
import json
import logging
import os
import re
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import aiohttp
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mistralai import ChatMistralAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel

from app.config.constants.http_status_code import HttpStatusCode
from app.modules.parsers.excel.prompt_template import RowDescriptions
from app.modules.qna.prompt_templates import (
    AnswerWithMetadataDict,
    AnswerWithMetadataJSON,
)
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    count_tokens,
    get_flattened_results,
    get_message_content_for_tool,
    record_to_message_content,
)
from app.utils.citations import (
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.logger import create_logger

logger = create_logger("streaming")

opik_tracer = None
api_key = os.getenv("OPIK_API_KEY")
workspace = os.getenv("OPIK_WORKSPACE")
if api_key and workspace:
    try:
        from opik import configure
        from opik.integrations.langchain import OpikTracer
        configure(use_local=False, api_key=api_key, workspace=workspace)
        opik_tracer = OpikTracer()
    except Exception as e:
        logger.warning(f"Error configuring Opik: {e}")
else:
    logger.info("OPIK_API_KEY and/or OPIK_WORKSPACE not set. Skipping Opik configuration.")

MAX_TOKENS_THRESHOLD = 80000
TOOL_EXECUTION_TOKEN_RATIO = 0.5
MAX_REFLECTION_RETRIES_DEFAULT = 2

# TypeVar for generic schema types in structured output functions
SchemaT = TypeVar('SchemaT', bound=BaseModel)

# Legacy Anthropic models that don't support structured output
# New models (claude-4+) support it by default, so only list older ones here
ANTHROPIC_LEGACY_MODEL_PATTERNS = [
    "claude-3",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-2",
    "claude-1",
    "claude-instant",
]


def supports_human_message_after_tool(llm: BaseChatModel) -> bool:
    """
    Check if the LLM provider supports adding a HumanMessage after ToolMessages.

    Some providers (e.g., MistralAI) do not support this message ordering pattern.
    """
    # MistralAI does not support Human message after Tool message
    if isinstance(llm, ChatMistralAI):
        return False
    return True


# Create a logger for this module
parser = PydanticOutputParser(pydantic_object=AnswerWithMetadataJSON)
format_instructions = parser.get_format_instructions()


async def stream_content(signed_url: str, record_id: Optional[str] = None, file_name: Optional[str] = None) -> AsyncGenerator[bytes, None]:
    # Validate that signed_url is actually a string, not a coroutine
    if not isinstance(signed_url, str):
        error_msg = f"Expected signed_url to be a string, but got {type(signed_url).__name__}"
        logger.error(f"❌ {error_msg} | Record ID: {record_id}")
        raise TypeError(error_msg)
    MAX_FILE_NAME_LEN = 200
    # Extract file path from signed URL for logging (remove query parameters for security)
    file_path_info = signed_url[:200] if len(signed_url) > MAX_FILE_NAME_LEN else signed_url  # Default fallback
    try:
        from urllib.parse import urlparse
        parsed_url = urlparse(signed_url)
        # Extract path without query parameters to avoid logging sensitive tokens
        file_path_info = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
    except Exception:
        # If parsing fails, use truncated URL
        file_path_info = signed_url[:200] if len(signed_url) > MAX_FILE_NAME_LEN else signed_url

    # Build log message parts with available information
    log_parts = []
    if record_id:
        log_parts.append(f"Record ID: {record_id}")
    if file_name:
        log_parts.append(f"File name: {file_name}")
    log_parts.append(f"File path: {file_path_info}")
    log_prefix = " | ".join(log_parts)

    # Log truncated presigned URL for debugging (first 150 chars to see structure)
    logger.debug(f"Fetching presigned URL (truncated): {signed_url[:150]}...")

    try:
        # Use a timeout to prevent hanging requests
        timeout = aiohttp.ClientTimeout(total=300, connect=10)

        # Create session - AWS presigned URLs must be used exactly as generated
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Make request - presigned URLs include all necessary authentication in the URL itself
            # We don't modify headers to avoid breaking the signature
            async with session.get(
                signed_url,
                allow_redirects=True
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    # Try to get error details from response body for better debugging
                    error_details = ""
                    try:
                        error_body = await response.text()
                        if error_body:
                            # Truncate long error messages
                            error_details = f" | Error details: {error_body[:500]}"
                    except Exception:
                        pass

                    # Distinguish between different error types
                    if response.status == HttpStatusCode.BAD_REQUEST.value:
                        logger.error(
                            f"❌ BAD REQUEST (400): The presigned URL may be malformed or the request is invalid. "
                            f"This could be: URL encoding issue, malformed query parameters, or invalid signature. "
                            f"{log_prefix}{error_details}"
                        )
                    elif response.status == HttpStatusCode.FORBIDDEN.value:
                        logger.error(
                            f"❌ ACCESS DENIED (403): Failed to fetch file content due to permissions issue. "
                            f"This could be: expired presigned URL, insufficient IAM permissions (s3:GetObject), "
                            f"or bucket policy restrictions. {log_prefix}{error_details}"
                        )
                    elif response.status == HttpStatusCode.NOT_FOUND.value:
                        logger.error(
                            f"❌ FILE NOT FOUND (404): The file may not exist or the key may be incorrect "
                            f"(possibly encoding issue with special characters). {log_prefix}{error_details}"
                        )
                    else:
                        logger.error(
                            f"❌ HTTP {response.status}: Failed to fetch file content. {log_prefix}{error_details}"
                        )
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Failed to fetch file content: {response.status}{error_details}"
                    )
                async for chunk in response.content.iter_chunked(8192):
                    yield chunk
    except aiohttp.ClientError as e:
        logger.error(
            f"❌ NETWORK ERROR: Failed to fetch file content from signed URL: {str(e)} | {log_prefix}"
        )
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to fetch file content from signed URL {str(e)}"
        )


def create_stream_record_response(
    content_stream: AsyncGenerator[bytes, None],
    filename: Optional[str],
    mime_type: Optional[str] = None,
    fallback_filename: Optional[str] = None,
    additional_headers: Optional[Dict[str, str]] = None
) -> StreamingResponse:
    """
    Create a StreamingResponse for file downloads with proper headers.

    This utility function encapsulates the common pattern of creating file download
    responses with Content-Disposition headers and sanitized filenames.

    Args:
        content_stream: The async generator yielding file bytes
        filename: Original filename (will be sanitized automatically)
        mime_type: MIME type for Content-Type header (defaults to "application/octet-stream")
        fallback_filename: Fallback if sanitization results in empty string
        additional_headers: Optional dict for any custom headers (e.g., UTF-8 encoded filenames)

    Returns:
        StreamingResponse configured for file download with proper headers
    """
    safe_filename = sanitize_filename_for_content_disposition(
        filename or "",
        fallback=fallback_filename or "file"
    )

    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"'
    }

    # Merge additional headers if provided
    if additional_headers:
        headers.update(additional_headers)

    media_type = mime_type if mime_type else "application/octet-stream"

    return StreamingResponse(
        content_stream,
        media_type=media_type,
        headers=headers
    )


def find_unescaped_quote(text: str) -> int:
    """Return index of first un-escaped quote (") or -1 if none."""
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '"':
            return i
    return -1


def escape_ctl(raw: str) -> str:
    """Replace literal \n, \r, \t that appear *inside* quoted strings with their escaped forms."""
    string_re = re.compile(r'"(?:[^"\\]|\\.)*"')   # match any JSON string literal

    def fix(match: re.Match) -> str:
        s = match.group(0)
        return (
            s.replace("\n", "\\n")
              .replace("\r", "\\r")
              .replace("\t", "\\t")
        )
    return string_re.sub(fix, raw)

def _stringify_content(content: Union[str, list, dict, None]) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    # Prefer explicit text field
                    if item.get("type") == "text":
                        text_val = item.get("text")
                        if isinstance(text_val, str):
                            parts.append(text_val)
                    # Some providers may return just {"text": "..."}
                    elif "text" in item and isinstance(item["text"], str):
                        parts.append(item["text"])
                    # Ignore non-text parts (e.g., images)
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    # Fallback to stringification
                    parts.append(str(item))
            return "".join(parts)
        # Fallback to stringification for other types
        return str(content)

async def aiter_llm_stream(llm, messages,parts=None) -> AsyncGenerator[str | dict, None]:
    """Async iterator for LLM streaming that normalizes content to text.

    The LLM provider may return content as a string or a list of content parts
    (e.g., [{"type": "text", "text": "..."}, {"type": "image_url", ...}]).
    We extract and concatenate only textual parts for streaming.
    """
    if parts is None:
        parts = []
    if opik_tracer is not None:
        config = {"callbacks": [opik_tracer]}
    else:
        config = {}
    try:
        if hasattr(llm, "astream"):
            async for part in llm.astream(messages, config=config):
                if not part:
                    continue
                parts.append(part)

                if isinstance(part, dict):
                    yield part
                    continue
                else:
                    content = getattr(part, "content", None)
                text = _stringify_content(content)
                if text:
                    yield text
        else:
            logger.info("Using non-streaming mode")
            response = await llm.ainvoke(messages, config=config)
            content = getattr(response, "content", response)
            parts.append(response)

            if isinstance(content, dict):
                yield content
            else:
                text = _stringify_content(content)
                if text:
                    yield text
                else:
                    logger.info("No content found in response")
    except Exception as e:
        logger.error(f"Error in aiter_llm_stream: {str(e)}", exc_info=True)
        raise

# Configuration for Qdrant limits based on context length.
VECTOR_DB_LIMIT_TIERS = [
    (17000, 43),  # For context lengths up to 17k
    (33000, 154),  # For context lengths up to 33k
    (65000, 213),  # For context lengths up to 65k
]
DEFAULT_VECTOR_DB_LIMIT = 266

def get_vectorDb_limit(context_length: int) -> int:
    """Determines the vector db search limit based on the LLM's context length."""
    for length_threshold, limit in VECTOR_DB_LIMIT_TIERS:
        if context_length <= length_threshold:
            return limit
    return DEFAULT_VECTOR_DB_LIMIT

async def execute_tool_calls(
    llm,
    messages: List[Dict],
    tools: List,
    tool_runtime_kwargs: Dict[str, Any],
    final_results: List[Dict[str, Any]],
    virtual_record_id_to_result: Dict[str, Dict[str, Any]],
    blob_store: BlobStorage,
    all_queries: List[str],
    retrieval_service: RetrievalService,
    user_id: str,
    org_id: str,
    context_length:int|None,
    target_words_per_chunk: int = 1,
    is_multimodal_llm: Optional[bool] = False,
    max_hops: int = 1,
) -> AsyncGenerator[Dict[str, Any], tuple[List[Dict], bool]]:
    """
    Execute tool calls if present in the LLM response.
    Yields tool events and returns updated messages and whether tools were executed.
    """

    if not tools:
        raise ValueError("Tools are required")

    llm_to_pass = bind_tools_for_llm(llm, tools)
    if not llm_to_pass:
        logger.warning("Failed to bind tools for LLM, so using structured output")
        llm_to_pass = _apply_structured_output(llm,schema=AnswerWithMetadataDict)

    hops = 0
    tools_executed = False
    tool_args = []
    tool_results = []
    while hops < max_hops:
        # with error handling for provider-level tool failures
        try:
            # Measure LLM invocation latency
            ai = None
            async for event in call_aiter_llm_stream(llm_to_pass, messages, final_results, records=[], target_words_per_chunk=target_words_per_chunk, original_llm=llm):
                if event.get("event") == "complete" or event.get("event") == "error":
                    yield event
                    return
                elif event.get("event") == "tool_calls":
                    ai = event.get("data").get("ai")
                else:
                    yield event

            ai = AIMessage(
                content = ai.content,
                tool_calls = getattr(ai, 'tool_calls', []),
            )
        except Exception as e:
            logger.debug("Error in llm call with tools: %s", str(e))
            break

        # Check if there are tool calls
        if not (isinstance(ai, AIMessage) and getattr(ai, "tool_calls", None)):
            logger.debug("execute_tool_calls: no tool_calls returned; exiting tool loop")
            messages.append(ai)
            break

        tools_executed = True
        logger.debug(
            "execute_tool_calls: tool_calls_detected count=%d",
            len(getattr(ai, "tool_calls", []) or []),
        )

        # Yield tool call events
        for call in ai.tool_calls:
            logger.info(
                "execute_tool_calls: tool_call | name=%s call_id=%s args_keys=%s",
                call.get("name"),
                call.get("id"),
                list((call.get("args") or {}).keys()),
            )
            yield {
                "event": "tool_call",
                "data": {
                    "tool_name": call["name"],
                    "tool_args": call.get("args", {}),
                    "call_id": call.get("id")
                }
            }

        # Execute tools
        tool_args = []
        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id")
            tool = next((t for t in tools if t.name == name), None)
            tool_args.append((args,tool))

        tool_results_inner = []
        valid_tool_names = [t.name for t in tools]
        # Execute all tools in parallel using asyncio.gather
        async def execute_single_tool(args, tool, tool_name, call_id) -> Dict[str, Any]:
            """Execute a single tool and return result with metadata"""
            if tool is None:
                logger.warning("execute_tool_calls: unknown tool requested name=%s", tool_name)
                return {
                    "ok": False,
                    "error": f"Unknown tool: {tool_name}",
                    "tool_name": tool_name,
                    "call_id": call_id
                }

            if tool_name not in valid_tool_names:
                logger.warning("invalid tool requested, name=%s", tool_name)
                return {
                    "ok": False,
                    "error": f"Invalid tool: {tool_name}",
                    "tool_name": tool_name,
                    "call_id": call_id
                }
            try:
                logger.debug(
                    "execute_tool_calls: running tool name=%s call_id=%s args_keys=%s",
                    tool.name,
                    call_id,
                    list(args.keys()),
                )
                tool_result = await tool.arun(args, **tool_runtime_kwargs)
                tool_result["tool_name"] = tool_name
                tool_result["call_id"] = call_id
                return tool_result
            except Exception as e:
                logger.exception(
                    "execute_tool_calls: exception while running tool name=%s call_id=%s",
                    tool_name,
                    call_id,
                )
                return {
                    "ok": False,
                    "error": str(e),
                    "tool_name": tool_name,
                    "call_id": call_id
                }

        # Create parallel tasks for all tools
        tool_tasks = []
        for (args, tool), call in zip(tool_args, ai.tool_calls):
            tool_name = call["name"]
            call_id = call.get("id")
            tool_tasks.append(execute_single_tool(args, tool, tool_name, call_id))

        # Execute all tools in parallel
        tool_results_inner = await asyncio.gather(*tool_tasks, return_exceptions=False)

        records = []
        # Process results and yield events
        for tool_result in tool_results_inner:
            tool_name = tool_result.get("tool_name", "unknown")
            call_id = tool_result.get("call_id")

            if tool_result.get("ok", False):
                tool_results.append(tool_result)
                logger.debug(
                    "execute_tool_calls: tool success name=%s call_id=%s has_record=%s",
                    tool_name,
                    call_id,
                    "record" in tool_result,
                )
                yield {
                    "event": "tool_success",
                    "data": {
                        "tool_name": tool_name,
                        "summary": f"Successfully executed {tool_name}",
                        "call_id": call_id,
                        "record_info": tool_result.get("record_info", {})
                    }
                }
                if "records" in tool_result:
                    records.extend(tool_result.get("records", []))
            else:
                logger.warning(
                    "execute_tool_calls: tool error result name=%s call_id=%s error=%s",
                    tool_name,
                    call_id,
                    tool_result.get("error", "Unknown error"),
                )
                yield {
                    "event": "tool_error",
                    "data": {
                        "tool_name": tool_name,
                        "error": tool_result.get("error", "Unknown error"),
                        "call_id": call_id
                    }
                }

        # First, add the AI message with tool calls to messages
        messages.append(ai)

        message_contents = []

        for record in records:
            message_content = record_to_message_content(record,final_results)
            message_contents.append(message_content)

        current_message_tokens, new_tokens = count_tokens(messages,message_contents)

        MAX_TOKENS_THRESHOLD = int(context_length * TOOL_EXECUTION_TOKEN_RATIO)

        logger.debug(
            "execute_tool_calls: token_count | current_messages=%d new_records=%d threshold=%d",
            current_message_tokens,
            new_tokens,
            MAX_TOKENS_THRESHOLD,
        )

        if new_tokens+current_message_tokens > MAX_TOKENS_THRESHOLD:

            message_contents = []
            logger.info(
                "execute_tool_calls: tokens exceed threshold; fetching reduced context via retrieval_service"
            )

            virtual_record_ids = [r.get("virtual_record_id") for r in records if r.get("virtual_record_id")]
            vector_db_limit =  get_vectorDb_limit(context_length)
            result = await retrieval_service.search_with_filters(
                queries=[all_queries[0]],
                org_id=org_id,
                user_id=user_id,
                limit=vector_db_limit,
                filter_groups=None,
                virtual_record_ids_from_tool=virtual_record_ids,
            )

            search_results = result.get("searchResults", [])
            status_code = result.get("status_code", 500)
            logger.debug(
                "execute_tool_calls: retrieval_service response | status=%s results=%d",
                status_code,
                len(search_results) if isinstance(search_results, list) else 0,
            )

            if status_code in [202, 500, 503]:
                raise HTTPException(
                    status_code=status_code,
                    detail={
                        "status": result.get("status", "error"),
                        "message": result.get("message", "No results found"),
                    }
                )

            if search_results:
                flatten_search_results = await get_flattened_results(search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result,from_tool=True)
                final_tool_results = sorted(flatten_search_results, key=lambda x: (x['virtual_record_id'], x['block_index']))

                message_contents = get_message_content_for_tool(final_tool_results, virtual_record_id_to_result,final_results)
                logger.debug(
                    "execute_tool_calls: prepared message_contents=%d",
                    len(message_contents)
                )

        # Build tool messages with actual content
        tool_msgs = []

        for tool_result in tool_results_inner:
            if tool_result.get("ok"):
                tool_msg = {
                        "ok": True,
                        "records": message_contents,
                        "record_count": tool_result.get("record_count", None),
                        "sql_result": tool_result.get("markdown_result", None),
                        "row_count": tool_result.get("row_count", None),
                        "column_count": tool_result.get("column_count", None),
                        "not_found": tool_result.get("not_found", None),
                    }

                # tool_msgs.append(HumanMessage(content=f"Full record: {message_content}"))
                tool_msgs.append(ToolMessage(content=json.dumps(tool_msg), tool_call_id=tool_result["call_id"]))
            else:
                tool_msg = {
                    "ok": False,
                    "error": tool_result.get("error", "Unknown error"),
                }
                tool_msgs.append(ToolMessage(content=json.dumps(tool_msg), tool_call_id=tool_result["call_id"]))

        # Add messages for next iteration
        logger.debug(
            "execute_tool_calls: appending %d tool messages; next hop",
            len(tool_msgs),
        )
        messages.extend(tool_msgs)

        hops += 1

    if len(tool_results) > 0 and supports_human_message_after_tool(llm):
        messages.append(HumanMessage(content="""Strictly follow the citation guidelines mentioned in the prompt above."""))

    yield {
        "event": "tool_execution_complete",
        "data": {
            "messages": messages,
            "tools_executed": tools_executed,
            "tool_args": tool_args,
            "tool_results": tool_results
        }
    }

async def stream_llm_response(
    llm,
    messages,
    final_results,
    logger,
    target_words_per_chunk: int = 1,
    mode: Optional[str] = "json",
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]] = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Incrementally stream the answer portion of an LLM response.
    For each chunk we also emit the citations visible so far.
    Supports both JSON mode (with structured output) and simple mode (direct streaming).
    """
    if records is None:
        records = []

    if mode == "json":
        # Original streaming logic for the final answer
        full_json_buf: str = ""         # whole JSON as it trickles in
        answer_buf: str = ""            # the running "answer" value (no quotes)
        answer_done = False
        ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
        # Match both regular and Chinese brackets for citations (with proper bracket pairing)
        CITE_BLOCK_RE = re.compile(r'(?:\s*(?:\[\d+\]|【\d+】))+')
        INCOMPLETE_CITE_RE = re.compile(r'(?:\[[^\]]*|【[^】]*)$')

        WORD_ITER = re.compile(r'\S+').finditer
        prev_norm_len = 0  # length of the previous normalised answer
        emit_upto = 0
        words_in_chunk = 0

        # Fast-path: if the last message is already an AI answer, stream that without invoking the LLM again
        try:
            last_msg = messages[-1] if messages else None
            existing_ai_content: Optional[str] = None
            if isinstance(last_msg, AIMessage):
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
                existing_ai_content = last_msg.get("content")

            if existing_ai_content:
                try:
                    parsed = json.loads(existing_ai_content)
                    final_answer = parsed.get("answer", existing_ai_content)
                    reason = parsed.get("reason")
                    confidence = parsed.get("confidence")
                except Exception:
                    final_answer = existing_ai_content
                    reason = None
                    confidence = None

                # Always normalize citations - don't use LLM-generated citations
                normalized, cites = normalize_citations_and_chunks_for_agent(final_answer, final_results, virtual_record_id_to_result, records)

                words = re.findall(r'\S+', normalized)
                for i in range(0, len(words), target_words_per_chunk):
                    chunk_words = words[i:i + target_words_per_chunk]
                    chunk_text = ' '.join(chunk_words)
                    accumulated = ' '.join(words[:i + len(chunk_words)])
                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": accumulated,
                            "citations": cites,  # Use normalized citations
                        },
                    }

                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": cites,  # Use normalized citations
                        "reason": reason,
                        "confidence": confidence,
                    },
                }
                return
        except Exception:
            # If detection fails, fall back to normal path
            pass


        try:
            async for token in aiter_llm_stream(llm, messages):
                full_json_buf += token

                # Look for the start of the "answer" field
                if not answer_buf:
                    match = ANSWER_KEY_RE.search(full_json_buf)
                    if match:
                        after_key = full_json_buf[match.end():]
                        answer_buf += after_key

                elif not answer_done:
                    answer_buf += token

                # Check if we've reached the end of the answer field
                if not answer_done:
                    end_idx = find_unescaped_quote(answer_buf)
                    if end_idx != -1:
                        answer_done = True
                        answer_buf = answer_buf[:end_idx]

                # Stream answer in word-based chunks
                if answer_buf:
                    for match in WORD_ITER(answer_buf[emit_upto:]):
                        words_in_chunk += 1
                        if words_in_chunk == target_words_per_chunk:
                            char_end = emit_upto + match.end()

                            # Include any citation blocks that immediately follow
                            if m := CITE_BLOCK_RE.match(answer_buf[char_end:]):
                                char_end += m.end()

                            emit_upto = char_end
                            words_in_chunk = 0

                            current_raw = answer_buf[:emit_upto]
                            # Skip if we have incomplete citations
                            if INCOMPLETE_CITE_RE.search(current_raw):
                                continue

                            normalized, cites = normalize_citations_and_chunks_for_agent(
                                current_raw, final_results, virtual_record_id_to_result, records
                            )

                            # CRITICAL DEBUG: Log citation generation
                            if not cites and "[R" in current_raw:
                                logger.warning("⚠️ CITATION BUG: Found [R markers but got 0 citations!")
                                logger.warning(f"   - Text has markers: {bool('[R' in current_raw)}")
                                logger.warning(f"   - final_results count: {len(final_results)}")
                                logger.warning(f"   - virtual_record_id_to_result count: {len(virtual_record_id_to_result) if virtual_record_id_to_result else 0}")
                                logger.warning(f"   - records count: {len(records) if records else 0}")

                            chunk_text = normalized[prev_norm_len:]
                            prev_norm_len = len(normalized)

                            yield {
                                "event": "answer_chunk",
                                "data": {
                                    "chunk": chunk_text,
                                    "accumulated": normalized,
                                    "citations": cites,
                                },
                            }

            # Final processing
            try:
                parsed = json.loads(escape_ctl(full_json_buf))
                final_answer = parsed.get("answer", answer_buf)

                normalized, c = normalize_citations_and_chunks_for_agent(final_answer, final_results, virtual_record_id_to_result, records)

                # CRITICAL DEBUG: Log final citation count
                logger.info("📊 CITATION DEBUG - Final complete event:")
                logger.info(f"   - Answer has [R markers: {bool('[R' in final_answer)}")
                logger.info(f"   - Citations generated: {len(c)}")
                if not c and "[R" in final_answer:
                    logger.error("⚠️ CITATION BUG: Answer has [R markers but NO citations created!")
                    logger.error(f"   - final_results: {len(final_results)}")
                    logger.error(f"   - virtual_record_id_to_result: {len(virtual_record_id_to_result) if virtual_record_id_to_result else 0}")
                    logger.error(f"   - records: {len(records) if records else 0}")

                complete_data = {
                        "answer": normalized,
                        "citations": c,  # Use normalized citations
                        "reason": parsed.get("reason"),
                        "confidence": parsed.get("confidence"),
                    }
                # Include referenceData if present (IDs for follow-up queries)
                if parsed.get("referenceData"):
                    complete_data["referenceData"] = parsed.get("referenceData")
                yield {
                    "event": "complete",
                    "data": complete_data,
                }
            except Exception:
                # Fallback if JSON parsing fails
                normalized, c = normalize_citations_and_chunks_for_agent(answer_buf, final_results, virtual_record_id_to_result, records)
                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": c,
                        "reason": None,
                        "confidence": None,
                    },
                }
        except Exception as exc:
            yield {
                "event": "error",
                "data": {"error": f"Error in LLM streaming: {exc}"},
            }
    else:
        # Simple mode: stream content directly without JSON parsing
        logger.debug("stream_llm_response: simple mode - streaming raw content")
        content_buf: str = ""
        WORD_ITER = re.compile(r'\S+').finditer
        prev_norm_len = 0
        emit_upto = 0
        words_in_chunk = 0
        # Match both regular and Chinese brackets for citations with R notation (e.g., [R1-2] or 【R1-2】)
        CITE_BLOCK_RE = re.compile(r'(?:\s*(?:\[R?\d+-?\d+\]|【R?\d+-?\d+】))+')
        INCOMPLETE_CITE_RE = re.compile(r'(?:\[R?\d*-?\d*|【R?\d*-?\d*)$')

        # Fast-path: if the last message is already an AI answer
        try:
            last_msg = messages[-1] if messages else None
            existing_ai_content: Optional[str] = None
            if isinstance(last_msg, AIMessage):
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
                existing_ai_content = last_msg.get("content")

            if existing_ai_content:
                logger.info("stream_llm_response: detected existing AI message (simple mode), streaming directly")
                normalized, cites = normalize_citations_and_chunks_for_agent(existing_ai_content, final_results, virtual_record_id_to_result, records)

                words = re.findall(r'\S+', normalized)
                for i in range(0, len(words), target_words_per_chunk):
                    chunk_words = words[i:i + target_words_per_chunk]
                    chunk_text = ' '.join(chunk_words)
                    accumulated = ' '.join(words[:i + len(chunk_words)])
                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": accumulated,
                            "citations": cites,
                        },
                    }

                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": cites,
                        "reason": None,
                        "confidence": None,
                    },
                }
                return
        except Exception as e:
            logger.debug("stream_llm_response: simple mode fast-path failed: %s", str(e))

        # Stream directly from LLM
        try:
            async for token in aiter_llm_stream(llm, messages):
                content_buf += token

                # Stream content in word-based chunks
                for match in WORD_ITER(content_buf[emit_upto:]):
                    words_in_chunk += 1
                    if words_in_chunk == target_words_per_chunk:
                        char_end = emit_upto + match.end()

                        # Include any citation blocks that immediately follow
                        if m := CITE_BLOCK_RE.match(content_buf[char_end:]):
                            char_end += m.end()

                        emit_upto = char_end
                        words_in_chunk = 0

                        current_raw = content_buf[:emit_upto]
                        # Skip if we have incomplete citations
                        if INCOMPLETE_CITE_RE.search(current_raw):
                            continue

                        normalized, cites = normalize_citations_and_chunks_for_agent(
                            current_raw, final_results, virtual_record_id_to_result, records
                        )

                        chunk_text = normalized[prev_norm_len:]
                        prev_norm_len = len(normalized)

                        yield {
                            "event": "answer_chunk",
                            "data": {
                                "chunk": chunk_text,
                                "accumulated": normalized,
                                "citations": cites,
                            },
                        }

            # Final normalization and emit complete
            normalized, cites = normalize_citations_and_chunks_for_agent(content_buf, final_results, virtual_record_id_to_result, records)
            yield {
                "event": "complete",
                "data": {
                    "answer": normalized,
                    "citations": cites,
                    "reason": None,
                    "confidence": None,
                },
            }
        except Exception as exc:
            logger.error("Error in simple mode LLM streaming", exc_info=True)
            yield {
                "event": "error",
                "data": {"error": f"Error in LLM streaming: {exc}"},
            }



def extract_json_from_string(input_string: str) -> "Dict[str, Any]":
    """
    Extracts a JSON object from a string that may contain markdown code blocks
    or other formatting, and returns it as a Python dictionary.

    Args:
        input_string (str): The input string containing JSON data

    Returns:
        Dict[str, Any]: The extracted JSON object.

    Raises:
        ValueError: If no valid JSON object is found in the input string.
    """
    # Remove markdown code block markers if present
    cleaned_string = input_string.strip()
    cleaned_string = re.sub(r"^```json\s*", "", cleaned_string)
    cleaned_string = re.sub(r"\s*```$", "", cleaned_string)
    cleaned_string = cleaned_string.strip()

    # Find the first '{' and the last '}'
    start_index = cleaned_string.find('{')
    end_index = cleaned_string.rfind('}')

    if start_index == -1 or end_index == -1 or end_index < start_index:
        raise ValueError("No JSON object found in input string")

    json_str = cleaned_string[start_index : end_index + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON structure: {e}") from e


async def handle_json_mode(
    llm: BaseChatModel,
    messages: List[BaseMessage],
    final_results: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    logger: logging.Logger,
    target_words_per_chunk: int = 1,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Handle JSON mode streaming.
    """
    # Fast-path: if the last message is already an AI answer (e.g., from invalid tool call conversion), stream it directly
    try:
        last_msg = messages[-1] if messages else None
        existing_ai_content: Optional[str] = None
        if isinstance(last_msg, AIMessage):
            existing_ai_content = getattr(last_msg, "content", None)
        elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
            existing_ai_content = getattr(last_msg, "content", None)
        elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
            existing_ai_content = last_msg.get("content")

        if existing_ai_content:
            logger.info("stream_llm_response_with_tools: detected existing AI message, streaming directly without LLM call")
            try:
                parsed = json.loads(existing_ai_content)
                final_answer = parsed.get("answer", existing_ai_content)
                reason = parsed.get("reason")
                confidence = parsed.get("confidence")
            except Exception:
                final_answer = existing_ai_content
                reason = None
                confidence = None

            normalized, cites = normalize_citations_and_chunks(final_answer, final_results, records)

            words = re.findall(r'\S+', normalized)
            for i in range(0, len(words), target_words_per_chunk):
                chunk_words = words[i:i + target_words_per_chunk]
                chunk_text = ' '.join(chunk_words)
                accumulated = ' '.join(words[:i + len(chunk_words)])
                yield {
                    "event": "answer_chunk",
                    "data": {
                        "chunk": chunk_text,
                        "accumulated": accumulated,
                        "citations": cites,
                    },
                }

            yield {
                "event": "complete",
                "data": {
                    "answer": normalized,
                    "citations": cites,
                    "reason": reason,
                    "confidence": confidence,
                },
            }
            return
    except Exception as e:
        # If fast-path detection fails, fall back to normal path
        logger.debug("stream_llm_response_with_tools: fast-path failed, falling back to LLM call: %s", str(e))


    try:
        logger.debug("handle_json_mode: Starting LLM stream")
        llm_with_structured_output = _apply_structured_output(llm,schema=AnswerWithMetadataDict)

        async for token in call_aiter_llm_stream(llm_with_structured_output, messages,final_results,records,target_words_per_chunk):
            yield token
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"error": f"Error in LLM streaming: {exc}"},
        }

async def handle_simple_mode(
    llm: BaseChatModel,
    messages: List[BaseMessage],
    final_results: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    logger: logging.Logger,
    target_words_per_chunk: int = 1,
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    # Simple mode: stream content directly without JSON parsing
        logger.debug("stream_llm_response_with_tools: simple mode - streaming raw content")
        content_buf: str = ""
        WORD_ITER = re.compile(r'\S+').finditer
        prev_norm_len = 0
        emit_upto = 0
        words_in_chunk = 0
        # Match both regular and Chinese brackets for citations (with proper bracket pairing)
        CITE_BLOCK_RE = re.compile(r'(?:\s*(?:\[\d+\]|【\d+】))+')
        INCOMPLETE_CITE_RE = re.compile(r'(?:\[[^\]]*|【[^】]*)$')

        # Fast-path: if the last message is already an AI answer
        try:
            last_msg = messages[-1] if messages else None
            existing_ai_content: Optional[str] = None
            if isinstance(last_msg, AIMessage):
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
                existing_ai_content = last_msg.get("content")

            if existing_ai_content:
                logger.info("stream_llm_response_with_tools: detected existing AI message (simple mode), streaming directly")
                normalized, cites = normalize_citations_and_chunks(existing_ai_content, final_results, records)

                words = re.findall(r'\S+', normalized)
                for i in range(0, len(words), target_words_per_chunk):
                    chunk_words = words[i:i + target_words_per_chunk]
                    chunk_text = ' '.join(chunk_words)
                    accumulated = ' '.join(words[:i + len(chunk_words)])
                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": accumulated,
                            "citations": cites,
                        },
                    }

                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": cites,
                        "reason": None,
                        "confidence": None,
                    },
                }
                return
        except Exception as e:
            logger.debug("stream_llm_response_with_tools: simple mode fast-path failed: %s", str(e))

        # Stream directly from LLM
        try:
            logger.debug("handle_simple_mode: Starting LLM stream")
            async for token in aiter_llm_stream(llm, messages):
                content_buf += token

                # Stream content in word-based chunks
                for match in WORD_ITER(content_buf[emit_upto:]):
                    words_in_chunk += 1
                    if words_in_chunk == target_words_per_chunk:
                        char_end = emit_upto + match.end()

                        # Include any citation blocks that immediately follow
                        if m := CITE_BLOCK_RE.match(content_buf[char_end:]):
                            char_end += m.end()

                        emit_upto = char_end
                        words_in_chunk = 0

                        current_raw = content_buf[:emit_upto]
                        # Skip if we have incomplete citations
                        if INCOMPLETE_CITE_RE.search(current_raw):
                            continue

                        normalized, cites = normalize_citations_and_chunks_for_agent(
                            current_raw, final_results, virtual_record_id_to_result, records
                        )

                        chunk_text = normalized[prev_norm_len:]
                        prev_norm_len = len(normalized)

                        yield {
                            "event": "answer_chunk",
                            "data": {
                                "chunk": chunk_text,
                                "accumulated": normalized,
                                "citations": cites,
                            },
                        }

            # Final normalization and emit complete
            normalized, cites = normalize_citations_and_chunks_for_agent(content_buf, final_results, virtual_record_id_to_result, records)
            yield {
                "event": "complete",
                "data": {
                    "answer": normalized,
                    "citations": cites,
                    "reason": "Not provided",
                    "confidence": "Medium",
                },
            }
        except Exception as exc:
            logger.error("Error in simple mode LLM streaming", exc_info=True)
            yield {
                "event": "error",
                "data": {"error": f"Error in LLM streaming: {exc}"},
            }

async def stream_llm_response_with_tools(
    llm,
    messages,
    final_results,
    all_queries,
    retrieval_service,
    user_id,
    org_id,
    virtual_record_id_to_result,
    blob_store,
    is_multimodal_llm,
    context_length:int|None,
    tools: Optional[List] = None,
    tool_runtime_kwargs: Optional[Dict[str, Any]] = None,
    target_words_per_chunk: int = 1,
    mode: Optional[str] = "json",

) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Enhanced streaming with tool support.
    Incrementally stream the answer portion of an LLM JSON response.
    For each chunk we also emit the citations visible so far.
    Now supports tool calls before generating the final answer.
    """
    logger.info(
        "stream_llm_response_with_tools: START | messages=%d tools=%s target_words_per_chunk=%d mode=%s user_id=%s org_id=%s",
        len(messages) if isinstance(messages, list) else -1,
        bool(tools),
        target_words_per_chunk,
        mode,
        user_id,
        org_id,
    )
    records = []

    # Force tools to None in simple mode to avoid any tool-related issues
    if mode != "json":
        tools = None
        tool_runtime_kwargs = None
        logger.debug("stream_llm_response_with_tools: simple mode detected, ignoring tools")

    # Handle tool calls first if tools are provided (only in JSON mode)
    if tools and tool_runtime_kwargs and mode == "json":
        # Execute tools and get updated messages
        final_messages = messages.copy()
        tools_were_called = False
        try:
            logger.info(f"executing tool calls with tools={tools}")

            async for tool_event in execute_tool_calls(
                llm=llm,
                messages=final_messages,
                tools=tools,
                tool_runtime_kwargs=tool_runtime_kwargs,
                final_results=final_results,
                virtual_record_id_to_result=virtual_record_id_to_result,
                blob_store=blob_store,
                all_queries=all_queries,
                retrieval_service=retrieval_service,
                user_id=user_id,
                org_id=org_id,
                context_length=context_length,
                is_multimodal_llm=is_multimodal_llm
            ):

                if tool_event.get("event") == "tool_execution_complete":
                    # Extract the final messages and tools_executed status
                    final_messages = tool_event["data"]["messages"]
                    tools_were_called = tool_event["data"]["tools_executed"]
                    tool_results = tool_event["data"]["tool_results"]
                    if tool_results:
                        # Handle both old and new format
                        records = []
                        for r in tool_results:
                            # New format with multiple records
                            if "records" in r:
                                records.extend(r.get("records", []))
                        logger.debug(
                            "stream_llm_response_with_tools: tool_execution_complete | tool_results=%d records=%d tools_were_called=%s",
                            len(tool_results),
                            len(records),
                            tools_were_called,
                        )
                elif tool_event.get("event") in ["tool_call", "tool_success", "tool_error"]:
                    # First time we see an actual tool event, show the status message
                    if not tools_were_called:
                        yield {
                            "event": "status",
                            "data": {"status": "checking_tools", "message": "Using tools to fetch additional information..."}
                        }
                        tools_were_called = True
                    logger.debug("stream_llm_response_with_tools: forwarding tool event type=%s", tool_event.get("event"))
                    yield tool_event
                elif tool_event.get("event") == "complete" or tool_event.get("event") == "error":
                    yield tool_event
                    return
                else:
                    yield tool_event

            messages = final_messages
        except Exception as e:
            logger.error("Error in execute_tool_calls", exc_info=True)
            # Yield error event instead of raising to allow graceful handling
            yield {
                "event": "error",
                "data": {"error": f"Error during tool execution: {str(e)}"}
            }
            # Return early to prevent further processing
            return

        yield {
            "event": "status",
            "data": {"status": "generating_answer", "message": "Generating final answer..."}
        }

    # Stream the final answer with comprehensive error handling
    try:
        if mode == "json":
            async for event in handle_json_mode(llm, messages, final_results, records, logger, target_words_per_chunk):
                yield event
        else:
            async for event in handle_simple_mode(llm, messages, final_results, records, logger, target_words_per_chunk, virtual_record_id_to_result):
                yield event

        logger.info("stream_llm_response_with_tools: COMPLETE | Successfully completed streaming")
    except Exception as e:
        logger.error("Error during final answer generation", exc_info=True)
        yield {
            "event": "error",
            "data": {"error": f"Error generating final answer: {str(e)}"}
        }

def create_sse_event(event_type: str, data: Union[str, dict, list]) -> str:
    """Create Server-Sent Event format"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class AnswerParserState:
    """State container for answer parsing during streaming."""
    def __init__(self) -> None:
        self.full_json_buf: str = ""
        self.answer_buf: str = ""
        self.answer_done: bool = False
        self.prev_norm_len: int = 0
        self.emit_upto: int = 0
        self.words_in_chunk: int = 0


def _initialize_answer_parser_regex() -> Tuple[re.Pattern, re.Pattern, re.Pattern, Any]:
    """Initialize regex patterns for answer parsing."""
    answer_key_re = re.compile(r'"answer"\s*:\s*"')
    cite_block_re = re.compile(r'(?:\s*(?:\[\d+\]|【\d+】))+')
    incomplete_cite_re = re.compile(r'[\[【][^\]】]*$')
    word_iter = re.compile(r'\S+').finditer
    return answer_key_re, cite_block_re, incomplete_cite_re, word_iter

async def call_aiter_llm_stream(
    llm,
    messages,
    final_results,
    records=None,
    target_words_per_chunk=1,
    reflection_retry_count=0,
    max_reflection_retries=MAX_REFLECTION_RETRIES_DEFAULT,
    original_llm=None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream LLM response and parse answer field from JSON, emitting chunks and final event."""
    state = AnswerParserState()
    answer_key_re, cite_block_re, incomplete_cite_re, word_iter = _initialize_answer_parser_regex()

    parts = []
    async for token in aiter_llm_stream(llm, messages,parts):
        if isinstance(token, dict):
            state.full_json_buf = token

            answer = token.get("answer", "")
            if answer:
                state.answer_buf = answer

                                # Check for incomplete citations at the end of the answer
                safe_answer = answer
                if incomplete_match := incomplete_cite_re.search(answer):
                    # Only process up to the incomplete citation
                    safe_answer = answer[:incomplete_match.start()]

                # Only process if we have new content beyond what we've already emitted
                if len(safe_answer) <= state.emit_upto:
                    # Nothing safe to emit yet, wait for more content
                    continue

                state.emit_upto = len(safe_answer)
                normalized, cites = normalize_citations_and_chunks(
                            safe_answer, final_results, records
                        )

                chunk_text = normalized[state.prev_norm_len:]
                state.prev_norm_len = len(normalized)

                if chunk_text:  # Only yield if there's actual content to emit
                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": normalized,
                            "citations": cites,
                        },
                    }

            continue

        state.full_json_buf += token
        # Look for the start of the "answer" field
        if not state.answer_buf:
            match = answer_key_re.search(state.full_json_buf)
            if match:
                after_key = state.full_json_buf[match.end():]
                state.answer_buf += after_key
        elif not state.answer_done:
            state.answer_buf += token

        # Check if we've reached the end of the answer field
        if not state.answer_done:
            end_idx = find_unescaped_quote(state.answer_buf)
            if end_idx != -1:
                state.answer_done = True
                state.answer_buf = state.answer_buf[:end_idx]
        # Stream answer in word-based chunks
        if state.answer_buf:
            # Process words from current emit position
            words_to_process = list(word_iter(state.answer_buf[state.emit_upto:]))

            if words_to_process:
                # Process words until we reach the threshold
                for match in words_to_process:
                    # Increment word counter
                    state.words_in_chunk += 1

                    # Check if we've reached the threshold
                    if state.words_in_chunk >= target_words_per_chunk:
                        char_end = state.emit_upto + match.end()

                        # Include any citation blocks that immediately follow
                        if m := cite_block_re.match(state.answer_buf[char_end:]):
                            char_end += m.end()

                        current_raw = state.answer_buf[:char_end]
                        # Skip if we have incomplete citations
                        incomplete_match = incomplete_cite_re.search(current_raw)
                        if incomplete_match:
                            # Don't update emit_upto or reset counter if we skip due to incomplete citations
                            # This allows the next token to complete the citation
                            # Reset words_in_chunk to threshold - 1 so we'll check again on next token
                            state.words_in_chunk = target_words_per_chunk - 1
                            break  # Break out of word iteration, wait for more tokens

                        # Only update emit_upto and reset counter if we're actually yielding
                        state.emit_upto = char_end
                        state.words_in_chunk = 0

                        normalized, cites = normalize_citations_and_chunks(
                            current_raw, final_results,records
                        )

                        chunk_text = normalized[state.prev_norm_len:]
                        state.prev_norm_len = len(normalized)

                        yield {
                            "event": "answer_chunk",
                            "data": {
                                "chunk": chunk_text,
                                "accumulated": normalized,
                                "citations": cites,
                            },
                        }
                        # Break after yielding to avoid re-processing the same words on next token
                        break

    ai = None
    tool_calls_happened = True
    for part in parts:
        if isinstance(part, dict):
            logger.info("part is a dict, breaking from loop")
            tool_calls_happened = False
            break
        if ai is None:
            ai = part
        else:
            ai += part

    if tool_calls_happened:
        tool_calls = getattr(ai, 'tool_calls', [])
        if tool_calls:
            yield {
                "event": "tool_calls",
                "data": {
                    "ai": ai,
                },
            }
            logger.info("tool_calls detected, returning")
            return

    # Try to parse the full JSON buffer
    try:
        response_text = state.full_json_buf
        logger.debug("[streaming] before cleanup_content (full_json_buf) type=%s", type(response_text).__name__)
        if  isinstance(response_text, str):
            response_text = cleanup_content(response_text)

        try:
            if isinstance(response_text, str):
                parsed = parser.parse(response_text)
            else:
                parsed = AnswerWithMetadataJSON.model_validate(response_text)
        except Exception as e:
            # JSON parsing failed - use reflection to guide the LLM
            if reflection_retry_count < max_reflection_retries:
                yield {"event": "restreaming","data": {}}
                yield {"event": "status", "data": {"status": "processing", "message": "Rethinking..."}}
                parse_error = str(e)
                logger.warning(
                    "JSON parsing failed for LLM response with error: %s. Using reflection to guide LLM to proper format. Retry count: %d.",
                    parse_error,
                    reflection_retry_count
                )

                # Create reflection message to guide the LLM
                reflection_message = HumanMessage(
                    content=(f"""The previous response failed validation with the following error: {parse_error}. {format_instructions}"""
                ))
                # Add the reflection message to the messages list
                updated_messages = messages.copy()

                # Ensure response_text is a string for AIMessage (can be dict from structured output)
                ai_message_content = response_text if isinstance(response_text, str) else json.dumps(response_text)
                ai_message = AIMessage(
                    content=ai_message_content,
                )
                updated_messages.append(ai_message)

                updated_messages.append(reflection_message)

                if original_llm:
                    llm = _apply_structured_output(original_llm,schema=AnswerWithMetadataDict)
                async for event in call_aiter_llm_stream(
                    llm,
                    updated_messages,
                    final_results,
                    records,
                    target_words_per_chunk,
                    reflection_retry_count + 1,
                    max_reflection_retries,
                    original_llm=original_llm,
                ):
                    yield event
                return
            else:
                logger.error(
                    "call_aiter_llm_stream: JSON parsing failed after %d reflection attempts. Falling back to answer_buf.",
                    max_reflection_retries
                )
                # After max retries, fallback to using answer_buf if available
                if state.answer_buf:
                    normalized, c = normalize_citations_and_chunks(state.answer_buf, final_results, records)
                    yield {
                        "event": "complete",
                        "data": {
                            "answer": normalized,
                            "citations": c,
                            "reason": None,
                            "confidence": None,
                        },
                    }
                else:
                    # No answer at all, return error
                    yield {
                        "event": "error",
                        "data": {
                            "error": "LLM did not provide any appropriate answer"
                        },
                    }
                return

        final_answer = parsed.answer if parsed.answer else state.answer_buf
        normalized, c = normalize_citations_and_chunks(final_answer, final_results, records)
        complete_data = {
            "answer": normalized,
            "citations": c,
            "reason": parsed.reason,
            "confidence": parsed.confidence,
        }
        # Include referenceData if present (IDs for follow-up queries)
        if hasattr(parsed, 'referenceData') and parsed.referenceData:
            complete_data["referenceData"] = parsed.referenceData
        yield {
            "event": "complete",
            "data": complete_data,
        }
    except Exception as e:
        logger.error("Error in call_aiter_llm_stream", exc_info=True)
        yield {"event": "error","data": {"error": f"Error in call_aiter_llm_stream: {str(e)}"}}
        return

def bind_tools_for_llm(llm, tools: List[object]) -> BaseChatModel|bool:
    """
    Bind tools to the LLM.
    """
    try:
        return llm.bind_tools(tools)
    except Exception:
        logger.warning("Tool binding failed, using llm without tools.")
        return False

def _apply_structured_output(llm: BaseChatModel,schema) -> BaseChatModel:
    if isinstance(llm, (ChatGoogleGenerativeAI,ChatAnthropic,ChatOpenAI,ChatMistralAI,AzureChatOpenAI,ChatBedrock)):

        additional_kwargs = {}
        if isinstance(llm, ChatAnthropic):
            model_str = getattr(llm, 'model', None)
            if not model_str:
                logger.warning("model name not found, using non-structured LLM")
                return llm
            is_legacy_model = any(pattern in model_str for pattern in ANTHROPIC_LEGACY_MODEL_PATTERNS)
            if is_legacy_model:
                logger.info("Legacy Anthropic model detected, using non-structured LLM")
                return llm

            additional_kwargs["stream"] = True

        if not isinstance(llm, ChatBedrock):
            additional_kwargs["method"] = "json_schema"

        try:
            model_with_structure = llm.with_structured_output(
                schema,
                **additional_kwargs
            )
            logger.info("Using structured output")
            return model_with_structure
        except Exception as e:
            logger.warning("Failed to apply structured output, falling back to default. Error: %s", str(e))
            logger.info("Using non-structured LLM")

    logger.info("Using non-structured LLM")
    return llm


def cleanup_content(response_text: str) -> str:
    # Diagnostic: catch 'tuple' (or other non-str) has no attribute 'startswith'
    if not isinstance(response_text, str):
        logger.warning(
            "[streaming cleanup_content] response_text is not str | type=%s repr=%s",
            type(response_text).__name__, repr(response_text)[:300],
        )
        response_text = str(response_text)
    response_text = response_text.strip()
    if '</think>' in response_text:
            response_text = response_text.split('</think>')[-1]
    logger.debug("[streaming cleanup_content] before startswith/endswith type=%s", type(response_text).__name__)
    if response_text.startswith("```json"):
        response_text = response_text.replace("```json", "", 1)
    if response_text.endswith("```"):
        response_text = response_text.rsplit("```", 1)[0]
    response_text = response_text.strip()
    return response_text


async def invoke_with_structured_output_and_reflection(
    llm: BaseChatModel,
    messages: List,
    schema: Type[SchemaT],
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
) -> Optional[SchemaT]:
    """
    Invoke LLM with structured output and automatic reflection on parse failure.

    Args:
        llm: The LangChain chat model to use
        messages: List of messages to send to the LLM
        schema: Pydantic model class to validate the response against
        max_retries: Maximum number of reflection retries on parse failure

    Returns:
        Validated Pydantic model instance, or None if parsing fails after all retries
    """
    llm_with_structured_output = _apply_structured_output(llm, schema=schema)

    try:
        response = await llm_with_structured_output.ainvoke(messages)
    except Exception as e:
        logger.error(f"LLM invocation failed: {e}")
        return None

    # Try to parse the response
    parsed_response = None
    try:

        if isinstance(response, dict):
            if 'content' in response:
                # Response is a dict with 'content' key (e.g., Bedrock non-structured response)
                logger.debug("Response is a dict with 'content' key, extracting content for parsing")
                response_content = response['content']
                logger.debug("[streaming] before cleanup_content (dict content) type=%s", type(response_content).__name__)
                response_text = cleanup_content(response_content)
                logger.debug(f"Cleaned response content length: {len(response_text)} chars")
                parsed_response = schema.model_validate_json(response_text)
                logger.debug("Dict with content response validated successfully")
            else:
                logger.debug("Response is a dict, validating directly")
                response_content = json.dumps(response)
                parsed_response = schema.model_validate(response)
                logger.debug("Dict response validated successfully")
        else:
            if hasattr(response, 'content'):
                # Response is an AIMessage or string
                logger.debug("Response is AIMessage, extracting content for parsing")
                response_content = response.content
                logger.debug("[streaming] before cleanup_content (AIMessage.content) type=%s", type(response_content).__name__)
                response_text = cleanup_content(response_content)
                logger.debug(f"Cleaned response content length: {len(response_text)} chars")
                parsed_response = schema.model_validate_json(response_text)
                logger.debug("AIMessage/string response validated successfully")
            else:
                # Response is already a Pydantic model
                logger.debug("Response is a Pydantic model, extracting and validating")
                response_content = response.model_dump_json()
                parsed_response = schema.model_validate(response.model_dump())
                logger.debug("Pydantic model response validated successfully")

        return parsed_response

    except Exception as parse_error:
        # Attempt reflection: ask LLM to correct its response
        logger.warning(f"Initial parse failed: {parse_error}. Attempting reflection...")

        reflection_messages = list(messages)  # Copy the original messages

        # Add the failed response to context
        reflection_messages.append(AIMessage(content=response_content))

        reflection_prompt = f"""Your previous response could not be parsed correctly.
Error: {str(parse_error)}

Please correct your response to match the expected JSON schema. Ensure all fields are properly formatted and all required fields are present.
Respond only with valid JSON that matches the schema."""

        reflection_messages.append(HumanMessage(content=reflection_prompt))

        for attempt in range(max_retries):
            try:
                reflection_response = await llm_with_structured_output.ainvoke(reflection_messages)
                if isinstance(reflection_response, dict):
                    if 'content' in reflection_response:
                        # Response is a dict with 'content' key (e.g., Bedrock non-structured response)
                        logger.debug("Reflection response is a dict with 'content' key, extracting content for parsing")
                        reflection_content = reflection_response['content']
                        logger.debug("[streaming] before cleanup_content (reflection dict content) type=%s", type(reflection_content).__name__)
                        reflection_text = cleanup_content(reflection_content)
                        logger.debug(f"Cleaned reflection content length: {len(reflection_text)} chars")
                        parsed_response = schema.model_validate_json(reflection_text)
                    else:
                        logger.debug("Reflection response is a dict, validating directly")
                        reflection_content = json.dumps(reflection_response)
                        parsed_response = schema.model_validate(reflection_response)
                else:
                    if hasattr(reflection_response, 'content'):
                        logger.debug("Reflection response is AIMessage, extracting content")
                        reflection_content = reflection_response.content
                        logger.debug("[streaming] before cleanup_content (reflection AIMessage.content) type=%s", type(reflection_content).__name__)
                        reflection_text = cleanup_content(reflection_content)
                        logger.debug(f"Cleaned reflection content length: {len(reflection_text)} chars")
                        parsed_response = schema.model_validate_json(reflection_text)
                    else:
                        logger.debug("Reflection response is a Pydantic model, extracting and validating")
                        reflection_content = reflection_response.model_dump_json()
                        parsed_response = schema.model_validate(reflection_response.model_dump())

                logger.info(f"Reflection successful on attempt {attempt + 1}")
                return parsed_response

            except Exception as reflection_error:
                logger.warning(f"Reflection attempt {attempt + 1} failed: {reflection_error}")
                if attempt < max_retries - 1:
                    # Update messages for next retry
                    reflection_messages.append(AIMessage(content=reflection_content))
                    reflection_messages.append(HumanMessage(content=f"Still incorrect. Error: {str(reflection_error)}. Please try again."))

        logger.error("All reflection attempts failed")
        return None


async def invoke_with_row_descriptions_and_reflection(
    llm: BaseChatModel,
    messages: List,
    expected_count: int,
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
) -> Optional[RowDescriptions]:
    """
    Invoke LLM with row description output and validate count matches expected.

    If the LLM returns an incorrect number of descriptions, performs reflection
    to give it one chance to correct the count mismatch.

    Args:
        llm: The LangChain chat model to use
        messages: List of messages to send to the LLM
        expected_count: Expected number of row descriptions
        max_retries: Maximum number of reflection retries on parse failure (default: 2)

    Returns:
        Validated RowDescriptions instance with correct count, or None if validation fails
    """
    # First, try to get a parsed response using the standard reflection function
    parsed_response = await invoke_with_structured_output_and_reflection(
        llm, messages, RowDescriptions, max_retries
    )

    if parsed_response is None:
        logger.warning("Failed to parse RowDescriptions after initial attempts")
        return None

    # Validate the count matches expected
    actual_count = len(parsed_response.descriptions)

    if actual_count == expected_count:
        logger.debug(f"Row count validation passed: {actual_count} descriptions")
        return parsed_response

    # Count mismatch detected - perform reflection to correct it
    logger.warning(
        f"Row count mismatch: LLM returned {actual_count} descriptions "
        f"but {expected_count} were expected. Attempting reflection..."
    )

    # Build reflection messages
    reflection_messages = list(messages)
    reflection_messages.append(
        AIMessage(content=json.dumps(parsed_response.model_dump()))
    )

    reflection_prompt = f"""Your previous response contained {actual_count} descriptions, but exactly {expected_count} descriptions are required.

CRITICAL: You must provide EXACTLY {expected_count} descriptions - one for each row, in the same order they were provided.

Please correct your response to include exactly {expected_count} descriptions. Do not skip any rows, do not combine rows, and do not split rows.

Respond with a valid JSON object:
{{
    "descriptions": [
        "Description for row 1",
        "Description for row 2",
        ...
        "Description for row {expected_count}"
    ]
}}"""

    reflection_messages.append(HumanMessage(content=reflection_prompt))

    # Try reflection once (per user preference: 1 reflection attempt for count mismatches)
    try:
        reflection_response = await invoke_with_structured_output_and_reflection(
            llm, reflection_messages, RowDescriptions, max_retries=1
        )

        if reflection_response is None:
            logger.error("Reflection failed to parse response")
            return None

        # Validate the count again
        reflection_count = len(reflection_response.descriptions)

        if reflection_count == expected_count:
            logger.info(
                f"Reflection successful: corrected from {actual_count} to {reflection_count} descriptions"
            )
            return reflection_response
        else:
            logger.error(
                f"Reflection failed: still have {reflection_count} descriptions "
                f"instead of {expected_count}"
            )
            return None

    except Exception as e:
        logger.error(f"Reflection attempt failed with error: {e}")
        return None
