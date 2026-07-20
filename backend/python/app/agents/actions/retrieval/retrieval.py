"""
Internal Knowledge Retrieval Tool

- Writes results directly to state (accumulates for parallel calls)
- Returns properly formatted <record> tool messages (same as chatbot)
- Block numbering (R-labels) happens ONCE after all parallel calls are merged
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import as_text, bullet_list, parse_json_maybe
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

logger = logging.getLogger(__name__)

# Cap the divisor to prevent excessively small per-source limits when many
# knowledge sources are configured simultaneously.
_MAX_RETRIEVAL_SOURCES_DIVISOR = 5


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
        summary += f" in {len(connector_ids)} source{'s' if len(connector_ids) != 1 else ''}"
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
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()

class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, writer: StreamWriter | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        self.writer = writer
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")

    @tool(
        path="/tools/retrieval/search_internal_knowledge",
        short_description="Search internal knowledge bases and connected data sources",
        description=(
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
            "unread mail right now'), pure greetings, or arithmetic."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="The search query to find relevant information", required=True),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Filter to specific connectors by their IDs. If not provided or IDs don't match agent scope, uses all agent connectors.", required=False, items={"type": "string"}),
            ToolParameter(name="collection_ids", type=ParameterType.ARRAY, description="Filter to specific KB collections by their record group IDs. If not provided or IDs don't match agent scope, uses all agent collections.", required=False, items={"type": "string"}),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_search_internal_knowledge_args_summary,
        result_summary=_search_internal_knowledge_result_summary,
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        connector_ids: list[str] | None = None,
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
            # Rule: if the caller explicitly provides connector_ids, resolve
            # each id against the agent's configured apps/KBs and scope
            # precisely to whichever bucket(s) it actually matched — an id
            # naming one app connector scopes to that connector only (no KB
            # broadening), an id naming one KB collection scopes to that KB
            # only, and a mix of both scopes to exactly that mix. This is
            # what "one parameter, resolved automatically" means in practice.
            #
            # Only when connector_ids is entirely omitted do we fall back to
            # the full agent scope (both connectors and KB collections).
            #
            explicit_ids = bool(connector_ids)
            broad_search = not explicit_ids

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

            if explicit_ids:
                resolved_apps = [cid for cid in connector_ids if cid in agent_filter_apps]
                resolved_kbs = [cid for cid in connector_ids if cid in agent_filter_kbs]
                if resolved_apps or resolved_kbs:
                    # At least one supplied id matched a known source — scope
                    # precisely to what matched; an unmatched bucket is
                    # excluded rather than broadened, since none of its ids
                    # were requested.
                    filter_groups["apps"] = resolved_apps
                    if resolved_kbs:
                        filter_groups["kb"] = resolved_kbs
                    else:
                        filter_groups["kb"] = [] if is_placeholder_agent else ['NO_KB_SELECTED']
                else:
                    # None of the supplied ids matched agent scope (the LLM
                    # hallucinated them) — fall back to the full agent scope
                    # on both sides as a safe default.
                    filter_groups["apps"] = list(agent_filter_apps) if agent_filter_apps else []
                    filter_groups["kb"] = list(agent_filter_kbs) if agent_filter_kbs else []
            else:
                # No explicit filter — include everything in agent scope.
                filter_groups["apps"] = list(agent_filter_apps) if agent_filter_apps else []
                filter_groups["kb"] = list(agent_filter_kbs) if agent_filter_kbs else []

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

