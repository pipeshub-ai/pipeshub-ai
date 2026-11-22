import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from functools import cache
from typing import (
    Any,
    TypeVar,
)

import aiohttp
from fastapi.responses import StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock, ChatBedrockConverse
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mistralai import ChatMistralAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.agent_loop_lib.transport.provider_conflicts import (
    refused_cache_params,
)
from app.agents.agent_loop.error_classification import classify_exception
from app.agents.agent_loop.prompt_cache import (
    CacheStrategy,
    NoCacheStrategy,
    forget_cache_params,
    without_cache_markers,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    map_source_status,
    not_downloadable,
)
from app.exceptions.non_retryable import NonRetryableError
from app.modules.retrieval import degraded_notice
from app.services.ai_models.discovery.catalog import lookup_catalog
from app.services.llm_gateway.gateway import Invocable, get_llm_gateway, provider_key
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.citations import (
    detect_hallucinated_citation_urls,
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,  # noqa: F401  # tests patch this module attribute
)
from app.utils.error_class import log_failure
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.is_truncated import TruncatedReplyError, is_truncated
from app.utils.llm import LLMUnavailableError
from app.utils.logger import create_logger
from app.utils.message_chunks import merge_message_chunks_off_loop
from app.utils.opik import get_llm_invoke_config, get_opik_tracer
from app.utils.url_fetcher import private_addresses_blocked
from app.utils.url_redaction import redact_urls_in_text

CITE_BLOCK_RE = re.compile(r'(?:\s*\[[^\]]*\]\([^\)]*\))+')
# search() tries every "[", so a scan stops at the next one: the last "[" before an unfinished
# citation decides the answer, and scanning on from each earlier one takes quadratic time.
INCOMPLETE_CITE_RE = re.compile(r'\[[^\[\]]*(?:\]\([^)\[]*(?:\[[^)\[]*){0,8})?$')
logger = create_logger("streaming")

# Re-export for tests that patch app.utils.streaming.opik_tracer
opik_tracer = get_opik_tracer()


MAX_TOKENS_THRESHOLD = 80000
TOOL_EXECUTION_TOKEN_RATIO = 0.5
MAX_REFLECTION_RETRIES_DEFAULT = 2
MAX_CITATION_REFLECTION_RETRIES = 2
MAX_TOOL_HOPS = 6


async def _ainvoke_throttled(
    runnable: Invocable,
    messages: list[Any],
    config: dict | None = None,
    *,
    provider: str | None = None,
    call_timeout: float | None = None,
    call_kwargs: Mapping[str, Any] | None = None,
) -> Any:  # noqa: ANN401
    """Invoke through the indexing LLM gateway: its process-wide cap, the provider's breaker, a
    ceiling on hangs and jittered 429 retries outside the permit.

    ``provider`` names the base model (a structured-output wrapper hides it); it defaults to
    ``runnable``'s own. ``call_timeout`` bounds each provider call once the permit is held;
    waiting for the permit is backpressure, not a hung call, so it is never timed. A timeout
    is raised as ``TimeoutError``, not retried.
    """
    kwargs = dict(call_kwargs or {})
    if config:
        kwargs["config"] = config
    return await get_llm_gateway().invoke(
        runnable, messages, provider=provider or provider_key(runnable), timeout=call_timeout, **kwargs
    )


def _build_citation_reflection_message(
    hallucinated_urls: list[str],
) -> str:
    """Build a reflection prompt telling the LLM to fix hallucinated citation URLs.

    """
    bad_urls_str = "\n".join(f"  - {url}" for url in hallucinated_urls)

    parts = [
        "⚠️ CITATION ERROR — Your previous response contained invalid citation references "
        "that do not correspond to any source block in the provided context.\n\n"
        "The following citations are INVALID and must NOT appear in your answer:\n"
        f"{bad_urls_str}\n",
    ]

    parts.append(
        "\nHOW TO FIX:\n"
        "  1. For each citation, find the fact in a source block and use that block's EXACT "
        "Citation ID.\n"
        "  2. If a fact cannot be matched to a valid Citation ID, omit the citation for that fact "
        "rather than inventing one.\n\n"
        "Please rewrite your previous response now with all citation links corrected."
    )

    return "\n".join(parts)


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


async def stream_content(
    signed_url: str,
    record_id: str | None = None,
    file_name: str | None = None,
    connector: str | None = None,
) -> AsyncGenerator[bytes, None]:
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

    # Never the query: it carries the signature.
    logger.debug(f"Fetching presigned URL: {file_path_info}")

    try:
        # Use a timeout to prevent hanging requests
        # No total limit: it would cut off a large file that is still downloading.
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)

        # The URL comes from the source, whose address a user chose: it gets the deployment's
        # rule for such hosts. Presigned URLs must be used exactly as generated.
        from app.connectors.sources.web.address_guard import create_guarded_session

        async with create_guarded_session(allow_private=not private_addresses_blocked(), timeout=timeout) as session:
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
                            # A source may echo the presigned URL, whose query is a credential.
                            error_details = f" | Error details: {redact_urls_in_text(error_body)[:500]}"
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
                    if response.headers.get("x-ms-error-code") == "BlobArchived":
                        raise not_downloadable(
                            "This file is in the Archive tier in Azure. Rehydrate it to the Hot or "
                            "Cool tier to read it.",
                            connector=connector,
                        )
                    # error_details stays in the log: it can contain presigned
                    # URL internals and bucket names.
                    raise map_source_status(
                        response.status,
                        connector=connector,
                        retry_after=response.headers.get("Retry-After")
                        if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value
                        else None,
                    )
                async for chunk in response.content.iter_chunked(8192):
                    yield chunk
    except asyncio.TimeoutError as e:
        logger.error(f"❌ TIMEOUT: Fetching file content timed out | {log_prefix}")
        raise map_source_status(
            HttpStatusCode.GATEWAY_TIMEOUT.value, connector=connector
        ) from e
    except aiohttp.ClientError as e:
        logger.error(
            # Only the type: the message of a redirect or URL error holds the whole signed URL.
            f"❌ NETWORK ERROR: Failed to fetch file content from signed URL: {type(e).__name__} | {log_prefix}"
        )
        raise map_source_status(
            HttpStatusCode.BAD_GATEWAY.value, connector=connector
        ) from e


async def start_streaming_response(response: StreamingResponse) -> StreamingResponse:
    """Pull the first chunk before the response starts.

    Starlette sends ``http.response.start`` — committing the status code —
    before it asks the body iterator for anything. Connectors that call the
    source API lazily inside that iterator (Slack file downloads, Confluence
    attachments) would otherwise fail after a 200 was already on the wire,
    leaving the client with a truncated body that looks like success.

    Draining one chunk here moves that call ahead of the status commit. A
    failure *after* the first chunk still cannot change the status; those
    abort the response instead.
    """
    response.body_iterator = await stream_with_eager_first_chunk(response.body_iterator)
    return response


async def _aclose(source: object) -> None:
    """Close *source* if it is a generator. Starlette wraps a sync iterable in
    `iterate_in_threadpool`, which has no `aclose`, so this cannot assume one."""
    closer = getattr(source, "aclose", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception:
        logger.debug("Failed to close stream source", exc_info=True)


async def stream_with_eager_first_chunk(
    source: AsyncGenerator[bytes | str, None],
) -> AsyncGenerator[bytes | str, None]:
    """Return a streaming generator after eagerly pulling its first chunk.

    Reading the first chunk before returning lets upstream auth / 404 / network
    errors surface here, where they can still be converted to a real HTTP status,
    rather than after ``StreamingResponse`` has already committed the status line
    and can only produce a truncated chunked body.

    Chunks may be `str` as well as `bytes` — Starlette encodes either.
    """
    source_iter = source.__aiter__()
    try:
        first = await source_iter.__anext__()
    except StopAsyncIteration:
        await _aclose(source)
        async def _empty() -> AsyncGenerator[bytes | str, None]:
            return
            yield b""  # unreachable; makes this an async generator
        return _empty()
    except Exception:
        await _aclose(source)
        raise

    async def _gen() -> AsyncGenerator[bytes | str, None]:
        # `finally` (not just falling off the end) so an abandoned download —
        # the client disconnecting mid-file — still releases the upstream
        # session. Starlette never closes `body_iterator` itself.
        try:
            yield first
            async for chunk in source_iter:
                yield chunk
        finally:
            await _aclose(source)

    return _gen()


def create_stream_record_response(
    content_stream: AsyncGenerator[bytes, None],
    filename: str | None,
    mime_type: str | None = None,
    fallback_filename: str | None = None,
    additional_headers: dict[str, str] | None = None
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

def _stringify_content(content: str | list | dict | None) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
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

async def aiter_llm_stream(
    llm: BaseChatModel, messages: list[Any], parts: list[Any] | None = None,
) -> AsyncGenerator[str | dict, None]:
    """Async iterator for LLM streaming that normalizes content to text.

    The LLM provider may return content as a string or a list of content parts
    (e.g., [{"type": "text", "text": "..."}, {"type": "image_url", ...}]).
    We extract and concatenate only textual parts for streaming.
    """
    if parts is None:
        parts = []
    config = get_llm_invoke_config(llm)
    # Keep streaming.opik_tracer in sync for tests that patch this module attr.
    if opik_tracer is not None:
        callbacks = list(config.get("callbacks") or [])
        if all(cb is not opik_tracer for cb in callbacks):
            callbacks.append(opik_tracer)
        config = {"callbacks": callbacks}
    if hasattr(llm, "astream"):
        # Fix #1710: Manual iteration to catch per-chunk ValidationError
        # when providers like LiteLLM send role=None on non-first chunks
        astream_iter = llm.astream(messages, config=config).__aiter__()
        while True:
            try:
                part = await astream_iter.__anext__()
            except StopAsyncIteration:
                break
            except ValidationError as e:
                # Only suppress the specific validation error related to 'role'
                if "role" in str(e).lower():
                    # Type only: pydantic's text quotes the rejected chunk, the model's output.
                    logger.warning(
                        "Skipping chunk due to validation error (likely role=None from provider)",
                        extra={"error_type": type(e).__name__},
                    )
                    continue
                # Re-raise other validation errors to avoid hiding unexpected issues.
                raise

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

# Configuration for Qdrant limits based on context length.
VECTOR_DB_LIMIT_TIERS = [
    (17000, 43),  # For context lengths up to 17k
    (33000, 154),  # For context lengths up to 33k
    (65000, 213),  # For context lengths up to 65k
]
DEFAULT_VECTOR_DB_LIMIT = 266

def extract_json_from_string(input_string: str) -> "dict[str, Any]":
    """
    Extracts a JSON object from a string that may contain markdown code blocks
    or other formatting, and returns it as a Python dictionary.

    Args:
        input_string (str): The input string containing JSON data

    Returns:
        dict[str, Any]: The extracted JSON object.

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


# The confidence trailer asked for by `agent_loop/prompt_builder.py`'s
# `_ANSWER_CONFIDENCE` and `modules/qna/prompt_templates.py`. Matched leniently
# because a trailer that fails to parse is not a missing indicator — it is the
# raw `Confidence: High` line rendered to the user as part of the answer. Models
# routinely drop the `---` rule entirely, bold the label (`**Confidence:** High`),
# or separate with a dash, so the rule is optional and emphasis is skipped
# wherever it can appear.
_CONFIDENCE_LINE_RE = re.compile(
    r"^[ \t*_]*confidence(?:[ \t]+(?:level|score))?[ \t*_]*[:\-\u2013][ \t*_]*"
    r"(very[ \t]+high|high|medium|low)[ \t*_]*[.!]?[ \t*_]*$",
    re.IGNORECASE,
)

# Every shape `_CONFIDENCE_LINE_RE` accepts, with emphasis/whitespace squashed
# out, so a streaming caller can ask "could this line still become a trailer?"
# by prefix — `re` has no partial-match mode.
_CONFIDENCE_TRAILER_FORMS = tuple(
    f"confidence{separator}{level}"
    for separator in (":", "-", "\u2013", "level:", "score:")
    for level in ("veryhigh", "high", "medium", "low")
)
_EMPHASIS_AND_SPACE_RE = re.compile(r"[*_\s]+")


def _canonical_confidence_level(raw: str) -> str:
    """`"very  high"` / `"HIGH"` -> `"Very High"` / `"High"`, so the frontend
    gets one spelling regardless of how the model cased or spaced it."""
    return " ".join(word.capitalize() for word in raw.split())


def _is_horizontal_rule(line: str) -> bool:
    """A markdown rule line (`---`, `***`, `___`) including the partial forms a
    streaming caller sees (`-`, `--`). A `- item` bullet has other characters,
    so it never matches."""
    stripped = line.strip()
    return bool(stripped) and not stripped.strip("-*_ \t")


def parse_confidence_from_answer(answer: str) -> tuple[str, str | None]:
    """Split the trailing confidence line (plus the optional markdown rule above
    it) off `answer`, returning `(clean_answer, level)`. `level` is `None` and
    `answer` comes back untouched when the last non-blank line is not a
    confidence line — the trailer only counts at the very end, never mid-body."""
    lines = answer.split("\n")
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    if end == 0:
        return answer, None

    match = _CONFIDENCE_LINE_RE.match(lines[end - 1])
    if match is None:
        return answer, None

    cut = end - 1
    while cut > 0 and not lines[cut - 1].strip():
        cut -= 1
    if cut > 0 and _is_horizontal_rule(lines[cut - 1]):
        cut -= 1
    return "\n".join(lines[:cut]).rstrip(), _canonical_confidence_level(match.group(1))


def _could_become_confidence_line(line: str) -> bool:
    """True when `line` is the start of a confidence line, complete or not:
    `""`, `"Conf"`, `"**Confidence:"`, `"Confidence: Med"`, ..."""
    squashed = _EMPHASIS_AND_SPACE_RE.sub("", line).lower().rstrip(".!")
    return not squashed or any(form.startswith(squashed) for form in _CONFIDENCE_TRAILER_FORMS)


def strip_partial_confidence_trailer(text: str) -> str:
    """Hide a still-being-generated confidence trailer.

    `parse_confidence_from_answer` only matches the COMPLETE trailer, which is
    all a caller finalizing a finished answer needs. A caller streaming the
    model's own tokens live (`agent_loop/answer_streamer.py`) sees the trailer
    arrive a few characters at a time and would flash every partial state on
    screen, so it also has to drop the incomplete forms.

    Only the final line is considered (plus a rule/blank lines above it), and
    only while it could still become a confidence line — real content after a
    rule, or a `- ` list item, is left alone.
    """
    lines = text.split("\n")
    cut = len(lines) - 1
    if _could_become_confidence_line(lines[cut]):
        while cut > 0 and not lines[cut - 1].strip():
            cut -= 1
        if cut > 0 and _is_horizontal_rule(lines[cut - 1]):
            cut -= 1
    elif not _is_horizontal_rule(lines[cut]):
        return text

    # Nothing but trailing whitespace so far — no trailer to hide yet, and
    # trimming it would churn the streamed text on every newline token.
    if all(not line.strip() for line in lines[cut:]):
        return text
    return "\n".join(lines[:cut]).rstrip()



async def handle_simple_mode(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]],
    logger: logging.Logger,
    target_words_per_chunk: int = 1,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    ref_to_url: dict[str, str] | None = None,
    web_records: list[dict[str, Any]] | None = None,
    chat_mode: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
        logger.debug("handle_simple_mode: streaming raw content")

        # Fast-path: if the last message is already an AI answer
        try:
            last_msg = messages[-1] if messages else None
            existing_ai_content: str | None = None
            if isinstance(last_msg, AIMessage):
                existing_ai_content = coerce_message_content_to_text(getattr(last_msg, "content", None))
            elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
                existing_ai_content = coerce_message_content_to_text(getattr(last_msg, "content", None))
            elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
                existing_ai_content = coerce_message_content_to_text(last_msg.get("content"))

            if existing_ai_content:
                logger.info("handle_simple_mode: detected existing AI message (simple mode), streaming directly")
                clean_answer, confidence = parse_confidence_from_answer(existing_ai_content)
                normalized, cites = normalize_citations_and_chunks(
                    clean_answer, final_results, records,
                    ref_to_url=ref_to_url,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    web_records=web_records,
                )

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
                            "confidence": confidence,
                        },
                    }

                yield {
                    "event": "complete",
                    "data": degraded_notice.flag({
                        "answer": normalized,
                        "citations": cites,
                        "reason": None,
                        "confidence": confidence,
                    }),
                }
                return
        except Exception as e:
            logger.debug("handle_simple_mode: fast-path failed, falling back to LLM call: %s", str(e))

        async for event in call_aiter_llm_stream_simple(
            llm, messages, final_results, records, target_words_per_chunk,
            virtual_record_id_to_result=virtual_record_id_to_result, original_llm=llm,
            ref_to_url=ref_to_url, web_records=web_records,chat_mode=chat_mode,
        ):
            yield event


# Markers the frontend interprets as artifact / download cards. These are
# authored ONLY by the backend (this function) — if an answer from the LLM
# happens to contain the same syntax (directly or via prompt injection from
# RAG content), the frontend would render attacker-controlled download cards
# pointing at arbitrary URLs. Strip any pre-existing matches before we append
# our own, trusted markers.
_LLM_ARTIFACT_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\]\([^)]+\)\{[^}]*\}",
)
# Mid-form: `::artifact[name]{meta}` — has the `{meta}` block but no `(url)`.
_LLM_ARTIFACT_NO_URL_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\]\{[^}]*\}",
)
# Short-form variant LLMs sometimes hallucinate (no `(url){meta}` block).
_LLM_ARTIFACT_SHORT_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\](?:\([^)]*\))?(?!\{)",
)
_LLM_DOWNLOAD_MARKER_RE = re.compile(
    r"::download_conversation_task\[[^\]]+\]\([^)]+\)",
)

# A real, clickable download is ALWAYS delivered via the `::artifact`
# marker this module appends below — never authored by the model itself.
# When a generation step doesn't actually deliver a file (e.g. `run_code`
# wrote outside `$OUTPUT_DIR`, or failed silently) a model can still hand-
# write an ordinary markdown link like "[Download the report]
# (sandbox://report.pdf)" claiming success anyway. The frontend's
# `rehype-sanitize` schema only allows `http`/`https`/`mailto` hrefs
# (`answer-content.tsx`) and drops anything else outright, so such a link
# renders styled (colored, underlined) but is inert — worse than plain
# text since it visually promises something that doesn't work. Flatten
# markdown links to plain text (dropping the link, keeping the words) only
# when the URL has an EXPLICIT non-allowed scheme — a genuine citation or
# search-result link the model quotes always carries a real absolute
# http(s) URL, and a same-origin relative link (no scheme at all) is left
# untouched since the sanitizer renders those as-is.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _flatten_unusable_markdown_links(answer: str) -> str:
    if not answer or "](" not in answer:
        return answer

    def _flatten_if_unusable(match: "re.Match[str]") -> str:
        text, url = match.group(1), match.group(2).strip()
        scheme_match = _URL_SCHEME_RE.match(url)
        if scheme_match is None or scheme_match.group(1).lower() in _ALLOWED_LINK_SCHEMES:
            return match.group(0)
        return text

    return _MARKDOWN_LINK_RE.sub(_flatten_if_unusable, answer)


def _strip_llm_authored_markers(answer: str) -> str:
    """Remove any LLM-authored artifact / download markers from *answer*,
    and flatten any hand-written markdown link the model composed with a
    URL scheme the frontend would render as dead (see
    `_flatten_unusable_markdown_links`).

    The frontend's marker parser runs against the full saved message, so a
    marker anywhere in the stream is a URL-spoofing primitive. We drop them
    before the backend appends its own known-good markers.
    """
    if not answer:
        return answer
    stripped = _LLM_ARTIFACT_MARKER_RE.sub("", answer)
    stripped = _LLM_ARTIFACT_NO_URL_MARKER_RE.sub("", stripped)
    stripped = _LLM_ARTIFACT_SHORT_MARKER_RE.sub("", stripped)
    stripped = _LLM_DOWNLOAD_MARKER_RE.sub("", stripped)
    return _flatten_unusable_markdown_links(stripped)


def strip_llm_authored_markers_in_parts(parts: list | None) -> None:
    """Sanitize every `text` part of a transcript in place, recursing into
    `sub_agent` children.

    :func:`_append_task_markers` only sanitizes the answer string, but the
    activity timeline renders every OTHER text part as raw markdown with no
    marker parsing (`agent-activity.tsx`). A marker the model copied out of
    an earlier turn — they are persisted in message content, so they are in
    its context — therefore surfaces there verbatim: literal
    ``::artifact[...]`` text plus an anchor whose ``record:`` href no
    browser can follow.
    """
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "sub_agent":
            strip_llm_authored_markers_in_parts(part.get("parts"))
        elif part.get("type") == "text" and part.get("content"):
            part["content"] = _strip_llm_authored_markers(part["content"])


def _append_task_markers(answer: str, conversation_tasks: list | None) -> str:
    """Append ::download_conversation_task and ::artifact markers to the answer string.

    Handles two task result shapes:
    - Legacy: ``{"type": "csv_download", "fileName": ..., "signedUrl": ...}``
    - Artifacts: ``{"type": "artifacts", "artifacts": [{"fileName": ..., "mimeType": ..., ...}]}``

    Before appending, any LLM-authored marker strings already present in
    ``answer`` are removed (see :func:`_strip_llm_authored_markers`) so the
    frontend only ever sees backend-emitted markers.
    """
    answer = _strip_llm_authored_markers(answer)

    if not conversation_tasks:
        return answer

    parts: list[str] = []
    seen_artifacts: set[str] = set()
    for t in conversation_tasks:
        task_type = t.get("type", "")

        if task_type == "artifacts":
            for art in t.get("artifacts", []):
                fname = art.get("fileName", "Download")
                mime = art.get("mimeType", "application/octet-stream")
                doc_id = art.get("documentId", "")
                record_id = art.get("recordId", "")
                # Optional trailing segments (the frontend parser tolerates
                # their absence for markers persisted before they existed).
                artifact_type = art.get("artifactType", "")
                version = art.get("version", "")

                # PERSISTED markers must never carry a signed URL: it expires
                # in ~10 min, so it would be permanent dead weight (and a
                # URL-trust surface the frontend already has to defend
                # against) in every saved message forever. `recordId` is
                # the durable identity the frontend already prefers for
                # streaming/download (`parseArtifactMarkers` in
                # `parse-download-markers.ts`) — a stable placeholder in the
                # `(url)` slot keeps that parser's regex (which requires a
                # non-empty segment there) satisfied without a real URL.
                # Only fall back to embedding a real URL for the rare
                # artifact that has none (no recordId to stream through) —
                # otherwise it would be permanently undownloadable.
                if record_id:
                    url = f"record:{record_id}"
                else:
                    url = art.get("signedUrl") or art.get("downloadUrl", "")
                    if not url:
                        continue

                # One download card per artifact version, even when two
                # producers (or a re-run) queued the same artifact twice.
                dedupe_key = f"{record_id or doc_id or url}:{version}"
                if dedupe_key in seen_artifacts:
                    continue
                seen_artifacts.add(dedupe_key)
                parts.append(
                    f"::artifact[{fname}]({url}){{{mime}|{doc_id}|{record_id}|{artifact_type}|{version}}}"
                )
        else:
            url = t.get("signedUrl") or t.get("downloadUrl", "")
            if url:
                fname = t.get("fileName", "Download")
                parts.append(f"::download_conversation_task[{fname}]({url})")

    if not parts:
        return answer

    return answer + "\n\n" + "\n\n".join(parts)


def create_sse_event(event_type: str, data: str | dict | list) -> str:
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


def _initialize_answer_parser_regex() -> tuple[re.Pattern, re.Pattern, re.Pattern, Any]:
    """Initialize regex patterns for answer parsing."""
    answer_key_re = re.compile(r'"answer"\s*:\s*"')
    # cite_block_re = re.compile(r'(?:\s*(?:\[\d+\]|【\d+】))+')
    cite_block_re = re.compile(r'(?:\s*\[[^\]]*\]\([^\)]*\))+')
    incomplete_cite_re = INCOMPLETE_CITE_RE
    # incomplete_cite_re = re.compile(r'[\[【][^\]】]*$')
    word_iter = re.compile(r'\S+').finditer
    return answer_key_re, cite_block_re, incomplete_cite_re, word_iter

async def call_aiter_llm_stream_simple(
    llm: BaseChatModel,
    messages: list[Any],
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]] | None = None,
    target_words_per_chunk: int = 1,
    citation_reflection_retry_count: int = 0,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    original_llm: BaseChatModel | None = None,
    ref_to_url: dict[str, str] | None = None,
    web_records: list[dict[str, Any]] | None = None,
    chat_mode: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream LLM response in simple (non-JSON) mode.

    Streams raw text content directly without JSON parsing or reflection.
    After streaming, checks for tool calls and yields a tool_calls event if
    present; otherwise emits a complete event with the accumulated answer.
    """
    content_buf: str = ""
    WORD_ITER = re.compile(r'\S+').finditer
    prev_norm_len = 0
    emit_upto = 0
    words_in_chunk = 0
    parts = []

    try:
        async for token in aiter_llm_stream(llm, messages, parts):

            content_buf += token

            # Stream content in word-based chunks.
            # Capture emit_upto once before the loop: match.end() positions are
            # relative to the substring passed to WORD_ITER, so we must add the
            # same base offset for every match — not the updated emit_upto.
            start_emit_upto = emit_upto
            # consumed_pos tracks how far we've actually consumed (including
            # citation blocks appended after a word), so that WORD_ITER matches
            # that overlap with an already-consumed citation block are skipped.
            consumed_pos = emit_upto
            for match in WORD_ITER(content_buf[start_emit_upto:]):
                # Skip words that fall inside a citation block consumed by a
                # prior iteration of this loop.
                if start_emit_upto + match.start() < consumed_pos:
                    continue

                words_in_chunk += 1
                if words_in_chunk >= target_words_per_chunk:
                    char_end = start_emit_upto + match.end()

                    # Include any citation blocks that immediately follow
                    if m := CITE_BLOCK_RE.match(content_buf[char_end:]):
                        char_end += m.end()

                    current_raw = content_buf[:char_end]
                    # If the citation is incomplete, don't advance emit_upto;
                    # continue so remaining words in this token can complete it.
                    if INCOMPLETE_CITE_RE.search(current_raw):
                        continue

                    emit_upto = char_end
                    consumed_pos = char_end
                    words_in_chunk = 0

                    clean_answer, confidence = parse_confidence_from_answer(current_raw)
                    normalized, cites = normalize_citations_and_chunks(
                        clean_answer, final_results, records,
                        ref_to_url=ref_to_url,
                        virtual_record_id_to_result=virtual_record_id_to_result,
                        web_records=web_records,
                    )

                    chunk_text = normalized[prev_norm_len:]
                    prev_norm_len = len(normalized)
                    yield {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": normalized,
                            "citations": cites,
                            "confidence": confidence,
                        },
                    }


        # Tool call detection
        tool_calls_happened = True
        ai = await merge_message_chunks_off_loop(parts) if parts else None

        if tool_calls_happened and ai is not None:
            tool_calls = getattr(ai, 'tool_calls', [])
            if tool_calls:
                yield {"event": "tool_calls", "data": {"ai": ai}}
                return

        # Final normalization and emit complete
        clean_answer, confidence = parse_confidence_from_answer(content_buf)

        # Citation URL reflection before final normalization
        if citation_reflection_retry_count < MAX_CITATION_REFLECTION_RETRIES and chat_mode != "web_search":
            hallucinated = detect_hallucinated_citation_urls(
                clean_answer, records, final_results,
                virtual_record_id_to_result=virtual_record_id_to_result,
                ref_to_url=ref_to_url,
            )
            if hallucinated:
                logger.warning(
                    "Citation reflection (chatbot simple): %d hallucinated URLs (attempt %d). URLs: %s",
                    len(hallucinated), citation_reflection_retry_count + 1, hallucinated,
                )
                yield {"event": "restreaming", "data": {}}
                yield {"event": "status", "data": {"status": "processing", "message": "Verifying citations..."}}
                reflection_content = _build_citation_reflection_message(hallucinated)
                updated_messages = list(messages)
                updated_messages.append(AIMessage(content=clean_answer))
                updated_messages.append(HumanMessage(content=reflection_content))
                async for event in call_aiter_llm_stream_simple(
                    llm=original_llm,
                    messages=updated_messages,
                    final_results=final_results,
                    records=records,
                    target_words_per_chunk=target_words_per_chunk,
                    citation_reflection_retry_count=citation_reflection_retry_count + 1,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    original_llm=original_llm,
                    ref_to_url=ref_to_url,
                    web_records=web_records,
                    chat_mode=chat_mode,
                ):
                    yield event
                return

        normalized, cites = normalize_citations_and_chunks(
            clean_answer, final_results, records,
            ref_to_url=ref_to_url,
            virtual_record_id_to_result=virtual_record_id_to_result,
            web_records=web_records,
        )
        yield {
            "event": "complete",
            "data": degraded_notice.flag({
                "answer": normalized,
                "citations": cites,
                "reason": None,
                "confidence": confidence,
            }),
        }
    except Exception as exc:
        log_failure(logger, "llm_stream.failed", exc, include_error_text=False)
        yield {"event": "error", "data": {"error": classify_exception(exc)[1]}}
        return

_NUMBER_BOUNDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")


#: Keys whose value maps names to schemas: a name there is a field, never a keyword.
_SCHEMA_NAME_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def _drop_number_bounds(node: Any) -> None:  # noqa: ANN401 - any JSON schema node
    """Removes number-bound keywords from every schema object in *node*, keeping field names."""
    if isinstance(node, list):
        for value in node:
            _drop_number_bounds(value)
        return
    if not isinstance(node, dict):
        return
    for key in _NUMBER_BOUNDS:
        if isinstance(node.get(key), (int, float)) and not isinstance(node.get(key), bool):
            del node[key]
    for key, value in node.items():
        if key in _SCHEMA_NAME_MAPS and isinstance(value, dict):
            for schema in value.values():
                _drop_number_bounds(schema)
        else:
            _drop_number_bounds(value)


@cache
def _without_number_bounds(schema: type[BaseModel]) -> type[BaseModel]:
    """*schema* as sent without numeric bounds; *schema* itself when it has none.

    OpenAI refuses ``minimum``/``maximum`` in a strict schema for a fine-tuned model with a 400,
    and neither an Azure deployment name nor an OpenAI-compatible server says which model is
    behind it. The subclass validates exactly as *schema* does, so bounds the schema's own
    validators enforce still hold on the reply.
    """
    sent = schema.model_json_schema()
    _drop_number_bounds(sent)
    if sent == schema.model_json_schema():
        return schema

    class _Unbounded(schema):  # type: ignore[valid-type, misc]
        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            result = super().model_json_schema(*args, **kwargs)
            _drop_number_bounds(result)
            return result

    _Unbounded.__name__ = _Unbounded.__qualname__ = schema.__name__
    _Unbounded.__doc__ = schema.__doc__
    return _Unbounded


def _apply_structured_output(llm: BaseChatModel, schema: type[BaseModel], *, include_raw: bool = False) -> BaseChatModel:
    if isinstance(llm, (ChatOpenAI, AzureChatOpenAI)) and isinstance(schema, type) and issubclass(schema, BaseModel):
        schema = _without_number_bounds(schema)
    if isinstance(llm, (ChatGoogleGenerativeAI,ChatAnthropic,ChatOpenAI,ChatMistralAI,AzureChatOpenAI,ChatBedrock,ChatBedrockConverse)):

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

        if not isinstance(llm, (ChatBedrock, ChatBedrockConverse)):
            additional_kwargs["method"] = "json_schema"
        if include_raw:
            additional_kwargs["include_raw"] = True

        try:
            model_with_structure = llm.with_structured_output(
                schema,
                **additional_kwargs
            )
            logger.debug("Using structured output")
            return model_with_structure
        except Exception as e:
            logger.warning("Failed to apply structured output, falling back to default. Error: %s", str(e))
            logger.debug("Using non-structured LLM")

    logger.debug("Using non-structured LLM")
    return llm


def cleanup_content(response_text: str) -> str:
    # Diagnostic: catch 'tuple' (or other non-str) has no attribute 'startswith'
    if not isinstance(response_text, str):
        logger.warning(
            "[streaming cleanup_content] response_text is not str | type=%s repr=%s",
            type(response_text).__name__, repr(response_text)[:300],
        )
        # Providers like Gemini return list-of-blocks content; extract text
        # rather than stringifying the Python repr.
        response_text = coerce_message_content_to_text(response_text)
    response_text = response_text.strip()
    if '</think>' in response_text:
            response_text = response_text.split('</think>')[-1]
    logger.debug("[streaming cleanup_content] before startswith/endswith type=%s", type(response_text).__name__)
    if response_text.startswith("```json"):
        response_text = response_text.replace("```json", "", 1)
    if response_text.endswith("```"):
        response_text = response_text.rsplit("```", 1)[0]
    return response_text.strip()


# An answer that does not fit the schema is the model's doing, not a bug of ours: a WARNING, as before.
_ANSWER_PARSE_ERRORS = (ValidationError, OutputParserException)


def _is_oversized_number(exc: BaseException) -> bool:
    """A reply holding a number longer than Python converts (``sys.get_int_max_str_digits()``).

    A model can run an integer field on until its output limit. The JSON parser behind
    ``with_structured_output`` lets the resulting ``ValueError`` out as is, so without this
    check the reply reads as a failed call.
    """
    return isinstance(exc, ValueError) and "integer string conversion" in str(exc)


class OversizedNumberError(OutputParserException):
    """A reply holding a number too long to read: a reply that ran away, not a failed call."""


#: Output tokens added to a per-call cap for a model that may reason: its reasoning counts
#: against the same limit, and many models reason without being asked (Gemini 2.5 and later,
#: OpenAI's reasoning families), so without it the visible reply could be cut off early.
REASONING_OUTPUT_RESERVE = 8192

# Model families known not to reason: ``-chat`` variants (gpt-5.x-chat-latest, deepseek-chat)
# and these prefixes. A name that says it reasons always gets the reserve; any other model
# may reason, and gets it too.
_NON_REASONING_PREFIXES = ("gpt-3.5", "gpt-4", "chatgpt-4o", "gemini-1.", "gemini-2.0")
_REASONING_WORDS = ("thinking", "reasoning", "reasoner")

# Our provider ids for the model catalog, by model class.
_CATALOG_PROVIDER = (
    (ChatGoogleGenerativeAI, "gemini"),
    (AzureChatOpenAI, "azureOpenAI"),
    (ChatOpenAI, "openAI"),
    (ChatAnthropic, "anthropic"),
    (ChatMistralAI, "mistral"),
)


def _model_name(llm: BaseChatModel) -> str:
    name = getattr(llm, "model_name", None) or getattr(llm, "model", None) or ""
    return str(name).lower().rsplit("/", 1)[-1]


def _reasoning_reserve(llm: BaseChatModel) -> int:
    """Output tokens to leave for reasoning on top of a cap; 0 only for a model known not to reason."""
    thinking = getattr(llm, "thinking", None)
    if isinstance(llm, ChatAnthropic):
        # Claude reasons only when asked to. Anthropic requires max_tokens above the thinking budget.
        if isinstance(thinking, Mapping) and thinking.get("type") not in (None, "disabled"):
            budget = thinking.get("budget_tokens")
            return budget if isinstance(budget, int) and budget > 0 else REASONING_OUTPUT_RESERVE
        return REASONING_OUTPUT_RESERVE if getattr(llm, "reasoning_effort", None) else 0
    if getattr(llm, "thinking_budget", None) == 0 or getattr(llm, "reasoning_effort", None) == "none":
        return 0
    name = _model_name(llm)
    if any(word in name for word in _REASONING_WORDS):
        return REASONING_OUTPUT_RESERVE
    if "-chat" in name or name.startswith(_NON_REASONING_PREFIXES):
        return 0
    if isinstance(llm, ChatMistralAI) and "magistral" not in name:
        return 0
    return REASONING_OUTPUT_RESERVE


def _known_output_limit(llm: BaseChatModel) -> int | None:
    """The most output tokens the model accepts, when its profile or the model catalog says;
    None when unknown, as for any model behind an OpenAI-compatible endpoint of its own."""
    if isinstance(llm, ChatOpenAI) and not isinstance(llm, AzureChatOpenAI):
        base = getattr(llm, "openai_api_base", None)
        if base and "api.openai.com" not in str(base):
            return None
    profile = getattr(llm, "profile", None)
    limit = profile.get("max_output_tokens") if isinstance(profile, Mapping) else None
    if not (isinstance(limit, int) and limit > 0):
        name = str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "").rsplit("/", 1)[-1]
        provider = next((pid for cls, pid in _CATALOG_PROVIDER if isinstance(llm, cls)), None)
        entry = lookup_catalog(provider, name) if provider and name else None
        limit = entry.get("max_output_tokens") if entry else None
    return limit if isinstance(limit, int) and limit > 0 else None


def with_output_cap(llm: BaseChatModel, max_output_tokens: int | None) -> BaseChatModel | None:
    """``llm`` with its replies capped at ``max_output_tokens`` plus a reasoning reserve, never
    above the most the model accepts; or None for a model this does not know how to cap.

    ``llm`` itself comes back, uncapped, when the model's own limit is already lower or when
    the most it accepts is unknown (a cap above it is refused with a 400 on every call): the
    caller still reads the raw reply and can tell a cut-off one. Bedrock is left alone: its
    thinking budget lives in request fields that a cap here could fall below.
    """
    if not max_output_tokens or max_output_tokens <= 0:
        return None
    if isinstance(llm, ChatGoogleGenerativeAI):
        field = "max_output_tokens"
    elif isinstance(llm, (ChatOpenAI, AzureChatOpenAI, ChatAnthropic, ChatMistralAI)):
        field = "max_tokens"
    else:
        return None
    reserve = _reasoning_reserve(llm)
    cap = max_output_tokens + reserve
    current = getattr(llm, field, None)
    if isinstance(current, int) and 0 < current <= cap:
        return llm
    limit = _known_output_limit(llm)
    if limit is None:
        return llm
    cap = min(cap, limit)
    if isinstance(llm, ChatAnthropic) and cap <= reserve:
        # Anthropic refuses max_tokens at or below the thinking budget.
        return llm
    try:
        return llm.model_copy(update={field: cap})
    except Exception as exc:
        logger.debug("Output cap not applied to %s: %s", type(llm).__name__, type(exc).__name__)
        return llm


_OUTPUT_LIMIT_REFUSAL = re.compile(
    r"max_tokens|max_completion_tokens|max_output_tokens|maxoutputtokens|output tokens|completion tokens"
    r"|context length|context window|maximum context",
    re.IGNORECASE,
)


def _is_output_limit_refusal(exc: BaseException) -> bool:
    """A 400 refusing the output-token limit the request carried."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    text = str(exc)
    is_400 = status == 400 or (status is None and "400" in text)
    return is_400 and bool(_OUTPUT_LIMIT_REFUSAL.search(text))


def _capped_reply(result: Any, *, check_message: bool) -> Any:  # noqa: ANN401 - whatever the structured model returns
    """Unwraps an ``include_raw`` result, raising for a cut-off reply or one that did not parse.

    With ``check_message`` a plain message is checked too: a model the structured wrapper could
    not be built for returns its message as is.
    """
    if not (isinstance(result, dict) and "raw" in result and "parsed" in result):
        if check_message and isinstance(result, BaseMessage) and is_truncated(result):
            _raise_oversized_number(result)
            raise TruncatedReplyError("the reply reached its output-token limit")
        return result
    error = result.get("parsing_error")
    # A runaway number often runs on until the cap: it is a runaway, not an ordinary cut-off.
    if error is not None and _is_oversized_number(error):
        raise error
    if is_truncated(result.get("raw")):
        raise TruncatedReplyError("the reply reached its output-token limit")
    if error is not None:
        raise error
    parsed = result.get("parsed")
    return parsed if parsed is not None else result.get("raw")


def _raise_oversized_number(message: BaseMessage) -> None:
    """Raises the parser's ``ValueError`` when a cut-off message's JSON stops inside a number too long to read."""
    try:
        json.loads(cleanup_content(coerce_message_content_to_text(message.content)))
    except ValueError as exc:
        if _is_oversized_number(exc):
            raise
    except Exception:  # any other failure is an ordinary cut-off reply
        pass


def answer_error_class(exc: BaseException) -> str | None:
    """``bad_request`` for a model answer that did not fit the schema; None lets ``classify_error`` decide."""
    return "bad_request" if isinstance(exc, _ANSWER_PARSE_ERRORS) or _is_oversized_number(exc) else None


def _recover_structured_json_from_exception(
    error: Exception,
    schema: type[SchemaT],
) -> SchemaT | None:
    """Recover JSON with duplicated wrapper braces from a validation error."""
    if not isinstance(error, ValidationError):
        return None

    for detail in error.errors():
        raw_input = detail.get("input")
        if not isinstance(raw_input, str):
            continue

        original = raw_input.strip()
        cleaned = cleanup_content(original)
        candidates = [original, cleaned]
        for candidate in (original, cleaned):
            if candidate.startswith("{{"):
                candidates.append(candidate[1:])
                if candidate.endswith("}}"):
                    candidates.append(candidate[1:-1])

        for candidate in dict.fromkeys(candidates):
            try:
                return schema.model_validate_json(candidate)
            except ValidationError:
                pass

    return None


async def invoke_with_structured_output_and_reflection(
    llm: BaseChatModel,
    messages: list,
    schema: type[SchemaT],
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
    *,
    enable_opik_tracing: bool = True,
    call_timeout: float | None = None,
    raise_call_errors: bool = False,
    prompt_cache: CacheStrategy | None = None,
    on_cache_rejected: Callable[[frozenset[str]], None] | None = None,
    raise_unavailable: bool = False,
    max_output_tokens: int | None = None,
    raise_truncated: bool = False,
    raise_oversized_number: bool = False,
) -> SchemaT | None:
    """
    Invoke LLM with structured output and automatic reflection on parse failure.

    With ``raise_unavailable`` a provider outage raises ``LLMUnavailableError`` instead of
    returning None, for callers that must tell an outage from output they cannot use.

    With ``max_output_tokens`` each reply is capped (``with_output_cap``) and its stop reason
    read. A reply cut off at its limit is not usable and is not asked for again (the same
    request would stop at the same limit): the call returns None after one warning, or with
    ``raise_truncated`` raises ``TruncatedReplyError`` for the caller to report.

    A reply holding a number too long to read is not asked for again either: the call returns
    None, or with ``raise_oversized_number`` raises ``OversizedNumberError``, for a caller that
    would otherwise read None as "ask again".

    Args:
        llm: The LangChain chat model to use
        messages: List of messages to send to the LLM
        schema: Pydantic model class to validate the response against
        max_retries: Maximum number of reflection retries on parse failure
        enable_opik_tracing: When False, skip Opik callbacks even if configured
            (the model's own callbacks still run)
        call_timeout: Optional bound, in seconds, on each provider call (the
            first and every reflection retry), excluding the wait for an
            indexing slot. A timed-out call counts as a failed attempt.
        raise_call_errors: Raise the first call's error (a timeout, an outage),
            and the last reflection call's timeout, instead of returning None,
            so a caller can tell a starved call from a reply that did not fit
            the schema.
        prompt_cache: The cache strategy ``messages`` were built with (its
            ``system`` block); its ``finalize`` is applied here. If the provider
            refuses a cache parameter or marker, that call (the first or a
            reflection) is resent without the ones the error names, in the
            kwargs or on the messages, and so is every later call, as
            ``LangChainTransport`` narrows its strategy.
        on_cache_rejected: Called with the refused names each time the
            provider refuses some. Pass the callback that records them under
            the identity the cache strategy was chosen with; by default they
            are recorded for ``llm`` with no model name.

    Returns:
        Validated Pydantic model instance, or None if parsing fails after all retries
    """
    capped = with_output_cap(llm, max_output_tokens)
    # The raw reply carries the stop reason, which tells a cut-off reply from a whole one.
    llm_with_structured_output = (
        _apply_structured_output(capped, schema=schema, include_raw=True)
        if capped is not None
        else _apply_structured_output(llm, schema=schema)
    )
    cap_sent = capped is not None and capped is not llm
    provider = provider_key(llm)
    # Use the base llm: structured-output wrappers drop constructor callbacks;
    # get_llm_invoke_config reattaches them with Opik.
    invoke_config = get_llm_invoke_config(llm, include_opik=enable_opik_tracing)
    cache = prompt_cache or NoCacheStrategy()
    messages, kwargs = cache.finalize(list(messages))
    sent = cache.sent_params | frozenset(kwargs)
    dropped: frozenset[str] = frozenset()

    async def _call(request: list) -> Any:  # noqa: ANN401 - whatever the structured model returns
        nonlocal cache, dropped, llm_with_structured_output, cap_sent
        # Each refusal names a parameter this request still carries, and the
        # resend drops it, so the loop ends.
        while True:
            try:
                return _capped_reply(await _ainvoke_throttled(
                    llm_with_structured_output, without_cache_markers(request, dropped) if dropped else request,
                    config=invoke_config, provider=provider, call_timeout=call_timeout,
                    call_kwargs={name: value for name, value in kwargs.items() if name not in dropped},
                ), check_message=capped is not None)
            except Exception as exc:
                if cap_sent and _is_output_limit_refusal(exc):
                    # The model's real limit is below the one on record. Sent once more without
                    # the cap, and every later call of this one too, so this happens once.
                    logger.warning(
                        "The %s request's output cap was refused; resending without it", schema.__name__,
                        extra={"error_type": type(exc).__name__},
                    )
                    cap_sent = False
                    llm_with_structured_output = _apply_structured_output(llm, schema=schema, include_raw=True)
                    continue
                refused = refused_cache_params(exc, sent - dropped)
                if not refused:
                    raise
                logger.warning(
                    "Prompt-cache parameters %s rejected; resending without them", sorted(refused),
                    extra={"error_type": type(exc).__name__},
                )
                if on_cache_rejected is not None:
                    on_cache_rejected(refused)
                else:
                    forget_cache_params(llm, params=refused)
                # The strategy decides what goes with them: Anthropic's and Bedrock's markers stand or fall together.
                cache = cache.without(refused)
                dropped = sent - cache.sent_params

    try:
        response = await _call(messages)
    except NonRetryableError:
        raise
    except Exception as e:
        if raise_unavailable and isinstance(e, LLMUnavailableError):
            raise
        recovered = _recover_structured_json_from_exception(e, schema)
        if recovered is not None:
            logger.info("Recovered schema-valid structured output from invocation error")
            return recovered
        if _is_oversized_number(e):
            # A reply that does not fit the schema, not a failed call. It is not asked for
            # again: there is no reply text to show the model, and a second runaway reply
            # costs a full output limit again. The message names only the digit count.
            logger.warning("The %s reply holds a number too long to read (%s); the reply is discarded", schema.__name__, e)
            if raise_oversized_number:
                raise OversizedNumberError(f"the {schema.__name__} reply holds a number too long to read") from e
            return None
        if isinstance(e, TruncatedReplyError):
            if raise_truncated:
                raise
            logger.warning("The %s reply was cut off at its output cap; the reply is discarded", schema.__name__)
            return None
        if raise_call_errors:
            raise
        log_failure(
            logger, "structured_llm.failed", e, include_error_text=False,
            error_class=answer_error_class(e), schema=schema.__name__, attempt=0,
        )
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
                logger.debug("dict with content response validated successfully")
            else:
                logger.debug("Response is a dict, validating directly")
                response_content = json.dumps(response)
                parsed_response = schema.model_validate(response)
                logger.debug("dict response validated successfully")
        else:
            if hasattr(response, 'content'):
                # Response is an AIMessage or string
                logger.debug("Response is AIMessage, extracting content for parsing")
                # Flattened here rather than only inside `cleanup_content` so the
                # reflection turn below replays plain text: OpenAI's Responses API
                # returns `content` as blocks, and feeding those back verbatim
                # would put reasoning items into the history we resend.
                response_content = coerce_message_content_to_text(response.content)
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
            # Unset when the call itself fails (timeout, network): there is no
            # reply to show the model on the next attempt.
            reflection_content = None
            try:
                reflection_response = await _call(reflection_messages)
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
                        reflection_content = coerce_message_content_to_text(reflection_response.content)
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

            except NonRetryableError:
                raise
            except Exception as reflection_error:
                if raise_unavailable and isinstance(reflection_error, LLMUnavailableError):
                    raise
                if raise_truncated and isinstance(reflection_error, TruncatedReplyError):
                    raise
                if isinstance(reflection_error, TruncatedReplyError) or _is_oversized_number(reflection_error):
                    # Asking again would stop at the same cap, or run away again.
                    logger.warning(
                        "The %s reflection reply was cut off or holds a number too long to read; giving up",
                        schema.__name__,
                    )
                    if raise_oversized_number and _is_oversized_number(reflection_error):
                        raise OversizedNumberError(
                            f"the {schema.__name__} reflection reply holds a number too long to read"
                        ) from reflection_error
                    return None
                recovered = _recover_structured_json_from_exception(reflection_error, schema)
                if recovered is not None:
                    logger.info(
                        "Recovered schema-valid structured output from reflection invocation error"
                    )
                    return recovered
                # An earlier timeout still leaves a retry; one that ends the loop means the
                # last word was a starved call, not a reply that did not fit the schema.
                if raise_call_errors and isinstance(reflection_error, TimeoutError) and attempt == max_retries - 1:
                    raise
                log_failure(
                    logger, "structured_llm.failed", reflection_error, include_error_text=False,
                    error_class=answer_error_class(reflection_error), schema=schema.__name__, attempt=attempt + 1,
                )
                # A call that failed outright left no reply to correct, and
                # the pending request already asks for valid JSON.
                if attempt < max_retries - 1 and reflection_content is not None:
                    reflection_messages.append(AIMessage(content=reflection_content))
                    reflection_messages.append(HumanMessage(content=f"Still incorrect. Error: {str(reflection_error)}. Please try again."))

        logger.error("All reflection attempts failed")
        return None
