"""
Internal Knowledge Retrieval Tool

- Writes results directly to state (accumulates for parallel calls)
- Returns properly formatted <record> tool messages (same as chatbot)
- Block dedup on accumulation by (virtual_record_id, block_index)
"""

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope
from app.agents.actions.util.tool_summaries import (
    as_text,
    bullet_list,
    parse_json_maybe,
)
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.models.entities import RecordType
from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    build_message_content_array,
    enrich_records_with_graph_context,
    get_flattened_results,
    get_record_id_shortener_if_enabled,
    image_dict_to_part,
)
from app.utils.image_admission import admission_from_state

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.core.types import ToolResult

logger = logging.getLogger(__name__)

# Cap the divisor to prevent excessively small per-source limits when many
# knowledge sources are configured simultaneously.
_MAX_RETRIEVAL_SOURCES_DIVISOR = 5

_RETRIEVAL_ERROR_STATUS_CODES = frozenset({202, 500, 503})

# connector_id → human label, populated per-request by Retrieval.__init__.
# Safe for concurrent requests: UUIDs are globally unique so the same ID
# always maps to the same label regardless of which request writes it.
_SOURCE_LABELS: dict[str, str] = {}


def _block_accumulation_key(entry: dict[str, Any]) -> str | None:
    """Stable dedup key for a retrieval block; None when key parts are missing."""
    virtual_record_id = entry.get("virtual_record_id")
    block_index = entry.get("block_index")
    if virtual_record_id is None or block_index is None:
        return None
    return f"{virtual_record_id}_{block_index}"


def _dedupe_append_final_results(
    existing: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append new_blocks, skipping duplicates by (virtual_record_id, block_index)."""
    if not isinstance(existing, list):
        existing = []
    seen_keys = {
        key
        for entry in existing
        if (key := _block_accumulation_key(entry)) is not None
    }
    appended = list(existing)
    for entry in new_blocks:
        key = _block_accumulation_key(entry)
        if key is None or key not in seen_keys:
            if key is not None:
                seen_keys.add(key)
            appended.append(entry)
    return appended


# ---------------------------------------------------------------------------
# Agent-activity summaries for search_internal_knowledge — declared here
# (colocated with the tool) rather than in a central registry, per `@tool`'s
# `args_summary`/`result_summary` params (see `agent_loop_lib/tools/
# decorators.py`). This tool returns a bare `str` rather than the
# `(bool, str)` tuple most connector tools use, so `ToolOutput.success` is
# always True — errors are only visible as `{"status": "error", ...}` JSON
# in the content, which is why the result formatter parses that instead of
# trusting `result.is_error`.
# ---------------------------------------------------------------------------

# `Name`/`Web URL` are fixed-width-padded labels rendered by
# `Record.to_llm_context()` (`app/models/entities.py`) inside every
# `<record>...</record>` block this tool returns. A tolerant, line-based
# parse (vs. a full XML parser) because these blocks are LLM-facing text,
# not strict markup, and any future field reordering/addition must not
# break this into raising.
_RECORD_NAME_RE = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)
# See the `summary = f"Retrieved {N} knowledge blocks from {M} documents.\n\n"`
# line below, prefixed to every successful result.
_RETRIEVED_COUNT_RE = re.compile(r"^Retrieved (\d+) knowledge blocks? from (\d+) documents?", re.IGNORECASE)

# Record types whose sub-items (or linked records) retrieval can never
# surface — it returns matching BLOCKS, not the record's children — so a
# hit on one of these is exactly when `knowledgegraph.navigate()` adds
# something search structurally cannot. Conditional on record type alone:
# `virtual_record_id_to_result` entries carry `record_type` but not
# `hasChildren`, and computing the latter would cost an extra query per hit.
_HIERARCHICAL_RECORD_TYPES = frozenset({
    RecordType.TICKET.value,
    RecordType.PROJECT.value,
    RecordType.CONFLUENCE_PAGE.value,
    RecordType.SHAREPOINT_LIST.value,
    RecordType.SHAREPOINT_DOCUMENT_LIBRARY.value,
})
_NAVIGATE_TIP = (
    "\n\nTip: these are content excerpts. To see a record's sub-items (epic to "
    "stories to sub-tasks, page to child pages) or its linked records, call "
    'knowledgegraph.navigate(node_id="<Record ID>") — it returns structure and '
    "Record IDs, not content, so reading what any of those records SAY is "
    "still a fetch."
)


def _has_hierarchical_record(virtual_record_id_to_result: dict[str, Any]) -> bool:
    return any(
        isinstance(rec, dict) and rec.get("record_type") in _HIERARCHICAL_RECORD_TYPES
        for rec in virtual_record_id_to_result.values()
    )


def compose_result_tail(
    virtual_record_id_to_result: dict[str, Any], candidate_suffix: str
) -> str:
    """Trailing guidance for a retrieval/search result, in the order the model
    should read it.

    The navigate tip must come BEFORE the candidate list, never after. It names
    a different tool, so in the last position it reads as the answer to "how do
    I get more?" and beats the fetch call-to-action on exactly the records where
    both apply — tickets and Confluence pages. Shared with
    `knowledge_graph.ops.search` so the two paths cannot drift.
    """
    tip = _NAVIGATE_TIP if _has_hierarchical_record(virtual_record_id_to_result) else ""
    return tip + candidate_suffix


def _extract_record_names(text: str) -> list[str]:
    names = []
    for block in text.split("<record>")[1:]:
        match = _RECORD_NAME_RE.search(block)
        if match is not None:
            names.append(match.group(1).strip())
    return names


def _search_internal_knowledge_args_summary(args: dict[str, Any]) -> str | None:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    summary = f'Searched for "{query.strip()}"'
    connector_ids = args.get("connector_ids")
    if isinstance(connector_ids, list) and connector_ids:
        resolved = [_SOURCE_LABELS[cid] for cid in connector_ids if cid in _SOURCE_LABELS]
        if resolved:
            summary += f"\nSources: {', '.join(resolved)}"
        else:
            summary += f"\n{len(connector_ids)} source{'s' if len(connector_ids) != 1 else ''}"
    return summary


def _search_internal_knowledge_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    text = as_text(result.content)
    if not text:
        return None
    parsed = parse_json_maybe(text)
    if isinstance(parsed, dict) and parsed.get("status") == "error":
        return f"Search failed: {parsed.get('message') or 'Unknown error'}"
    if isinstance(parsed, dict) and (
        parsed.get("result_count") == 0 or (isinstance(parsed.get("results"), list) and not parsed["results"])
    ):
        return str(parsed.get("message") or "No results found")

    match = _RETRIEVED_COUNT_RE.search(text)
    if not match:
        return None
    blocks, docs = match.group(1), match.group(2)
    header = f"Retrieved {blocks} block{'s' if blocks != '1' else ''} from {docs} document{'s' if docs != '1' else ''}"
    names = _extract_record_names(text)
    if not names:
        return header
    return header + "\n" + bullet_list(names)


def _normalize_list_param(value: str | list[str] | None) -> list[str] | None:
    """Normalize a parameter that should be a list of strings.
    Handles LLM sending a single string instead of a list, or empty list."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else None
    if isinstance(value, list):
        filtered = [str(v).strip() for v in value if v]
        return filtered if filtered else None
    return None


class RetrievalToolOutput(BaseModel):
    """Structured output from the retrieval tool."""
    status: str = Field(default="success", description="Status: 'success' or 'error'")
    content: str = Field(description="Formatted content for LLM consumption")
    final_results: list[dict[str, Any]] = Field(description="Processed results for citation generation")
    virtual_record_id_to_result: dict[str, dict[str, Any]] = Field(description="Mapping for citation normalization")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SearchInternalKnowledgeInput(BaseModel):
    """Input schema for the search_internal_knowledge tool"""
    query: str = Field(description="The search query to find relevant information")
    connector_ids: list[str] | None = Field(
        default=None,
        description=(
            "Filter to specific sources by their connector ID — covers BOTH app "
            "connectors (Jira, Confluence, Slack, ...) and KB collections alike, "
            "since a KB collection's ID is a connector ID too. If not provided or "
            "the IDs don't match agent scope, uses all agent-configured sources."
        ),
    )


@ToolsetBuilder("Retrieval")\
    .in_group("Internal Tools")\
    .with_description("Internal knowledge retrieval tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .as_essential()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()

class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")
        self._populate_source_labels()

    def _populate_source_labels(self) -> None:
        """Populate module-level source-ID → label mapping for args summaries."""
        if not self.state:
            return
        try:
            catalog = SourceCatalog.from_state(self.state)
            for source in catalog.sources:
                if source.source_id and source.label:
                    _SOURCE_LABELS[source.source_id] = source.label
        except Exception:
            pass

    @tool(
        path="/tools/retrieval/search_internal_knowledge",
        short_description="Search internal knowledge bases and connected data sources",
        description=(
            "Search and retrieve information from indexed company documents, knowledge "
            "bases, and connected data sources. Returns content chunks with citations.\n\n"
            "For a topic that also has a live-API search tool (e.g. Jira, Confluence), "
            "call BOTH this and that tool together — this covers historical/cross-service "
            "content the live API snapshot may not have; a service name in the query "
            "narrows which live tool to also call, it does not replace this call.\n\n"
            "Pass a single `connector_ids` value per call and run one call per source "
            "in parallel. Use the source IDs from the Knowledge Sources section of the "
            "system prompt. Omit `connector_ids` to search all accessible sources."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="The search query to find relevant information", required=True),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Filter to a specific source by its ID. Pass a single ID per call — use one call per source and run them in parallel. Pass a KB collection's id or an app connector's id — both use this same parameter. Omit to search all accessible sources.", required=False, items={"type": "string"}),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_search_internal_knowledge_args_summary,
        result_summary=_search_internal_knowledge_result_summary,
        display_name="Searched the knowledge base",
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        connector_ids: list[str] | None = None,
    ) -> "str | list[Part]":
        """Search internal knowledge bases and return formatted results."""
        search_query = query

        if not search_query:
            return json.dumps({
                "status": "error",
                "message": "No search query provided (expected 'query' or 'text' parameter)"
            })

        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Retrieval tool state not initialized"
            })

        try:
            logger_instance = self.state.get("logger", logger)
            logger_instance.info(f"🔍 Retrieval tool called with query: {search_query[:100]}")

            retrieval_service = self.state.get("retrieval_service")
            graph_provider = self.state.get("graph_provider")
            config_service = self.state.get("config_service")

            if not retrieval_service or not graph_provider:
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval services not available"
                })

            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")

            # Normalize list input. A KB collection's id IS a connector id now,
            # so there is a single unified `connector_ids` parameter covering
            # both app connectors and KB collections — the LLM never needs to
            # know which bucket an id belongs to; this tool resolves that.
            connector_ids = _normalize_list_param(connector_ids)

            # === BUILD FILTERS — always scoped to agent's configured knowledge ===
            #
            # Retrieval uses the curated session `filters` as its primary scope
            # (the user's explicit filter selection for the turn), not state["apps"]
            # which is the full agent knowledge config. Placeholder agents are
            # the exception: their filters are synthetic, so fall back to the
            # full configured sources.
            is_placeholder_agent = self.state.get("is_placeholder_agent", False)
            agent_filters = self.state.get("filters", {}) or {}

            if is_placeholder_agent:
                raw_filter_apps: list[str] = list(self.state.get("apps") or [])
                raw_filter_kbs: list[str] = list(self.state.get("kb") or [])
            else:
                raw_filter_apps = list(agent_filters.get("apps") or [])
                raw_filter_kbs = list(agent_filters.get("kb") or [])

            from app.agents.actions.knowledge_graph.ops.scope import _clean_kb
            base_scope = KnowledgeScope(
                app_ids=tuple(raw_filter_apps),
                kb_ids=_clean_kb(raw_filter_kbs),
            )

            explicit_ids = bool(connector_ids)

            # Only fan-out when the LLM provided specific ids that all resolved;
            # a fallback (no match → full scope) uses a single combined call.
            narrowed_scope: KnowledgeScope | None = None
            if explicit_ids:
                candidate = base_scope.narrow_to(connector_ids)
                if candidate is not base_scope:
                    narrowed_scope = candidate

            resolved_scope = narrowed_scope if narrowed_scope is not None else base_scope

            agent_connector_ids_count = len(base_scope.app_ids)
            agent_collection_ids_count = len(base_scope.kb_ids)
            total_sources = agent_connector_ids_count + agent_collection_ids_count
            if total_sources <= 1:
                adjusted_limit = 50
            else:
                adjusted_limit = 100 // min(total_sources, _MAX_RETRIEVAL_SOURCES_DIVISOR)

            logger_instance.debug(f"is_placeholder_agent: {is_placeholder_agent}")
            logger_instance.debug(f"base_scope.app_ids: {sorted(base_scope.app_ids)}")
            logger_instance.debug(f"base_scope.kb_ids: {sorted(base_scope.kb_ids)}")

            filter_groups = resolved_scope.to_filter_groups()

            # Use narrowed ids for fan-out (empty when fallback occurred)
            resolved_apps = list(narrowed_scope.app_ids) if narrowed_scope else []
            resolved_kbs = list(narrowed_scope.kb_ids) if narrowed_scope else []

            # === SEARCH ===
            is_service_account = bool(self.state.get("is_service_account", False))
            logger_instance.debug(
                f"Executing retrieval with limit: {adjusted_limit} "
                f"(service_account={is_service_account})"
            )

            logger_instance.debug(f"filter_groups: {filter_groups}")

            # Fan-out only when there are multiple sources of the SAME type.
            # A single app + single KB is combined into one call so the
            # retrieval service can cross-rank them; fan-out is for when each
            # source deserves its own adjusted_limit allocation.
            fan_out_sources = explicit_ids and (len(resolved_apps) > 1 or len(resolved_kbs) > 1)
            per_source_fan_out = False

            async def _search_with_filter_groups(
                source_filter_groups: dict[str, list[str]],
            ) -> dict[str, Any] | None:
                return await retrieval_service.search_with_filters(
                    queries=[search_query],
                    org_id=org_id,
                    user_id=user_id,
                    limit=adjusted_limit,
                    filter_groups=source_filter_groups,
                )

            if fan_out_sources:
                per_source_fan_out = True
                search_tasks: list[Any] = []
                for app_id in resolved_apps:
                    search_tasks.append(_search_with_filter_groups(
                        resolved_scope.to_filter_groups_for_source(
                            app_id=app_id, placeholder_agent=is_placeholder_agent,
                        )
                    ))
                for kb_id in resolved_kbs:
                    search_tasks.append(_search_with_filter_groups(
                        resolved_scope.to_filter_groups_for_source(
                            kb_id=kb_id, placeholder_agent=is_placeholder_agent,
                        )
                    ))

                raw_results = await asyncio.gather(*search_tasks, return_exceptions=True)
                search_results: list[dict[str, Any]] = []
                virtual_to_record_map: dict[str, Any] = {}
                any_success = False
                error_status: int | None = None
                error_message = "Retrieval service unavailable"

                for raw in raw_results:
                    if isinstance(raw, Exception):
                        logger_instance.warning(
                            "Per-source retrieval failed: %s", raw, exc_info=raw,
                        )
                        continue
                    if raw is None:
                        logger_instance.warning("Per-source retrieval returned None")
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
                logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
                results = await _search_with_filter_groups(filter_groups)

                if results is None:
                    logger_instance.warning("Retrieval service returned None")
                    return json.dumps({
                        "status": "error",
                        "message": "Retrieval service returned no results"
                    })

                status_code = results.get("status_code", 200)
                if status_code in _RETRIEVAL_ERROR_STATUS_CODES:
                    return json.dumps({
                        "status": "error",
                        "status_code": status_code,
                        "message": results.get("message", "Retrieval service unavailable")
                    })

                search_results = results.get("searchResults", [])
                virtual_to_record_map = results.get("virtual_to_record_map", {})

            logger_instance.info(f"✅ Retrieved {len(search_results)} documents")

            if not search_results:
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0
                })

            # === FLATTEN ===

            blob_store = BlobStorage(
                logger=logger_instance,
                config_service=config_service,
                graph_provider=graph_provider
            )

            # `is_multimodal_llm` is the authoritative flag set from the LLM
            # config's `isMultimodal` field (see `context.py`'s
            # `AgentContext.is_multimodal_llm`, seeded onto this same
            # `tool_state` dict) — NOT a substring match against the model
            # name, which misses GPT-5, Gemini 2.x, Ollama VLMs, etc.
            is_multimodal_llm = bool(self.state.get("is_multimodal_llm", False))

            virtual_record_id_to_result = {}
            # Enrich records with graph-DB metadata (record type, web URL, etc.)
            # from the search response's virtual_to_record_map.

            flattened_results = await get_flattened_results(
                search_results,
                blob_store,
                org_id,
                is_multimodal_llm,
                virtual_record_id_to_result,
                virtual_to_record_map,
                graph_provider=graph_provider,
            )
            logger_instance.info(f"Processed {len(flattened_results)} flattened results")

            # === GRAPH CONTEXT ENRICHMENT ===
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

            # === TRIM ===
            # Do NOT sort here. The upstream retrieval service returns results
            # ranked by relevance. Single-call searches share one top-K; when
            # multiple explicit connector_ids fan out, each source already
            # received its own adjusted_limit.
            if not per_source_fan_out:
                final_results = final_results[:adjusted_limit]

            # ================================================================
            # Write results directly to state (accumulate for parallel calls)
            # and return properly formatted tool message like the chatbot.
            #
            # Accumulation dedupes blocks by (virtual_record_id, block_index)
            # so parallel or repeat calls do not inflate final_results.
            # ================================================================

            existing_final_results = self.state.get("final_results", [])
            self.state["final_results"] = _dedupe_append_final_results(
                existing_final_results, final_results,
            )

            existing_virtual_map = self.state.get("virtual_record_id_to_result", {})
            if not isinstance(existing_virtual_map, dict):
                existing_virtual_map = {}
            self.state["virtual_record_id_to_result"] = {**existing_virtual_map, **virtual_record_id_to_result}

            existing_tool_records = self.state.get("tool_records", [])
            if not isinstance(existing_tool_records, list):
                existing_tool_records = []
            new_tool_records = list(virtual_record_id_to_result.values())
            existing_record_ids = {r.get("_id") for r in existing_tool_records if isinstance(r, dict) and "_id" in r}
            unique_new = [r for r in new_tool_records if not (isinstance(r, dict) and r.get("_id") in existing_record_ids)]
            self.state["tool_records"] = existing_tool_records + unique_new

            # --- Candidate list (record escalation) ---
            # Must run BEFORE the display sort so candidate order follows the
            # upstream relevance ranking (final_results[:adjusted_limit]) that
            # the comment at line 384 asks us to preserve.
            #
            # Runs regardless of needs_whole_document: the counts ("you have 4
            # of 87 blocks") are the only way the model can tell whether
            # reading further would add anything, and withholding them when the
            # query signal misfires leaves it unable to judge a fetch at all.
            # The signal only chooses the header's framing.
            candidate_suffix = ""
            coverage_note = ""
            needs_whole_doc = bool(self.state.get("needs_whole_document", False))
            from app.modules.agents.record_escalation import (
                analyze_coverage,
                build_candidates,
                render_candidate_table,
                render_coverage_note,
            )
            # Coverage over ALL accumulated results, not just this call's, so
            # parallel/repeat retrieval calls do not each report partial counts.
            all_final = self.state.get("final_results", [])
            full_vr_map = self.state.get("virtual_record_id_to_result", {})
            coverage = analyze_coverage(all_final, full_vr_map)
            already_fetched: set[str] = set(self.state.get("full_records_fetched", set()))

            # Relevance order: records in final_results order (before the
            # display sort), de-duped on first appearance.
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
            # Stash on state for observability/debugging.
            self.state["fetch_coverage"] = coverage
            self.state["fetch_plan"] = plan

            logger_instance.info(
                "record_escalation: needs_whole_document=%s | candidates=%s | excluded=%s",
                needs_whole_doc,
                [c.record_id for c in plan.candidates],
                list(plan.excluded),
            )

            if plan.has_candidates:
                candidate_suffix = render_candidate_table(
                    plan, needs_whole_document=needs_whole_doc,
                )
                # Placed at the TOP of the result (see `summary` below) —
                # the full table above lands at the bottom, potentially
                # thousands of tokens away on a long result, where the
                # model may already be composing an answer from the blocks
                # by the time it reaches it.
                coverage_note = render_coverage_note(
                    plan, needs_whole_document=needs_whole_doc,
                )

            # TEMPORARY token-savings experiment (opt-in, disabled by
            # default — see `ChatQuery.enableRecordIdShortening`): shorten
            # "Record ID: <id>" to a short "R<n>" label in everything the
            # model sees from this call. The mapping lives on tool_state so
            # `_FetchFullRecordTool` can resolve it back to the full id —
            # see `RecordIdShortener`.
            record_id_shortener = get_record_id_shortener_if_enabled(self.state)
            if candidate_suffix and record_id_shortener is not None:
                candidate_suffix = record_id_shortener.shorten_record_ids_in_text(candidate_suffix)

            # --- Format results like the chatbot does ---
            sorted_results = sorted(
                final_results,
                key=lambda x: (x.get("virtual_record_id") or "", -1 if x.get("block_index") is None else x.get("block_index"))
            )
            ref_mapper = self.state.get("citation_ref_mapper") or CitationRefMapper()
            image_budget: ImageBudget = self.state.setdefault(
                "image_budget", ImageBudget(),
            )
            collected_images: list[dict[str, Any]] = []
            message_content_array, ref_mapper = build_message_content_array(
                sorted_results, virtual_record_id_to_result,is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper,from_tool=True,
                record_id_shortener=record_id_shortener,
                collected_images=collected_images,
                image_budget=image_budget,
                image_admission=admission_from_state(self.state),
            )
            self.state["citation_ref_mapper"] = ref_mapper

            formatted_records = []
            for content in message_content_array:
                content_string = ""
                for item in content:
                    if item["type"] == "text":
                        content_string += item["text"]
                formatted_records.append(content_string)

            logger_instance.info(
                f"✅ Retrieved {len(final_results)} blocks from "
                f"{len(virtual_record_id_to_result)} documents "
                f"(state updated, formatted as tool message)"
            )

            summary = (
                f"Top {len(final_results)} block{'s' if len(final_results) != 1 else ''} "
                f"from {len(virtual_record_id_to_result)} "
                f"record{'s' if len(virtual_record_id_to_result) != 1 else ''} "
                f"(ranked sample — other records may match).\n\n"
                f"{coverage_note}"
            )
            text_output = summary + "\n".join(formatted_records) + compose_result_tail(
                virtual_record_id_to_result, candidate_suffix,
            )

            if collected_images and is_multimodal_llm:
                # Multipart return: `_normalize_legacy_output` (decorators.py)
                # wraps a non-str/non-(bool,str) return as
                # `ToolOutput(data=result)`, and `executor.py` copies
                # `ToolOutput.data` straight into `ToolResult.content` — so
                # this list of Parts flows through unchanged into a
                # multipart `ToolMessage` (see `agent/__init__.py`'s
                # `_tool_result_content_to_message_content`). Only stash a
                # fallback copy when the transport actually needs one —
                # models with native multipart tool-result support (the
                # `supports_multipart_tool_result=True` default) already
                # get the images via the multipart return above, so
                # `shape_retrieved_image_injection` (which alone consumes
                # this stash) is never registered for them; retaining the
                # images here regardless would just leak memory across
                # turns. Ollama's transport strips images back out at
                # format time — that fallback hook re-injects them via
                # `UserMessage` using this same `pending_tool_images` stash.
                if not self.state.get("supports_multipart_tool_result", True):
                    self.state.setdefault("pending_tool_images", []).extend(collected_images)
                from app.agent_loop_lib.core.messages import TextPart  # noqa: PLC0415
                image_parts = [
                    part for img in collected_images
                    if (part := image_dict_to_part(img)) is not None
                ]
                return [TextPart(text=text_output), *image_parts]

            return text_output

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })

