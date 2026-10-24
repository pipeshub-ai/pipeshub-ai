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

from app.api.connection_limit import refuse_outside_connection
from app.api.middlewares.auth import connection_limit, require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.modules.demo_data.access import is_hidden_demo_record
from app.modules.retrieval.selection_scope import within_connection
from app.utils.chat_helpers import accessible_node_ids
from app.utils.fetch_full_record import _fetch_multiple_records_impl
from app.utils.fetch_slack_thread import (
    _fetch_thread_records_impl,
    _resolve_slack_channel_record,
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


async def _check_access(request: Request, graph_provider: Any, record_id: str) -> set[str]:  # noqa: ANN401
    """The same gate as the connector service's content route: no access is a
    403, a switched-off demo record a 404. Returns the accessible ids, for the
    fetch to reuse."""
    container: QueryAppContainer = request.app.container
    logger = container.logger()
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    try:
        accessible = await accessible_node_ids(graph_provider, {record_id}, user_id, org_id)
        if record_id not in accessible:
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
        await refuse_outside_connection(request, graph_provider, org_id, user_id, record_id)
        return accessible
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
        description=(
            "`record` reads this record; `thread` reads the whole Slack thread it belongs to. "
            "A Slack record in no single thread (several channel messages, or a message "
            "without replies) reads as itself, then the threads its messages started."
        ),
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

    accessible = await _check_access(request, graph_provider, record_id)
    blob_store = await container.blob_store()

    if mode == "thread":
        # Told apart before reading, so "not from Slack" is the caller's 422
        # rather than a failure of ours -- and a graph read that failed is
        # ours, a 500, not a claim that the record has no thread. Any Slack
        # record reads: one in no single thread comes back as itself, followed
        # by the threads its messages started.
        try:
            thread_group = await _resolve_thread_record_group(
                record_id, graph_provider, raise_on_error=True,
            )
            channel_record = None if thread_group else await _resolve_slack_channel_record(
                record_id, graph_provider, raise_on_error=True,
            )
        except Exception as exc:
            logger.error("Error resolving the thread of %s: %s", record_id, exc, exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to read the thread",
            ) from exc
        if not thread_group and not channel_record:
            raise HTTPException(
                status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                detail=(
                    "This record is not part of a Slack thread: thread mode reads Slack "
                    "records only. Read this record without thread mode."
                ),
            )
        limit = connection_limit(request.state.user)
        try:
            result = await _fetch_thread_records_impl(
                record_id=record_id,
                virtual_record_id_to_result={},
                graph_provider=graph_provider,
                blob_store=blob_store,
                org_id=org_id,
                config_service=container.config_service(),
                user_id=user_id,
                # Bounds the replies, and so the note, thread summaries and skipped ids.
                scope_state=None if limit is None else {"filters": within_connection({}, limit)},
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
        records = result.get("records") or []
        # A record in no single thread says what it holds: which of its
        # messages started threads, or that none has replies. Said once, at
        # the start, inside the caller's budget.
        note = result.get("note")
        first_page = start_record == 0 and not start_block
        lead = f"[{note}]\n\n" if note and first_page else ""
        response = await read_thread(
            records,
            max_chars=max_chars - len(lead),
            start_record=start_record,
            start_block=start_block or 0,
        )
        if lead:
            response["content"] = lead + response["content"]
            response["chars"] = len(response["content"])
        response["maxChars"] = max_chars
        if "threads" in result:
            response["threads"] = [
                {
                    "threadRecordGroupId": t.get("thread_record_group_id"),
                    "recordCount": t.get("record_count"),
                }
                for t in result.get("threads") or []
            ]
            response["threadsNotRead"] = result.get("threads_not_read") or 0
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
            accessible=accessible,
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
