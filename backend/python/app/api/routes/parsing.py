"""Parsing Service HTTP API routes.

Endpoints
---------
POST /api/v1/parse
    Parse a file into blocks (or a raw parsed document for Docling-backed
    providers, which defer block construction to the caller). File content is
    sent as multipart form-data (field: ``file``). Metadata fields are passed
    as form fields.

GET  /api/v1/parse/providers
    List registered providers per format key.

GET  /health
    Standard health probe (defined in parsing_main.py but kept here for ref).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.middlewares.auth import (
    authMiddleware,
    require_service_token,
    service_token_org,
)
from app.config.constants.service import TokenScopes
from app.exceptions.indexing_exceptions import (
    MultimodalModelNotConfiguredError,
    PasswordProtectedError,
)
from app.modules.parsers.encryption import raise_if_password_protected_office
from app.modules.pipeline.fingerprint import content_revision
from app.services.model_identity.propagation import scope_from_claims
from app.services.model_identity.workload import Workload
from app.services.paid_cache import locks as lock_limits
from app.services.paid_cache.access import abandon_run, complete_run, start_run
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParserProvider,
)
from app.services.parsing.load import (
    parse_load,
    parsing_max_concurrent,
    stuck_conversions,
)
from app.services.parsing.registry import ParserRegistry
from app.services.parsing.thread_work import ThreadWork, track
from app.services.resource_governor import (
    acquire_gate_with_backpressure,
    classify,
    gate_pool,
    parse_cost,
)
from app.services.resource_governor.kinds import (
    EXCESS_WAIT_SECONDS,
    classify_kind,
    permit_excess_bytes,
)
from app.services.resource_governor.lanes import LaneWaitTimeout
from app.services.resource_governor.memory_domain import (
    parse_admission_cap,
    reset_permit_covers,
    set_permit_covers,
    stamp_admitted_here,
)
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.org_share import OrgShare, max_org_share
from app.services.resource_governor.policy import INDEX_LIGHT_WORKING_SET_GB
from app.utils.request_context import current_display_id
from app.utils.semaphore_logger import SemaphoreLogger
from app.utils.user_errors import SCANNED_DOCUMENT_NEEDS_OCR

logger = logging.getLogger(__name__)

require_parse_token = require_service_token(TokenScopes.DOCUMENT_PARSE)

# On the router, not the app, so the checks hold wherever this router is included.
router = APIRouter(
    prefix="/api/v1/parse",
    tags=["parsing"],
    dependencies=[Depends(authMiddleware), Depends(require_parse_token)],
)

# How long a request will wait for a parsing slot before it's reported as
# "saturated" (still waiting) and, ultimately, before the gate gives up and
# returns 429 — see PARSE_GATE_TIMEOUT_SECONDS below.
PARSE_QUEUE_WAIT_WARN_SECONDS = 10.0
# Total time a request may wait for a free slot before the gate responds 429
# backpressure. Generous relative to the old 30s: the indexing consumer's
# record-level timeout is 1800s with auto-renewed leases, so a record that
# waits this long in the parsing queue and eventually gets a slot is far
# cheaper than one that gets shed and has to re-enter the whole pipeline.
PARSE_GATE_TIMEOUT_SECONDS = 120.0
# Suggested client backoff before its next backpressured attempt — see
# ParsingClient/base_client's Retry-After handling (services/base_client.py).
PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS = 5
# Parses slower than this are logged as an outlier so pathological documents
# are identifiable without turning on debug logging. Large PDFs (OCR/VLM
# heavy documents) can legitimately take several minutes, so this is set
# well above typical large-document parse times, not just above the median.
SLOW_PARSE_WARN_SECONDS = 300.0
# Per pool, one org holds at most this share while another org wants the pool (S-4).
_ORG_SHARE = OrgShare(max_org_share())

# Why parse requests were refused with a 429, cumulative since start, per tier. No caller is
# recorded. Counted on the request path, read from the health endpoint's thread.
_REJECTION_REASONS = ("gate_timeout", "over_share", "memory_excess", "lane_timeout")
_rejected_lock = threading.Lock()
_rejected: dict[str, dict[str, int]] = {
    tier.value: dict.fromkeys(_REJECTION_REASONS, 0) for tier in ParseTier
}


class _StageDeadlineExceeded(Exception):
    """The parse ran longer than the stage deadline (not any other timeout raised inside the parser)."""

    def __init__(self, limit_s: float) -> None:
        super().__init__(limit_s)
        self.limit_s = limit_s


#: A conversion still running this long after its parse ended is logged (with its age) at every tick.
THREAD_OVERRUN_WARN_SECONDS = 30.0
#: How often a held admission is re-reported and the stuck-conversion gauges refreshed.
STUCK_TICK_SECONDS = 30.0

#: Held admissions are counted on the parsing service's ``/metrics`` (``pipeshub_parse_stuck_*``).


def _release_when_work_ends(release: Any, work: ThreadWork, deadline_at: float, record_name: str) -> None:  # noqa: ANN401
    """Keep the gate permit and the memory reservation until the conversion's threads return, then free them once.

    The 504 has been answered, but the cancelled parse may still be running in a thread on the memory those stand
    for; releasing them now would admit the next parse beside it. The release runs on the event loop. While it is
    held, every tick logs the age and the stuck-conversion gauges say how many and how old.
    """
    loop = asyncio.get_running_loop()
    done = threading.Event()
    token = id(done)
    logger.warning(
        "Parse ended with %d conversion(s) still running in threads; the permit is held until they return",
        work.running(),
    )
    stuck_conversions().add(token, deadline_at)

    def tick() -> None:
        if done.is_set():
            return
        age = time.monotonic() - deadline_at
        if age > THREAD_OVERRUN_WARN_SECONDS:
            logger.warning("A conversion for '%s' still runs %.0f s after its parse; its permit is held", record_name, age)
        loop.call_later(STUCK_TICK_SECONDS, tick)

    loop.call_later(STUCK_TICK_SECONDS, tick)

    def free() -> None:
        if done.is_set():
            return
        done.set()
        stuck_conversions().remove(token)
        over = time.monotonic() - deadline_at
        if over > THREAD_OVERRUN_WARN_SECONDS:
            logger.warning("A conversion for '%s' ended %.0f s after its parse", record_name, over)
        release()

    def hop_to_loop() -> None:
        # Runs on the thread that finished last. A loop that is already closed means the process is stopping: the
        # release has nothing left to free and is dropped quietly.
        if loop.is_closed():
            stuck_conversions().remove(token)
            return
        try:
            loop.call_soon_threadsafe(free)
        except RuntimeError:
            stuck_conversions().remove(token)

    work.when_idle(hop_to_loop)


_budget_mismatch_logged: set[tuple[float, float]] = set()


def _warn_if_budget_differs(sent: float | None, extension: str, mime_type: str | None) -> None:
    """One warning per pair when the caller's record budget is not this service's own (the settings drifted)."""
    from app.services.messaging.config import messaging_env

    if sent is None:
        return
    own = messaging_env.record_processing_timeout_for(extension, mime_type)
    if abs(own - sent) > 1.0 and (round(sent), round(own)) not in _budget_mismatch_logged:
        _budget_mismatch_logged.add((round(sent), round(own)))
        logger.warning(
            "The caller's record budget (%.0f s) differs from this service's (%.0f s); the caller's is used. "
            "Set RECORD_PROCESSING_TIMEOUT and PDF_RECORD_PROCESSING_TIMEOUT alike on both.", sent, own,
        )


def _count_rejection(tier: ParseTier, reason: str) -> None:
    with _rejected_lock:
        _rejected[tier.value][reason] += 1


def parse_admission_stats() -> dict[str, Any]:
    """Parse requests refused with a 429, by tier and reason, for the health endpoint."""
    with _rejected_lock:
        return {"rejected": {tier: dict(reasons) for tier, reasons in _rejected.items()}}


def _config_service(request: Request) -> Any:  # noqa: ANN401
    container = getattr(request.app, "container", None)
    return container.config_service() if container is not None else None


def _get_registry(request: Request) -> ParserRegistry:
    """Pull the ParserRegistry from FastAPI app state."""
    registry: ParserRegistry = request.app.state.parser_registry
    return registry


# ---------------------------------------------------------------------------
# POST /api/v1/parse
# ---------------------------------------------------------------------------


def _saturated_response(max_concurrent: int | None) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS)},
        content={
            "success": False,
            "error": {
                "code": ParseErrorCode.PARSE_BACKPRESSURE.value,
                "message": "Parsing service is at capacity; retry later.",
                "details": {"reason": "max_concurrent", "limit": max_concurrent},
            },
        },
    )


@router.post("", summary="Parse a file into a BlocksContainer")
async def parse_file(
    request: Request,
    file: Annotated[UploadFile, File(description="File to parse")],
    claims: Annotated[Mapping[str, Any], Depends(require_parse_token)],
    record_name: Annotated[str, Form(description="Human-readable filename")] = "",
    mime_type: Annotated[str, Form(description="MIME type of the file")] = "",
    extension: Annotated[str, Form(description="File extension (without dot)")] = "",
    org_id: Annotated[str | None, Form(description="Organisation ID")] = None,
    provider: Annotated[str | None, Form(description="Parser provider override")] = None,
    skip_table_enrichment: Annotated[bool, Form(description="Skip LLM table summaries")] = False,  # noqa: FBT002
    file_path: Annotated[str | None, Form(description="Repo-relative path of the file")] = None,
    refresh_paid_outputs: Annotated[bool, Form(description="Bypass the stored model outputs for this run")] = False,  # noqa: FBT002
    record_budget_s: Annotated[float | None, Form(description="The caller's processing budget for the record, seconds")] = None,
) -> JSONResponse:
    """Count the request in, refuse it at once when ``PARSING_MAX_CONCURRENT`` is reached, else parse it."""
    load = parse_load()
    max_concurrent = parsing_max_concurrent()
    if not load.try_enter(max_concurrent):
        return _saturated_response(max_concurrent)
    try:
        response = await _parse_file(
            request, file, claims, record_name, mime_type, extension, org_id,
            provider, skip_table_enrichment, file_path, refresh_paid_outputs, record_budget_s,
        )
    finally:
        load.leave()
    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        # The gate's wait or the memory wait ran out: saturated all the same.
        load.note_saturated()
    return response


async def _parse_file(
    request: Request,
    file: Annotated[UploadFile, File(description="File to parse")],
    claims: Annotated[Mapping[str, Any], Depends(require_parse_token)],
    record_name: Annotated[str, Form(description="Human-readable filename")] = "",
    mime_type: Annotated[str, Form(description="MIME type of the file")] = "",
    extension: Annotated[str, Form(description="File extension (without dot)")] = "",
    org_id: Annotated[str | None, Form(description="Organisation ID")] = None,
    provider: Annotated[str | None, Form(description="Parser provider override")] = None,
    skip_table_enrichment: Annotated[bool, Form(description="Skip LLM table summaries")] = False,  # noqa: FBT002
    file_path: Annotated[str | None, Form(description="Repo-relative path of the file")] = None,
    refresh_paid_outputs: Annotated[bool, Form(description="Bypass the stored model outputs for this run")] = False,  # noqa: FBT002
    record_budget_s: Annotated[float | None, Form(description="The caller's processing budget for the record, seconds")] = None,
) -> JSONResponse:
    """Parse *file* into blocks, or a raw parsed document for Docling-backed providers.

    The response body is ``ParseResponse`` JSON. Exactly one of ``block_container``
    or ``raw_document`` is populated - Docling-backed providers defer block
    construction (and any LLM calls) to the caller so this service stays stateless::

        {
          "success": true,
          "block_container": { ... } | null,
          "raw_document": "<serialized DoclingDocument>" | null,
          "provider_used": "docling",
          "error": null
        }
    """
    service_token_org(claims, org_id)
    registry = _get_registry(request)

    # Resolve provider enum if supplied
    provider_enum: ParserProvider | None = None
    if provider:
        try:
            provider_enum = ParserProvider(provider)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.INVALID_INPUT.value,
                        "message": f"Unknown provider '{provider}'. Valid values: "
                        + ", ".join(p.value for p in ParserProvider),
                        "details": {},
                    },
                },
            )

    content = await file.read()
    if not record_name:
        record_name = file.filename or "unknown"
    if not mime_type and file.content_type:
        mime_type = file.content_type
    if not extension and "." in record_name:
        extension = record_name.rsplit(".", 1)[-1]

    config: dict = {"extension": extension}
    if skip_table_enrichment:
        config["skip_table_enrichment"] = True
    if file_path:
        config["file_path"] = file_path

    if provider_enum is None:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error": {
                    "code": ParseErrorCode.NO_PROVIDER_PROVIDED.value,
                    "message": "No provider provided",
                },
            })

    message_id = current_display_id()
    tier = classify(extension, mime_type)
    cost = parse_cost(tier, len(content))
    logger.debug(
        "Received parse request: record='%s' format=%s provider=%s size_bytes=%d tier=%s cost=%d",
        record_name, extension or mime_type or "unknown", provider_enum.value, len(content),
        tier.value, cost,
    )

    governor = request.app.state.governor
    pool = gate_pool(tier).value
    gate = governor.gate(gate_pool(tier))
    cap = parse_admission_cap(governor, gate_pool(tier))
    org_key = org_id or ""
    reject_reason = "gate_timeout"

    _ORG_SHARE.arrive(pool, org_key)
    try:
        if _ORG_SHARE.over_share(pool, org_key, cost, gate.limit if cap is None else cap):
            admitted = False
            reject_reason = "over_share"
        else:
            admitted = await acquire_gate_with_backpressure(
                gate, cost, tier, message_id,
                logger=logger,
                log_prefix="parsing",
                queue_wait_warn_seconds=PARSE_QUEUE_WAIT_WARN_SECONDS,
                gate_timeout_seconds=PARSE_GATE_TIMEOUT_SECONDS,
                cap=cap,
            )
    except BaseException:
        _ORG_SHARE.leave(pool, org_key)
        raise
    if not admitted:
        _ORG_SHARE.leave(pool, org_key)
        _count_rejection(tier, reject_reason)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS)},
            content={
                "success": False,
                "error": {
                    "code": ParseErrorCode.PARSE_BACKPRESSURE.value,
                    "message": "Parsing service is at capacity; retry later.",
                    "details": {"tier": tier.value, "limit": gate.limit if cap is None else cap},
                },
            },
        )
    _ORG_SHARE.hold(pool, org_key, cost)

    if cap is None:
        # Braked here: a Docling service sharing this memory need not brake it again.
        stamp_admitted_here()

    parse_start = time.monotonic()
    excess_held = 0
    thread_work = ThreadWork()
    covers = set_permit_covers() if tier is ParseTier.HEAVY else None
    try:
        # The permit's base covers the parent's fixed part for the PdfPlumber parse only; a kind
        # above it reserves the rest here, and never waits for it for long while it holds the permit.
        # A light parse holds no heavy permit, but text still grows the parent with its size.
        working_set = governor.heavy_working_set
        base_gb = working_set.gb if tier is ParseTier.HEAVY else INDEX_LIGHT_WORKING_SET_GB
        excess = permit_excess_bytes(
            classify_kind(extension, mime_type), base_gb, working_set.source, len(content)
        )
        if excess:
            if not await governor.memory_budget.reserve(excess, EXCESS_WAIT_SECONDS):
                logger.warning("Parse deferred: record='%s' waited too long for memory", record_name)
                _count_rejection(tier, "memory_excess")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS)},
                    content={
                        "success": False,
                        "error": {
                            "code": ParseErrorCode.PARSE_BACKPRESSURE.value,
                            "message": "Parsing service is at capacity; retry later.",
                            "details": {"tier": tier.value, "reason": "memory"},
                        },
                    },
                )
            excess_held = excess
        try:
            parser = registry.resolve(mime_type, extension, provider_enum)
            async with scope_from_claims(
                claims,
                Workload.PARSING,
                fallback_service="parsing",
                config_service=_config_service(request),
            ):
                raise_if_password_protected_office(
                    content, extension=extension, mime_type=mime_type
                )
                # The paid calls this parse makes (page OCR, table summaries) go into the revision's manifest.
                # The deadline is the caller's own record budget (the figure it waits for); an older caller sends none
                # and the service's own settings apply. A budget that differs from this service's is logged.
                stage_limit = lock_limits.stage_deadline_for(record_budget_s)
                _warn_if_budget_differs(record_budget_s, extension, mime_type)
                paid_run = start_run(
                    content_revision(content), "parse", refresh=refresh_paid_outputs, stage_deadline_s=stage_limit,
                )
                # Bounded server-side: a client that gave up does not stop a parse, and its paid calls must not
                # outlive what the delayed re-purge after a delete covers. The bound is the longest record budget
                # configured (never shorter than the client's own read timeout for the record), and only this
                # timeout is the stage deadline: a TimeoutError raised inside the parser is the parser's.
                stage_timer = asyncio.timeout(stage_limit)
                try:
                    with track(thread_work):
                        async with stage_timer:
                            result = await parser.parse(content, record_name, config)
                except BaseException as exc:
                    # A parse that failed keeps its open manifest, brought up to date: what it paid for stays protected.
                    await abandon_run(paid_run, _config_service(request), logger)
                    if isinstance(exc, TimeoutError) and stage_timer.expired():
                        raise _StageDeadlineExceeded(stage_limit) from exc
                    raise
                await complete_run(paid_run, _config_service(request), logger)
        except PasswordProtectedError as exc:
            logger.warning(
                "Parse refused: record='%s' provider=%s code=%s",
                record_name, provider_enum.value, ParseErrorCode.PASSWORD_REQUIRED.value,
            )
            # A fact about the file, like every ParseError: 422, never retried.
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "success": False,
                    "error": ParseError(
                        ParseErrorCode.PASSWORD_REQUIRED, exc.message, exc.details
                    ).to_dict(),
                },
            )
        except MultimodalModelNotConfiguredError as exc:
            logger.warning(
                "Parse refused: record='%s' provider=%s: %s",
                record_name, provider_enum.value, exc.message,
            )
            # A fact about the org's AI settings, not an outage: as a 500 the
            # indexer retried it as transient and then told the person of a
            # "temporary problem". The message is the reason the record stores.
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "success": False,
                    "error": ParseError(
                        ParseErrorCode.PARSE_FAILED, SCANNED_DOCUMENT_NEEDS_OCR, exc.details
                    ).to_dict(),
                },
            )
        except ParseError as exc:
            parse_ms = (time.monotonic() - parse_start) * 1000
            logger.warning(
                "Parse failed: record='%s' provider=%s parse_ms=%.0f code=%s message=%s",
                record_name, provider_enum.value, parse_ms, exc.code.value, exc.message,
            )
            # 422 for every ParseError, not just the unsupported/invalid
            # ones: a document this service could not parse is a fact about
            # the document, and the client reads the error code from the
            # body either way. Reported as a 500 it looked like an outage --
            # the client retried it, counted it against the parsing circuit
            # breaker, and five unparseable files in a row then failed every
            # other record fast for the breaker's cooldown and sent them to
            # the back of the queue with a retry attempt spent.
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"success": False, "error": exc.to_dict()},
            )
        except LaneWaitTimeout:
            # A local conversion found no CPU lane in time: retryable, like a full gate.
            logger.warning("Parse deferred: record='%s' waited too long for a CPU lane", record_name)
            _count_rejection(tier, "lane_timeout")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS)},
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.PARSE_BACKPRESSURE.value,
                        "message": "Parsing service is at capacity; retry later.",
                        "details": {"tier": tier.value, "reason": "cpu_lane"},
                    },
                },
            )
        except _StageDeadlineExceeded as exc:
            logger.warning(
                "Parse stopped: record='%s' provider=%s ran longer than %s s",
                record_name, provider_enum.value, exc.limit_s,
            )
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.PARSE_FAILED.value,
                        "message": "Parsing took longer than the service allows",
                        "details": {"requestId": message_id},
                    },
                },
            )
        except Exception:  # noqa: BLE001
            parse_ms = (time.monotonic() - parse_start) * 1000
            logger.exception(
                "Unexpected error parsing '%s' (provider=%s, parse_ms=%.0f)",
                record_name, provider_enum.value, parse_ms,
            )
            # The exception and its type stay in the log above: the text can quote the
            # document or a path, and the caller only needs to correlate.
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.PARSE_FAILED.value,
                        "message": "Unexpected error while parsing the document",
                        "details": {"requestId": message_id},
                    },
                },
            )
    finally:
        reset_permit_covers(covers)

        def release_admission() -> None:
            if excess_held:
                governor.memory_budget.release(excess_held)
            gate.release(cost)
            _ORG_SHARE.leave(pool, org_key, cost)
            SemaphoreLogger.log_semaphore_release(
                f"parsing:{tier.value}", message_id, gate.limit - gate.in_use, gate.limit
            )

        # Whatever way the parse ended (deadline, a timeout raised inside it, a cancel, any other error), a conversion
        # still running in a thread keeps its memory: the permit and the reservation are held until it returns.
        if thread_work.running():
            _release_when_work_ends(release_admission, thread_work, time.monotonic(), record_name)
        else:
            release_admission()

    parse_ms = (time.monotonic() - parse_start) * 1000
    if parse_ms >= SLOW_PARSE_WARN_SECONDS * 1000:
        logger.warning(
            "Slow parse: record='%s' provider=%s parse_ms=%.0f",
            record_name, provider_enum.value, parse_ms,
        )
    blocks_count = len(result.block_container.blocks) if result.block_container is not None else 0
    logger.debug(
        "Parse completed: record='%s' outcome=success provider_used=%s parse_ms=%.0f blocks=%d raw_document=%s",
        record_name,
        result.provider_used.value if result.provider_used is not None else "default",
        parse_ms,
        blocks_count,
        result.raw_document is not None,
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "block_container": (
                json.loads(result.block_container.model_dump_json())
                if result.block_container is not None
                else None
            ),
            "raw_document": result.raw_document,
            "provider_used": (
                result.provider_used.value if result.provider_used is not None else None
            ),
            "metadata": result.metadata,
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/parse/providers
# ---------------------------------------------------------------------------


@router.get("/providers", summary="List available providers per format")
async def list_providers(request: Request) -> JSONResponse:
    """Return a dict mapping format keys to their available provider names."""
    registry = _get_registry(request)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=registry.list_all_formats(),
    )
