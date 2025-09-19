import json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp
from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage

from app.config.constants.http_status_code import HttpStatusCode
from app.modules.qna.prompt_templates import AnswerWithMetadata
from app.utils.citations import normalize_citations_and_chunks


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
    """Async iterator for LLM streaming"""
    if hasattr(llm, "astream"):
        async for part in llm.astream(messages):
            if part and getattr(part, "content", ""):
                yield part.content
    else:
        # Non-streaming – yield whole blob once
        response = await llm.ainvoke(messages)
        yield getattr(response, "content", str(response))


async def execute_tool_calls(
    llm,
    messages: List[Dict],
    tools: List,
    tool_runtime_kwargs: Dict[str, Any],
    max_hops: int = 4
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
        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id")

            tool = next((t for t in tools if t.name == name), None)

            if tool is None:
                tool_result = json.dumps({
                    "ok": False,
                    "error": f"Unknown tool: {name}"
                })
                yield {
                    "event": "tool_error",
                    "data": {
                        "tool_name": name,
                        "error": f"Unknown tool: {name}",
                        "call_id": call_id
                    }
                }
            else:
                try:
                    tool_result = await tool.arun(args, **tool_runtime_kwargs)

                    # Parse result for user feedback
                    try:
                        parsed_result = json.loads(tool_result)
                        if parsed_result.get("ok", False):
                            yield {
                                "event": "tool_success",
                                "data": {
                                    "tool_name": name,
                                    "summary": f"Successfully executed {name}",
                                    "call_id": call_id,
                                    "record_info": parsed_result.get("record_info", {})
                                }
                            }
                        else:
                            yield {
                                "event": "tool_error",
                                "data": {
                                    "tool_name": name,
                                    "error": parsed_result.get("error", "Unknown error"),
                                    "call_id": call_id
                                }
                            }
                    except json.JSONDecodeError:
                        yield {
                            "event": "tool_success",
                            "data": {
                                "tool_name": name,
                                "summary": f"Tool {name} executed successfully",
                                "call_id": call_id
                            }
                        }

                except Exception as e:
                    tool_result = json.dumps({
                        "ok": False,
                        "error": str(e)
                    })
                    yield {
                        "event": "tool_error",
                        "data": {
                            "tool_name": name,
                            "error": str(e),
                            "call_id": call_id
                        }
                    }

            tool_msgs.append(ToolMessage(content=tool_result, tool_call_id=call_id))

        # Add messages for next iteration
        messages.append(ai)
        messages.extend(tool_msgs)
        hops += 1

    # Return the final values as the last yielded item
    yield {
        "event": "tool_execution_complete",
        "data": {
            "messages": messages,
            "tools_executed": tools_executed
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

    # Handle tool calls first if tools are provided
    if tools and tool_runtime_kwargs:
        yield {
            "event": "status",
            "data": {"status": "checking_tools", "message": "Checking if tools are needed..."}
        }

        # Execute tools and get updated messages
        final_messages = messages.copy()
        async for tool_event in execute_tool_calls(llm, final_messages, tools, tool_runtime_kwargs):
            if tool_event.get("event") == "tool_execution_complete":
                # Extract the final messages and tools_executed status
                final_messages = tool_event["data"]["messages"]
                tool_event["data"]["tools_executed"]
            else:
                yield tool_event

        # Update messages with the final state
        messages = final_messages

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


async def stream_llm_response_with_tools_integrated(
    llm,
    messages,
    final_results,
    tools: Optional[List] = None,
    tool_runtime_kwargs: Optional[Dict[str, Any]] = None,
    target_words_per_chunk: int = 5,
    max_tool_hops: int = 4
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Alternative implementation that fully integrates tools into the streaming process.
    This version executes tools inline and continues streaming seamlessly.
    """
    if not tools or not tool_runtime_kwargs:
        # No tools, use original implementation
        async for event in stream_llm_response_with_tools(llm, messages, final_results, target_words_per_chunk=target_words_per_chunk):
            yield event
        return

    llm_with_tools = llm.bind_tools(tools)
    hops = 0

    # Try structured output
    try:
        llm_with_tools.with_structured_output(AnswerWithMetadata)
    except Exception as e:
        print(f"LLM provider does not support structured output: {e}")

    while hops < max_tool_hops:
        # Get response from LLM
        response = await llm_with_tools.ainvoke(messages)

        # Check for tool calls
        if isinstance(response, AIMessage) and getattr(response, "tool_calls", None):
            # Execute tools
            for call in response.tool_calls:
                yield {
                    "event": "tool_call",
                    "data": {
                        "tool_name": call["name"],
                        "tool_args": call.get("args", {}),
                        "hop": hops + 1
                    }
                }

                name = call["name"]
                args = call.get("args", {}) or {}
                call_id = call.get("id")

                tool = next((t for t in tools if t.name == name), None)

                if tool:
                    try:
                        tool_result = await tool.arun(args, **tool_runtime_kwargs)
                        yield {
                            "event": "tool_success",
                            "data": {
                                "tool_name": name,
                                "summary": f"Executed {name}",
                                "hop": hops + 1
                            }
                        }
                    except Exception as e:
                        tool_result = json.dumps({"ok": False, "error": str(e)})
                        yield {
                            "event": "tool_error",
                            "data": {"tool_name": name, "error": str(e)}
                        }
                else:
                    tool_result = json.dumps({"ok": False, "error": f"Unknown tool: {name}"})

                messages.append(response)
                messages.append(ToolMessage(content=tool_result, tool_call_id=call_id))

            hops += 1
            continue

        # No more tool calls, stream the final response
        if hasattr(response, "content") and response.content:
            # Use the original streaming logic for the final answer
            yield {"event": "status", "data": {"status": "streaming_answer", "message": "Streaming final answer..."}}

            # Parse and stream the content
            content = response.content
            try:
                # Try to parse as JSON first
                parsed = json.loads(content)
                final_answer = parsed.get("answer", content)

                # Stream the answer in chunks
                normalized, cites = normalize_citations_and_chunks(final_answer, final_results)

                # Stream in word-based chunks
                words = re.findall(r'\S+', normalized)
                chunk_size = target_words_per_chunk

                for i in range(0, len(words), chunk_size):
                    chunk_words = words[i:i + chunk_size]
                    chunk_text = ' '.join(chunk_words)

                    # Get accumulated text up to this point
                    accumulated = ' '.join(words[:i + len(chunk_words)])

                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": accumulated,
                            "citations": cites,
                        }
                    }

                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": cites,
                        "reason": parsed.get("reason"),
                        "confidence": parsed.get("confidence"),
                        "tools_used": hops > 0
                    }
                }

            except json.JSONDecodeError:
                # Not JSON, stream as plain text
                normalized, cites = normalize_citations_and_chunks(content, final_results)

                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized,
                        "citations": cites,
                        "reason": None,
                        "confidence": None,
                        "tools_used": hops > 0
                    }
                }
        break


def create_sse_event(event_type: str, data: Union[str, dict, list]) -> str:
    """Create Server-Sent Event format"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
