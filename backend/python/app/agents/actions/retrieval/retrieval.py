"""
Internal Knowledge Retrieval Tool

- Writes results directly to state (accumulates for parallel calls)
- Returns properly formatted <record> tool messages (same as chatbot)
- Block numbering (R-labels) happens ONCE after all parallel calls are merged
"""

import json
import logging
from datetime import datetime
from typing import Any

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

logger = logging.getLogger(__name__)

# Cap the divisor to prevent excessively small per-source limits when many
# knowledge sources are configured simultaneously.
_MAX_RETRIEVAL_SOURCES_DIVISOR = 5

# Small grace (5 minutes) for client/server clock skew when validating that
# created_after is not set to a future timestamp.
_FUTURE_TIMESTAMP_GRACE_MS = 5 * 60 * 1000


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


def _parse_iso_time_bound(value: str, field_name: str) -> tuple[int | None, str | None]:
    """Parse an ISO 8601 bound to epoch ms. Returns (epoch_ms, error_json)."""
    value = value.strip()
    if not value:
        return None, None
    try:
        iso = value
        if iso.endswith("Z") or iso.endswith("z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            return None, json.dumps({
                "status": "error",
                "message": (
                    f"Invalid ISO 8601 timestamp for {field_name}: include a timezone offset "
                    "(e.g. -07:00 or Z)."
                ),
            })
        return parse_timestamp(value), None
    except (ValueError, TypeError):
        return None, json.dumps({
            "status": "error",
            "message": (
                f"Invalid ISO 8601 timestamp for {field_name}: {value!r}. "
                "Use ISO 8601 with timezone offset."
            ),
        })


def _build_time_range_from_iso(
    created_after: str | None,
    created_before: str | None,
    updated_after: str | None = None,
    updated_before: str | None = None,
) -> tuple[dict[str, int] | None, str | None]:
    """Build time_range dict from optional ISO bounds. Returns (time_range, error_json).

    Keys in the returned dict:
      source_created_after_ms / source_created_before_ms  → filters on sourceCreatedAtTimestamp
      source_updated_after_ms / source_updated_before_ms  → filters on sourceLastModifiedTimestamp
    """
    time_range: dict[str, int] = {}

    if created_after:
        after_ms, err = _parse_iso_time_bound(created_after, "created_after")
        if err:
            return None, err
        if after_ms is not None:
            time_range["source_created_after_ms"] = after_ms

    if created_before:
        before_ms, err = _parse_iso_time_bound(created_before, "created_before")
        if err:
            return None, err
        if before_ms is not None:
            time_range["source_created_before_ms"] = before_ms

    if updated_after:
        after_ms, err = _parse_iso_time_bound(updated_after, "updated_after")
        if err:
            return None, err
        if after_ms is not None:
            time_range["source_updated_after_ms"] = after_ms

    if updated_before:
        before_ms, err = _parse_iso_time_bound(updated_before, "updated_before")
        if err:
            return None, err
        if before_ms is not None:
            time_range["source_updated_before_ms"] = before_ms

    if (
        "source_created_after_ms" in time_range
        and "source_created_before_ms" in time_range
        and time_range["source_created_after_ms"] > time_range["source_created_before_ms"]
    ):
        return None, json.dumps({
            "status": "error",
            "message": (
                "created_after must be on or before created_before. "
                f"Got created_after={created_after!r}, created_before={created_before!r}."
            ),
        })

    if (
        "source_updated_after_ms" in time_range
        and "source_updated_before_ms" in time_range
        and time_range["source_updated_after_ms"] > time_range["source_updated_before_ms"]
    ):
        return None, json.dumps({
            "status": "error",
            "message": (
                "updated_after must be on or before updated_before. "
                f"Got updated_after={updated_after!r}, updated_before={updated_before!r}."
            ),
        })

    # Guard: created_after must not be in the future — no document can be ingested
    # in the future, so a future lower bound on creation time returns zero results.
    now_ms = get_epoch_timestamp_in_ms()
    c_after_ms = time_range.get("source_created_after_ms")
    if c_after_ms is not None and c_after_ms > now_ms + _FUTURE_TIMESTAMP_GRACE_MS:
        return None, json.dumps({
            "status": "error",
            "message": (
                f"created_after={created_after!r} is in the future. This filter is the "
                "document's ingestion time and must not be a future date. For event-time "
                "queries (e.g. 'scheduled for next week', 'will happen', 'upcoming'), the "
                "planning document was created BEFORE the event — retry with created_after "
                "set to a planning lead time before today (typically ~4 weeks for near-term "
                "events, ~12 months for yearly horizons) and leave created_before null. If "
                "the planning horizon is genuinely unknowable, omit both bounds."
            ),
        })

    return (time_range if time_range else None), None


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
    connector_ids: list[str] | None = Field(default=None, description="Filter to specific connectors by their IDs. If not provided or IDs don't match agent scope, uses all agent connectors.")
    collection_ids: list[str] | None = Field(default=None, description="Filter to specific KB collections by their record group IDs. If not provided or IDs don't match agent scope, uses all agent collections.")
    created_after: str | None = Field(
        default=None,
        description=(
            "Optional inclusive lower bound on the document's INGESTION time in the source "
            "system. ISO 8601 with timezone offset, e.g. '2026-05-14T00:00:00-07:00'. Resolve "
            "relative dates against the **Current time** and **Time zone** in your system "
            "prompt — never invent a date. Must never be in the future (no document can be "
            "ingested in the future).\n\n"
            "Two ways to use:\n\n"
            "1. DOCUMENT-time queries — the time word modifies the document itself: "
            "'docs created last week', 'emails I received in May', 'files uploaded since "
            "Monday'. Set created_after / created_before to the exact window the user gave.\n\n"
            "2. EVENT-time queries — the time word refers to an event, plan, deployment, or "
            "milestone DESCRIBED IN the document. The planning / announcement document for "
            "that event was almost always created BEFORE the event, so back off created_after "
            "by a reasonable planning lead time:\n"
            "  - Near-term events (next week / this week / tomorrow): set created_after to "
            "~4 weeks before today; leave created_before null.\n"
            "  - This month / next month: ~2-3 months before today; leave created_before null.\n"
            "  - This quarter / next quarter: ~6 months before today.\n"
            "  - This year / last year / longer-range: ~12 months before today, or skip the "
            "filter entirely if the planning horizon could be multi-year.\n"
            "  For past events that have already happened, you may also set created_before to "
            "the event date plus a short tail (~2 weeks) to exclude post-mortem rewrites.\n\n"
            "Examples (assume today is 2026-05-21):\n"
            "  - 'ECOs scheduled for deployment next week' → created_after='2026-04-23' "
            "(~4 weeks back), created_before=null.\n"
            "  - 'Which ECOs will cause downtime next week?' → created_after='2026-04-23', "
            "created_before=null.\n"
            "  - 'How many ECOs were deployed this year?' → created_after='2025-05-21' "
            "(~12 months back), created_before=null.\n"
            "  - 'Docs I created last week' → created_after='2026-05-14', "
            "created_before='2026-05-21' (exact window).\n\n"
            "If the planning horizon is genuinely unknowable, OMIT both bounds and let "
            "semantic search find the document on its own."
        ),
    )
    created_before: str | None = Field(
        default=None,
        description=(
            "Optional inclusive upper bound on the document's INGESTION time in the source "
            "system. Same format as created_after. For DOCUMENT-time queries, set to the "
            "exact upper edge of the user's window. For EVENT-time queries, usually leave "
            "null (planning docs may still be updated); for past events, you may cap it at "
            "the event date plus a short tail."
        ),
    )
    updated_after: str | None = Field(
        default=None,
        description=(
            "Optional inclusive lower bound on the document's LAST MODIFICATION time in the "
            "source system (when the file/page/ticket was last edited or updated). ISO 8601 "
            "with timezone offset, e.g. '2026-05-14T00:00:00-07:00'.\n\n"
            "USE for queries about documents that were recently modified, regardless of when "
            "they were first created: 'pages updated last week', 'files edited in May', "
            "'tickets modified since Monday', 'Confluence pages changed this month'.\n\n"
            "A page created a year ago but edited last week has a creation timestamp from a "
            "year ago — using created_after would miss it entirely. Use updated_after instead.\n\n"
            "DO NOT use for queries where 'update' refers to an event in content (e.g. "
            "'status update on the Q4 launch' — that is a semantic search, not a time filter)."
        ),
    )
    updated_before: str | None = Field(
        default=None,
        description=(
            "Optional inclusive upper bound on the document's LAST MODIFICATION time. "
            "Same format as updated_after. Usually omitted for 'since / after' queries; "
            "set to close the window for 'between X and Y' queries."
        ),
    )


@ToolsetBuilder("Retrieval")\
    .in_group("Internal Tools")\
    .with_description("Internal knowledge retrieval tool - always available, no authentication required")\
    .with_category(ToolCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()

class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, writer: StreamWriter | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        self.writer = writer
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")

    @tool(
        app_name="retrieval",
        tool_name="search_internal_knowledge",
        description=(
            "Search and retrieve information from internal collections and indexed applications"
        ),
        args_schema=SearchInternalKnowledgeInput,
        llm_description=(
            "Search and retrieve information from indexed company documents, knowledge "
            "bases, and connected data sources. Returns content chunks with citations.\n\n"
            "HYBRID-SEARCH RULE: when the agent has BOTH this tool AND a search tool for "
            "an indexed service (e.g. Confluence, Jira, Drive, OneDrive, etc.) available, call "
            "BOTH in PARALLEL for any topic / information query. Indexed snapshots and "
            "live API data complement each other — the user gets a richer answer when "
            "both are merged. Some service tools are live-only (e.g. Slack, Outlook, "
            "Gmail, Calendar) — for those, follow the planner's per-service rules instead "
            "of pairing with retrieval. Only skip this tool entirely for: exact ID "
            "lookups (use the service tool), write actions, real-time-only data ('my "
            "unread mail right now'), pure greetings, or arithmetic.\n\n"
            "TIME-RANGE — choose the right pair of bounds:\n"
            "- 'pages updated last week', 'files edited in May', 'tickets changed since "
            "Monday' → use updated_after / updated_before (last-modification time). A doc "
            "created a year ago but edited last week will be MISSED if you use created_after.\n"
            "- 'docs created last week', 'emails I received in May' → use created_after / "
            "created_before (original ingestion time).\n"
            "- Event-time queries ('scheduled for next week', 'deployed this year'): the "
            "planning doc was created BEFORE the event — back created_after off by a lead "
            "time (~4 weeks for near-term, ~12 months for yearly) and leave created_before "
            "null. See the created_after schema for examples.\n"
            "- NEVER set created_after to a future timestamp; the server will reject it.\n"
            "Resolve relative dates from the **Current time** and **Time zone** in your "
            "system prompt."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "Any topic, keyword, concept, name, or phrase — even a single bare word",
            "Information / documentation requests ('what is X', 'how does Y work', 'tell me about Z')",
            "Policy / procedure / general knowledge questions",
            "ALWAYS in parallel with a service search tool when one is configured for the same topic",
            "When the query asks about a person, entity, or topic that is NOT present in the attached documents** — do NOT refuse; search the internal knowledge base instead.",
            "Modification-time queries ('pages updated last week', 'files edited in May', 'tickets changed since Monday'): use updated_after / updated_before — NOT created_after, which would miss docs created before the window.",
            "Document-creation-time queries ('docs created last week', 'emails I received in May'): use created_after / created_before.",
            "Event-time queries ('scheduled for next week', 'will be deployed', 'deployed this year'): use created_after set to a planning lead time before today (~4 weeks for near-term, ~12 months for yearly), leave created_before null. NEVER set created_after to a future date.",
        ],
        when_not_to_use=[
            "Exact ID lookup ('get page 12345') — use the service tool directly",
            "Write actions (create / update / delete) — use the service tool",
            "Real-time-only data ('my unread mail right now', 'today's calendar') — use the service tool",
            "Pure greetings, thanks, or arithmetic",
            "ONLY when the attachment content fully and directly answers the query for the **exact same** person, entity, or topic being asked about — do not call this tool unnecessarily.",
            "Omit created_after / created_before only when the planning horizon is genuinely unknowable (e.g. multi-year roadmap with no anchor date)."
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What is our vacation policy?",
            "How do I submit expenses?",
            "Find information about Q4 results"
        ]
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        connector_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
    ) -> str:
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
            logger_instance.info(
                "🔍 search_internal_knowledge called | "
                "query=%r | connector_ids=%r | collection_ids=%r | "
                "created_after=%r | created_before=%r | "
                "updated_after=%r | updated_before=%r",
                search_query[:200],
                connector_ids,
                collection_ids,
                created_after,
                created_before,
                updated_after,
                updated_before,
            )

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

            # Normalize list inputs
            connector_ids = _normalize_list_param(connector_ids)
            collection_ids = _normalize_list_param(collection_ids)

            time_range, time_range_error = _build_time_range_from_iso(
                created_after, created_before, updated_after, updated_before
            )
            if time_range_error:
                logger_instance.warning(
                    "search_internal_knowledge time-range rejected | "
                    "created_after=%r | created_before=%r | "
                    "updated_after=%r | updated_before=%r | error=%s",
                    created_after,
                    created_before,
                    updated_after,
                    updated_before,
                    time_range_error,
                )
                return time_range_error
            logger_instance.info(
                "search_internal_knowledge time_range resolved | %s",
                time_range if time_range else "no time filter",
            )

            # === BUILD FILTERS — always scoped to agent's configured knowledge ===
            # Get agent's configured filters from state
            agent_filters = self.state.get("filters", {}) or {}
            agent_filter_apps = set(agent_filters.get("apps") or [])
            agent_filter_kbs = set(agent_filters.get("kb") or [])

            agent_configured_apps = self.state.get("apps", [])
            agent_configured_kbs = self.state.get("kb", [])

            # Start from an empty filter dict — we build it precisely below.
            filter_groups: dict[str, list[str]] = {}

            # === TARGETED vs BROAD FILTER LOGIC ===
            #
            # Rule: if the caller explicitly provides EITHER connector_ids OR
            # collection_ids, treat that as a targeted search and do NOT add the
            # other side from the agent scope. Mixing both would create an
            # unnecessary union that defeats the purpose of the explicit filter.
            #
            # Only when NEITHER is provided do we fall back to the full agent
            # scope (both connectors and KB collections).
            #
            explicit_connectors = bool(connector_ids)
            explicit_collections = bool(collection_ids)
            broad_search = not explicit_connectors and not explicit_collections

            # Placeholder agent: broaden scope to all configured connectors/KBs
            # since filters are not author-curated for this synthetic agent.
            is_placeholder_agent = self.state.get("is_placeholder_agent", False)
            if is_placeholder_agent:
                agent_filter_apps = list(agent_configured_apps) if agent_configured_apps else []
                agent_filter_kbs = list(agent_configured_kbs) if agent_configured_kbs else []

            agent_connector_ids_count = len(agent_filter_apps)
            agent_collection_ids_count = len(agent_filter_kbs)
            total_sources = agent_connector_ids_count + agent_collection_ids_count
            if total_sources <= 1:
                adjusted_limit = 50
            else:
                adjusted_limit = 100 // min(total_sources, _MAX_RETRIEVAL_SOURCES_DIVISOR)

            logger_instance.debug(f"is_placeholder_agent: {is_placeholder_agent}")
            logger_instance.debug(f"agent_filter_apps: {sorted(agent_filter_apps)}")
            logger_instance.debug(f"agent_filter_kbs: {sorted(agent_filter_kbs)}")

            # --- App connectors ---
            if explicit_connectors:
                # Scope to the intersection with the agent's allowed connectors.
                resolved_apps = [cid for cid in connector_ids if cid in agent_filter_apps]
                # If the LLM hallucinated an ID not in scope, ignore it and use
                # the full agent connector set as a safe fallback.
                filter_groups["apps"] = resolved_apps if resolved_apps else list(agent_filter_apps)
            elif broad_search:
                # No explicit filter — include all agent connectors.
                filter_groups["apps"] = list(agent_filter_apps) if agent_filter_apps else []
            else:
                # collection_ids were given but connector_ids were not:
                # exclude connectors entirely so the search is KB-only.
                filter_groups["apps"] = []

            # --- KB collections ---
            if explicit_collections:
                # Scope to the intersection with the agent's allowed KB groups.
                resolved_kbs = [cid for cid in collection_ids if cid in agent_filter_kbs]
                # Fallback to full KB scope if IDs don't match.
                filter_groups["kb"] = resolved_kbs if resolved_kbs else list(agent_filter_kbs)
            elif broad_search:
                # No explicit filter — include all agent KB collections.
                filter_groups["kb"] = list(agent_filter_kbs) if agent_filter_kbs else []
            else:
                # connector_ids were given but collection_ids were not:
                # exclude KB collections so the search is connector-only.
                filter_groups["kb"] = ['NO_KB_SELECTED']
                if is_placeholder_agent:
                    filter_groups["kb"] = []

            # === SEARCH ===
            is_service_account = bool(self.state.get("is_service_account", False))
            logger_instance.debug(
                f"Executing retrieval with limit: {adjusted_limit} "
                f"(service_account={is_service_account})"
            )

            logger_instance.debug(f"filter_groups: {filter_groups}")

            logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
            results = await retrieval_service.search_with_filters(
                queries=[search_query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups,
                time_range=time_range,
            )

            if results is None:
                logger_instance.warning("Retrieval service returned None")
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval service returned no results"
                })

            status_code = results.get("status_code", 200)
            if status_code in [202, 500, 503]:
                return json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable")
                })

            search_results = results.get("searchResults", [])
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

            is_multimodal_llm = False
            try:
                llm_config = self.state.get("llm")
                if hasattr(llm_config, 'model_name'):
                    model_name = str(llm_config.model_name).lower()
                    is_multimodal_llm = any(m in model_name for m in [
                        'gpt-4-vision', 'gpt-4o', 'claude-3', 'gemini-pro-vision'
                    ])
            except Exception:
                pass

            virtual_record_id_to_result = {}
            # Retrieve virtual_to_record_map from search results — same as chatbot.
            # This enriches records with graph-DB metadata (record type, web URL, etc.)
            # so that context_metadata is populated for get_message_content().
            virtual_to_record_map = results.get("virtual_to_record_map", {})

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


            final_results = search_results if not flattened_results else flattened_results

            # === TRIM ===
            # Do NOT sort here. The upstream retrieval service returns results
            # ranked by relevance. merge_and_number_retrieval_results() in
            # nodes.py will correctly:
            #   1. Deduplicate blocks across parallel retrieval calls
            #   2. Group blocks by document (by best-score descending)
            #   3. Sort blocks within each document by block_index
            final_results = final_results[:adjusted_limit]

            # ================================================================
            # Write results directly to state (accumulate for parallel calls)
            # and return properly formatted tool message like the chatbot.
            #
            # Block numbering (R-labels) still happens ONCE after all parallel
            # calls are merged in nodes.py (merge_and_number_retrieval_results()).
            # But the ToolMessage content the LLM sees during planning/ReAct
            # is now properly formatted with <record> XML blocks instead of
            # raw JSON dumps.
            # ================================================================

            # --- Accumulate results in state (same pattern as _process_retrieval_output) ---
            existing_final_results = self.state.get("final_results", [])
            if not isinstance(existing_final_results, list):
                existing_final_results = []
            self.state["final_results"] = existing_final_results + final_results

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

            # --- Format results like the chatbot does ---
            sorted_results = sorted(
                final_results,
                key=lambda x: (x.get("virtual_record_id") or "", -1 if x.get("block_index") is None else x.get("block_index"))
            )
            ref_mapper = self.state.get("citation_ref_mapper") or CitationRefMapper()
            message_content_array, ref_mapper = build_message_content_array(
                sorted_results, virtual_record_id_to_result,is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper,from_tool=True
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
                f"Retrieved {len(final_results)} knowledge blocks from "
                f"{len(virtual_record_id_to_result)} documents.\n\n"
            )
            return summary + "\n".join(formatted_records)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })

