import json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp
from fastapi import HTTPException
from langchain.schema import HumanMessage
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

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
from app.utils.citations import normalize_citations_and_chunks
from app.utils.logger import create_logger

MAX_TOKENS_THRESHOLD = 80000

# Create a logger for this module
logger = create_logger("streaming")


def count_tokens_in_messages(messages: List[Dict[str, Any]]) -> int:
    """
    Count the total number of tokens in a messages array.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys

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
        if not isinstance(message, dict):
            continue

        # Extract content from message
        content = message.get("content", "")

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
    """Stream content from a signed URL"""
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
    if not tools:
        raise ValueError("Tools are required")

    llm_with_tools = llm.bind_tools(tools)

    hops = 0
    tools_executed = False
    tool_args = []
    tool_results = []
    previous_tokens = count_tokens_in_messages(messages)
    while hops < max_hops:
        # Get response from LLM
        ai: AIMessage = await llm_with_tools.ainvoke(messages)

        # Check if there are tool calls
        if not (isinstance(ai, AIMessage) and getattr(ai, "tool_calls", None)):
            # No more tool calls, add final AI message and break
            messages.append(ai)
            break

        tools_executed = True

        # Yield tool call events
        for call in ai.tool_calls:
            yield {
                "event": "tool_call",
                "data": {
                    "tool_name": call["name"],
                    "tool_args": call.get("args", {}),
                    "call_id": call.get("id")
                }
            }

        # Execute tools
        tool_msgs = []
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

        tool_results_inner= []

        for args,tool in tool_args:
            if tool is None:
                tool_result = json.dumps({
                    "ok": False,
                    "error": f"Unknown tool: {name}"
                })
                tool_results_inner.append(tool_result)
                tool_results.append(tool_result)
                yield {
                    "event": "tool_error",
                    "data": {
                        "tool_name": name,
                        "error": f"Unknown tool: {name}",
                        "call_id": call_id
                    }
                }
                continue

            try:
                    tool_result = await tool.arun(args, **tool_runtime_kwargs)
                    tool_results_inner.append(tool_result)
                    tool_results.append(tool_result)
                    # Parse result for user feedback
                    if tool_result.get("ok", False):

                        yield {
                            "event": "tool_success",
                            "data": {
                                "tool_name": name,
                                "summary": f"Successfully executed {name}",
                                "call_id": call_id,
                                "record_info": tool_result.get("record_info", {})
                            }
                        }
                        # tool_message_content = record_to_message_content(tool_result, final_results)
                        # tool_msgs.append(ToolMessage(content=tool_message_content, tool_call_id=call_id))
                    else:

                        yield {
                            "event": "tool_error",
                            "data": {
                                "tool_name": name,
                                "error": tool_result.get("error", "Unknown error"),
                                "call_id": call_id
                            }
                        }
            except Exception as e:

                    tool_result = {
                        "ok": False,
                        "error": str(e)
                    }
                    tool_results_inner.append(tool_result)
                    tool_results.append(tool_result)
                    yield {
                        "event": "tool_error",
                        "data": {
                            "tool_name": name,
                            "error": str(e),
                            "call_id": call_id
                        }
                    }

        records = [tool_result.get("record",{}) for tool_result in tool_results_inner if tool_result.get("ok")]
        new_tokens = count_tokens_in_records(records)

        message_contents = []
        record_ids = []
        if new_tokens+previous_tokens > MAX_TOKENS_THRESHOLD:

            virtual_record_ids = [tool_result.get("record",{}).get("virtual_record_id") for tool_result in tool_results_inner if tool_result.get("ok")]

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

            if status_code in [202, 500, 503]:
                raise HTTPException(
                    status_code=status_code,
                    content={
                        "status": result.get("status", "error"),
                        "message": result.get("message", "No results found"),
                    }
                )

            if search_results:
                flatten_search_results = await get_flattened_results(search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result,from_tool=True)
                final_tool_results = sorted(flatten_search_results, key=lambda x: (x['virtual_record_id'], x['block_index']))

                message_contents,record_ids = get_message_content_for_tool(final_tool_results, virtual_record_id_to_result,final_results)
                print("message_contents",len(message_contents))
                print("record_ids",record_ids)
                print("tool_call_ids",tool_call_ids)
        else:
            for record in records:
                message_content = record_to_message_content(record,final_results)
                message_contents.append(message_content)
                record_ids.append(record.get("id"))

        msg_ind=0
        for i,tool_result in enumerate(tool_results_inner):
            if tool_result.get("ok") and msg_ind < len(message_contents):
                message_content = message_contents[msg_ind]
                # tool_msgs.append(HumanMessage(content=f"Full record: {message_content}"))
                tool_msgs.append(ToolMessage(content=message_content, tool_call_id=tool_call_ids[record_ids[msg_ind]]))
                msg_ind += 1
            else:
                message_content = tool_result.get("error", "Unknown error")
                tool_msgs.append(ToolMessage(content=message_content, tool_call_id=tool_call_ids_list[i]))
        # Add messages for next iteration
        messages.append(ai)
        messages.extend(tool_msgs)

        hops += 1

    if len(tool_results)>0:
        messages.append(HumanMessage(content="""Now produce the final answer STRICTLY following the previously provided Output format.\n
                CRITICAL REQUIREMENTS:\n
                - Always include block citations (e.g., [R1-2]) wherever the answer is derived from blocks.\n
                - Use only one citation per bracket pair and ensure the numbers correspond to the block numbers shown above.\n
                - Return a single JSON object exactly as specified (answer, reason, confidence, answerMatchType, blockNumbers).
                - Do not list excessive citations for the same point. Include only the top 4-5 most relevant block citations per answer."""))

    # Return the final values as the last yielded item
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
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Incrementally stream the answer portion of an LLM JSON response.
    For each chunk we also emit the citations visible so far.
    Now supports tool calls before generating the final answer.
    """

    # Original streaming logic for the final answer
    full_json_buf: str = ""         # whole JSON as it trickles in
    answer_buf: str = ""            # the running "answer" value (no quotes)
    answer_done = False
    ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
    CITE_BLOCK_RE = re.compile(r'(?:\s*\[\d+])+')
    INCOMPLETE_CITE_RE = re.compile(r'\[[^\]]*$')

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

            normalized, cites = normalize_citations_and_chunks(final_answer, final_results)

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
        print(f"LLM bound with structured output: {llm}")
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

                        normalized, cites = normalize_citations_and_chunks(
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

            normalized, c = normalize_citations_and_chunks(final_answer, final_results)
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
            normalized, c = normalize_citations_and_chunks(answer_buf, final_results)
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
    target_words_per_chunk: int = 3,

) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Enhanced streaming with tool support.
    Incrementally stream the answer portion of an LLM JSON response.
    For each chunk we also emit the citations visible so far.
    Now supports tool calls before generating the final answer.
    """
    records = []

    # Handle tool calls first if tools are provided
    if tools and tool_runtime_kwargs:
        yield {
            "event": "status",
            "data": {"status": "checking_tools", "message": "Checking if tools are needed..."}
        }

        # Execute tools and get updated messages
        final_messages = messages.copy()
        try:
            async for tool_event in execute_tool_calls(llm, final_messages, tools, tool_runtime_kwargs, final_results,virtual_record_id_to_result, blob_store, all_queries, retrieval_service, user_id, org_id, is_multimodal_llm):
                if tool_event.get("event") == "tool_execution_complete":
                    # Extract the final messages and tools_executed status
                    final_messages = tool_event["data"]["messages"]
                    # tool_args = tool_event["data"]["tool_args"]
                    tool_results = tool_event["data"]["tool_results"]
                    if tool_results:
                        records = [r.get("record") for r in tool_results]
                    # tool_event["data"]["tools_executed"]
                else:
                    yield tool_event

            messages = final_messages
        except Exception:
            pass

        if len(messages) > 0 and isinstance(messages[-1], AIMessage):
            final_ai_msg = messages[-1]
            if not getattr(final_ai_msg, "content", None):
                raise HTTPException(status_code=500, detail="Model returned no final content after tool calls")

            # Stream chunks from the existing AI content instead of a single complete event
            existing_content = final_ai_msg.content
            try:
                parsed = json.loads(existing_content)
                final_answer = parsed.get("answer", existing_content)
                reason = parsed.get("reason")
                confidence = parsed.get("confidence")
            except Exception:
                final_answer = existing_content
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

        # Re-bind tools for the final response
        if tools:
            llm = llm.bind_tools(tools)

        yield {
            "event": "status",
            "data": {"status": "generating_answer", "message": "Generating final answer..."}
        }

    # Original streaming logic for the final answer
    full_json_buf: str = ""         # whole JSON as it trickles in
    answer_buf: str = ""            # the running "answer" value (no quotes)
    answer_done = False
    ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
    CITE_BLOCK_RE = re.compile(r'(?:\s*\[\d+])+')
    INCOMPLETE_CITE_RE = re.compile(r'\[[^\]]*$')

    WORD_ITER = re.compile(r'\S+').finditer
    prev_norm_len = 0  # length of the previous normalised answer
    emit_upto = 0
    words_in_chunk = 0

    # Try to bind structured output
    try:
        llm.with_structured_output(AnswerWithMetadata)
        logger.debug(f"LLM bound with structured output: {llm}")
    except Exception as e:
        logger.warning(f"LLM provider or api does not support structured output: {e}")

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

def create_sse_event(event_type: str, data: Union[str, dict, list]) -> str:
    """Create Server-Sent Event format"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
