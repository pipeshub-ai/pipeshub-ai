import json
import re
import time
from typing import Any, AsyncGenerator, Dict, Union

from app.utils.citations import normalize_citations_and_chunks


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
    # TIMING: Start timing the LLM streaming
    llm_start_time = time.time()
    first_part_received = False
    first_part_time = None

    if hasattr(llm, "astream"):
        print(f"TIMING: Starting LLM astream at {llm_start_time}")

        # TIMING: Log the time before the actual astream call
        pre_astream_call = time.time()
        astream_setup_time = pre_astream_call - llm_start_time
        print(f"TIMING: LLM astream setup took {astream_setup_time:.3f}s")

        # TIMING: Track the actual HTTP request timing
        http_request_start = time.time()


        async for part in llm.astream(messages):
            # TIMING: Log when we receive the first part
            if not first_part_received:
                first_part_time = time.time()
                llm_to_first_part = first_part_time - llm_start_time
                http_to_first_part = first_part_time - http_request_start
                first_part_received = True
                print(f"TIMING: First LLM part received after {llm_to_first_part:.3f}s from astream start")
                print(f"TIMING: HTTP request to first part took {http_to_first_part:.3f}s")

                # Additional analysis
                if http_to_first_part > 1.0:
                    print(f"⚠️  WARNING: HTTP request took {http_to_first_part:.3f}s - this is high for Azure OpenAI")
                    print("💡 SUGGESTION: Check Azure region, deployment size, or network latency")

            if part and getattr(part, "content", ""):
                yield part.content
    else:
        # Non-streaming – yield whole blob once
        print(f"TIMING: Starting LLM ainvoke at {llm_start_time}")
        response = await llm.ainvoke(messages)
        first_part_time = time.time()
        llm_to_first_part = first_part_time - llm_start_time
        print(f"TIMING: LLM ainvoke completed after {llm_to_first_part:.3f}s")
        yield getattr(response, "content", str(response))


async def stream_llm_response(
    llm,
    messages,
    final_results,
    target_words_per_chunk: int = 5,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Incrementally stream the answer portion of an LLM JSON response.
    For each chunk we also emit the citations visible so far.
    """
    # TIMING: Start timing the streaming process
    stream_start_time = time.time()
    timing_logs = {}

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

    first_token_received = False
    first_token_time = None
    first_chunk_generated = False
    first_chunk_time = None

    # Heartbeat tracking
    last_heartbeat_time = time.time()
    heartbeat_interval = 10.0  # Send heartbeat every 10 seconds

    try:
        # TIMING: Log when we start the LLM stream
        llm_stream_start = time.time()
        timing_logs["llm_stream_start"] = llm_stream_start - stream_start_time

        async for token in aiter_llm_stream(llm, messages):
            # TIMING: Log when we receive the first token
            if not first_token_received:
                first_token_time = time.time()
                timing_logs["first_token"] = first_token_time - stream_start_time
                timing_logs["llm_to_first_token"] = first_token_time - llm_stream_start
                first_token_received = True
                print(f"TIMING: First token received after {timing_logs['llm_to_first_token']:.3f}s from LLM stream start")

            # Send heartbeat if enough time has passed
            current_time = time.time()
            if current_time - last_heartbeat_time > heartbeat_interval:
                yield {
                    "event": "heartbeat",
                    "data": {"timestamp": current_time, "status": "generating"}
                }
                last_heartbeat_time = current_time

            if token:
                full_json_buf += token

            if not answer_buf:
                match = ANSWER_KEY_RE.search(full_json_buf)
                if match:
                    after_key = full_json_buf[match.end():]
                    answer_buf += after_key

            elif not answer_done:
                answer_buf += token

            if not answer_done:
                end_idx = find_unescaped_quote(answer_buf)
                if end_idx != -1:
                    answer_done = True
                    answer_buf = answer_buf[:end_idx]

            if answer_buf:
                for match in WORD_ITER(answer_buf[emit_upto:]):
                    words_in_chunk += 1
                    if words_in_chunk == target_words_per_chunk:
                        char_end = emit_upto + match.end()

                        if m := CITE_BLOCK_RE.match(answer_buf[char_end:]):
                            char_end += m.end()

                        emit_upto = char_end
                        words_in_chunk = 0

                        current_raw = answer_buf[:emit_upto]
                        if INCOMPLETE_CITE_RE.search(current_raw):
                            continue

                        # TIMING: Log when we generate the first chunk
                        if not first_chunk_generated:
                            first_chunk_time = time.time()
                            timing_logs["first_chunk_generated"] = first_chunk_time - stream_start_time
                            timing_logs["first_token_to_first_chunk"] = first_chunk_time - first_token_time
                            first_chunk_generated = True
                            print(f"TIMING: First chunk generated after {timing_logs['first_token_to_first_chunk']:.3f}s from first token")

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

        # TIMING: Log final timing summary
        if first_chunk_time:
            total_time = time.time() - stream_start_time
            print(f"TIMING SUMMARY (stream_llm_response): Total={total_time:.3f}s, LLM-to-first-token={timing_logs.get('llm_to_first_token', 0):.3f}s, First-token-to-first-chunk={timing_logs.get('first_token_to_first_chunk', 0):.3f}s")

    except Exception as exc:
        yield {
            "event": "error",
            "data": {"error": f"Error in LLM streaming: {exc}"},
        }


def create_sse_event(event_type: str, data: Union[str, dict, list]) -> str:
    """Create Server-Sent Event format"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
