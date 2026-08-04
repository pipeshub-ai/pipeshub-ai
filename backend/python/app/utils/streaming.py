import asyncio
import json
import logging
import os
import random
import re
from collections.abc import AsyncGenerator
from typing import (
    Any,
    TypeVar,
)

import aiohttp
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mistralai import ChatMistralAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.config.constants.http_status_code import HttpStatusCode
from app.modules.agents.qna.reference_data import normalize_reference_data_items
from app.modules.parsers.excel.prompt_template import RowDescriptions
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    count_tokens,
    flattened_result_sort_key,
    get_flattened_results,
    record_to_message_content,
)
from app.utils.citations import (
    detect_hallucinated_citation_urls,
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)
from app.utils.concurrency import indexing_llm_slot
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.indexing_metrics import note_llm_call, note_rate_limit_retry
from app.utils.logger import create_logger
from app.utils.tool_handlers import ContentHandler, ToolHandlerRegistry

CITE_BLOCK_RE = re.compile(r'(?:\s*\[[^\]]*\]\([^\)]*\))+')
INCOMPLETE_CITE_RE = re.compile(r'\[[^\]]*(?:\]\([^\)]*)?$')
logger = create_logger("streaming")

opik_tracer = None
api_key = os.getenv("OPIK_API_KEY")
workspace = os.getenv("OPIK_WORKSPACE")
if api_key and workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        opik_tracer = OpikTracer()
    except Exception as e:
        logger.warning(f"Error configuring Opik: {e}")
else:
    logger.info("OPIK_API_KEY and/or OPIK_WORKSPACE not set. Skipping Opik configuration.")

MAX_TOKENS_THRESHOLD = 80000
TOOL_EXECUTION_TOKEN_RATIO = 0.5
MAX_REFLECTION_RETRIES_DEFAULT = 2
MAX_CITATION_REFLECTION_RETRIES = 2
MAX_TOOL_HOPS = 6
MAX_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "too many requests", "quota exceeded")


def _is_rate_limit_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == HttpStatusCode.TOO_MANY_REQUESTS.value:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


async def _ainvoke_throttled(llm: BaseChatModel, messages: list[Any]) -> Any:  # noqa: ANN401
    """Invoke *llm*, holding a slot in the process-wide indexing budget.

    Retries rate-limit errors with jittered backoff. The jitter matters more than the
    retry: LangChain's own ``max_retries`` has none, so concurrent row batches that get
    429ed all retry in lockstep.
    """
    delay = 0.0
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        async with indexing_llm_slot():
            try:
                result = await llm.ainvoke(messages)
                note_llm_call()
                return result
            except Exception as e:
                if not _is_rate_limit_error(e) or attempt == MAX_RATE_LIMIT_RETRIES - 1:
                    raise
                note_rate_limit_retry()
                delay = 2 ** attempt + random.uniform(0, 1)
        logger.warning(
            f"Rate limited (attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES}), "
            f"retrying in {delay:.1f}s"
        )
        # Sleep outside the slot so a backing-off call does not occupy the budget.
        await asyncio.sleep(delay)

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


async def stream_content(signed_url: str, record_id: str | None = None, file_name: str | None = None) -> AsyncGenerator[bytes, None]:
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

async def aiter_llm_stream(llm, messages,parts=None) -> AsyncGenerator[str | dict, None]:
    """Async iterator for LLM streaming that normalizes content to text.

    The LLM provider may return content as a string or a list of content parts
    (e.g., [{"type": "text", "text": "..."}, {"type": "image_url", ...}]).
    We extract and concatenate only textual parts for streaming.
    """
    if parts is None:
        parts = []
    config = {"callbacks": [opik_tracer]} if opik_tracer is not None else {}
    try:
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
                        logger.warning(
                            f"Skipping chunk due to validation error "
                            f"(likely role=None from provider): {e}"
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
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, BaseMessage) and getattr(last_msg, "type", None) == "ai":
                existing_ai_content = getattr(last_msg, "content", None)
            elif isinstance(last_msg, dict) and last_msg.get("role") == "assistant":
                existing_ai_content = last_msg.get("content")

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
                    "data": {
                        "answer": normalized,
                        "citations": cites,
                        "reason": None,
                        "confidence": confidence,
                    },
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
    stripped = _flatten_unusable_markdown_links(stripped)
    return stripped


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
    incomplete_cite_re = re.compile(r'\[[^\]]*(?:\]\([^\)]*)?$')
    # incomplete_cite_re = re.compile(r'[\[【][^\]】]*$')
    word_iter = re.compile(r'\S+').finditer
    return answer_key_re, cite_block_re, incomplete_cite_re, word_iter

async def call_aiter_llm_stream_simple(
    llm,
    messages,
    final_results,
    records=None,
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
        ai = None
        tool_calls_happened = True
        for part in parts:
            if ai is None:
                ai = part
            else:
                ai += part

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
            "data": {
                "answer": normalized,
                "citations": cites,
                "reason": None,
                "confidence": confidence,
            },
        }
    except Exception as exc:
        logger.error("Error in call_aiter_llm_stream_simple", exc_info=True)
        yield {"event": "error", "data": {"error": f"Error in LLM streaming: {exc}"}}
        return

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


async def invoke_with_structured_output_and_reflection(
    llm: BaseChatModel,
    messages: list,
    schema: type[SchemaT],
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
) -> SchemaT | None:
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
        response = await _ainvoke_throttled(llm_with_structured_output, messages)
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
                reflection_response = await _ainvoke_throttled(llm_with_structured_output, reflection_messages)
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


async def invoke_with_count_validation_and_reflection(
    llm: BaseChatModel,
    messages: list,
    schema: type[SchemaT],
    count_field: str,
    expected_count: int,
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
) -> SchemaT | None:
    """
    Invoke LLM with structured output and validate that a list field has the expected length.

    If the LLM returns the wrong number of items, performs one reflection to correct it.

    Args:
        llm: The LangChain chat model to use
        messages: List of messages to send to the LLM
        schema: Pydantic model class to validate the response against
        count_field: Name of the list field on *schema* whose length must match
        expected_count: Expected length of that field
        max_retries: Maximum number of reflection retries on parse failure (default: 2)

    Returns:
        Validated schema instance with correct count, or None if validation fails
    """
    # First, try to get a parsed response using the standard reflection function
    parsed_response = await invoke_with_structured_output_and_reflection(
        llm, messages, schema, max_retries
    )

    if parsed_response is None:
        logger.warning(f"Failed to parse {schema.__name__} after initial attempts")
        return None

    # Validate the count matches expected
    actual_count = len(getattr(parsed_response, count_field) or [])

    if actual_count == expected_count:
        logger.debug(f"Count validation passed: {actual_count} {count_field}")
        return parsed_response

    # Count mismatch detected - perform reflection to correct it
    logger.warning(
        f"Count mismatch: LLM returned {actual_count} {count_field} "
        f"but {expected_count} were expected. Attempting reflection..."
    )

    # Build reflection messages
    reflection_messages = list(messages)
    reflection_messages.append(
        AIMessage(content=json.dumps(parsed_response.model_dump()))
    )

    reflection_prompt = f"""Your previous response contained {actual_count} {count_field}, but exactly {expected_count} {count_field} are required.

CRITICAL: You must provide EXACTLY {expected_count} {count_field} - one for each row, in the same order they were provided.

Please correct your response to include exactly {expected_count} {count_field}. Do not skip any rows, do not combine rows, and do not split rows.

Respond with a valid JSON object:
{{
    "{count_field}": [
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
            llm, reflection_messages, schema, max_retries=1
        )

        if reflection_response is None:
            logger.error("Reflection failed to parse response")
            return None

        # Validate the count again
        reflection_count = len(getattr(reflection_response, count_field) or [])

        if reflection_count == expected_count:
            logger.info(
                f"Reflection successful: corrected from {actual_count} to {reflection_count} {count_field}"
            )
            return reflection_response
        else:
            logger.error(
                f"Reflection failed: still have {reflection_count} {count_field} "
                f"instead of {expected_count}"
            )
            return None

    except Exception as e:
        logger.error(f"Reflection attempt failed with error: {e}")
        return None


async def invoke_with_row_descriptions_and_reflection(
    llm: BaseChatModel,
    messages: list,
    expected_count: int,
    max_retries: int = MAX_REFLECTION_RETRIES_DEFAULT,
) -> RowDescriptions | None:
    """Invoke LLM for row descriptions and validate the count matches expected."""
    return await invoke_with_count_validation_and_reflection(
        llm, messages, RowDescriptions, "descriptions", expected_count, max_retries
    )
