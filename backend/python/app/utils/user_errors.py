"""Plain-language reasons for why a file wasn't indexed.

A record's ``reason`` is shown to people in the knowledge base ("Failed - <reason>"),
so it must say what went wrong in their terms and what to do next. Exception text
never goes there: it names internals (vector store, batches, record ids) and is
often a repr. Callers log the exception and store ``to_user_reason(exc)``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.agents.agent_loop.error_classification import classify_error
from app.exceptions.indexing_exceptions import (
    BlockContainerValidationError,
    ChunkingError,
    DocumentProcessingError,
    EmbeddingError,
    EmbeddingModelUnavailableError,
    EmbeddingNotConfiguredError,
    ExtractionError,
    ProcessingError,
    RecordStatusUpdateError,
    VectorStoreError,
)
from app.services.base_client import ServiceCallError
from app.services.parsing.interface import ParseErrorCode

if TYPE_CHECKING:
    from collections.abc import Iterator

# "Reindex" is the action's label in the knowledge base.
GENERIC_FAILURE = (
    "We couldn't index this file. Try Reindex; if it keeps failing, ask your admin for help."
)
RETRIES_EXHAUSTED = (
    "Indexing kept failing after several tries. Try Reindex; if it keeps failing, "
    "ask your admin for help."
)
TEMPORARY_PROBLEM = (
    "PipesHub had a temporary problem while indexing this file. Try Reindex in a few minutes."
)
RETRY_SCHEDULED = "Indexing hit a temporary problem and will retry automatically."
UNREADABLE_FILE = (
    "We couldn't read this file. It may be damaged or in a format we can't open. "
    "Open it on your computer, save a fresh copy (or export it as PDF), and upload it again."
)
PASSWORD_PROTECTED = (
    "This file is password-protected, so we can't read it. Remove the password, "
    "then upload it again."
)
FILE_TOO_LARGE = "This file is too large to index. Split it into smaller files and upload them."
PROCESSING_TIMED_OUT = (
    "This file took too long to process. Try Reindex; if it keeps failing, split it "
    "into smaller files and upload them."
)
SCANNED_DOCUMENT_NEEDS_OCR = (
    "This looks like a scanned document, and reading scanned pages needs an AI model "
    "that can read images. An admin can add one in Workspace → AI Models, then Reindex the file."
)
EMBEDDING_NOT_CONFIGURED = (
    "No embedding model is set up for this workspace, so this file couldn't be made "
    "searchable. An admin can add one in Workspace → AI Models, then Reindex the file."
)
EMBEDDING_UNAVAILABLE = (
    "PipesHub couldn't reach the embedding model, so this file wasn't made searchable. "
    "Try Reindex; if it keeps failing, an admin can check the embedding model in "
    "Workspace → AI Models."
)
CONNECTOR_OFF = (
    "This file's connector is turned off, so it wasn't indexed. Turn the connector on "
    "again (or ask your admin to), and the file will be indexed."
)
CONNECTOR_REMOVED = "The connector this file came from was removed, so the file won't be indexed."
FOLDER_NOTHING_TO_INDEX = "Folders have no content of their own to index."
ENRICHMENT_FAILED = (
    "This file is searchable, but PipesHub couldn't add extra details such as its "
    "topics and categories. Try Reindex to add them."
)
RECOVERY_REQUEUED = "Indexing was interrupted and has been queued to run again."
RECOVERY_RETRY = "Indexing was interrupted and will be retried automatically."
STORED_CONTENT_MISSING = (
    "We couldn't find this file's saved contents. Try Reindex; if that fails, "
    "upload the file again."
)
STORED_CONTENT_DAMAGED = (
    "This file's processed contents are missing or damaged. Try Reindex; if that fails, "
    "upload the file again."
)
EPUB_UNREADABLE = (
    "We couldn't open this e-book. It may be damaged or not really an EPUB file. "
    "Check that it opens in an e-book reader, save a fresh copy from there, and upload it again."
)
EPUB_COPY_PROTECTED = (
    "This book is copy-protected (DRM), so it can't be indexed. Upload a copy without "
    "copy protection, such as one from the publisher or author."
)
EPUB_TOO_LARGE = (
    "This e-book is too large to index: it unpacks to more data or more files than PipesHub "
    "reads from one book. Split it into smaller books, or remove large pictures, and upload it again."
)
EPUB_UNSAFE_PATHS = (
    "This e-book contains files stored outside the book's own folder, so PipesHub didn't open it. "
    "Save a fresh copy from an e-book app and upload that."
)
EPUB_NO_READABLE_CHAPTERS = (
    "This e-book has no chapters we could read. Check that it opens in an e-book reader, "
    "save a fresh copy from there, and upload it again."
)


# Written for people by the code that raised them, and more specific than any
# reason derived from the exception type, so they are stored as they are.
_STORED_AS_WRITTEN = frozenset({
    SCANNED_DOCUMENT_NEEDS_OCR,
    EPUB_UNREADABLE,
    EPUB_COPY_PROTECTED,
    EPUB_TOO_LARGE,
    EPUB_UNSAFE_PATHS,
    EPUB_NO_READABLE_CHAPTERS,
})


def unsupported_file_type(extension: str | None) -> str:
    ext = (extension or "").strip().lstrip(".").lower()
    what = f".{ext} files" if ext and ext not in {"unknown", "none"} else "this type of file"
    return f"PipesHub can't read {what} yet. Convert the file to PDF, DOCX or TXT to make it searchable."


def duplicate_failed(primary_reason: str | None) -> str:
    base = "An identical file failed to index, so this copy was skipped."
    return f"{base} {primary_reason}" if primary_reason else base


def _ai_messages(model: str) -> dict[str, str]:
    unreachable = (
        f"The {model} didn't respond, so this file wasn't indexed. Reindex it in a few "
        "minutes; if it keeps failing, ask your admin to check the model in "
        "Workspace → AI Models."
    )
    return {
        "rate_limit": (
            f"The {model} is busy right now, so this file wasn't indexed. Reindex it in "
            "a few minutes."
        ),
        "auth_error": (
            f"The {model} rejected PipesHub's API key, so this file wasn't indexed. An "
            "admin can update the key in Workspace → AI Models, then Reindex the file."
        ),
        "server_error": unreachable,
        "timeout": unreachable,
        "content_filter": (
            f"The {model} refused to process this file. Ask your admin to check the "
            "model's settings in Workspace → AI Models."
        ),
        "request_too_large": (
            f"This file has more text than the {model} can take in at once. Split it "
            "into smaller files and upload them."
        ),
        "invalid_request": (
            f"The {model} couldn't process this file's contents, so it wasn't indexed. "
            "Reindex it; if it keeps failing, ask your admin to check the model in "
            "Workspace → AI Models."
        ),
    }


def _ai_message(model: str, code: str) -> str:
    """Every AI failure gets an AI message; none reads as a damaged file."""
    messages = _ai_messages(model)
    return messages.get(code, messages["invalid_request"])


# Packages whose errors come from an AI provider rather than from PipesHub itself,
# each matched as a whole package ("openai" or "openai.x"). LangChain is listed
# per model integration: its vector-store integrations (langchain_qdrant, …)
# are storage. Google's AI SDKs are named exactly: googleapiclient errors come
# from connectors, not models.
_PROVIDER_MODULES = (
    "openai", "anthropic", "google.genai", "google.generativeai", "vertexai",
    "groq", "mistralai", "cohere", "litellm", "voyageai", "together", "fireworks",
    "ollama", "xai_sdk",
    "langchain_core.exceptions", "langchain_openai", "langchain_anthropic",
    "langchain_google_genai", "langchain_google_vertexai", "langchain_aws",
    "langchain_mistralai", "langchain_cohere", "langchain_ollama", "langchain_groq",
    "langchain_fireworks", "langchain_xai", "langchain_huggingface",
    "langchain_together", "langchain_voyageai", "langchain_community.chat_models",
    "langchain_community.embeddings", "langchain_community.llms",
)
# Plain HTTP clients. While a file is being processed, the outside services it
# calls over HTTP are AI models (table summaries, image checks); PipesHub's own
# services are reached through ServiceCallError or a database client instead.
_HTTP_CLIENT_MODULES = ("httpx", "aiohttp", "requests", "urllib3")
# Packages whose errors mean one of PipesHub's own stores was briefly unreachable.
_STORE_MODULES = (
    "neo4j", "arango", "qdrant_client", "redis", "aiokafka", "kafka", "pymongo",
    "motor", "opensearchpy", "grpc", "etcd3",
)
_CONTENT_ERRORS = (
    DocumentProcessingError, ExtractionError, ChunkingError, ProcessingError,
    BlockContainerValidationError,
)
_STORAGE_ERRORS = (VectorStoreError, RecordStatusUpdateError)
_EMBEDDING_ERRORS = (EmbeddingError, EmbeddingModelUnavailableError)
_PASSWORD_HINTS = ("password", "encrypted", "decrypt")
_TOO_LARGE_HINTS = ("file too large", "file is too large", "file size exceeds", "exceeds the maximum")
_TOO_LARGE_FOR_MODEL_HINTS = ("context length", "context_length", "maximum context", "too many tokens", "token limit")


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 20:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _module(exc: BaseException) -> str:
    return type(exc).__module__ or ""


def _from(exc: BaseException, packages: tuple[str, ...]) -> bool:
    module = _module(exc)
    return any(module == package or module.startswith(package + ".") for package in packages)


def _ours(exc: BaseException) -> bool:
    return _module(exc).startswith("app.")


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _bad_request_code(exc: BaseException) -> str:
    text = str(exc).lower()
    return "request_too_large" if any(h in text for h in _TOO_LARGE_FOR_MODEL_HINTS) else "invalid_request"


def _provider_code(chain: list[BaseException]) -> str:
    """How an AI call failed: by status code, then error type, then text.

    PipesHub's own errors are skipped: their messages carry counts ("45000
    chars") that read as status codes, and their status codes are ours.
    """
    theirs = [e for e in chain if not _ours(e)]
    for e in theirs:
        status = _status_code(e)
        if status == 429:
            return "rate_limit"
        if status in (401, 403):
            return "auth_error"
        if status == 413:
            return "request_too_large"
        if status == 400:
            return _bad_request_code(e)
        if status is not None and status >= 500:
            return "server_error"
    for e in theirs:
        name = type(e).__name__
        if "RateLimit" in name:
            return "rate_limit"
        if "Authentication" in name or "PermissionDenied" in name:
            return "auth_error"
        if "ContextWindow" in name or "ContextLength" in name:
            return "request_too_large"
        if "BadRequest" in name:
            return _bad_request_code(e)
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name:
            return "timeout"
        if isinstance(e, ConnectionError) or any(
            part in name for part in ("Connection", "ConnectError", "InternalServer", "ServiceUnavailable")
        ):
            return "server_error"
    for e in reversed(theirs):
        code, _ = classify_error(f"{type(e).__name__}: {e}")
        if code != "unknown":
            return code
    return "unknown"


def _llm_not_configured(exc: BaseException) -> bool:
    try:
        from app.utils.llm import LLMNotConfiguredError
    except ImportError:  # pragma: no cover - llm.py always ships with the indexer
        return False
    return isinstance(exc, LLMNotConfiguredError)


def _store_outage(chain: list[BaseException]) -> bool:
    return any(
        _from(e, _STORE_MODULES) or isinstance(e, (ServiceCallError, ConnectionError))
        for e in chain
    )


def _file_problem(chain: list[BaseException]) -> str | None:
    text = " ".join(str(e) for e in chain).lower()
    if any(isinstance(e, MemoryError) for e in chain) or any(h in text for h in _TOO_LARGE_HINTS):
        return FILE_TOO_LARGE
    if any(h in text for h in _PASSWORD_HINTS):
        return PASSWORD_PROTECTED
    return None


def to_user_reason(exc: BaseException | None) -> str:
    """The reason to store on a record that failed to index because of ``exc``.

    The outermost PipesHub error says what was being done (embedding, storing,
    reading the file); the errors under it say why. Checked in that order, so a
    processing timeout reads as a slow file and a storage timeout as an outage.
    """
    if exc is None:
        return GENERIC_FAILURE
    chain = list(_chain(exc))

    for e in chain:
        # ParseError raised in-process, or ParsingClientError carrying the
        # parsing service's answer: the same code and details either way.
        if getattr(e, "code", None) == ParseErrorCode.UNSUPPORTED_FORMAT:
            details = getattr(e, "details", None) or {}
            return unsupported_file_type(details.get("extension"))
        if _llm_not_configured(e):
            return str(e)
        if e.args and isinstance(e.args[0], str) and e.args[0] in _STORED_AS_WRITTEN:
            return e.args[0]
        if isinstance(e, EmbeddingNotConfiguredError):
            return EMBEDDING_NOT_CONFIGURED

    embedding = any(isinstance(e, _EMBEDDING_ERRORS) for e in chain)
    storage = any(isinstance(e, _STORAGE_ERRORS) for e in chain)
    content = any(isinstance(e, _CONTENT_ERRORS) for e in chain)
    provider = [i for i, e in enumerate(chain) if _from(e, _PROVIDER_MODULES)]
    http = [i for i, e in enumerate(chain) if _from(e, _HTTP_CLIENT_MODULES)]

    if embedding:
        code = _provider_code(chain)
        return _ai_messages("embedding model").get(code, EMBEDDING_UNAVAILABLE)

    # Storing a file means embedding it first, so a provider error under a
    # storage wrapper is the embedding model's.
    model = "embedding model" if storage else "AI model"
    if provider:
        return _ai_message(model, _provider_code(chain[provider[0]:]))

    if storage:
        return TEMPORARY_PROBLEM

    if content:
        if _store_outage(chain):
            return TEMPORARY_PROBLEM
        if http:
            return _ai_message(model, _provider_code(chain[http[0]:]))
        file_problem = _file_problem(chain)
        if file_problem:
            return file_problem
        if any(isinstance(e, (asyncio.TimeoutError, TimeoutError)) for e in chain) or any(
            "timed out" in str(e).lower() for e in chain if isinstance(e, _CONTENT_ERRORS)
        ):
            return PROCESSING_TIMED_OUT
        return UNREADABLE_FILE

    if _store_outage(chain) or http or isinstance(chain[-1], (asyncio.TimeoutError, TimeoutError)):
        return TEMPORARY_PROBLEM
    return _file_problem(chain) or GENERIC_FAILURE
