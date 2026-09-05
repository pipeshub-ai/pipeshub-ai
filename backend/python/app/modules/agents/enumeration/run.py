"""Answer an enumeration query without asking a model to count.

`try_answer_enumeration` is a no-op for every query that is not a census
question, so the normal agent path is untouched. When it does fire it computes
the answer, registers the records so the citations resolve, and hands the result
to the ordinary `AnswerFinalizer` — the same call the agent path makes. Nothing
about event emission, citation normalisation or persistence is duplicated here.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.enumeration.answer import build_enumeration_answer
from app.modules.agents.enumeration.policy import is_enumeration_query

logger = logging.getLogger(__name__)


class EnumerationFinalizationError(RuntimeError):
    """Raised when the census fails *after* finalisation has begun.

    Everything before that point can safely fall back to the agent: nothing has
    been sent to the client and no state has been kept. Once the finaliser
    starts it may already have emitted an answer, so running the agent as well
    would show the reader two answers to one question. This exception tells the
    caller to surface a failure instead of retrying.
    """


async def _fetch_summaries(
    retrieval_service: Any,
    org_id: str,
    virtual_ids: list[str],
) -> dict[str, str]:
    """Read the stored summaries for the records about to be listed.

    Summaries are written at index time as their own block, flagged
    `isRecordSummary` and keyed by virtual record id
    (`transformers/vectorstore.py`); nothing on the query side had ever read
    them back. Scoped to the ids being listed rather than the whole
    organisation, so a large corpus does not pull every summary into memory to
    join a couple of hundred.

    Returns an empty map on any failure — a listing without summaries is still
    correct and still cited, so this must never fail the answer.
    """
    summaries: dict[str, str] = {}
    if not virtual_ids:
        return summaries
    vector_db = getattr(retrieval_service, "vector_db_service", None)
    if vector_db is None:
        logger.warning("enumeration: no vector_db_service on %s", type(retrieval_service).__name__)
        return summaries
    try:
        from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME

        payload_filter = await vector_db.filter_collection(must={
            "isRecordSummary": True,
            "orgId": org_id,
            "virtualRecordId": virtual_ids,
        })
        result = await vector_db.scroll(
            collection_name=VECTOR_DB_COLLECTION_NAME,
            scroll_filter=payload_filter,
            limit=max(len(virtual_ids) * 2, 10),
        )
        points = getattr(result, "points", None) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("enumeration: summary scroll failed, listing without them: %s", exc)
        return summaries

    for point in points:
        # A malformed point must not abandon the whole census: this loop is
        # outside the try above, so an unguarded .get() on a non-dict payload
        # would propagate and drop the answer back to the agent.
        payload = getattr(point, "payload", None)
        if not isinstance(payload, dict):
            continue
        meta = payload.get("metadata")
        if not isinstance(meta, dict):
            continue
        vrid = meta.get("virtualRecordId")
        text = payload.get("page_content")
        if vrid and isinstance(text, str) and text.strip():
            summaries[vrid] = text.strip()
    return summaries


async def _record_lookup_factory(
    graph_provider: Any,
    org_id: str,
    summaries: dict[str, str] | None = None,
) -> Any:
    """Resolve a record id to the fields an enumeration answer needs.

    Reads the record node directly rather than going through retrieval: this
    path is a census over the record set, so it must not depend on a document
    having matched a query. Records that cannot be read are skipped by the
    caller rather than counted, because a row nobody can cite is the failure
    this module exists to remove.
    """
    from app.config.constants.arangodb import CollectionNames

    async def lookup(vrid: str, record_id: str) -> dict[str, Any] | None:
        try:
            doc = await graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
        except Exception as exc:  # noqa: BLE001 - one bad record must not fail the count
            logger.debug("enumeration: record fetch failed for %s: %s", record_id, exc)
            return None
        if not isinstance(doc, dict) or not doc:
            return None
        name = doc.get("recordName") or doc.get("record_name") or record_id
        return {
            "id": doc.get("_key") or doc.get("id") or record_id,
            "record_name": name,
            "record_type": doc.get("recordType", ""),
            "virtual_record_id": vrid,
            "org_id": org_id,
            "webUrl": doc.get("webUrl") or f"/record/{record_id}",
            "mime_type": doc.get("mimeType") or "text/plain",
            "origin": doc.get("origin") or "UPLOAD",
            "connector_name": doc.get("connectorName") or "KB",
            "summary": (summaries or {}).get(vrid) or None,
        }

    return lookup


async def try_answer_enumeration(
    *,
    query: str,
    context: Any,
    retrieval_service: Any,
    graph_provider: Any,
    filters: dict[str, Any] | None,
    event_sink: Any,
    log: Any = logger,
) -> bool:
    """Return True when this query was answered here and the agent should not run."""
    marker = getattr(context, "tool_state", {}).get("corpus_census_marker")
    if not is_enumeration_query(marker, query):
        return False

    org_id = getattr(context, "org_id", "") or ""
    user_id = getattr(context, "user_id", "") or ""
    if not org_id or not user_id or graph_provider is None:
        return False

    try:
        accessible = await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id, filters=filters or {},
        )
    except Exception as exc:  # noqa: BLE001 - fall back to the agent rather than fail
        log.warning("enumeration: accessible-record lookup failed, deferring: %s", exc)
        return False

    if accessible is None:
        return False

    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.agents.agent_loop.respond import AnswerFinalizer
    from app.utils.chat_helpers import CitationRefMapper

    state = context.tool_state
    ref_mapper = state.get("citation_ref_mapper") or CitationRefMapper()

    from app.modules.agents.enumeration.answer import MAX_LISTED  # noqa: PLC0415
    listed_ids = [vrid for vrid, _ in sorted(accessible.items())[:MAX_LISTED]]
    summaries = await _fetch_summaries(retrieval_service, org_id, listed_ids)
    lookup = await _record_lookup_factory(graph_provider, org_id, summaries)
    # A filter narrows what "all documents" means, and the answer has to say so.
    narrowed = bool(
        (filters or {}).get("apps") or (filters or {}).get("kb")
    )
    result = await build_enumeration_answer(
        accessible=accessible, record_lookup=lookup, ref_mapper=ref_mapper,
        org_id=org_id, scoped=narrowed,
    )

    # A citation whose record never reaches these maps is discarded during
    # finalisation, so registration is not optional (utils/citations.py).
    #
    # Snapshot first. If finalisation fails after this point the caller falls
    # through to the agent, and an agent turn that inherited half-registered
    # census records would cite documents its own answer never mentioned.
    previous = {
        key: state.get(key)
        for key in ("final_results", "virtual_record_id_to_result", "tool_records")
    }
    state["final_results"] = [*state.get("final_results", []), *result.final_results]
    state["virtual_record_id_to_result"] = {
        **state.get("virtual_record_id_to_result", {}),
        **result.virtual_record_id_to_result,
    }
    state["tool_records"] = [*state.get("tool_records", []), *result.tool_records]
    state["citation_ref_mapper"] = ref_mapper

    # Deliberately no query text: a question can carry names, deal values or
    # anything else a person typed, and logs outlive the conversation.
    log.info(
        "enumeration: answered %d record(s), %d listed, without an agent turn",
        result.total, result.listed,
    )
    finalizer = AnswerFinalizer(context, CitationCollector(context))
    try:
        await finalizer.run(
            agent_success=True, agent_error=None, agent_output=result.text,
            event_sink=event_sink, streamed_answer="", reasoning_turns=[],
        )
    except Exception as exc:
        # Roll back so nothing downstream inherits citations for records it
        # never mentioned, then mark this as past the point of no return.
        for key, value in previous.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        raise EnumerationFinalizationError(
            "census finalisation failed after emitting"
        ) from exc

    return True
