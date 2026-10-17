"""Read part of a record, or a whole Slack thread, within a character budget.

The connector service's `GET /api/v1/records/{record_id}/content` returns a
record whole, which a long document or a pull request with its diffs makes
larger than any caller's tool-output limit. This route serves the same record
through the assistant's budgeted reader, which needs the search index to rank
a record's blocks against a question -- and the index lives in this service.
The API gateway sends a record-content request here when it asks for part of a
record (`query`, `startBlock`, `maxChars`) or a thread (`mode=thread`), and to
the connector service otherwise, so existing callers see no change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.modules.demo_data.access import is_hidden_demo_record
from app.utils.fetch_full_record import _fetch_multiple_records_impl
from app.utils.fetch_slack_thread import (
    _fetch_thread_records_impl,
    _resolve_thread_record_group,
)
from app.utils.not_indexed import not_indexed_note
from app.utils.record_reader import (
    DEFAULT_READ_CHARS,
    MAX_READ_CHARS,
    MIN_READ_CHARS,
    read_record,
    read_thread,
)

if TYPE_CHECKING:
    from app.containers.query import QueryAppContainer

router = APIRouter()

# A question longer than this is not a question about one record.
_MAX_QUERY_CHARS = 2_000


async def _check_access(request: Request, graph_provider: Any, record_id: str) -> None:  # noqa: ANN401
    """The same gate as the connector service's content route: no access is a
    403, a switched-off demo record a 404."""
    container: QueryAppContainer = request.app.container
    logger = container.logger()
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    try:
        if not await graph_provider.check_record_access_with_details(
            user_id=user_id, org_id=org_id, record_id=record_id,
        ):
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="You do not have permission to access this record",
            )
        doc = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value)
        try:
            hidden = await is_hidden_demo_record(
                graph_provider, container.config_service(), org_id, user_id,
                (doc or {}).get("connectorId"),
            )
        except Exception as exc:
            logger.warning("demo data setting unreadable for user=%s: %s", user_id, exc)
            hidden = False
        if hidden:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error checking record access for %s: %s", record_id, exc)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to check record access",
        ) from exc


@router.get(
    "/records/{record_id}/read",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def read_record_content(
    record_id: str,
    request: Request,
    mode: Literal["record", "thread"] = Query(
        "record",
        description="`record` reads this record; `thread` reads the whole Slack thread it belongs to.",
    ),
    query: str | None = Query(
        None, max_length=_MAX_QUERY_CHARS,
        description=(
            "What the caller is looking for. A record too large for `max_chars` then shows "
            "the blocks most relevant to it first. Ignored when `start_block` is given."
        ),
    ),
    start_block: int | None = Query(
        None, ge=0,
        description=(
            "Read in order from this block (the `nextStartBlock` of a previous read). "
            "In thread mode it applies to the record at `start_record`."
        ),
    ),
    start_record: int = Query(
        0, ge=0, description="Thread mode: start at this record (the `nextStartRecord` of a previous read).",
    ),
    max_chars: int = Query(
        DEFAULT_READ_CHARS, ge=MIN_READ_CHARS, le=MAX_READ_CHARS,
        description="Most characters of content to return.",
    ),
) -> dict[str, Any]:
    container: QueryAppContainer = request.app.container
    logger = container.logger()
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    graph_provider = await container.graph_provider()

    await _check_access(request, graph_provider, record_id)
    blob_store = await container.blob_store()

    if mode == "thread":
        # Told apart before reading, so "not a thread" is the caller's 422
        # rather than a failure of ours -- and a graph read that failed is
        # ours, a 500, not a claim that the record has no thread.
        try:
            thread_group = await _resolve_thread_record_group(
                record_id, graph_provider, raise_on_error=True,
            )
        except Exception as exc:
            logger.error("Error resolving the thread of %s: %s", record_id, exc, exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to read the thread",
            ) from exc
        if not thread_group:
            raise HTTPException(
                status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                detail=(
                    "This record is not part of a Slack thread. Pass a thread reply, "
                    "or a channel message that has replies."
                ),
            )
        try:
            result = await _fetch_thread_records_impl(
                record_id=record_id,
                virtual_record_id_to_result={},
                graph_provider=graph_provider,
                blob_store=blob_store,
                org_id=org_id,
                config_service=container.config_service(),
                user_id=user_id,
            )
        except Exception as exc:
            logger.error("Error reading the thread of %s: %s", record_id, exc, exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to read the thread",
            ) from exc
        if not result.get("ok"):
            logger.error("Thread read failed for %s: %s", record_id, result.get("error"))
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to read the thread",
            )
        response = await read_thread(
            result.get("records") or [],
            max_chars=max_chars,
            start_record=start_record,
            start_block=start_block or 0,
        )
        # Replies the caller may see but whose content could not be loaded:
        # named, so a gap in the conversation is not read as silence.
        response["unreadableRecordIds"] = result.get("skipped_record_ids") or []
        return response

    try:
        result = await _fetch_multiple_records_impl(
            record_ids=[record_id],
            virtual_record_id_to_result={},
            graph_provider=graph_provider,
            blob_store=blob_store,
            org_id=org_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.error("Error fetching record content for %s: %s", record_id, exc, exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to fetch record content",
        ) from exc

    if not result.get("ok") or not result.get("records"):
        # Past the access check, so a record without indexed content is
        # described rather than reported as missing.
        unindexed = (result.get("not_indexed_records") or {}).get(record_id)
        content = (
            f"{unindexed['context']}\n\n{not_indexed_note(unindexed['status'])}"
            if unindexed else "No record found"
        )
        return {"content": content, "mode": "record", "recordId": record_id, "complete": True,
                "nextStartBlock": None, "chars": len(content), "maxChars": max_chars}

    retrieval_service = None
    if query and query.strip() and start_block is None:
        retrieval_service = await container.retrieval_service()
    try:
        return await read_record(
            result["records"][0],
            max_chars=max_chars,
            start_block=start_block,
            query=query,
            retrieval_service=retrieval_service,
            user_id=user_id,
            org_id=org_id,
        )
    except Exception as exc:
        logger.error("Error formatting record content for %s: %s", record_id, exc, exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to format record content",
        ) from exc
