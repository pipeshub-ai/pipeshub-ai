"""Answer an enumeration query without asking a model to count.

`try_answer_enumeration` is a no-op for every query that is not a census
question, so the normal agent path is untouched. When it does fire it computes
the answer, registers the records so the citations resolve, and hands the result
to the ordinary `AnswerFinalizer` — the same call the agent path makes. Nothing
about event emission, citation normalisation or persistence is duplicated here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.modules.agents.enumeration.answer import RecordLookup, build_enumeration_answer
from app.modules.agents.enumeration.policy import is_enumeration_query
from app.modules.agents.knowledge_scope import NO_KB_SELECTED_FILTER
from app.modules.demo_data.chat import excluded_app_ids
from app.modules.retrieval.selection_scope import allowed_filters, has_selection
from app.services.graph_db.interface.graph_db_provider import requested_scope_ids

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.event_sink import EventSink
    from app.modules.retrieval.retrieval_service import RetrievalService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

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
    retrieval_service: RetrievalService,
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
    except Exception as exc:
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
    graph_provider: IGraphDBProvider,
    org_id: str,
    summaries: dict[str, str] | None = None,
) -> RecordLookup:
    """Resolve a record id to the fields an enumeration answer needs.

    Reads the record node directly rather than going through retrieval: this
    path is a census over the record set, so it must not depend on a document
    having matched a query. Records that cannot be read are left out of the
    listing by the caller, because a row nobody can cite is the failure this
    module exists to remove -- but they stay in the total, which is the size of
    the permission-filtered record set, and the answer says how many were left
    out.
    """
    from app.config.constants.arangodb import CollectionNames

    async def lookup(vrid: str, record_id: str) -> dict[str, Any] | None:
        try:
            doc = await graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
        except Exception as exc:  # one bad record must not fail the count
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


def _bound_the_read_would_miss(filters: dict[str, Any] | None) -> bool:
    """Whether a server-set bound is not already reflected in what will be read.

    Every saved-agent turn carries these bounds, and most of them only say
    which apps the agent may read — which the read is already scoped to, so
    the count is right and the census can answer. Treating their mere presence
    as a reason to decline turned the census off for every saved agent, which
    is the custom-agent case it exists for.

    What the read will cover is `requested_scope_ids`, which is `apps` and `kb`
    together: a Collection id is honoured under either key, so comparing the
    bound against `apps` alone gets it wrong twice over. A Collection agent
    sends its Collection under `kb` with `apps` empty, and would be declined
    although its bound matches exactly; and a Collection named under `kb` but
    outside the bound would be counted, telling the reader about documents the
    agent is not allowed to see. `NO_KB_SELECTED` is left out because it names
    no app — it is how an agent says it has no Collection, not a Collection the
    bound has to admit.

    Two bounds are not reflected and must decline: one that lists nothing at
    all, which admits nothing rather than everything; and one that admits
    fewer apps than the read is about to count, the unscoped read included,
    since the read would then count outside it.

    Record groups and records on a bound are not a third. They widen a bound,
    they never narrow one of its apps: `SelectionScope.admits`
    (`selection_scope.py:144`) takes any record whose `connectorId` is one of
    the bound's apps, whatever else the bound names. And a source that really
    is limited to some nodes is not in `apps` at all —
    `resolve_agent_filters` puts it in one place or the other
    (`knowledge_scope.py:198-206`) and copies the limit onto the turn, so
    `has_selection` declines before this is asked. What is left over is reach
    outside what was asked for, which the read does not count and the answer
    does not claim.
    """
    asked = {
        app
        for app in (requested_scope_ids(filters) or ())
        if app != NO_KB_SELECTED_FILTER
    }
    for bound in allowed_filters(filters):
        bound_apps = {app for app in (bound.get("apps") or []) if app}
        if not bound_apps or not asked or not asked <= bound_apps:
            return True
    return False


async def try_answer_enumeration(
    *,
    query: str,
    context: AgentContext,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    event_sink: EventSink,
    log: logging.Logger = logger,
) -> bool:
    """Return True when this query was answered here and the agent should not run."""
    marker = getattr(context, "tool_state", {}).get("corpus_census_marker")
    if not is_enumeration_query(marker, query):
        return False
    # The read below scopes by apps and kb only, so a folder or item selection
    # would be miscounted and the agent answers instead.
    if has_selection(filters) or _bound_the_read_would_miss(filters):
        return False

    org_id = getattr(context, "org_id", "") or ""
    user_id = getattr(context, "user_id", "") or ""
    if not org_id or not user_id or graph_provider is None:
        return False

    # Without raise_on_error a failed read, or one source that failed, comes
    # back as {} or a partial map, which would be answered as "no documents"
    # or as a complete count. Switched-off demo records are not the user's.
    try:
        accessible = await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id, filters=filters or {},
            raise_on_error=True,
            exclude_app_ids=excluded_app_ids(getattr(context, "tool_state", None)),
        )
    except Exception as exc:  # fall back to the agent rather than fail
        log.warning("enumeration: accessible-record lookup failed, deferring: %s", exc)
        return False

    # The answer is built from the ids in this map and cites the records they
    # resolve to, so check its shape rather than trust the provider's. Anything
    # else defers to the agent, the same as a failed lookup.
    if not isinstance(accessible, dict) or not all(
        isinstance(vrid, str) and vrid and isinstance(record_id, str) and record_id
        for vrid, record_id in accessible.items()
    ):
        if accessible is not None:
            log.warning(
                "enumeration: accessible-record lookup returned an unexpected shape, deferring"
            )
        return False

    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.agents.agent_loop.respond import AnswerFinalizer
    from app.utils.chat_helpers import CitationRefMapper

    state = context.tool_state
    ref_mapper = state.get("citation_ref_mapper") or CitationRefMapper()

    from app.modules.agents.enumeration.answer import MAX_LISTED
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


async def answer_census_if_asked(
    *,
    query: str,
    context: AgentContext,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    event_sink: EventSink,
    log: logging.Logger = logger,
) -> bool:
    """`try_answer_enumeration` for a chat bridge, which must not break on it.

    Call it after `PipesHubAgentFactory.create()`: that is where the intent call
    stores the CORPUS_CENSUS marker, and before it the pattern fallback is the
    only gate, so a model's "no" could not decline a pattern match.

    A failure before finalisation falls back to the agent, since nothing has
    been sent yet. One after it is re-raised, because an answer may already
    have reached the client and the agent would send a second.
    """
    try:
        return await try_answer_enumeration(
            query=query, context=context, retrieval_service=retrieval_service,
            graph_provider=graph_provider, filters=filters, event_sink=event_sink, log=log,
        )
    except EnumerationFinalizationError:
        raise
    except Exception as exc:
        # Never let this path break chat.
        log.warning("enumeration path failed, falling back to agent: %s", exc, exc_info=True)
        return False
