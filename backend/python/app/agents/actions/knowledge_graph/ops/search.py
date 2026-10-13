"""Knowledge graph search operation.

Extracted from Retrieval.search_internal_knowledge so it can be called by
knowledgegraph__search while the old retrieval__search_internal_knowledge
shim (kept during transition) delegates here too.

The parameter rename from ``connector_ids`` → ``source_ids`` is the only
LLM-visible change; the resolution logic is identical.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
    merge_filter_groups,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    resolve_entity_virtual_ids,
)
from app.agents.actions.knowledge_graph.ops.results import (
    compose_result_tail,
    dedupe_append_final_results,
    tool_output,
)
from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope, _clean_kb
from app.agents.agent_loop.protocol.run_usage import grep_generation_usage_reporter
from app.modules.agents.qna.chat_state import remember_shown_results
from app.modules.retrieval.context.builder import KnowledgeContextBuilder
from app.modules.retrieval.context.ordering import relevance_ordered_context
from app.modules.retrieval.context.renderer import KnowledgeRenderer, Selection
from app.modules.retrieval.context.units import unit_children
from app.modules.retrieval.entity_permissions import (
    SEARCH_SCOPE_MAX_ENTITIES,
    EntityAccessError,
)
from app.modules.retrieval.provenance import note_retrieved_by
from app.modules.retrieval.result_merging import (
    CollectionResults,
    ReciprocalRankFusionMerger,
    search_hit_identity,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    get_record_id_shortener_if_enabled,
    is_base64_image,
    merge_into_records_map,
)
from app.utils.image_admission import admission_from_state
from app.utils.pattern_match import (
    await_pattern_match,
    cancel_task_if_running,
    merge_pattern_match_results,
    render_pattern_match_hint,
    run_pattern_match_with_llm_grep,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.agents.record_escalation.models import FetchPlan

logger = logging.getLogger(__name__)

_MAX_RETRIEVAL_SOURCES_DIVISOR = 5
# 422: the request's entity filter was refused; its message tells how to narrow it.
_RETRIEVAL_ERROR_STATUS_CODES = frozenset({202, 422, 500, 503})

# Returned when a search limited with source_ids finds nothing.
NARROWED_SEARCH_EMPTY_MESSAGE = (
    "No results in the source(s) you named. Before concluding this does not "
    "exist, search again with source_ids omitted: a source's name rarely says "
    "everything it holds. Skip that only if the user asked to search just "
    "these sources."
)

UNKNOWN_ENTITY_IDS_MESSAGE = (
    "None of the entity_ids {ids} came from knowledgegraph__search_entities in this "
    "turn, so nothing was searched. Entity ids only work in the turn that returned "
    "them, and record ids cannot scope a search. Call knowledgegraph__search_entities "
    "again, or search without entity_ids."
)

# A record-group/subcategory scope that stopped at a cap or scan budget.
ENTITY_SCOPE_INCOMPLETE_MESSAGE = (
    "No accessible records turned up among the records checked for the requested "
    "entities, but the check stopped before covering all of them, so this is not "
    "conclusive. Search without entity_ids, or page through the entity with "
    "knowledgegraph__find_records_by_entity."
)

def _fuse_sources(per_source: list[CollectionResults]) -> list[dict[str, Any]]:
    """One ranked list from the per-source searches of a fan-out.

    Each source's scores are rank-fused positions within that search, so they
    do not compare across sources, and appending the lists puts every hit of
    the first source ahead of the next. Fusing the ranks interleaves the
    sources by position, and the fused score replaces each hit's so that
    later ranking by score or by order follows it.
    """
    fused = ReciprocalRankFusionMerger(identity=search_hit_identity).fuse(per_source)
    for hit, score in fused:
        hit["score"] = score
    return [hit for hit, _ in fused]


# The text around the records (fetch plan, header) follows which records are
# shown, so the records are cut again, at most this many times in all, when a
# cut changed it.
_MAX_FIT_PASSES = 3


def _ranked_header(blocks: int, records: int, omitted: int, *, by_relevance: bool) -> str:
    order = "most relevant record first; " if by_relevance else ""
    header = (
        f"Top {blocks} block{'s' if blocks != 1 else ''} "
        f"from {records} record{'s' if records != 1 else ''} "
        f"({order}ranked sample — other records may match)."
    )
    if omitted:
        header += (
            f" {omitted} lower-ranked record{'s' if omitted != 1 else ''} "
            "left out to fit the result size."
        )
    return header


@dataclass(frozen=True)
class _ResultFrame:
    """Everything in a search result around the records, for one selection."""

    summary: str
    candidate_suffix: str
    """The fetch candidates, with full record ids (shortened when used)."""
    tail_chars: int
    shown_records: dict[str, Any]
    final_results: list[dict[str, Any]]
    coverage: dict[str, tuple[int, int]]
    plan: FetchPlan

    @property
    def chars(self) -> int:
        return len(self.summary) + self.tail_chars


def _result_frame(
    state: "ChatState",
    selection: Selection,
    records: dict[str, Any],
    pm_shown: list[dict[str, Any]],
    *,
    entity_notes: str,
    pm_hint: str,
    by_relevance: bool,
) -> _ResultFrame:
    """The summary, fetch plan and tail a result showing ``selection`` gets.

    Reads the request state and changes nothing in it, so it can be sized for
    more than one selection.
    """
    from app.modules.agents.record_escalation import (
        analyze_coverage,
        build_candidates,
        render_candidate_table,
        render_coverage_note,
    )

    shown_vrids = {unit.get("virtual_record_id") for unit in selection.ranked}
    shown_vrids.update(entry.get("virtual_record_id") for entry in pm_shown)
    shown_records = {vrid: record for vrid, record in records.items() if vrid in shown_vrids}

    # Coverage over everything accumulated this request, not just this call,
    # so parallel or repeated searches do not each report partial counts.
    final_results = dedupe_append_final_results(state.get("final_results", []), selection.ranked)
    live = state.get("virtual_record_id_to_result")
    full_map = dict(live) if isinstance(live, dict) else {}
    merge_into_records_map(full_map, shown_records)
    coverage = analyze_coverage(final_results, full_map)
    seen_rids: set[str] = set()
    records_in_order: list[dict[str, Any]] = []
    for entry in final_results:
        record = full_map.get(entry.get("virtual_record_id") or "")
        rid = record.get("id") if isinstance(record, dict) else None
        if rid and rid not in seen_rids:
            seen_rids.add(rid)
            records_in_order.append(record)
    plan = build_candidates(
        coverage=coverage,
        records_in_relevance_order=records_in_order,
        already_fetched_ids=set(state.get("full_records_fetched", set())),
    )

    candidate_suffix = coverage_note = ""
    if plan.has_candidates:
        needs_whole_doc = bool(state.get("needs_whole_document", False))
        candidate_suffix = render_candidate_table(plan, needs_whole_document=needs_whole_doc)
        # At the TOP of the result: the table lands at the bottom, possibly
        # thousands of tokens later, where the model may already be answering.
        coverage_note = render_coverage_note(plan, needs_whole_document=needs_whole_doc)
    shown_candidates = candidate_suffix
    if candidate_suffix and selection.record_id_shortener is not None:
        shown_candidates = selection.record_id_shortener.copy().shorten_record_ids_in_text(candidate_suffix)

    if selection.ranked:
        header = _ranked_header(
            len(selection.ranked),
            len({unit.get("virtual_record_id") for unit in selection.ranked}),
            selection.omitted_records,
            by_relevance=by_relevance,
        )
        summary = f"{header}\n\n{entity_notes}{coverage_note}"
    else:
        n_pm = len(pm_shown)
        summary = (
            f"No content blocks from semantic search, but {n_pm} "
            f"record{'s' if n_pm != 1 else ''} found via keyword matching. "
            "Review the record names below and fetch the most relevant "
            "one(s) directly.\n\n"
            f"{entity_notes}"
        )
    tail_chars = len(compose_result_tail(shown_records, shown_candidates)) + len(pm_hint)
    return _ResultFrame(
        summary=summary,
        candidate_suffix=candidate_suffix,
        tail_chars=tail_chars,
        shown_records=shown_records,
        final_results=final_results,
        coverage=coverage,
        plan=plan,
    )


def _note_provenance(
    state: "ChatState",
    semantic_units: list[dict[str, Any]],
    shown: dict[str, Any],
    raw_pattern_records: list[dict[str, Any]],
) -> None:
    """Note which layer surfaced each record this search shows (opt-in trace only).

    A record counts as found by the vector search only when its semantic units
    were shown: one whose hits were cut, listed through the keyword hint, was
    shown by grep alone.
    """
    semantic_vrids = {unit.get("virtual_record_id") for unit in semantic_units}
    grep_vrids = {record.get("virtual_record_id") for record in raw_pattern_records}
    shown_refs = [
        (
            vrid,
            record.get("id") or record.get("_key"),
            record.get("record_name") or record.get("recordName"),
        )
        for vrid, record in shown.items() if isinstance(record, dict)
    ]
    note_retrieved_by(state, "vector", [ref for ref in shown_refs if ref[0] in semantic_vrids])
    note_retrieved_by(state, "pattern_match", [ref for ref in shown_refs if ref[0] in grep_vrids])


def resolve_entity_filter_groups(
    state: "ChatState",
    entity_ids: list[str] | None,
) -> dict[str, list[str]]:
    """Turn department/category/topic/language ``entity_ids`` returned by
    ``search_entities`` in this request into a ``filter_groups``-shaped dict. Graph filters match entity
    names, never ids, so each id is resolved through the cache
    ``search_entities`` populated; ids with no cache entry are dropped.
    """
    filters: dict[str, list[str]] = {}
    if not entity_ids:
        return filters
    id_to_key: dict[str, tuple[str, str]] = state.get(ENTITY_ID_FILTER_KEY_CACHE_KEY) or {}
    for entity_id in entity_ids:
        cached = id_to_key.get(entity_id)
        if not cached:
            continue
        filter_key, entity_name = cached
        bucket = filters.setdefault(filter_key, [])
        if entity_name not in bucket:
            bucket.append(entity_name)
    return filters


def resolve_record_scoped_entities(
    state: "ChatState", entity_ids: list[str] | None,
) -> list[tuple[str, str]]:
    """``(entity_id, entity_type)`` for the ``entity_ids`` that are record
    groups or subcategories returned by ``search_entities`` in this request.
    They have no name-based graph filter, so they become a permission-checked
    ``virtualRecordId`` allow-list (``resolve_entity_virtual_ids``) passed to
    the retrieval service as ``virtual_record_ids_from_tool``.
    """
    if not entity_ids:
        return []
    known: dict[str, str] = state.get(RECORD_SCOPED_ENTITY_CACHE_KEY) or {}
    return [(entity_id, known[entity_id]) for entity_id in entity_ids if entity_id in known]


def unresolved_entity_ids(state: "ChatState", entity_ids: list[str] | None) -> list[str]:
    """``entity_ids`` that can scope nothing: never returned by
    ``search_entities`` in this request, or record ids, which have no scope."""
    name_filtered: dict[str, Any] = state.get(ENTITY_ID_FILTER_KEY_CACHE_KEY) or {}
    record_scoped: dict[str, str] = state.get(RECORD_SCOPED_ENTITY_CACHE_KEY) or {}
    return [
        entity_id
        for entity_id in dict.fromkeys(entity_ids or [])
        if entity_id and entity_id not in name_filtered and entity_id not in record_scoped
    ]


def _entity_notes(
    *,
    unknown_entity_ids: list[str],
    filter_dropped: bool,
    record_scope_applied: bool,
    scope_truncated: bool,
    entities_skipped: int = 0,
) -> str:
    notes: list[str] = []
    if unknown_entity_ids:
        notes.append(
            f"Note: entity_ids {unknown_entity_ids} were not returned by "
            "search_entities in this turn and were ignored; the other entity_ids "
            "still scope these results."
        )
    if filter_dropped:
        scope_left = (
            " The record group/subcategory scope still applies." if record_scope_applied else ""
        )
        notes.append(
            "Note: nothing matched inside the requested department/category/topic/"
            f"language, so these results are NOT limited to it.{scope_left}"
        )
    if scope_truncated:
        notes.append(
            "Note: the requested record group/subcategory has more records than one "
            "search can cover, so only part of its accessible records were searched."
        )
    if entities_skipped:
        notes.append(
            f"Note: only the first {SEARCH_SCOPE_MAX_ENTITIES} record group/subcategory "
            f"entity_ids scoped this search; {entities_skipped} more record group/subcategory "
            "entity_ids were not applied. Search them separately. Department, category, "
            "topic and language entity_ids are not limited."
        )
    return "".join(f"{note}\n\n" for note in notes)


def normalize_source_ids(value: Any) -> list[str] | None:
    """Normalize source_ids parameter (a string or list of strings)."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else None
    if isinstance(value, list):
        filtered = [str(v).strip() for v in value if v]
        return filtered if filtered else None
    return None


async def execute_search(
    state: "ChatState",
    query: str | None,
    source_ids: list[str] | None = None,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
    entity_ids: list[str] | None = None,
) -> str | list[Part]:
    """Run semantic search over the agent's knowledge scope.

    ``source_ids`` accepts both app-connector IDs and KB-collection IDs;
    resolution against the agent's configured scope is done here.

    ``created_after``/``created_before``/``modified_after``/``modified_before``
    are optional ISO 8601 date bounds (see ``ops/time_range.parse_time_range``)
    that narrow results to records whose source creation/last-modified
    timestamp falls in the given window. Applied as a hard pre-filter at the
    graph permission-scoping step, before vector search ever runs.

    ``entity_ids`` narrows results to records connected to specific
    departments/categories/topics/languages, or to the accessible records of a
    record group or subcategory. IDs must come from a ``search_entities`` call
    in this request: if none is recognised the call fails without searching,
    and unrecognised ones next to recognised ones are named in a note. A record
    group or subcategory that resolves to zero accessible records reports "no
    results", and a failed permission lookup reports an error — neither
    silently widens to an unscoped search.

    Returns a plain-text string suitable for LLM consumption (same format as
    the legacy retrieval tool).
    """
    if not query:
        return json.dumps({
            "status": "error",
            "message": "No search query provided",
        })

    if not state:
        return json.dumps({
            "status": "error",
            "message": "Tool state not initialized",
        })

    from app.agents.actions.knowledge_graph.ops.time_range import parse_time_range

    time_range, time_error = parse_time_range(
        created_after=created_after,
        created_before=created_before,
        modified_after=modified_after,
        modified_before=modified_before,
    )
    if time_error is not None:
        return time_error

    # Declared outside the try so the finally can always cancel it.
    pattern_match_task: asyncio.Task[list[dict[str, Any]]] | None = None
    try:
        logger_instance = state.get("logger", logger)
        logger_instance.info("knowledgegraph__search: query_chars=%d", len(query))

        retrieval_service = state.get("retrieval_service")
        graph_provider = state.get("graph_provider")
        config_service = state.get("config_service")

        if not retrieval_service or not graph_provider:
            return json.dumps({"status": "error", "message": "Retrieval services not available"})

        org_id = state.get("org_id", "")
        user_id = state.get("user_id", "")
        is_placeholder_agent = bool(state.get("is_placeholder_agent", False))

        source_ids_norm = normalize_source_ids(source_ids)

        agent_filters = state.get("filters", {}) or {}
        if is_placeholder_agent:
            raw_apps: list[str] = list(state.get("apps") or [])
            raw_kbs: list[str] = list(state.get("kb") or [])
        else:
            raw_apps = list(agent_filters.get("apps") or [])
            raw_kbs = list(agent_filters.get("kb") or [])

        base_scope = KnowledgeScope(
            app_ids=tuple(raw_apps),
            kb_ids=_clean_kb(raw_kbs),
        )

        explicit_ids = bool(source_ids_norm)
        narrowed_scope: KnowledgeScope | None = None
        if explicit_ids:
            candidate = base_scope.narrow_to(source_ids_norm)
            if candidate is not base_scope:
                narrowed_scope = candidate
        resolved_scope = narrowed_scope if narrowed_scope is not None else base_scope

        total_sources = len(base_scope.app_ids) + len(base_scope.kb_ids)
        adjusted_limit = 50 if total_sources <= 1 else (
            100 // min(total_sources, _MAX_RETRIEVAL_SOURCES_DIVISOR)
        )

        filter_groups = resolved_scope.to_filter_groups()
        resolved_apps = list(narrowed_scope.app_ids) if narrowed_scope else []
        resolved_kbs = list(narrowed_scope.kb_ids) if narrowed_scope else []

        requested_entity_ids = [e for e in dict.fromkeys(entity_ids or []) if e]
        unknown_entity_ids = unresolved_entity_ids(state, requested_entity_ids)
        if requested_entity_ids and len(unknown_entity_ids) == len(requested_entity_ids):
            # Searching anyway would return unscoped results the model would
            # present as the entity's.
            return json.dumps({
                "status": "error",
                "message": UNKNOWN_ENTITY_IDS_MESSAGE.format(ids=unknown_entity_ids),
            })

        entity_filter_groups = resolve_entity_filter_groups(state, entity_ids)

        record_scoped_entities = resolve_record_scoped_entities(state, entity_ids)
        virtual_record_ids_from_tool: list[str] | None = None
        entity_scope_truncated = False
        entities_skipped = 0
        if record_scoped_entities:
            try:
                entity_scope = await resolve_entity_virtual_ids(
                    state, record_scoped_entities,
                )
            except EntityAccessError:
                logger_instance.warning(
                    "knowledgegraph__search: entity scoping failed", exc_info=True,
                )
                return json.dumps({
                    "status": "error",
                    "message": "Could not scope the search to the requested entities — try again.",
                })
            virtual_record_ids_from_tool = entity_scope.virtual_ids
            entity_scope_truncated = entity_scope.truncated
            entities_skipped = entity_scope.entities_skipped
            if not virtual_record_ids_from_tool:
                # Every requested entity resolved to zero accessible
                # records — report that plainly rather than silently
                # falling through to an unscoped, org-wide search.
                message = (
                    ENTITY_SCOPE_INCOMPLETE_MESSAGE
                    if entity_scope_truncated or entities_skipped
                    else "No accessible records found for the requested entities."
                )
                skipped_note = _entity_notes(
                    unknown_entity_ids=[], filter_dropped=False, record_scope_applied=False,
                    scope_truncated=False, entities_skipped=entities_skipped,
                ).strip()
                return json.dumps({
                    "status": "success",
                    "message": f"{message}\n\n{skipped_note}" if skipped_note else message,
                    "results": [],
                    "result_count": 0,
                })

        def _empty_result() -> str:
            if failed_sources:
                # Nothing found where the search ran, but some sources were never searched.
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"{failed_sources} of the sources you named could not be searched "
                        "and the rest returned nothing, so this does not show the information is missing. "
                        "Try again, or search with source_ids omitted."
                    ),
                    "results": [],
                    "result_count": 0,
                })
            message = "No results found"
            # The model picks sources by name, and a name rarely says what a
            # source holds, so an empty narrowed search says little about
            # whether the answer exists. source_ids stays a hard filter; the
            # model is told to look everywhere before concluding, as with dates.
            if narrowed_scope is not None and filter_groups != base_scope.to_filter_groups():
                message = NARROWED_SEARCH_EMPTY_MESSAGE
            notes = _entity_notes(
                unknown_entity_ids=unknown_entity_ids,
                filter_dropped=False,
                record_scope_applied=bool(virtual_record_ids_from_tool),
                scope_truncated=entity_scope_truncated,
                entities_skipped=entities_skipped,
            )
            if notes:
                message = f"{message}\n\n{notes.strip()}"
            return json.dumps({
                "status": "success",
                "message": message,
                "results": [],
                "result_count": 0,
            })

        # Retrieval treats this key as a control flag, not a filter: with it, an
        # empty scope searches nothing instead of everything the user can reach.
        strict_scope = bool(agent_filters.get(STRICT_SCOPE_FILTER_KEY))
        grep_filters: dict[str, Any] = (
            {**filter_groups, STRICT_SCOPE_FILTER_KEY: True} if strict_scope else filter_groups
        )
        # The user's entity filter scopes every content search of the turn. It is
        # not the tool's own entity filter below, and the retry without that one
        # keeps it.
        request_entity_filters = agent_filters.get("entityFilters")
        # Grep knows nothing of entity scope, so an entity-scoped search would
        # be widened by its hits; it runs only for unscoped-by-entity searches.
        entity_scoped = bool(entity_filter_groups or record_scoped_entities or request_entity_filters)
        if config_service is not None and not entity_scoped:
            pattern_match_task = asyncio.create_task(
                run_pattern_match_with_llm_grep(
                    query=query,
                    config_service=config_service,
                    org_id=org_id,
                    user_id=user_id,
                    graph_provider=graph_provider,
                    filters=grep_filters,
                    logger_instance=logger_instance,
                    llm=state.get("llm"),
                    user_query=state.get("query"),
                    on_llm_usage=grep_generation_usage_reporter(state),
                )
            )

        is_service_account = bool(state.get("is_service_account", False))
        fan_out_sources = explicit_ids and (len(resolved_apps) > 1 or len(resolved_kbs) > 1)
        per_source_fan_out = fan_out_sources
        failed_sources = 0

        async def _search_one(
            fg: dict[str, list[str]],
            entity_fg: dict[str, list[str]],
            vrids: list[str] | None,
        ) -> dict[str, Any] | None:
            filter_groups_for_call: dict[str, Any] = merge_filter_groups(fg, entity_fg)
            if strict_scope:
                filter_groups_for_call[STRICT_SCOPE_FILTER_KEY] = True
            if request_entity_filters:
                filter_groups_for_call["entityFilters"] = request_entity_filters
            return await retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups_for_call,
                time_range=time_range,
                virtual_record_ids_from_tool=vrids,
            )

        async def _attempt(
            entity_fg: dict[str, list[str]],
            vrids: list[str] | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
            """Run one full search attempt (fan-out or single) with *entity_fg*
            as the entity filter and *vrids* (record_group scoping, if any) to
            apply. Returns ``(search_results, virtual_to_record_map,
            error_json)`` — *error_json* is set only for a genuine service
            error; an empty-but-successful result returns ``([], {}, None)``
            so the caller can decide whether to retry. *failed_sources* is
            reset per attempt, so it always describes the attempt whose
            results the caller keeps.
            """
            nonlocal failed_sources
            if fan_out_sources:
                failed_sources = 0
                tasks: list[Any] = []
                for app_id in resolved_apps:
                    tasks.append(_search_one(
                        resolved_scope.to_filter_groups_for_source(
                            app_id=app_id, placeholder_agent=is_placeholder_agent,
                        ),
                        entity_fg,
                        vrids,
                    ))
                for kb_id in resolved_kbs:
                    tasks.append(_search_one(
                        resolved_scope.to_filter_groups_for_source(
                            kb_id=kb_id, placeholder_agent=is_placeholder_agent,
                        ),
                        entity_fg,
                        vrids,
                    ))

                raw_results = await asyncio.gather(*tasks, return_exceptions=True)
                per_source: list[CollectionResults] = []
                attempt_map: dict[str, Any] = {}
                any_success = False
                error_status: int | None = None
                error_message = "Retrieval service unavailable"

                for raw in raw_results:
                    if isinstance(raw, Exception):
                        logger_instance.warning("Per-source search failed: %s", raw, exc_info=raw)
                        failed_sources += 1
                        continue
                    if raw is None:
                        failed_sources += 1
                        continue
                    status_code = raw.get("status_code", 200)
                    if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                        failed_sources += 1
                        error_status = error_status or status_code
                        error_message = raw.get("message", error_message)
                        continue
                    any_success = True
                    per_source.append(CollectionResults(
                        collection_name=f"source-{len(per_source)}",
                        results=raw.get("searchResults", []),
                    ))
                    attempt_map.update(raw.get("virtual_to_record_map", {}))

                if not any_success:
                    if error_status is not None:
                        return [], {}, json.dumps({
                            "status": "error",
                            "status_code": error_status,
                            "message": error_message,
                        })
                    # Every source raised or returned nothing: nothing was searched.
                    return [], {}, json.dumps({"status": "error", "message": error_message})
                return _fuse_sources(per_source), attempt_map, None

            results = await _search_one(filter_groups, entity_fg, vrids)
            if results is None:
                return [], {}, json.dumps({"status": "error", "message": "Retrieval service returned no results"})
            status_code = results.get("status_code", 200)
            if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                return [], {}, json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable"),
                })
            return results.get("searchResults", []), results.get("virtual_to_record_map", {}), None

        entity_filter_dropped = False
        search_results, virtual_to_record_map, error_json = await _attempt(
            entity_filter_groups, virtual_record_ids_from_tool,
        )
        if error_json is not None:
            await cancel_task_if_running(pattern_match_task)
            return error_json

        # Entity filters are a hard AND constraint at the graph layer — if no
        # accessible record has a belongsTo* edge to the entity (e.g. an
        # extraction/linking gap), the candidate set is empty even though
        # content-matching documents exist. Retry once without them, and say
        # so in the result so the model does not treat it as entity-scoped.
        if not search_results and entity_filter_groups:
            logger_instance.info(
                "knowledgegraph__search: entity-filtered search returned zero "
                "results (query_chars=%d, filter_groups=%d) — retrying without entity filters",
                len(query), len(entity_filter_groups),
            )
            # Only the name-based filter is dropped — record-scoped entities
            # are already permission-checked record membership, so an empty
            # one must still report "no results" instead of broadening.
            fallback_results, fallback_map, fallback_error = await _attempt(
                {}, virtual_record_ids_from_tool,
            )
            if fallback_error is not None:
                await cancel_task_if_running(pattern_match_task)
                return fallback_error
            if fallback_results:
                search_results, virtual_to_record_map = fallback_results, fallback_map
                entity_filter_dropped = True

        raw_pattern_records: list[dict[str, Any]] = []
        if pattern_match_task is not None:
            raw_pattern_records = await await_pattern_match(
                pattern_match_task, logger_instance, run_timer=state.get("_run_timer"),
            )
            if raw_pattern_records:
                logger_instance.info(
                    "Pattern match: %d raw record(s)", len(raw_pattern_records),
                )

        if not search_results and not raw_pattern_records:
            return _empty_result()

        blob_store = BlobStorage(
            logger=logger_instance,
            config_service=config_service,
            graph_provider=graph_provider,
        )
        is_multimodal_llm = bool(state.get("is_multimodal_llm", False))

        knowledge = await KnowledgeContextBuilder(
            blob_store=blob_store,
            graph_provider=graph_provider,
            org_id=org_id,
            user_id=user_id,
            config_service=config_service,
        ).build(
            search_results,
            virtual_to_record_map,
            is_multimodal_llm=is_multimodal_llm,
            # A fan-out already gave each source its own limit.
            max_units=None if per_source_fan_out else adjusted_limit,
        )
        virtual_record_id_to_result = knowledge.virtual_record_id_to_result

        pm_record_entries: list[dict[str, Any]] = []
        if raw_pattern_records:
            try:
                pm_record_entries = await merge_pattern_match_results(
                    raw_records=raw_pattern_records,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    user_id=user_id,
                    org_id=org_id,
                    blob_store=blob_store,
                    graph_provider=graph_provider,
                    is_multimodal_llm=is_multimodal_llm,
                    logger_instance=logger_instance,
                    time_range=time_range,
                    filters=grep_filters,
                    config_service=config_service,
                )
                if pm_record_entries:
                    logger_instance.info(
                        "Pattern match: %d record(s) found via grep", len(pm_record_entries),
                    )
            except Exception as exc:
                logger_instance.warning(
                    "Pattern match merge failed, continuing with semantic results only: %s", exc,
                )

        # Grep hits can all be dropped by the merge (permissions, time range);
        # that is still an empty search and must answer like one.
        if not knowledge.ranked and not pm_record_entries:
            return _empty_result()

        has_semantic_blocks = bool(knowledge.ranked)
        try:
            pm_hint = render_pattern_match_hint(
                pm_record_entries, virtual_record_id_to_result,
                has_semantic_blocks=has_semantic_blocks,
            )
        except Exception as exc:
            # The semantic answer above is complete without the hint.
            logger_instance.warning("Pattern match hint failed, omitting it: %s", exc)
            pm_hint = ""
        pm_shown = pm_record_entries if pm_hint else []
        if not has_semantic_blocks and not pm_shown:
            return _empty_result()
        entity_notes = _entity_notes(
            unknown_entity_ids=unknown_entity_ids,
            filter_dropped=entity_filter_dropped,
            record_scope_applied=bool(virtual_record_ids_from_tool),
            scope_truncated=entity_scope_truncated,
            entities_skipped=entities_skipped,
        )

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`) — see `RecordIdShortener`
        # in `utils/chat_helpers.py`. Same shared shortener as
        # navigate/lookup_record/list_files, keyed off tool_state so whichever
        # tool the model calls first mints it.
        record_id_shortener = get_record_id_shortener_if_enabled(state)
        ref_mapper = state.get("citation_ref_mapper")
        if not isinstance(ref_mapper, CitationRefMapper):
            ref_mapper = CitationRefMapper()
        image_budget = state.get("image_budget")
        if not isinstance(image_budget, ImageBudget):
            image_budget = state["image_budget"] = ImageBudget()
        image_admission = admission_from_state(state)
        if is_multimodal_llm:
            # Downscales are decided off the event loop here, so the sizing
            # renders below and the real render only hit the cache.
            await image_admission.warm([
                content
                for unit in knowledge.ranked
                for item in (unit, *unit_children(unit))
                if is_base64_image(content := item.get("content"))
            ])
        by_relevance = relevance_ordered_context()
        renderer = KnowledgeRenderer(
            virtual_record_id_to_result,
            ref_mapper=ref_mapper,
            is_multimodal_llm=is_multimodal_llm,
            by_relevance=by_relevance,
            record_id_shortener=record_id_shortener,
            image_budget=image_budget,
            image_admission=image_admission,
        )

        def frame(selection: Selection) -> _ResultFrame:
            return _result_frame(
                state,
                selection,
                virtual_record_id_to_result,
                pm_shown,
                entity_notes=entity_notes,
                pm_hint=pm_hint,
                by_relevance=by_relevance,
            )

        # Everything else in the result is sized first, so the records are cut
        # here, least relevant first, rather than mid-record by the generic
        # tool-result cap. No await from here to the render: the selection is
        # measured against the shared ref mapper and image budget as they are now.
        selection = Selection(ranked=knowledge.ranked)
        result_frame = frame(selection)
        for _ in range(_MAX_FIT_PASSES if knowledge.ranked else 0):
            selection = renderer.select(
                knowledge.ranked, DEFAULT_MAX_RESULT_CHARS - result_frame.chars,
            )
            result_frame = frame(selection)
            if (selection.chars or 0) <= DEFAULT_MAX_RESULT_CHARS - result_frame.chars:
                break
        else:
            if knowledge.ranked:
                logger_instance.warning(
                    "knowledgegraph__search: result still over %d chars after %d fits; "
                    "the tool-result cap will cut it",
                    DEFAULT_MAX_RESULT_CHARS, _MAX_FIT_PASSES,
                )
        rendered = renderer.render(selection)
        state["citation_ref_mapper"] = ref_mapper
        candidate_suffix = result_frame.candidate_suffix
        if candidate_suffix and record_id_shortener is not None:
            candidate_suffix = record_id_shortener.shorten_record_ids_in_text(candidate_suffix)

        # Only what was shown is accumulated: the citation pipeline, the fetch
        # plan and the retrieval trace all read these.
        final_results = rendered.ranked
        shown_records = result_frame.shown_records
        state["final_results"] = result_frame.final_results
        # Updated in place: a fetch running in the same turn writes into this
        # same dict, and replacing it would drop what that fetch added.
        live_virtual_map = state.get("virtual_record_id_to_result")
        if not isinstance(live_virtual_map, dict):
            live_virtual_map = state["virtual_record_id_to_result"] = {}
        merge_into_records_map(live_virtual_map, shown_records)
        existing_tool_records = state.get("tool_records")
        if not isinstance(existing_tool_records, list):
            existing_tool_records = []
        existing_ids = {r.get("_id") for r in existing_tool_records if isinstance(r, dict)}
        state["tool_records"] = existing_tool_records + [
            r for r in shown_records.values()
            if not (isinstance(r, dict) and r.get("_id") in existing_ids)
        ]
        state["fetch_coverage"] = result_frame.coverage
        state["fetch_plan"] = result_frame.plan

        _note_provenance(state, final_results, shown_records, raw_pattern_records)
        remember_shown_results(state, [*final_results, *pm_shown], shown_records)
        text = (
            result_frame.summary
            + rendered.text
            + compose_result_tail(shown_records, candidate_suffix)
            + pm_hint
        )
        return tool_output(text, rendered.images, state)

    except Exception as exc:
        logger_instance = state.get("logger", logger) if state else logger
        logger_instance.error("knowledgegraph__search error: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Search error: {exc}"})
    finally:
        # No-op once awaited; stops the grep + LLM call when semantic search fails.
        await cancel_task_if_running(pattern_match_task)
