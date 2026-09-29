"""Knowledge search: the one implementation behind ``knowledgegraph__search``
and the legacy ``retrieval__search_internal_knowledge`` tool.
"""
from __future__ import annotations

import asyncio
import json
import logging
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
from app.agents.actions.knowledge_graph.ops.repeat_hits import (
    observe_search,
    repeat_hit_note,
)
from app.agents.actions.knowledge_graph.ops.results import (
    compose_result_tail,
    dedupe_append_final_results,
    tool_output,
)
from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope, _clean_kb
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.context.builder import KnowledgeContextBuilder
from app.modules.retrieval.context.manifest import manifest_registry
from app.modules.retrieval.context.renderer import render_knowledge
from app.modules.retrieval.context.reranking import ranker_for
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.result_merging import (
    CollectionResults,
    ReciprocalRankFusionMerger,
    search_hit_identity,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    get_record_id_shortener_if_enabled,
)
from app.utils.image_admission import admission_from_state
from app.utils.pattern_match import (
    cancel_task_if_running,
    merge_pattern_match_results,
    pattern_match_record_ids,
    render_pattern_match_hint,
    run_pattern_match_with_llm_grep,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_MAX_RETRIEVAL_SOURCES_DIVISOR = 5
# Room left under the tool-result cap for the header, candidate table and
# keyword-match hint, so records are dropped whole, lowest-ranked first,
# before the generic cap would cut the result in the middle.
_MAX_RECORDS_CHARS = DEFAULT_MAX_RESULT_CHARS - 12_000
_RETRIEVAL_ERROR_STATUS_CODES = frozenset({202, 500, 503})

# Returned when a search limited with source_ids finds nothing.
NARROWED_SEARCH_EMPTY_MESSAGE = (
    "No results in the source(s) you named. Before concluding this does not "
    "exist, search again with source_ids omitted: a source's name rarely says "
    "everything it holds. Skip that only if the user asked to search just "
    "these sources."
)


def _fuse_sources(per_source: list[CollectionResults]) -> list[dict[str, Any]]:
    """One ranked list from the per-source searches of a fan-out.

    Each source's scores are rank-fused positions within that search, so
    sorting them together would favour whichever search scored higher.
    Fusing the ranks interleaves the sources by position instead, and the
    fused score replaces each hit's so that ranking downstream follows it.
    """
    fused = ReciprocalRankFusionMerger(identity=search_hit_identity).fuse(per_source)
    for hit, score in fused:
        hit["score"] = score
    return [hit for hit, _ in fused]


def _ranked_header(blocks: int, records: int, omitted: int) -> str:
    header = (
        f"Top {blocks} block{'s' if blocks != 1 else ''} from {records} "
        f"record{'s' if records != 1 else ''}, most relevant record first "
        "(a ranked sample — other records may match)."
    )
    if omitted:
        header += (
            f" {omitted} lower-ranked block{'s' if omitted != 1 else ''} "
            "left out to fit the result size."
        )
    return header


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
    in this request; unknown IDs are dropped. A record group or subcategory
    that resolves to zero accessible records reports "no results", and a
    failed permission lookup reports an error — neither silently widens to an
    unscoped search.

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

    try:
        logger_instance = state.get("logger", logger)
        logger_instance.info("knowledgegraph__search: query=%r", query[:100])

        retrieval_service = state.get("retrieval_service")
        graph_provider = state.get("graph_provider")
        config_service = state.get("config_service")

        disable_semantic = bool(state.get("disable_semantic", False))
        disable_pattern_match = bool(state.get("disable_pattern_match", False))

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

        pattern_match_task: asyncio.Task[list[dict[str, Any]]] | None = None
        if config_service is not None and not disable_pattern_match:
            pattern_match_task = asyncio.create_task(
                run_pattern_match_with_llm_grep(
                    query=query,
                    config_service=config_service,
                    org_id=org_id,
                    user_id=user_id,
                    graph_provider=graph_provider,
                    filters=filter_groups,
                    logger_instance=logger_instance,
                    llm=state.get("llm"),
                    user_query=state.get("query"),
                )
            )

        entity_filter_groups = resolve_entity_filter_groups(state, entity_ids)

        record_scoped_entities = resolve_record_scoped_entities(state, entity_ids)
        virtual_record_ids_from_tool: list[str] | None = None
        if record_scoped_entities:
            try:
                virtual_record_ids_from_tool = await resolve_entity_virtual_ids(
                    state, record_scoped_entities,
                )
            except EntityAccessError:
                logger_instance.warning(
                    "knowledgegraph__search: entity scoping failed", exc_info=True,
                )
                await cancel_task_if_running(pattern_match_task)
                return json.dumps({
                    "status": "error",
                    "message": "Could not scope the search to the requested entities — try again.",
                })
            if not virtual_record_ids_from_tool:
                # Every requested entity resolved to zero accessible
                # records — report that plainly rather than silently
                # falling through to an unscoped, org-wide search.
                await cancel_task_if_running(pattern_match_task)
                return json.dumps({
                    "status": "success",
                    "message": "No accessible records found for the requested entities.",
                    "results": [],
                    "result_count": 0,
                })

        is_service_account = bool(state.get("is_service_account", False))
        fan_out_sources = explicit_ids and (len(resolved_apps) > 1 or len(resolved_kbs) > 1)
        per_source_fan_out = fan_out_sources

        async def _search_one(
            fg: dict[str, list[str]],
            entity_fg: dict[str, list[str]],
            vrids: list[str] | None,
        ) -> dict[str, Any] | None:
            return await retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=merge_filter_groups(fg, entity_fg),
                time_range=time_range,
                virtual_record_ids_from_tool=vrids,
            )

        async def _attempt(
            entity_fg: dict[str, list[str]],
            vrids: list[str] | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any], str | None, int]:
            """Run one full search attempt (fan-out or single) with *entity_fg*
            as the entity filter and *vrids* (record_group scoping, if any) to
            apply. Returns ``(search_results, virtual_to_record_map,
            error_json, failed_sources)`` — *error_json* is set only for a
            genuine service error; an empty-but-successful result returns
            ``([], {}, None, n)`` so the caller can decide whether to retry.
            """
            if fan_out_sources:
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
                failed = 0
                error_status: int | None = None
                error_message = "Retrieval service unavailable"

                for raw in raw_results:
                    if isinstance(raw, Exception):
                        logger_instance.warning("Per-source search failed: %s", raw, exc_info=raw)
                        failed += 1
                        continue
                    if raw is None:
                        failed += 1
                        continue
                    status_code = raw.get("status_code", 200)
                    if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                        failed += 1
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
                        }), failed
                    # Every source raised or returned nothing: nothing was searched.
                    return [], {}, json.dumps({"status": "error", "message": error_message}), failed
                return _fuse_sources(per_source), attempt_map, None, failed

            results = await _search_one(filter_groups, entity_fg, vrids)
            if results is None:
                return [], {}, json.dumps({"status": "error", "message": "Retrieval service returned no results"}), 0
            status_code = results.get("status_code", 200)
            if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                return [], {}, json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable"),
                }), 0
            return results.get("searchResults", []), results.get("virtual_to_record_map", {}), None, 0

        failed_sources = 0
        if disable_semantic:
            logger_instance.info("Semantic search disabled via flag")
            search_results: list[dict[str, Any]] = []
            virtual_to_record_map: dict[str, Any] = {}
            error_json = None
            per_source_fan_out = False
        else:
            search_results, virtual_to_record_map, error_json, failed_sources = await _attempt(
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
        entity_filter_dropped = False
        if not search_results and entity_filter_groups and not disable_semantic:
            logger_instance.info(
                "knowledgegraph__search: entity-filtered search returned zero "
                "results for query=%r filters=%r — retrying without entity filters",
                query[:100], entity_filter_groups,
            )
            # Only the name-based filter is dropped — record-scoped entities
            # are already permission-checked record membership, so an empty
            # one must still report "no results" instead of broadening.
            fallback_results, fallback_map, fallback_error, fallback_failed = await _attempt(
                {}, virtual_record_ids_from_tool,
            )
            if fallback_error is not None:
                await cancel_task_if_running(pattern_match_task)
                return fallback_error
            if fallback_results:
                search_results, virtual_to_record_map = fallback_results, fallback_map
                failed_sources = fallback_failed
                entity_filter_dropped = True

        raw_pattern_records: list[dict[str, Any]] = []
        if pattern_match_task is not None:
            try:
                raw_pattern_records = await pattern_match_task
            except Exception as exc:
                logger_instance.warning(
                    "Pattern match failed, continuing with semantic results only: %s", exc,
                )
                raw_pattern_records = []
            if raw_pattern_records:
                logger_instance.info(
                    "Pattern match: %d raw record(s)", len(raw_pattern_records),
                )

        if not search_results and not raw_pattern_records and failed_sources:
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

        if not search_results and not raw_pattern_records:
            message = "No results found"
            # The model picks sources by name, and a name rarely says what a
            # source holds, so an empty narrowed search says little about
            # whether the answer exists. source_ids stays a hard filter; the
            # model is told to look everywhere before concluding, as with dates.
            if narrowed_scope is not None and filter_groups != base_scope.to_filter_groups():
                message = NARROWED_SEARCH_EMPTY_MESSAGE
            return json.dumps({
                "status": "success",
                "message": message,
                "results": [],
                "result_count": 0,
            })

        blob_store = BlobStorage(
            logger=logger_instance,
            config_service=config_service,
            graph_provider=graph_provider,
        )
        is_multimodal_llm = bool(state.get("is_multimodal_llm", False))
        reranker_resolver = state.get("reranker_resolver")
        reranker = await reranker_resolver.active() if reranker_resolver else None

        knowledge = await KnowledgeContextBuilder(
            blob_store=blob_store,
            graph_provider=graph_provider,
            org_id=org_id,
            user_id=user_id,
            config_service=config_service,
            ranker=ranker_for(reranker),
        ).build(
            search_results,
            virtual_to_record_map,
            query=query,
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
                )
                if pm_record_entries:
                    logger_instance.info(
                        "Pattern match: %d record(s) found via grep", len(pm_record_entries),
                    )
                    # Their Record IDs are shown for fetching, but they add no
                    # stored record to the map, which is what normally grants
                    # the fetch tool.
                    remember_record_ids(state, pattern_match_record_ids(pm_record_entries))
            except Exception as exc:
                logger_instance.warning(
                    "Pattern match merge failed, continuing with semantic results only: %s", exc,
                )

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`) — see `RecordIdShortener`
        # in `utils/chat_helpers.py`. Same shared shortener as
        # navigate/lookup_record/list_files, keyed off tool_state so whichever
        # tool the model calls first mints it.
        record_id_shortener = get_record_id_shortener_if_enabled(state)
        ref_mapper = state.get("citation_ref_mapper") or CitationRefMapper()
        rendered = render_knowledge(
            knowledge.units,
            virtual_record_id_to_result,
            ref_mapper=ref_mapper,
            is_multimodal_llm=is_multimodal_llm,
            record_id_shortener=record_id_shortener,
            image_budget=state.setdefault("image_budget", ImageBudget()),
            image_admission=admission_from_state(state),
            max_chars=_MAX_RECORDS_CHARS,
        )
        state["citation_ref_mapper"] = ref_mapper
        final_results = rendered.units
        surfaced_record_ids = observe_search(state, final_results, virtual_record_id_to_result)

        # Accumulate into state for citation pipeline
        state["final_results"] = dedupe_append_final_results(
            state.get("final_results", []), final_results,
        )
        # Updated in place: a fetch running in the same turn writes into this
        # same dict, and replacing it would drop what that fetch added.
        live_virtual_map = state.get("virtual_record_id_to_result")
        if not isinstance(live_virtual_map, dict):
            live_virtual_map = state["virtual_record_id_to_result"] = {}
        live_virtual_map.update(virtual_record_id_to_result)
        existing_tool_records = state.get("tool_records")
        if not isinstance(existing_tool_records, list):
            existing_tool_records = []
        new_tool_records = list(virtual_record_id_to_result.values())
        existing_ids = {r.get("_id") for r in existing_tool_records if isinstance(r, dict)}
        state["tool_records"] = existing_tool_records + [
            r for r in new_tool_records
            if not (isinstance(r, dict) and r.get("_id") in existing_ids)
        ]

        # Record escalation candidates
        candidate_suffix = ""
        coverage_note = ""
        needs_whole_doc = bool(state.get("needs_whole_document", False))
        from app.modules.agents.record_escalation import (
            analyze_coverage,
            build_candidates,
            render_candidate_table,
            render_coverage_note,
        )
        all_final = state.get("final_results", [])
        full_vr_map = state.get("virtual_record_id_to_result", {})
        coverage = analyze_coverage(all_final, full_vr_map)
        already_fetched: set[str] = set(state.get("full_records_fetched", set()))
        seen_rids: set[str] = set()
        records_in_order: list[dict] = []
        for entry in all_final:
            vrid = entry.get("virtual_record_id")
            if not vrid:
                continue
            rec = full_vr_map.get(vrid)
            if not rec:
                continue
            rid = rec.get("id")
            if rid and rid not in seen_rids:
                seen_rids.add(rid)
                records_in_order.append(rec)
        plan = build_candidates(
            coverage=coverage,
            records_in_relevance_order=records_in_order,
            already_fetched_ids=already_fetched,
        )
        state["fetch_coverage"] = coverage
        state["fetch_plan"] = plan
        if plan.has_candidates:
            candidate_suffix = render_candidate_table(plan, needs_whole_document=needs_whole_doc)
            # Placed at the TOP of the result (see `summary` below) — the
            # full table above lands at the bottom, potentially thousands of
            # tokens away on a long result, where the model may already be
            # composing an answer from the blocks by the time it reaches it.
            coverage_note = render_coverage_note(plan, needs_whole_document=needs_whole_doc)
        if candidate_suffix and record_id_shortener is not None:
            candidate_suffix = record_id_shortener.shorten_record_ids_in_text(candidate_suffix)

        entity_filter_note = (
            "Note: nothing matched inside the requested entity, so these results "
            "are NOT limited to it.\n\n"
            if entity_filter_dropped else ""
        )
        has_semantic_blocks = len(final_results) > 0
        if has_semantic_blocks:
            summary = (
                f"{_ranked_header(len(final_results), len(rendered.records), rendered.omitted_hits)}\n\n"
                f"{entity_filter_note}"
                f"{repeat_hit_note(state, surfaced_record_ids, record_id_shortener=record_id_shortener)}"
                f"{coverage_note}"
            )
        else:
            n_pm = len(pm_record_entries)
            summary = (
                f"No content blocks from semantic search, but {n_pm} "
                f"record{'s' if n_pm != 1 else ''} found via keyword matching. "
                "Review the record names below and fetch the most relevant "
                "one(s) directly.\n\n"
            ) if n_pm > 0 else (
                "No results found.\n\n"
            )
        pm_hint = render_pattern_match_hint(
            pm_record_entries, has_semantic_blocks=has_semantic_blocks,
        )
        text = summary + rendered.text + compose_result_tail(
            virtual_record_id_to_result, candidate_suffix,
        ) + pm_hint
        manifest_registry(state).register(text, rendered.manifest.shifted(len(summary)))
        return tool_output(text, rendered.images, state)

    except Exception as exc:
        logger_instance = state.get("logger", logger) if state else logger
        logger_instance.error("knowledgegraph__search error: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Search error: {exc}"})
