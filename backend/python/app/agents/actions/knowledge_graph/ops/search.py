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
import re
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope, _clean_kb
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    enrich_records_with_graph_context,
    get_flattened_results,
    get_record_id_shortener_if_enabled,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_MAX_RETRIEVAL_SOURCES_DIVISOR = 5
_RETRIEVAL_ERROR_STATUS_CODES = frozenset({202, 500, 503})

_RECORD_NAME_RE = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)
_RETRIEVED_COUNT_RE = re.compile(
    r"^Top (\d+) blocks? from (\d+) records?", re.IGNORECASE | re.MULTILINE
)
_RETRIEVED_COUNT_RE_LEGACY = re.compile(
    r"^Retrieved (\d+) knowledge blocks? from (\d+) documents?", re.IGNORECASE | re.MULTILINE
)


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
) -> str:
    """Run semantic search over the agent's knowledge scope.

    ``source_ids`` accepts both app-connector IDs and KB-collection IDs;
    resolution against the agent's configured scope is done here.

    ``created_after``/``created_before``/``modified_after``/``modified_before``
    are optional ISO 8601 date bounds (see ``ops/time_range.parse_time_range``)
    that narrow results to records whose source creation/last-modified
    timestamp falls in the given window. Applied as a hard pre-filter at the
    graph permission-scoping step, before vector search ever runs.

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

        is_service_account = bool(state.get("is_service_account", False))
        fan_out_sources = explicit_ids and (len(resolved_apps) > 1 or len(resolved_kbs) > 1)
        per_source_fan_out = False

        async def _search_one(fg: dict[str, list[str]]) -> dict[str, Any] | None:
            return await retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=fg,
                time_range=time_range,
            )

        if fan_out_sources:
            per_source_fan_out = True
            tasks: list[Any] = []
            for app_id in resolved_apps:
                tasks.append(_search_one(
                    resolved_scope.to_filter_groups_for_source(
                        app_id=app_id, placeholder_agent=is_placeholder_agent,
                    )
                ))
            for kb_id in resolved_kbs:
                tasks.append(_search_one(
                    resolved_scope.to_filter_groups_for_source(
                        kb_id=kb_id, placeholder_agent=is_placeholder_agent,
                    )
                ))

            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            search_results: list[dict[str, Any]] = []
            virtual_to_record_map: dict[str, Any] = {}
            any_success = False
            error_status: int | None = None
            error_message = "Retrieval service unavailable"

            for raw in raw_results:
                if isinstance(raw, Exception):
                    logger_instance.warning("Per-source search failed: %s", raw, exc_info=raw)
                    continue
                if raw is None:
                    continue
                status_code = raw.get("status_code", 200)
                if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                    error_status = error_status or status_code
                    error_message = raw.get("message", error_message)
                    continue
                any_success = True
                search_results.extend(raw.get("searchResults", []))
                virtual_to_record_map.update(raw.get("virtual_to_record_map", {}))

            if not any_success:
                if error_status is not None:
                    return json.dumps({
                        "status": "error",
                        "status_code": error_status,
                        "message": error_message,
                    })
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0,
                })
        else:
            results = await _search_one(filter_groups)
            if results is None:
                return json.dumps({"status": "error", "message": "Retrieval service returned no results"})
            status_code = results.get("status_code", 200)
            if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                return json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable"),
                })
            search_results = results.get("searchResults", [])
            virtual_to_record_map = results.get("virtual_to_record_map", {})

        if not search_results:
            return json.dumps({
                "status": "success",
                "message": "No results found",
                "results": [],
                "result_count": 0,
            })

        blob_store = BlobStorage(
            logger=logger_instance,
            config_service=config_service,
            graph_provider=graph_provider,
        )
        is_multimodal_llm = False
        try:
            llm_config = state.get("llm")
            if hasattr(llm_config, "model_name"):
                model_name = str(llm_config.model_name).lower()
                is_multimodal_llm = any(m in model_name for m in [
                    "gpt-4-vision", "gpt-4o", "claude-3", "gemini-pro-vision",
                ])
        except Exception:
            pass

        virtual_record_id_to_result: dict[str, Any] = {}
        flattened_results = await get_flattened_results(
            search_results,
            blob_store,
            org_id,
            is_multimodal_llm,
            virtual_record_id_to_result,
            virtual_to_record_map,
            graph_provider=graph_provider,
        )

        if flattened_results and graph_provider:
            await enrich_records_with_graph_context(
                virtual_record_id_to_result,
                graph_provider,
                flattened_results,
                virtual_to_record_map,
                blob_store=blob_store,
                org_id=org_id,
                config_service=config_service,
            )

        final_results = search_results if not flattened_results else flattened_results
        if not per_source_fan_out:
            final_results = final_results[:adjusted_limit]

        # Accumulate into state for citation pipeline
        from app.agents.actions.retrieval.retrieval import _dedupe_append_final_results
        state["final_results"] = _dedupe_append_final_results(
            state.get("final_results", []), final_results,
        )
        existing_virtual_map = state.get("virtual_record_id_to_result") or {}
        state["virtual_record_id_to_result"] = {**existing_virtual_map, **virtual_record_id_to_result}
        existing_tool_records = state.get("tool_records") or []
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

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`) — see `RecordIdShortener`
        # in `utils/chat_helpers.py`. Same shared shortener as
        # search_internal_knowledge/navigate/lookup_record/list_files, keyed
        # off tool_state so whichever tool the model calls first mints it.
        record_id_shortener = get_record_id_shortener_if_enabled(state)
        if candidate_suffix and record_id_shortener is not None:
            candidate_suffix = record_id_shortener.shorten_record_ids_in_text(candidate_suffix)

        sorted_results = sorted(
            final_results,
            key=lambda x: (
                x.get("virtual_record_id") or "",
                -1 if x.get("block_index") is None else x.get("block_index"),
            ),
        )
        ref_mapper = state.get("citation_ref_mapper") or CitationRefMapper()
        message_content_array, ref_mapper = build_message_content_array(
            sorted_results,
            virtual_record_id_to_result,
            is_multimodal_llm=is_multimodal_llm,
            ref_mapper=ref_mapper,
            from_tool=True,
            record_id_shortener=record_id_shortener,
        )
        state["citation_ref_mapper"] = ref_mapper

        formatted_records = []
        for content in message_content_array:
            content_string = ""
            for item in content:
                if item["type"] == "text":
                    content_string += item["text"]
            formatted_records.append(content_string)

        summary = (
            f"Top {len(final_results)} block{'s' if len(final_results) != 1 else ''} "
            f"from {len(virtual_record_id_to_result)} record{'s' if len(virtual_record_id_to_result) != 1 else ''} "
            "(ranked sample — other records may match).\n\n"
            f"{coverage_note}"
        )
        from app.agents.actions.retrieval.retrieval import compose_result_tail
        return summary + "\n".join(formatted_records) + compose_result_tail(
            virtual_record_id_to_result, candidate_suffix,
        )

    except Exception as exc:
        logger_instance = state.get("logger", logger) if state else logger
        logger_instance.error("knowledgegraph__search error: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Search error: {exc}"})
