"""Shared pattern-match helpers for internal search paths (chatbot + retrieval)."""

import asyncio
import json
import logging
import re
from typing import Any

from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch,
    _validate_command,
    is_local_storage,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.chat_helpers import get_flattened_results, get_record

_PATTERN_MATCH_TIMEOUT = 15
_MAX_PATTERN_MATCH_RECORDS = 5

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she", "it",
    "they", "them", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "and", "or", "but", "not", "no", "nor", "for", "to", "of", "in",
    "on", "at", "by", "with", "from", "about", "into", "through",
    "if", "then", "so", "as", "than", "also", "just", "any", "all",
    "some", "each", "every", "very", "much", "more", "most",
    "tell", "show", "find", "get", "give", "know", "think", "see",
    "look", "want", "say", "make", "go", "take",
})


def build_grep_command_from_query(query: str) -> str | None:
    """Extract keywords from a user query and build a grep command.

    Returns None if no meaningful keywords (>= 3 chars, not stop words)
    can be extracted.
    """
    words = re.findall(r"[a-zA-Z0-9_-]+", query.lower())
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]
    if not keywords:
        return None
    keywords = keywords[:5]
    pattern = r"\|".join(keywords)
    return f'grep -rli "{pattern}" .'


async def check_pattern_match_eligible(
    config_service: ConfigurationService,
    logger_instance: logging.Logger,
) -> bool:
    """Return True when storage is local (pattern match only works on local)."""
    try:
        storage_cfg = await config_service.get_config(
            config_node_constants.STORAGE.value
        )
        return is_local_storage(storage_cfg)
    except Exception as exc:
        logger_instance.warning(
            "Could not read storage config for pattern match: %s", exc
        )
        return False


async def resolve_connector_ids_for_search(
    graph_provider: IGraphDBProvider,
    org_id: str,
    filters: dict[str, Any] | None,
) -> list[str]:
    """Resolve connector IDs for pattern match fan-out.

    - filters["apps"] present → use those connector IDs directly.
    - No filters (chatbot "search all" mode) → get all org app IDs.
    - Only filters["kb"] present → empty (record-group IDs can't be used for PM).
    """
    if filters:
        app_ids = filters.get("apps")
        if app_ids:
            return list(app_ids)
    try:
        org_apps = await graph_provider.get_org_apps(org_id)
        return [app["_key"] for app in org_apps if app.get("_key")]
    except Exception:
        return []


async def execute_pattern_match_pipeline(
    *,
    query: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    logger_instance: logging.Logger,
) -> list[dict]:
    """Full pipeline: build grep → eligibility check → resolve connectors → run.

    Designed to be fired in parallel with semantic search via asyncio.gather.
    Returns raw (unfiltered) pattern match records; caller must merge/permission-check.
    """
    grep_command = build_grep_command_from_query(query)
    if not grep_command:
        return []
    if not await check_pattern_match_eligible(config_service, logger_instance):
        return []
    connector_ids = await resolve_connector_ids_for_search(
        graph_provider, org_id, filters
    )
    if not connector_ids:
        return []
    return await run_pattern_match(
        config_service=config_service,
        org_id=org_id,
        user_id=user_id,
        graph_provider=graph_provider,
        command=grep_command,
        connector_ids=connector_ids,
        logger_instance=logger_instance,
    )


async def run_pattern_match(
    *,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    command: str,
    connector_ids: list[str],
    logger_instance: logging.Logger,
    timeout: int = _PATTERN_MATCH_TIMEOUT,
) -> list[dict]:
    """Run grep pattern match across connectors. Returns raw unfiltered records."""
    if not connector_ids or not command:
        return []

    valid, err = _validate_command(command)
    if not valid:
        logger_instance.warning("Pattern match command rejected: %s", err)
        return []

    state: dict[str, Any] = {
        "config_service": config_service,
        "org_id": org_id,
        "user_id": user_id,
        "graph_provider": graph_provider,
    }
    storage_tool = StoragePatternMatch(state)

    async def _search_connector(connector_id: str) -> list[dict]:
        success, output = await storage_tool.find_records(
            connector_id=connector_id,
            command=command,
            max_results=10,
        )
        if not success:
            return []
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed.get("records", [])

    tasks = [_search_connector(cid) for cid in connector_ids]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger_instance.warning("Pattern match timed out after %ds", timeout)
        return []

    all_records: list[dict] = []
    for result in results:
        if isinstance(result, Exception):
            logger_instance.debug("Pattern match connector error: %s", result)
            continue
        all_records.extend(result)
    return all_records


async def merge_pattern_match_results(
    *,
    raw_records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    user_id: str,
    org_id: str,
    blob_store: Any,
    graph_provider: IGraphDBProvider,
    is_multimodal_llm: bool,
    logger_instance: logging.Logger,
    max_records: int = _MAX_PATTERN_MATCH_RECORDS,
) -> list[dict]:
    """Dedup → permission check → fetch blob → flatten.

    Returns enriched block entries compatible with final_results.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for r in raw_records:
        vrid = r.get("virtual_record_id")
        if vrid and vrid not in seen:
            seen.add(vrid)
            unique.append(r)

    new_records = [
        r
        for r in unique
        if r.get("virtual_record_id") not in virtual_record_id_to_result
    ][:max_records]

    if not new_records:
        return []

    check_vrids = [r["virtual_record_id"] for r in new_records]
    accessible_vrids = await graph_provider.check_vrids_accessible(
        user_id=user_id,
        org_id=org_id,
        virtual_record_ids=check_vrids,
    )
    if not accessible_vrids:
        logger_instance.info(
            "Pattern match: %d records checked, none accessible", len(new_records)
        )
        return []

    accessible_records = [
        r for r in new_records if r.get("virtual_record_id") in accessible_vrids
    ]
    logger_instance.info(
        "Pattern match: %d accessible of %d checked (%d raw, %d unique)",
        len(accessible_records),
        len(new_records),
        len(raw_records),
        len(unique),
    )

    frontend_url = await _get_frontend_url(blob_store)

    fetch_tasks = []
    for rec in accessible_records:
        vrid = rec["virtual_record_id"]
        record_id = accessible_vrids[vrid]
        fetch_tasks.append(
            _fetch_pattern_record(
                vrid=vrid,
                record_id=record_id,
                virtual_record_id_to_result=virtual_record_id_to_result,
                blob_store=blob_store,
                org_id=org_id,
                graph_provider=graph_provider,
                frontend_url=frontend_url,
                logger_instance=logger_instance,
            )
        )
    await asyncio.gather(*fetch_tasks, return_exceptions=True)

    synthetic = _build_synthetic_search_results(
        accessible_records, virtual_record_id_to_result, org_id, logger_instance
    )
    if not synthetic:
        return []

    flattened = await get_flattened_results(
        synthetic,
        blob_store,
        org_id,
        is_multimodal_llm,
        virtual_record_id_to_result,
        graph_provider=graph_provider,
    )
    return flattened if flattened else []


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _get_frontend_url(blob_store: Any) -> str | None:
    try:
        endpoints_config = await blob_store.config_service.get_config(
            config_node_constants.ENDPOINTS.value,
            default={},
        )
        if isinstance(endpoints_config, dict):
            return endpoints_config.get("frontend", {}).get("publicEndpoint")
    except Exception:
        pass
    return None


async def _fetch_pattern_record(
    *,
    vrid: str,
    record_id: str,
    virtual_record_id_to_result: dict,
    blob_store: Any,
    org_id: str,
    graph_provider: Any,
    frontend_url: str | None,
    logger_instance: logging.Logger,
) -> None:
    try:
        graph_record = await graph_provider.get_document(
            document_key=record_id,
            collection=CollectionNames.RECORDS.value,
        )
        if not graph_record:
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
        logger_instance.warning("Failed to fetch PM record %s: %s", vrid, e)


def _build_synthetic_search_results(
    records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    org_id: str,
    logger_instance: logging.Logger,
) -> list[dict]:
    results: list[dict] = []
    for rec in records:
        vrid = rec["virtual_record_id"]
        record = virtual_record_id_to_result.get(vrid)
        if not record:
            continue
        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        if not blocks:
            results.append(
                {
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": 0,
                        "isBlockGroup": False,
                    },
                    "score": 0.0,
                }
            )
            continue
        for idx in range(len(blocks)):
            results.append(
                {
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": idx,
                        "isBlockGroup": False,
                        "isBlock": True,
                        "orgId": org_id,
                    },
                    "score": 0.0,
                }
            )
    logger_instance.info("Pattern match: %d synthetic search results", len(results))
    return results
