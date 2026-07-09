"""
Internal Knowledge Retrieval Tool

- Writes results directly to state (accumulates for parallel calls)
- Returns properly formatted <record> tool messages (same as chatbot)
- Block numbering (R-labels) happens ONCE after all parallel calls are merged
"""

import asyncio
import json
import logging
import os
from typing import Any

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch,
    _validate_command,
    is_local_storage,
)
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
    get_record,
)

logger = logging.getLogger(__name__)

# Cap the divisor to prevent excessively small per-source limits when many
# knowledge sources are configured simultaneously.
_MAX_RETRIEVAL_SOURCES_DIVISOR = 5
_MAX_PATTERN_MATCH_RECORDS = 5
_PATTERN_MATCH_TIMEOUT = 15


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
    connector_ids: list[str] | None = Field(default=None, description="Filter to specific connectors by their IDs. If not provided or IDs don't match agent scope, uses all agent connectors.")
    collection_ids: list[str] | None = Field(default=None, description="Filter to specific KB collections by their record group IDs. If not provided or IDs don't match agent scope, uses all agent collections.")
    command: str | None = Field(
        default=None,
        description=(
            "Optional grep/find command for exact pattern matching on local record files. "
            "Complements semantic search by catching exact-match results that semantic "
            "ranking might miss. Allowed binaries: grep, egrep, fgrep, rg, find, ls, wc, "
            "head, tail, cat, sort, uniq, xargs, file, echo. "
            "ALWAYS use double quotes for patterns. "
            "Example: grep -rilZ \"asana\" . | xargs -0 grep -il \"Q3\""
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
            "PATTERN MATCH: Also provide a `command` parameter with a grep command for "
            "exact-match pattern search on local record files. This complements semantic "
            "search by catching records that contain specific terms verbatim.\n"
            "Example: command='grep -rilZ \"asana\" . | xargs -0 grep -il \"Q3\"'\n"
            "COMMAND RULES: Use double quotes for patterns, allowed binaries only "
            "(grep, egrep, fgrep, rg, find, ls, wc, head, tail, cat, sort, uniq, "
            "xargs, file, echo), no shell operators except pipe."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "Any topic, keyword, concept, name, or phrase — even a single bare word",
            "Information / documentation requests ('what is X', 'how does Y work', 'tell me about Z')",
            "Policy / procedure / general knowledge questions",
            "ALWAYS in parallel with a service search tool when one is configured for the same topic",
            "When the query asks about a person, entity, or topic that is NOT present in the attached documents** — do NOT refuse; search the internal knowledge base instead."
        ],
        when_not_to_use=[
            "Exact ID lookup ('get page 12345') — use the service tool directly",
            "Write actions (create / update / delete) — use the service tool",
            "Real-time-only data ('my unread mail right now', 'today's calendar') — use the service tool",
            "Pure greetings, thanks, or arithmetic",
            "ONLY when the attachment content fully and directly answers the query for the **exact same** person, entity, or topic being asked about — do not call this tool unnecessarily."
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
        command: str | None = None,
    ) -> str:
        """Search internal knowledge bases and return formatted results.

        Orchestrates: filter building → parallel search (semantic + pattern match)
        → permission filtering → flatten → dedup & fetch → trim → state accumulation → format.
        """
        if not query:
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
            logger_instance.info(f"🔍 Retrieval tool called with query: {query[:100]}")

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

            connector_ids = _normalize_list_param(connector_ids)
            collection_ids = _normalize_list_param(collection_ids)

            filter_groups, adjusted_limit = self._build_filter_groups(
                connector_ids=connector_ids,
                collection_ids=collection_ids,
                logger_instance=logger_instance,
            )

            connector_ids_in_scope = filter_groups.get("apps", [])

            semantic_response, raw_pattern_records = (
                await self._execute_parallel_search(
                    search_query=query,
                    filter_groups=filter_groups,
                    adjusted_limit=adjusted_limit,
                    command=command,
                    connector_ids_in_scope=connector_ids_in_scope,
                    retrieval_service=retrieval_service,
                    logger_instance=logger_instance,
                )
            )

            if semantic_response is None:
                logger_instance.warning("Retrieval service returned None")
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval service returned no results"
                })

            status_code = semantic_response.get("status_code", 200)
            if status_code in [202, 500, 503]:
                return json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": semantic_response.get("message", "Retrieval service unavailable"),
                })

            search_results = semantic_response.get("searchResults", [])
            logger_instance.info(f"Semantic search: {len(search_results)} results")

            if not search_results and not raw_pattern_records:
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
            is_multimodal_llm = self._detect_multimodal_llm()

            final_results, virtual_record_id_to_result = await self._flatten_semantic_results(
                search_results=search_results,
                semantic_response=semantic_response,
                blob_store=blob_store,
                org_id=org_id,
                is_multimodal_llm=is_multimodal_llm,
                graph_provider=graph_provider,
                logger_instance=logger_instance,
            )

            # Semantic results get their own budget
            # final_results = final_results[:adjusted_limit]

            if raw_pattern_records:
                pm_blocks = await self._merge_pattern_match_blocks(
                    raw_pattern_records=raw_pattern_records,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    user_id=user_id,
                    org_id=org_id,
                    blob_store=blob_store,
                    graph_provider=graph_provider,
                    is_multimodal_llm=is_multimodal_llm,
                    logger_instance=logger_instance,
                )

                # PM gets its own budget, distributed proportionally across records
                if len(pm_blocks) > adjusted_limit:
                    pm_by_record: dict[str, list[dict]] = {}
                    for block in pm_blocks:
                        vrid = block.get("virtual_record_id", "")
                        pm_by_record.setdefault(vrid, []).append(block)

                    total_pm = len(pm_blocks)
                    budget_left = adjusted_limit
                    blocks_left = total_pm

                    distributed: list[dict] = []
                    for vrid, blocks in pm_by_record.items():
                        if budget_left <= 0:
                            break
                        share = max(1, round(len(blocks) / blocks_left * budget_left))
                        take = min(share, len(blocks), budget_left)
                        distributed.extend(blocks[:take])
                        budget_left -= take
                        blocks_left -= len(blocks)

                    logger_instance.info(
                        "PM trimmed %d → %d (distributed across %d records)",
                        len(pm_blocks), len(distributed), len(pm_by_record),
                    )

                    surviving_vrids = {
                        b.get("virtual_record_id") for b in distributed
                        if b.get("virtual_record_id")
                    }
                    orphan_vrids = set(pm_by_record.keys()) - surviving_vrids
                    if orphan_vrids:
                        for vrid in orphan_vrids:
                            virtual_record_id_to_result.pop(vrid, None)
                        logger_instance.info(
                            "Pruned %d orphaned PM records from vrid map",
                            len(orphan_vrids),
                        )

                    final_results.extend(distributed)
                else:
                    final_results.extend(pm_blocks)

            self._accumulate_state(
                final_results=final_results,
                virtual_record_id_to_result=virtual_record_id_to_result,
            )

            return self._format_tool_response(
                final_results=final_results,
                virtual_record_id_to_result=virtual_record_id_to_result,
                is_multimodal_llm=is_multimodal_llm,
            )

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })

    # ------------------------------------------------------------------
    # Filter construction
    # ------------------------------------------------------------------

    def _build_filter_groups(
        self,
        *,
        connector_ids: list[str] | None,
        collection_ids: list[str] | None,
        logger_instance: logging.Logger,
    ) -> tuple[dict[str, list[str]], int]:
        """Build retrieval filter_groups from agent scope and caller params.

        Returns (filter_groups, adjusted_limit).
        """
        agent_filters = self.state.get("filters", {}) or {}
        agent_filter_apps = set(agent_filters.get("apps") or [])
        agent_filter_kbs = set(agent_filters.get("kb") or [])

        is_placeholder_agent = self.state.get("is_placeholder_agent", False)
        if is_placeholder_agent:
            agent_filter_apps = list(self.state.get("apps", []) or [])
            agent_filter_kbs = list(self.state.get("kb", []) or [])

        total_sources = len(agent_filter_apps) + len(agent_filter_kbs)
        if total_sources <= 1:
            adjusted_limit = 50
        else:
            adjusted_limit = 100 // min(total_sources, _MAX_RETRIEVAL_SOURCES_DIVISOR)

        explicit_connectors = bool(connector_ids)
        explicit_collections = bool(collection_ids)
        broad_search = not explicit_connectors and not explicit_collections

        filter_groups: dict[str, list[str]] = {}

        if explicit_connectors:
            resolved = [cid for cid in connector_ids if cid in agent_filter_apps]
            filter_groups["apps"] = resolved if resolved else list(agent_filter_apps)
        elif broad_search:
            filter_groups["apps"] = list(agent_filter_apps) if agent_filter_apps else []
        else:
            filter_groups["apps"] = []

        if explicit_collections:
            resolved = [cid for cid in collection_ids if cid in agent_filter_kbs]
            filter_groups["kb"] = resolved if resolved else list(agent_filter_kbs)
        elif broad_search:
            filter_groups["kb"] = list(agent_filter_kbs) if agent_filter_kbs else []
        else:
            filter_groups["kb"] = ['NO_KB_SELECTED']
            if is_placeholder_agent:
                filter_groups["kb"] = []

        logger_instance.debug("filter_groups: %s, adjusted_limit: %d", filter_groups, adjusted_limit)
        return filter_groups, adjusted_limit

    # ------------------------------------------------------------------
    # Parallel search execution
    # ------------------------------------------------------------------

    async def _execute_parallel_search(
        self,
        *,
        search_query: str,
        filter_groups: dict[str, list[str]],
        adjusted_limit: int,
        command: str | None,
        connector_ids_in_scope: list[str],
        retrieval_service: Any,
        logger_instance: logging.Logger,
    ) -> tuple[dict | None, list[dict]]:
        """Run semantic search and pattern match grep in parallel.

        Permission checking for pattern match results is done later via a
        targeted check_vrids_accessible call — much cheaper than the broad
        get_accessible_virtual_record_ids scan.

        Returns (semantic_response, raw_pattern_records).
        Raises if semantic search fails.
        """
        # Test flags: set via env to disable either search path independently.
        # DISABLE_SEMANTIC_SEARCH=1  → skip semantic, return empty stub
        # DISABLE_STORAGE_PATTERN=1  → skip pattern match even if command given
        disable_semantic = os.getenv("DISABLE_SEMANTIC_SEARCH", "false").strip().lower() == "true"
        disable_pattern = os.getenv("DISABLE_STORAGE_PATTERN", "false").strip().lower() == "true"

        pm_command_valid = False
        if command and not disable_pattern:
            pm_command_valid, pm_err = _validate_command(command)
            if not pm_command_valid:
                logger_instance.warning("Pattern match command rejected: %s", pm_err)

        # Only read storage config when we'd otherwise fan out pattern-match
        # calls per connector -- avoids a wasted config read when the command
        # was invalid or no connector is in scope anyway. Fails closed (treats
        # unreadable config as non-local) so a config error can't cause N
        # wasted per-connector calls downstream.
        pattern_storage_is_local = True
        if pm_command_valid and connector_ids_in_scope:
            config_service = self.state.get("config_service")
            try:
                storage_cfg = await config_service.get_config(
                    config_node_constants.STORAGE.value
                )
                pattern_storage_is_local = is_local_storage(storage_cfg)
            except Exception as exc:
                logger_instance.warning(
                    "Could not read storage config for pattern match gate, "
                    "skipping pattern match: %s", exc,
                )
                pattern_storage_is_local = False

            if not pattern_storage_is_local:
                logger_instance.info(
                    "Pattern match skipped: storage backend is not local (org=%s)",
                    self.state.get("org_id", ""),
                )

        run_semantic = not disable_semantic
        run_pattern_match = (
            pm_command_valid
            and bool(connector_ids_in_scope)
            and pattern_storage_is_local
        )

        parallel_tasks: list = []
        semantic_task_idx: int | None = None
        pm_task_idx: int | None = None

        if run_semantic:
            semantic_task_idx = len(parallel_tasks)
            parallel_tasks.append(
                retrieval_service.search_with_filters(
                    queries=[search_query],
                    org_id=self.state.get("org_id", ""),
                    user_id=self.state.get("user_id", ""),
                    limit=adjusted_limit,
                    filter_groups=filter_groups,
                ),
            )

        if run_pattern_match:
            pm_task_idx = len(parallel_tasks)
            parallel_tasks.append(
                self._run_pattern_match(
                    command=command,
                    connector_ids_in_scope=connector_ids_in_scope,
                    logger_instance=logger_instance,
                )
            )

        if parallel_tasks:
            parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
        else:
            parallel_results = []

        semantic_response: dict | None = None
        if semantic_task_idx is not None:
            result = parallel_results[semantic_task_idx]
            if isinstance(result, Exception):
                raise result
            semantic_response = result
        elif disable_semantic:
            logger_instance.info("Semantic search disabled via DISABLE_SEMANTIC_SEARCH")
            semantic_response = {"status_code": 200, "searchResults": []}

        raw_pattern_records: list[dict] = []
        if pm_task_idx is not None:
            pm_result = parallel_results[pm_task_idx]
            if isinstance(pm_result, Exception):
                logger_instance.error("Pattern match failed: %s", pm_result, exc_info=True)
            else:
                raw_pattern_records = pm_result
        elif disable_pattern and command:
            logger_instance.info("Storage pattern match disabled via DISABLE_STORAGE_PATTERN")

        return semantic_response, raw_pattern_records

    # ------------------------------------------------------------------
    # Semantic result flattening
    # ------------------------------------------------------------------

    async def _flatten_semantic_results(
        self,
        *,
        search_results: list[dict],
        semantic_response: dict,
        blob_store: "BlobStorage",
        org_id: str,
        is_multimodal_llm: bool,
        graph_provider: Any,
        logger_instance: logging.Logger,
    ) -> tuple[list[dict], dict[str, dict]]:
        """Flatten semantic search results and fetch blob content.

        Returns (final_results, virtual_record_id_to_result).
        """
        virtual_record_id_to_result: dict[str, dict] = {}
        virtual_to_record_map = semantic_response.get("virtual_to_record_map", {})

        flattened_results = await get_flattened_results(
            search_results,
            blob_store,
            org_id,
            is_multimodal_llm,
            virtual_record_id_to_result,
            virtual_to_record_map,
            graph_provider=graph_provider,
        )
        logger_instance.info("Semantic search: %d flattened results", len(flattened_results))

        final_results = search_results if not flattened_results else flattened_results
        return final_results, virtual_record_id_to_result

    # ------------------------------------------------------------------
    # Pattern match: dedup, fetch, and block extraction
    # ------------------------------------------------------------------

    async def _merge_pattern_match_blocks(
        self,
        *,
        raw_pattern_records: list[dict],
        virtual_record_id_to_result: dict[str, dict],
        user_id: str,
        org_id: str,
        blob_store: "BlobStorage",
        graph_provider: Any,
        is_multimodal_llm: bool,
        logger_instance: logging.Logger,
    ) -> list[dict]:
        """Full pattern match pipeline: dedup → targeted permission check → fetch → flatten.

        Instead of scanning all accessible records for the connector (broad),
        this checks only the specific vrids returned by grep — orders of
        magnitude cheaper for large connectors.

        Returns enriched block entries to append to final_results, processed
        through the same get_flattened_results pipeline as semantic search.
        """
        # Step 1: Dedup by vrid (same record can appear across connectors)
        seen_vrids: set[str] = set()
        unique_records: list[dict] = []
        for r in raw_pattern_records:
            vrid = r.get("virtual_record_id")
            if vrid and vrid not in seen_vrids:
                seen_vrids.add(vrid)
                unique_records.append(r)

        # Step 2: Remove records already in semantic results (already accessible + fetched)
        semantic_vrids = set(virtual_record_id_to_result.keys())
        new_records = [
            r for r in unique_records
            if r.get("virtual_record_id") not in semantic_vrids
        ][:_MAX_PATTERN_MATCH_RECORDS]

        if not new_records:
            return []

        # Step 3: Targeted permission check for just these vrids
        check_vrids = [r["virtual_record_id"] for r in new_records]
        accessible_vrids = await graph_provider.check_vrids_accessible(
            user_id=user_id,
            org_id=org_id,
            virtual_record_ids=check_vrids,
        )

        if not accessible_vrids:
            logger_instance.info(
                "Pattern match: %d records checked, none accessible",
                len(new_records),
            )
            return []

        accessible_records = [
            r for r in new_records
            if r.get("virtual_record_id") in accessible_vrids
        ]
        logger_instance.info(
            "Pattern match: %d accessible of %d checked (%d raw, %d unique)",
            len(accessible_records), len(new_records),
            len(raw_pattern_records), len(unique_records),
        )

        # Step 4: Fetch blob content for accessible records
        frontend_url = await self._get_frontend_url(blob_store)

        fetch_tasks = []
        for rec in accessible_records:
            vrid = rec["virtual_record_id"]
            record_id = accessible_vrids[vrid]
            fetch_tasks.append(self._fetch_pattern_record(
                vrid=vrid,
                record_id=record_id,
                virtual_record_id_to_result=virtual_record_id_to_result,
                blob_store=blob_store,
                org_id=org_id,
                graph_provider=graph_provider,
                frontend_url=frontend_url,
                logger_instance=logger_instance,
            ))
        await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Step 5: Build synthetic search results and flatten through the same
        # pipeline as semantic search (get_flattened_results) — ensures identical
        # block enrichment, table grouping, fragment handling, and citation format
        synthetic_results = self._build_synthetic_search_results(
            accessible_records, virtual_record_id_to_result, org_id, logger_instance,
        )
        if not synthetic_results:
            return []

        flattened = await get_flattened_results(
            synthetic_results,
            blob_store,
            org_id,
            is_multimodal_llm,
            virtual_record_id_to_result,
            graph_provider=graph_provider,
        )
        logger_instance.info("Pattern match: %d flattened blocks", len(flattened))
        return flattened if flattened else []

    @staticmethod
    def _build_synthetic_search_results(
        records: list[dict],
        virtual_record_id_to_result: dict[str, dict],
        org_id: str,
        logger_instance: logging.Logger,
    ) -> list[dict]:
        """Create synthetic vector-DB-format search results from PM records.

        These are fed into get_flattened_results so PM blocks go through
        the exact same enrichment pipeline as semantic search blocks.
        """
        results: list[dict] = []
        for rec in records:
            vrid = rec["virtual_record_id"]
            record = virtual_record_id_to_result.get(vrid)
            if not record:
                continue
            block_containers = record.get("block_containers", {})
            blocks = block_containers.get("blocks", [])

            if not blocks:
                results.append({
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": 0,
                        "isBlockGroup": False,
                    },
                    "score": 0.0,
                })
                continue

            for idx in range(len(blocks)):
                results.append({
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": idx,
                        "isBlockGroup": False,
                        "isBlock": True,
                        "orgId": org_id,
                    },
                    "score": 0.0,
                })

        logger_instance.info("Pattern match: %d synthetic search results", len(results))
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_multimodal_llm(self) -> bool:
        """Check if the configured LLM supports multimodal (image) content."""
        try:
            llm_config = self.state.get("llm")
            if hasattr(llm_config, "model_name"):
                model_name = str(llm_config.model_name).lower()
                return any(m in model_name for m in [
                    "gpt-4-vision", "gpt-4o", "claude-3", "gemini-pro-vision",
                ])
        except Exception:
            pass
        return False

    @staticmethod
    async def _get_frontend_url(blob_store: "BlobStorage") -> str | None:
        """Resolve the frontend public URL from config service."""
        try:
            endpoints_config = await blob_store.config_service.get_config(
                config_node_constants.ENDPOINTS.value, default={},
            )
            if isinstance(endpoints_config, dict):
                return endpoints_config.get("frontend", {}).get("publicEndpoint")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # State accumulation
    # ------------------------------------------------------------------

    def _accumulate_state(
        self,
        *,
        final_results: list[dict],
        virtual_record_id_to_result: dict[str, dict],
    ) -> None:
        """Write results to state for the downstream citation pipeline.

        Accumulates into final_results, virtual_record_id_to_result, and
        tool_records — merging with any results from parallel retrieval calls.
        """
        existing_final = self.state.get("final_results", [])
        if not isinstance(existing_final, list):
            existing_final = []
        self.state["final_results"] = existing_final + final_results

        existing_vmap = self.state.get("virtual_record_id_to_result", {})
        if not isinstance(existing_vmap, dict):
            existing_vmap = {}
        self.state["virtual_record_id_to_result"] = {**existing_vmap, **virtual_record_id_to_result}

        existing_tool_records = self.state.get("tool_records", [])
        if not isinstance(existing_tool_records, list):
            existing_tool_records = []
        new_tool_records = list(virtual_record_id_to_result.values())
        existing_ids = {r.get("_id") for r in existing_tool_records if isinstance(r, dict) and "_id" in r}
        unique_new = [r for r in new_tool_records if not (isinstance(r, dict) and r.get("_id") in existing_ids)]
        self.state["tool_records"] = existing_tool_records + unique_new

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def _format_tool_response(
        self,
        *,
        final_results: list[dict],
        virtual_record_id_to_result: dict[str, dict],
        is_multimodal_llm: bool,
    ) -> str:
        """Format results as XML <record> blocks for the LLM tool message.

        Block numbering (R-labels) happens once after all parallel calls are
        merged in merge_and_number_retrieval_results().
        """
        sorted_results = sorted(
            final_results,
            key=lambda x: (
                x.get("virtual_record_id") or "",
                -1 if x.get("block_index") is None else x.get("block_index"),
            ),
        )

        ref_mapper = self.state.get("citation_ref_mapper") or CitationRefMapper()
        message_content_array, ref_mapper = build_message_content_array(
            sorted_results, virtual_record_id_to_result,
            is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper, from_tool=True,
        )
        self.state["citation_ref_mapper"] = ref_mapper

        formatted_records = []
        for content in message_content_array:
            text_parts = [item["text"] for item in content if item["type"] == "text"]
            formatted_records.append("".join(text_parts))

        logger_instance = self.state.get("logger", logger)
        logger_instance.info(
            "Retrieved %d blocks from %d documents",
            len(final_results), len(virtual_record_id_to_result),
        )

        summary = (
            f"Retrieved {len(final_results)} knowledge blocks from "
            f"{len(virtual_record_id_to_result)} documents.\n\n"
        )
        return summary + "\n".join(formatted_records)

    # ------------------------------------------------------------------
    # Pattern match: grep execution
    # ------------------------------------------------------------------

    async def _run_pattern_match(
        self,
        *,
        command: str,
        connector_ids_in_scope: list[str],
        logger_instance: logging.Logger,
    ) -> list[dict]:
        """Run grep pattern match across connectors and return raw (unfiltered) records.

        Permission filtering is done by the caller using a separate
        apps-only permission map so that metadata filters don't incorrectly
        exclude accessible records.
        """
        if not connector_ids_in_scope or not self.state:
            return []

        storage_tool = StoragePatternMatch(self.state)

        async def _search_connector(connector_id: str) -> list[dict]:
            success, output = await storage_tool.find_records(
                connector_id=connector_id,
                command=command,
                max_results=10,
            )
            if not success:
                logger_instance.debug("Pattern match failed for connector %s: %s", connector_id, output)
                return []

            try:
                parsed = json.loads(output)
            except (json.JSONDecodeError, TypeError):
                return []

            records = parsed.get("records", [])
            logger_instance.debug(
                "Pattern match connector=%s: %d found", connector_id, len(records),
            )
            return records

        tasks = [_search_connector(cid) for cid in connector_ids_in_scope]
        try:
            results_per_connector = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_PATTERN_MATCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger_instance.warning("Pattern match timed out after %ds", _PATTERN_MATCH_TIMEOUT)
            return []

        all_records: list[dict] = []
        for result in results_per_connector:
            if isinstance(result, Exception):
                logger_instance.warning("Pattern match connector error: %s", result)
                continue
            all_records.extend(result)

        return all_records

    # ------------------------------------------------------------------
    # Pattern match: single record fetch
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch_pattern_record(
        *,
        vrid: str,
        record_id: str,
        virtual_record_id_to_result: dict,
        blob_store: "BlobStorage",
        org_id: str,
        graph_provider: Any,
        frontend_url: str | None,
        logger_instance: logging.Logger,
    ) -> None:
        """Fetch blob content for a single pattern match record into virtual_record_id_to_result."""
        try:
            graph_record = await graph_provider.get_document(
                document_key=record_id,
                collection=CollectionNames.RECORDS.value,
            )
            if not graph_record:
                logger_instance.debug("Pattern match record %s: graph document not found for record_id=%s", vrid, record_id)
                return
            await get_record(
                vrid,
                virtual_record_id_to_result,
                blob_store,
                org_id,
                {vrid: graph_record},
                graph_provider,
                frontend_url,
            )
        except Exception as e:
            logger_instance.warning("Failed to fetch pattern match record %s: %s", vrid, e)
