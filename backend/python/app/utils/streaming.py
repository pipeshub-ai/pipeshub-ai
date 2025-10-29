import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp
from fastapi import HTTPException
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config.constants.http_status_code import HttpStatusCode
from app.modules.qna.prompt_templates import AnswerWithMetadata
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    count_tokens_in_records,
    get_flattened_results,
    get_message_content_for_tool,
    record_to_message_content,
)
from app.utils.citations import (
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)
from app.utils.logger import create_logger

MAX_TOKENS_THRESHOLD = 80000

# Create a logger for this module
logger = create_logger("streaming")


def count_tokens_in_messages(messages: List[Any]) -> int:
    """
    Count the total number of tokens in a messages array.
    Supports both dict messages and LangChain message objects.

    Args:
        messages: List of message dictionaries or LangChain message objects

    Returns:
        Total number of tokens across all messages
    """
    # Lazy import tiktoken; fall back to a rough heuristic if unavailable
    enc = None
    try:
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    except Exception:
        enc = None

    def count_tokens(text: str) -> int:
        """Count tokens in text using tiktoken or fallback heuristic"""
        if not text:
            return 0
        if enc is not None:
            try:
                return len(enc.encode(text))
            except Exception:
                pass
        # Fallback heuristic: ~4 chars per token
        return max(1, len(text) // 4)

    total_tokens = 0

    for message in messages:
        # Handle LangChain message objects (AIMessage, HumanMessage, ToolMessage, etc.)
        if hasattr(message, "content"):
            content = getattr(message, "content", "")
        # Handle dict messages
        elif isinstance(message, dict):
            content = message.get("content", "")
        else:
            # Skip unknown types
            continue

        # Handle different content types
        if isinstance(content, str):
            total_tokens += count_tokens(content)
        elif isinstance(content, list):
            # Handle content as list of content objects (like in get_message_content)
            for content_item in content:
                if isinstance(content_item, dict):
                    if content_item.get("type") == "text":
                        text_content = content_item.get("text", "")
                        total_tokens += count_tokens(text_content)
                    # Skip image_url and other non-text content for token counting
                elif isinstance(content_item, str):
                    total_tokens += count_tokens(content_item)
        else:
            # Convert other types to string
            total_tokens += count_tokens(str(content))

    return total_tokens


async def stream_content(signed_url: str) -> AsyncGenerator[bytes, None]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(signed_url) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Failed to fetch file content: {response.status}"
                    )
                async for chunk in response.content.iter_chunked(8192):
                    yield chunk
    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to fetch file content from signed URL {str(e)}"
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


async def aiter_llm_stream(llm, messages) -> AsyncGenerator[str, None]:
    """Async iterator for LLM streaming that normalizes content to text.

    The LLM provider may return content as a string or a list of content parts
    (e.g., [{"type": "text", "text": "..."}, {"type": "image_url", ...}]).
    We extract and concatenate only textual parts for streaming.
    """

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

    try:
        if hasattr(llm, "astream"):
            async for part in llm.astream(messages):
                if not part:
                    continue
                content = getattr(part, "content", None)
                text = _stringify_content(content)
                if text:
                    yield text
        else:
            # Non-streaming – yield whole blob once
            response = await llm.ainvoke(messages)
            content = getattr(response, "content", response)
            yield _stringify_content(content)
    except Exception as e:
        logger.error(f"Error in aiter_llm_stream: {str(e)}", exc_info=True)
        raise


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
    is_multimodal_llm: Optional[bool] = False,
    max_hops: int = 1,

) -> AsyncGenerator[Dict[str, Any], tuple[List[Dict], bool]]:
    """
    Execute tool calls if present in the LLM response.
    Yields tool events and returns updated messages and whether tools were executed.
    """
    logger.debug(
        "execute_tool_calls: start | messages=%d tools=%d max_hops=%d is_multimodal_llm=%s",
        len(messages) if isinstance(messages, list) else -1,
        len(tools) if tools else 0,
        max_hops,
        str(is_multimodal_llm),
    )
    if not tools:
        raise ValueError("Tools are required")

    llm_with_tools = llm.bind_tools(tools)

    hops = 0
    tools_executed = False
    tool_args = []
    tool_results = []
    while hops < max_hops:
        # with error handling for provider-level tool failures
        try:
            ai: AIMessage = await llm_with_tools.ainvoke(messages)
        except Exception as e:
            error_str = str(e).lower()
            # Check if this is a tool-related error from the provider (400, tool_use_failed, etc.)
            if any(keyword in error_str for keyword in ['tool_use_failed', 'tool use failed', 'failed to call a function', 'invalid tool', 'function call failed']):
                logger.warning(
                    "execute_tool_calls: provider-level tool failure detected: %s. Applying reflection.",
                    str(e)
                )

                # Add reflection message to guide the LLM away from using tools incorrectly
                valid_tool_names = [t.name for t in tools]
                reflection_content = (
                    f"Error: The AI provider rejected the function call. This usually means:\n"
                    f"1. Invalid arguments were provided to the tool\n"
                    f"2. A non-existent tool was called\n"
                    f"3. The function call format was incorrect\n\n"
                    f"Available tools: {', '.join(valid_tool_names)}\n\n"
                    f"Please provide your final answer directly as a JSON object with this structure:\n"
                    f'{{"answer": "your answer here", "reason": "reasoning", "confidence": "High/Medium/Low", '
                    f'"answerMatchType": "Derived From Blocks/Exact Match/etc", "blockNumbers": [list of block numbers]}}.\n\n'
                    f"Do NOT attempt to call any tools. Provide your answer based on the blocks already provided in the context."
                )

                # Add a human message with the reflection (treating it as system guidance)
                messages.append(HumanMessage(content=reflection_content))

                logger.info("execute_tool_calls: added reflection for provider tool failure, retrying without tools")

                # Continue loop to get a direct answer
                hops += 1
                continue
            else:
                # Non-tool-related error, re-raise
                logger.error("execute_tool_calls: non-tool error during LLM invocation: %s", str(e))
                raise

        # Check if there are tool calls
        if not (isinstance(ai, AIMessage) and getattr(ai, "tool_calls", None)):
            # No more tool calls - don't add the AI message, let the streaming function handle it
            logger.debug("execute_tool_calls: no tool_calls returned; exiting tool loop without adding AI message (will be streamed)")
            break

        # Check if LLM incorrectly made a tool call for unknown tools (e.g., tool name "json")
        # This happens when the model wraps the final answer in a tool call instead of returning it directly
        valid_tool_names = [t.name for t in tools]

        # If there are any invalid tool calls, use reflection to guide the LLM
        invalid_tool_calls = [call for call in ai.tool_calls if call.get("name") not in valid_tool_names]

        if invalid_tool_calls:
            logger.warning(
                "execute_tool_calls: detected invalid tool calls: %s. Using reflection to guide LLM.",
                [call.get("name") for call in invalid_tool_calls]
            )

            # Add the AI message with invalid tool calls
            messages.append(ai)

            # Create reflection messages for each invalid tool call
            for call in invalid_tool_calls:
                call_id = call.get("id")
                tool_name = call.get("name")

                reflection_message = (
                    f"Error: Tool '{tool_name}' is not a valid tool. "
                    f"Available tools are: {', '.join(valid_tool_names)}. "
                    "Please provide your final answer directly as a JSON object with the following structure: "
                    '{"answer": "your answer here", "reason": "reasoning", "confidence": "High/Medium/Low", '
                    '"answerMatchType": "Derived From Blocks/Exact Match/etc", "blockNumbers": [list of block numbers]}. '
                    "Do NOT wrap your response in any tool call."
                )

                messages.append(
                    ToolMessage(
                        content=reflection_message,
                        tool_call_id=call_id
                    )
                )

                logger.info(
                    "execute_tool_calls: added reflection message for invalid tool '%s' with call_id=%s",
                    tool_name,
                    call_id
                )

            # Continue the loop to let the LLM try again with the reflection
            hops += 1
            continue

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
        tool_call_ids = {}
        tool_call_ids_list = []
        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id")
            tool_call_ids_list.append(call_id)
            record_id = args.get("record_id")
            if record_id:
                tool_call_ids[record_id] = call_id
            tool = next((t for t in tools if t.name == name), None)
            tool_args.append((args,tool))

        tool_results_inner = []

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
        tool_names = []
        for (args, tool), call in zip(tool_args, ai.tool_calls):
            tool_name = call["name"]
            call_id = call.get("id")
            tool_names.append(tool_name)
            tool_tasks.append(execute_single_tool(args, tool, tool_name, call_id))

        # Execute all tools in parallel
        tool_results_inner = await asyncio.gather(*tool_tasks, return_exceptions=False)

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

        # Handle both old single-record format and new multi-record format
        records = []
        for tool_result in tool_results_inner:
            if tool_result.get("ok"):
                # New format: multiple records
                if "records" in tool_result:
                    records.extend(tool_result.get("records", []))

        # First, add the AI message with tool calls to messages
        messages.append(ai)

        # Count tokens in current messages (including the AI message with tool calls)
        # This gives us the baseline before adding tool response content
        current_message_tokens = count_tokens_in_messages(messages)

        # Count tokens in the new records that will be added
        new_tokens = count_tokens_in_records(records)

        # Also estimate tokens for the tool message overhead (JSON structure, field names, etc.)
        # Each ToolMessage has overhead: {"ok": true, "records": [...], "record_count": N}
        # Rough estimate: ~50 tokens per tool message for JSON overhead
        # tool_message_overhead = len(tool_results_inner) * 50

        # Calculate total tokens including current messages, new records, and tool message overhead
        # estimated_total_tokens = current_message_tokens + new_tokens + tool_message_overhead

        logger.debug(
            "execute_tool_calls: token_count | current_messages=%d new_records=%d threshold=%d",
            current_message_tokens,
            new_tokens,
            # tool_message_overhead,
            # estimated_total_tokens,
            MAX_TOKENS_THRESHOLD,
        )

        message_contents = []
        record_ids = []
        if new_tokens+current_message_tokens > MAX_TOKENS_THRESHOLD:
            logger.info(
                "execute_tool_calls: tokens exceed threshold; fetching reduced context via retrieval_service"
            )

            virtual_record_ids = [r.get("virtual_record_id") for r in records if r.get("virtual_record_id")]

            result = await retrieval_service.search_with_filters(
                queries=[all_queries[0]],
                org_id=org_id,
                user_id=user_id,
                limit=500,
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

                message_contents,record_ids = get_message_content_for_tool(final_tool_results, virtual_record_id_to_result,final_results)
                logger.debug(
                    "execute_tool_calls: prepared message_contents=%d record_ids=%d",
                    len(message_contents),
                    len(record_ids),
                )
                logger.debug("execute_tool_calls: tool_call_ids keys=%s", list(tool_call_ids.keys()))
        else:
            for record in records:
                message_content = record_to_message_content(record,final_results)
                message_contents.append(message_content)
                record_ids.append(record.get("id"))

        # Build tool messages with actual content
        tool_msgs = []

        for tool_result in tool_results_inner:
            if tool_result.get("ok"):
                tool_msg = {
                    "ok": True,
                    "records": message_contents,
                    "record_count": tool_result.get("record_count", None),
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

    if len(tool_results)>0:
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
    target_words_per_chunk: int = 3,
    mode: Optional[str] = "json",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Incrementally stream the answer portion of an LLM response.
    For each chunk we also emit the citations visible so far.
    Supports both JSON mode (with structured output) and simple mode (direct streaming).
    """

    if mode == "json":
        # Original streaming logic for the final answer
        full_json_buf: str = ""         # whole JSON as it trickles in
        answer_buf: str = ""            # the running "answer" value (no quotes)
        answer_done = False
        ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
        # Match both regular and Chinese brackets for citations
        CITE_BLOCK_RE = re.compile(r'(?:\s*[\[【]\d+[\]】])+')
        INCOMPLETE_CITE_RE = re.compile(r'[\[【][^\]】]*$')

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

                    normalized, cites = normalize_citations_and_chunks_for_agent(final_answer, final_results)

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
        except Exception:
            # If detection fails, fall back to normal path
            pass

        # Try to bind structured output
        try:
            llm.with_structured_output(AnswerWithMetadata)
            logger.info("LLM bound with structured output successfully")
        except Exception as e:
            print(f"LLM provider or api does not support structured output: {e}")

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
                                current_raw, final_results
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

            # Final processing
            try:
                parsed = json.loads(escape_ctl(full_json_buf))
                final_answer = parsed.get("answer", answer_buf)

                normalized, c = normalize_citations_and_chunks_for_agent(final_answer, final_results)
                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": c,
                        "reason": parsed.get("reason"),
                        "confidence": parsed.get("confidence"),
                    },
                }
            except Exception:
                # Fallback if JSON parsing fails
                normalized, c = normalize_citations_and_chunks_for_agent(answer_buf, final_results)
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
        # Match both regular and Chinese brackets for citations
        CITE_BLOCK_RE = re.compile(r'(?:\s*[\[【]R?\d+-?\d+[\]】])+')
        INCOMPLETE_CITE_RE = re.compile(r'[\[【]R?\d*-?\d*$')

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
                normalized, cites = normalize_citations_and_chunks_for_agent(existing_ai_content, final_results)

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
                            current_raw, final_results
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
            normalized, cites = normalize_citations_and_chunks_for_agent(content_buf, final_results)
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
    target_words_per_chunk: int = 2,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Handle JSON mode streaming.
    """
    # Original streaming logic for the final answer
    full_json_buf: str = ""         # whole JSON as it trickles in
    answer_buf: str = ""            # the running "answer" value (no quotes)
    answer_done = False
    ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
    # Match both regular and Chinese brackets for citations
    CITE_BLOCK_RE = re.compile(r'(?:\s*[\[【]\d+[\]】])+')
    INCOMPLETE_CITE_RE = re.compile(r'[\[【][^\]】]*$')

    WORD_ITER = re.compile(r'\S+').finditer
    prev_norm_len = 0  # length of the previous normalised answer
    emit_upto = 0
    words_in_chunk = 0

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

    # Try to bind structured output
    try:
        llm.with_structured_output(AnswerWithMetadata)
        logger.info("LLM bound with structured output successfully")
    except Exception as e:
        logger.warning(f"LLM provider or api does not support structured output: {e}")

    try:
        logger.debug("handle_json_mode: Starting LLM stream")
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

                        normalized, cites = normalize_citations_and_chunks(
                            current_raw, final_results,records
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

        try:
            parsed = json.loads(escape_ctl(full_json_buf))
            final_answer = parsed.get("answer", answer_buf)
            normalized, c = normalize_citations_and_chunks(final_answer, final_results,records)
            yield {
                "event": "complete",
                "data": {
                    "answer": normalized,
                    "citations": c,
                    "reason": parsed.get("reason"),
                    "confidence": parsed.get("confidence"),
                },
            }
        except Exception:
            # Fallback if JSON parsing fails
            normalized, c = normalize_citations_and_chunks(answer_buf, final_results,records)
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

async def handle_simple_mode(
    llm: BaseChatModel,
    messages: List[BaseMessage],
    final_results: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    logger: logging.Logger,
    target_words_per_chunk: int = 2,
) -> AsyncGenerator[Dict[str, Any], None]:
    # Simple mode: stream content directly without JSON parsing
        logger.debug("stream_llm_response_with_tools: simple mode - streaming raw content")
        content_buf: str = ""
        WORD_ITER = re.compile(r'\S+').finditer
        prev_norm_len = 0
        emit_upto = 0
        words_in_chunk = 0
        # Match both regular and Chinese brackets for citations
        CITE_BLOCK_RE = re.compile(r'(?:\s*[\[【]\d+[\]】])+')
        INCOMPLETE_CITE_RE = re.compile(r'[\[【]\d*[\]】]?$')

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
                            current_raw, final_results
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
            normalized, cites = normalize_citations_and_chunks_for_agent(content_buf, final_results)
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
    tools: Optional[List] = None,
    tool_runtime_kwargs: Optional[Dict[str, Any]] = None,
    target_words_per_chunk: int = 2,
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
            async for tool_event in execute_tool_calls(llm, final_messages, tools, tool_runtime_kwargs, final_results,virtual_record_id_to_result, blob_store, all_queries, retrieval_service, user_id, org_id, is_multimodal_llm):

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
                else:
                    logger.debug("stream_llm_response_with_tools: forwarding tool event type=%s", tool_event.get("event"))
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
            async for event in handle_simple_mode(llm, messages, final_results, records, logger, target_words_per_chunk):
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
