"""Plain-language reasons for why a file wasn't indexed.

A record's ``reason`` is shown to people in the knowledge base ("Failed - <reason>"),
so it must say what went wrong in their terms and what to do next. Exception text
never goes there: it names internals (vector store, batches, record ids) and is
often a repr. Callers log the exception and store ``to_user_reason(exc)``.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.agents.agent_loop.error_classification import classify_error
from app.config.constants.arangodb import Connectors, OriginTypes, RecordTypes
from app.exceptions.indexing_exceptions import (
    AISettingsUnavailableError,
    BlockContainerValidationError,
    ChunkingError,
    DocumentProcessingError,
    EmbeddingError,
    EmbeddingModelUnavailableError,
    EmbeddingNotConfiguredError,
    ExtractionError,
    MultimodalModelNotConfiguredError,
    PasswordProtectedError,
    ProcessingError,
    RecordStatusUpdateError,
    VectorStoreError,
)
from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.services.base_client import ServiceCallError
from app.services.parsing.interface import ParseErrorCode
from app.utils.api_call import ApiCallError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

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
ENRICHMENT_STOPPED_CONNECTOR_OFF = (
    "This file is searchable, but its connector was turned off before PipesHub finished "
    "adding extra details such as its topics and categories. Turn the connector on again "
    "and Reindex the file to add them."
)
ENRICHMENT_STOPPED_CONNECTOR_REMOVED = (
    "This file is searchable, but the connector it came from was removed before PipesHub "
    "finished adding extra details such as its topics and categories."
)
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
EXCEL_TOO_LARGE = (
    "This spreadsheet is too large to index: it holds more data than PipesHub reads from one workbook. "
    "Split it into several smaller workbooks, or remove unused rows and columns, and upload it again."
)
PARSE_WORKER_UNAVAILABLE = (
    "PipesHub could not start the process that reads this file, so it wasn't indexed. "
    "Try Reindex; if it keeps failing, ask your admin for help."
)
GENERATED_FILE_SKIPPED = (
    "This is a generated file (a lock file, minified bundle, source map, test snapshot or "
    "a file its generator marked DO NOT EDIT), "
    "so PipesHub skips it to keep search results useful. Nothing needs fixing."
)
LFS_POINTER_SKIPPED = (
    "This file is stored with Git LFS, so the repository holds only a pointer to it, not "
    "its content. PipesHub skips the pointer. Nothing needs fixing."
)
BINARY_FILE_SKIPPED = (
    "This file holds binary data rather than text, so PipesHub can't read it. If it is "
    "meant to be text, commit it as plain text (UTF-8) and the next sync will index it."
)
PARSE_WORKER_OUT_OF_MEMORY = (
    "PipesHub ran out of memory while reading this file, so it wasn't indexed. Split it "
    "into smaller files and sync or upload them again; if it keeps failing, ask your admin "
    "to give PipesHub more memory."
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
    EXCEL_TOO_LARGE,
    UNREADABLE_FILE,
    PARSE_WORKER_OUT_OF_MEMORY,
    PARSE_WORKER_UNAVAILABLE,
    PROCESSING_TIMED_OUT,
})


def unsupported_file_type(extension: str | None) -> str:
    ext = (extension or "").strip().lstrip(".").lower()
    what = f".{ext} files" if ext and ext not in {"unknown", "none"} else "this type of file"
    return f"PipesHub can't read {what} yet. Convert the file to PDF, DOCX or TXT to make it searchable."


# What a synced item is called in a reason. Some sources let a file be fixed
# where it lives, but not all (a mail attachment can't be edited), so reasons
# say what a new copy in the source leads to rather than asking for an edit.
_SYNCED_FILE_TYPES = frozenset({RecordTypes.FILE.value, RecordTypes.CODE_FILE.value})
_SYNCED_ITEM_KINDS = {
    RecordTypes.ATTACHMENT.value: "attachment",
    RecordTypes.MAIL.value: "email",
    RecordTypes.GROUP_MAIL.value: "email",
    RecordTypes.MESSAGE.value: "message",
    RecordTypes.TICKET.value: "ticket",
    RecordTypes.PULL_REQUEST.value: "pull request",
    RecordTypes.WEBPAGE.value: "page",
    RecordTypes.SHAREPOINT_PAGE.value: "page",
    RecordTypes.COMMENT.value: "comment",
    RecordTypes.WEBPAGE_COMMENT.value: "comment",
    RecordTypes.MEETING.value: "meeting",
    RecordTypes.CASE.value: "case",
    RecordTypes.TASK.value: "task",
}
# The app refuses Reindex on one record of a code repository and points at a
# reindex of the whole repository instead (isRecordReindexBlocked in
# frontend/app/(main)/knowledge-base/utils/reindex-label.ts).
_CODE_REPO_CONNECTORS = frozenset({
    Connectors.GITHUB.value, Connectors.GITHUB_TEAMS.value,
    Connectors.GITLAB.value, Connectors.GITLAB_PERSONAL.value,
})


def for_synced_item(
    reason: str, record_type: str | None, source: str | None, connector: str | None = None,
) -> str:
    """``reason`` for an item a connector synced from ``source``.

    The reasons below tell someone to upload the file again, which they never
    did and can't do for a synced item. Any other reason is returned as it is.
    ``connector`` is the record's connector type, which decides whether the
    item can be reindexed on its own.
    """
    src = source or "the source"
    code_repo = (connector or "").strip().upper() in _CODE_REPO_CONNECTORS
    retry = "Reindex the whole repository from the knowledge hub" if code_repo else "Try Reindex"
    if record_type in _SYNCED_FILE_TYPES:
        return {
            UNREADABLE_FILE: (
                f"We couldn't read this file from {src}. It may be damaged or in a format we "
                f"can't open, so it isn't searchable. A fresh copy (or a PDF export) added to "
                f"{src} is indexed on the next sync."
            ),
            FILE_TOO_LARGE: (
                f"This file from {src} is too large to index, so it isn't searchable. Smaller "
                f"files split from it and added to {src} are indexed on the next sync."
            ),
            PASSWORD_PROTECTED: (
                f"This file from {src} is password-protected, so we can't read it and it isn't "
                f"searchable. A copy without the password added to {src} is indexed on the "
                "next sync."
            ),
            PROCESSING_TIMED_OUT: (
                f"This file from {src} took too long to process. {retry}; if it keeps "
                "failing, ask your admin for help."
            ),
        }.get(reason, reason)
    kind = _SYNCED_ITEM_KINDS.get(record_type or "", "item")
    after_change = (
        f"It's indexed again on the next sync after it changes in {src}" if code_repo
        else f"Try Reindex after it changes in {src}"
    )
    return {
        UNREADABLE_FILE: (
            f"We couldn't read this {kind} from {src}: part of its content is in a form "
            f"PipesHub can't process, so it isn't searchable. {after_change}; if it keeps "
            "failing, ask your admin for help."
        ),
        FILE_TOO_LARGE: (
            f"This {kind} from {src} is larger than PipesHub can index, so it isn't "
            f"searchable. Nothing is wrong with it in {src}; if you need it in search, "
            "ask your admin for help."
        ),
        PASSWORD_PROTECTED: (
            f"This {kind} from {src} is password-protected, so we can't read it and it "
            "isn't searchable. If you need it in search, ask your admin for help."
        ),
        PROCESSING_TIMED_OUT: (
            f"This {kind} from {src} took too long to process. {retry}; if it keeps "
            "failing, ask your admin for help."
        ),
    }.get(reason, reason)


def _megabytes(size_bytes: int, *, round_up: bool = False) -> str:
    tenths = size_bytes / (1024 * 1024) * 10
    return f"{(math.ceil(tenths) if round_up else round(tenths)) / 10:.1f}".removesuffix(".0")


def text_file_too_large(size_bytes: int, limit_bytes: int, *, repository_file: bool) -> str:
    # A record marked "File Type Not Supported" has no Reindex action of its
    # own, and a sync skips a repository that has not changed. What brings the
    # file back is the repository row's own action, which reads "Index all":
    # record groups carry no indexing status, so the UI never offers them
    # "Re-index all" (frontend reindex-label.ts).
    after_raising = (
        "choose Index all on the repository" if repository_file else "upload it again"
    )
    # Rounded up, so a file one byte over a 5 MB limit never reads as "5 MB".
    return (
        f"This file is {_megabytes(size_bytes, round_up=True)} MB, and PipesHub reads code "
        f"and plain-text files up to {_megabytes(limit_bytes)} MB. Split it into smaller "
        "files, or ask your admin to raise the limit (CODE_FILE_MAX_SIZE_MB) and then "
        f"{after_raising}."
    )


def duplicate_password_protected(primary_reason: str | None) -> str:
    base = "An identical file is password-protected, so this copy was skipped."
    return f"{base} {primary_reason}" if primary_reason else base


def duplicate_failed(primary_reason: str | None) -> str:
    base = "An identical file failed to index, so this copy was skipped."
    return f"{base} {primary_reason}" if primary_reason else base


def _utc_time(at_ms: int, now_ms: int) -> str:
    at = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc)
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    if at.date() == now.date():
        return at.strftime("%H:%M UTC")
    return f"{at.strftime('%H:%M UTC')} on {at.day} {at.strftime('%b')}"


def rate_limited_will_retry(source: str | None, retry_at_ms: int, now_ms: int) -> str:
    # The UI shows a QUEUED record's reason only when it reads "is limiting how
    # often PipesHub can read from it" (frontend/lib/utils/rate-limit-pause.ts).
    return (
        f"{source or 'The source'} is limiting how often PipesHub can read from it, so "
        f"indexing this file is paused. It will retry automatically after "
        f"{_utc_time(retry_at_ms, now_ms)}."
    )


def rate_limited_gave_up(
    source: str | None, retry_at_ms: int, now_ms: int, max_wait_hours: float
) -> str:
    when = _utc_time(retry_at_ms, now_ms)
    return (
        f"{source or 'The source'} kept limiting how often PipesHub can read from it and "
        f"would not allow it again until {when}, more than {max_wait_hours:g} hours after "
        f"indexing this file was first paused, so PipesHub stopped trying. Reindex it "
        f"after {when}."
    )


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
        "model_not_found": (
            f"The {model}'s provider couldn't find the model it names at its endpoint, so "
            "this file wasn't indexed. An admin can check the model name and endpoint in "
            "Workspace → AI Models, then Reindex the file."
        ),
        "access_blocked": (
            f"The {model}'s provider refused access to the model (model access, billing "
            "or account policy), so this file wasn't indexed. An admin can check the "
            "model's access in the provider's console, then Reindex the file."
        ),
        "permission_denied": (
            f"The {model}'s credentials work but aren't allowed to use the model, so this "
            "file wasn't indexed. An admin can grant them access to the model (on Bedrock, "
            "bedrock:InvokeModel and bedrock:InvokeModelWithResponseStream on the model or "
            "inference profile), then Reindex the file."
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
    # google-genai keeps the HTTP status on `code`; its `status` is a name like "UNAUTHENTICATED".
    code = getattr(exc, "code", None)
    if _from(exc, ("google.genai",)) and isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    # botocore's ClientError keeps the parsed response as a dict.
    if isinstance(response, dict):
        value = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return value if isinstance(value, int) else None
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


# What Bedrock says when the key itself is wrong. All of them are HTTP 403, like
# a permission denial, and AWS words the three most common ones the same way.
_BEDROCK_CREDENTIAL_MARKERS = (
    "(unrecognizedclientexception)",
    "(invalidclienttokenid)",
    "(expiredtokenexception)",
    "(expiredtoken)",
    "(invalidsignatureexception)",
    "(signaturedoesnotmatch)",
    "(incompletesignature)",
    "security token included in the request is invalid",
    "security token included in the request is expired",
    "signature we calculated does not match",
)


def bedrock_access_denied_cause(exc: BaseException | None) -> str | None:
    """Why Bedrock refused a call: ``credentials``, ``account``, ``marketplace``,
    ``scp``, ``iam``, ``other``, or None when there is no exception to read.

    All of these are HTTP 403, but each has a different fix. ``credentials`` (an
    unknown, mistyped or expired key, secret or session token) is the caller's
    to re-enter. ``iam`` is fixed by the caller's policy. ``account`` (the model
    is not enabled for the AWS account) needs AWS Support; ``marketplace`` (the
    model's subscription failed, e.g. INVALID_PAYMENT_INSTRUMENT) needs billing
    fixed; ``scp`` needs the Organizations admin. An unrecognised denial is
    ``other`` so it is not mistaken for an IAM problem.
    """
    if exc is None:
        return None
    for current in _chain(exc):
        text = str(current).lower()
        if any(marker in text for marker in _BEDROCK_CREDENTIAL_MARKERS):
            return "credentials"
        if "not available for this account" in text:
            return "account"
        if "marketplace subscription" in text or "payment instrument" in text:
            return "marketplace"
        if "service control policy" in text:
            return "scp"
        if "not authorized to perform" in text or "(notauthorized)" in text:
            return "iam"
    return "other"


def _aws_error_code(exc: BaseException) -> str | None:
    """botocore's error code (``AccessDeniedException``, ``NotAuthorized``, ...)."""
    if not _from(exc, ("botocore",)):
        return None
    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


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
        # Bedrock documents NotAuthorized, a missing permission, as HTTP 400,
        # which would otherwise read as a request the model could not process.
        if _aws_error_code(e) == "NotAuthorized":
            return "auth_error"
        status = _status_code(e)
        if status == 429:
            return "rate_limit"
        if status in (401, 403):
            return "auth_error"
        if status == 404:
            return "model_not_found"
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


# Bedrock refusals where the key is fine and something on the AWS side says no.
_BEDROCK_BLOCKED_CAUSES = frozenset({"account", "marketplace", "scp", "other"})


# bedrock-runtime operations PipesHub's chat and embedding models call. botocore
# stays out of _PROVIDER_MODULES: a connector's S3 ClientError is a store failure,
# and the operation name is what tells the two apart.
_BEDROCK_MODEL_OPERATIONS = frozenset(
    {"Converse", "ConverseStream", "InvokeModel", "InvokeModelWithResponseStream"}
)


def _bedrock_model_error(exc: BaseException) -> bool:
    """A botocore error raised by a Bedrock model call, which langchain_aws lets out unwrapped."""
    return _from(exc, ("botocore",)) and getattr(exc, "operation_name", None) in _BEDROCK_MODEL_OPERATIONS


def _ai_failure_code(chain: list[BaseException]) -> str:
    """`_provider_code`, except a Bedrock denial that is not a bad key.

    botocore answers those with the same 403 as a bad key, which reads as
    "rejected PipesHub's API key" and sends the admin to rotate a key that works.
    """
    code = _provider_code(chain)
    if code != "auth_error" or not any(_bedrock_model_error(e) for e in chain):
        return code
    cause = bedrock_access_denied_cause(chain[0])
    if cause == "iam":
        return "permission_denied"
    if cause in _BEDROCK_BLOCKED_CAUSES:
        return "access_blocked"
    return code


def provider_error_code(exc: BaseException) -> str:
    """`classify_error`'s code for an exception raised by an AI provider's SDK."""
    return _provider_code(list(_chain(exc)))


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


def too_large_refusal(exc: BaseException | None) -> StorageRequestRejectedError | None:
    """The storage service's refusal of the record's content for its size, if that is what failed.

    Narrower than ``to_user_reason(exc) == FILE_TOO_LARGE``, which also covers
    running out of memory and parser messages about size.
    """
    if exc is None:
        return None
    return next(
        (e for e in _chain(exc) if isinstance(e, StorageRequestRejectedError) and e.too_large), None
    )


def to_user_reason(
    exc: BaseException | None,
    *,
    record: Mapping[str, object] | None = None,
    source: str | None = None,
) -> str:
    """The reason to store on a record that failed to index because of ``exc``.

    The outermost PipesHub error says what was being done (embedding, storing,
    reading the file); the errors under it say why. Checked in that order, so a
    processing timeout reads as a slow file and a storage timeout as an outage.

    Several reasons tell someone to upload the file again. For a ``record``
    synced by a connector they say what to do in ``source`` (the connector's
    name) instead, see ``for_synced_item``.
    """
    reason = _reason(exc)
    if record is not None and record.get("origin") == OriginTypes.CONNECTOR.value:
        record_type = record.get("recordType")
        connector = record.get("connectorName")
        return for_synced_item(
            reason,
            record_type if isinstance(record_type, str) else None,
            source,
            connector if isinstance(connector, str) else None,
        )
    return reason


def is_password_protected(exc: BaseException | None) -> bool:
    """Whether the file itself was found to need a password, which ends its indexing as
    ``PASSWORD_PROTECTED`` rather than ``FAILED``.

    Only the typed error counts, or the parsing service's PASSWORD_REQUIRED code.
    The wording of an error does not: a reason can still read "password-protected"
    from ``_PASSWORD_HINTS`` for a file nothing recognised as encrypted.
    """
    if exc is None:
        return False
    return any(
        isinstance(e, PasswordProtectedError)
        or getattr(e, "code", None) == ParseErrorCode.PASSWORD_REQUIRED
        for e in _chain(exc)
    )


def _reason(exc: BaseException | None) -> str:
    if exc is None:
        return GENERIC_FAILURE
    chain = list(_chain(exc))

    for e in chain:
        # The source refused the item, and the connector said why in its own words.
        if isinstance(e, ApiCallError) and e.source_reason:
            return e.source_reason
        if isinstance(e, PasswordProtectedError):
            return PASSWORD_PROTECTED
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
        if isinstance(e, MultimodalModelNotConfiguredError):
            return SCANNED_DOCUMENT_NEEDS_OCR
        if isinstance(e, AISettingsUnavailableError):
            return TEMPORARY_PROBLEM
        if isinstance(e, StorageRequestRejectedError):
            # Refused for what it is, not for when it was sent: not a temporary problem.
            return FILE_TOO_LARGE if e.too_large else GENERIC_FAILURE

    embedding = any(isinstance(e, _EMBEDDING_ERRORS) for e in chain)
    storage = any(isinstance(e, _STORAGE_ERRORS) for e in chain)
    content = any(isinstance(e, _CONTENT_ERRORS) for e in chain)
    provider = [i for i, e in enumerate(chain) if _from(e, _PROVIDER_MODULES) or _bedrock_model_error(e)]
    http = [i for i, e in enumerate(chain) if _from(e, _HTTP_CLIENT_MODULES)]

    if embedding:
        code = _ai_failure_code(chain)
        return _ai_messages("embedding model").get(code, EMBEDDING_UNAVAILABLE)

    # Storing a file means embedding it first, so a provider error under a
    # storage wrapper is the embedding model's.
    model = "embedding model" if storage else "AI model"
    if provider:
        return _ai_message(model, _ai_failure_code(chain[provider[0]:]))

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
