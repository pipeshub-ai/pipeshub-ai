"""Fetch full Slack thread tool for the chatbot agent.

Given a single Slack record id from the conversation context (a thread-burst
record, a thread-parent channel message, etc.), this tool resolves the
SLACK_THREAD RecordGroup the record belongs to and returns every record that
BELONGS_TO that group (thread-burst MessageRecords + dependent FileRecords),
shaped exactly like records produced by the normal retrieval flow in
`chat_helpers.py` so downstream tools (`fetch_full_record`, etc.) can reuse
them via the shared `virtual_record_id_to_result` map.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.utils.fetch_full_record import _fetch_record_by_id
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = create_logger("fetch_slack_thread")


_SLACK_CONNECTOR_TYPES = {
    Connectors.SLACK.value,
    Connectors.SLACK_WORKSPACE.value,
}

_SLACK_THREAD_GROUP_TYPE = "SLACK_THREAD"
_SLACK_CHANNEL_GROUP_TYPE = "SLACK_CHANNEL"


async def has_slack_connector_configured(
    graph_provider: "IGraphDBProvider",
    user_id: str,
    org_id: str,
) -> bool:
    """Return True if the user/org has any configured Slack connector instance."""
    try:
        instances = await graph_provider.get_user_connector_instances(
            collection=CollectionNames.APPS.value,
            user_id=user_id,
            org_id=org_id,
            team_scope=ConnectorScope.TEAM.value,
            personal_scope=ConnectorScope.PERSONAL.value,
        )
        return any(
            str(i.get("type", "")).upper() in _SLACK_CONNECTOR_TYPES and bool(i.get("isConfigured"))
            for i in (instances or [])
        )
    except Exception as e:
        logger.warning(f"Slack connector check failed: {e}")
        return False


def agent_knowledge_has_slack_connector(agent_knowledge: Optional[List[Dict[str, Any]]]) -> bool:
    """Return True if the agent's attached knowledge includes a Slack connector.

    The default agent is synthesized with every user connector attached, so this
    is naturally True whenever the org has a Slack connector. Custom agents only
    return True when the user explicitly attached a Slack connector as knowledge.
    """
    if not agent_knowledge:
        return False
    return any(
        isinstance(k, dict) and str(k.get("type", "")).upper() in _SLACK_CONNECTOR_TYPES
        for k in agent_knowledge
    )


class FetchSlackThreadArgs(BaseModel):
    """Required tool args for fetching a full Slack thread."""

    record_id: str = Field(
        ...,
        description=(
            "The Record ID of any Slack record currently in your context — typically a "
            "thread-burst record (its metadata shows recordGroupType='SLACK_THREAD'), or a "
            "channel-level Slack message that is the parent of a thread. Use the exact "
            "'Record ID :' value from the context — do NOT invent or guess."
        ),
    )
    reason: str = Field(
        default="Fetching full Slack thread to see all replies in context",
        description="Brief explanation of why the full thread is needed.",
    )


def _model_to_dict(record: Any) -> Dict[str, Any]:
    """Serialize a Record / RecordGroup model (or pass-through dict) to a JSON-safe dict."""
    if record is None:
        return {}
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        try:
            return record.model_dump(mode="json")
        except Exception:
            return record.model_dump()
    return dict(record)


async def _resolve_thread_record_group(
    record_id: str,
    graph_provider: "IGraphDBProvider",
) -> Optional[Dict[str, Any]]:
    """Resolve a Slack record id to its SLACK_THREAD RecordGroup id + connector_id.

    Two cases the caller might hand us:

    1. The record is already inside a SLACK_THREAD RG (thread-burst / file inside
       a thread). We just read `record_group_id` straight off the record.
    2. The record is a channel-level message that happens to be a thread parent
       (record_group_type=SLACK_CHANNEL, is_thread_parent=True). We derive the
       thread external id `thread_<channel>_<ts>` and look up the RG.

    Anything else (channel burst, non-parent single, non-Slack record) returns None
    — there is no thread to expand.
    """
    try:
        record = await graph_provider.get_record_by_id(record_id)
    except Exception as e:
        logger.warning(f"Failed to fetch record {record_id} from graph: {e}")
        return None

    if not record:
        return None

    meta = _model_to_dict(record)
    rg_type = meta.get("record_group_type")
    rg_type_str = rg_type if isinstance(rg_type, str) else getattr(rg_type, "value", None)

    connector_id = meta.get("connector_id") or ""
    org_id = meta.get("org_id") or ""

    # Case 1: already inside a SLACK_THREAD record group
    if rg_type_str == _SLACK_THREAD_GROUP_TYPE:
        rg_id = meta.get("record_group_id")
        if rg_id:
            return {
                "record_group_id": rg_id,
                "external_record_group_id": meta.get("external_record_group_id"),
                "connector_id": connector_id,
                "org_id": org_id,
            }

    # Case 2: channel-level thread parent — look up the thread RG by external id
    if rg_type_str == _SLACK_CHANNEL_GROUP_TYPE:
        is_parent = bool(meta.get("is_thread_parent"))
        thread_ts = meta.get("thread_id") or meta.get("thread_ts") or meta.get("external_record_id")
        channel_id = meta.get("external_record_group_id")
        if is_parent and thread_ts and channel_id and connector_id:
            ext_thread_id = f"thread_{channel_id}_{thread_ts}"
            try:
                thread_rg = await graph_provider.get_record_group_by_external_id(
                    connector_id=connector_id,
                    external_id=ext_thread_id,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to look up thread RG external_id={ext_thread_id}: {e}"
                )
                return None
            if thread_rg:
                rg_dict = _model_to_dict(thread_rg)
                rg_id = rg_dict.get("id") or rg_dict.get("_key")
                if rg_id:
                    return {
                        "record_group_id": rg_id,
                        "external_record_group_id": ext_thread_id,
                        "connector_id": connector_id,
                        "org_id": org_id,
                    }

    return None


async def _fetch_thread_records_impl(
    record_id: str,
    virtual_record_id_to_result: Dict[str, Any],
    graph_provider: Optional["IGraphDBProvider"] = None,
    blob_store: Optional["BlobStorage"] = None,
    org_id: Optional[str] = None,
    config_service: Optional["ConfigurationService"] = None,
) -> Dict[str, Any]:
    """Resolve the thread RG for the given record and return every record in it.

    Returned records are built through the same `chat_helpers.get_record` pipeline
    used for first-pass retrieval, so their `block_containers`, `context_metadata`
    and graph-derived fields match what other tools / the LLM already see.

    ``blob_store`` may be omitted on deep-agent runs where tools are built before
    retrieval mutates state; when ``config_service`` and ``graph_provider`` are set,
    a ``BlobStorage`` instance is created on demand (same pattern as ``execute_query``).
    ``org_id`` may be taken from the resolved Slack record when state has no org.
    """
    if not graph_provider:
        return {
            "ok": False,
            "error": "Slack thread tool requires graph_provider (graph DB).",
        }

    resolved = await _resolve_thread_record_group(record_id, graph_provider)
    if not resolved:
        return {
            "ok": False,
            "error": (
                f"Record '{record_id}' is not part of a Slack thread "
                f"(no SLACK_THREAD record group found). Pass a thread-burst record id, "
                f"or a channel message that is itself a thread parent."
            ),
        }

    effective_org = (org_id or "").strip() or (resolved.get("org_id") or "").strip()
    if not effective_org:
        return {
            "ok": False,
            "error": (
                "Slack thread tool could not determine org_id. "
                "Ensure chat state includes org_id or the Slack record includes org metadata."
            ),
        }

    effective_blob = blob_store
    if effective_blob is None and config_service is not None:
        try:
            from app.modules.transformers.blob_storage import BlobStorage

            effective_blob = BlobStorage(
                logger=logger,
                config_service=config_service,
                graph_provider=graph_provider,
            )
        except Exception as e:
            logger.warning("Slack thread tool: could not initialise BlobStorage: %s", e)

    if effective_blob is None:
        return {
            "ok": False,
            "error": (
                "Slack thread tool requires blob_store, or config_service to build one. "
                "Pass config_service when creating the tool (see execute_query / agent tool_system)."
            ),
        }

    thread_rg_id = resolved["record_group_id"]
    connector_id = resolved["connector_id"]

    try:
        thread_records = await graph_provider.get_records_by_record_group(
            record_group_id=thread_rg_id,
            connector_id=connector_id,
            org_id=effective_org,
            depth=0,
        )
    except Exception as e:
        logger.error(f"get_records_by_record_group failed for thread {thread_rg_id}: {e}")
        return {"ok": False, "error": f"Failed to list thread records: {e}"}

    if not thread_records:
        return {
            "ok": True,
            "records": [],
            "record_count": 0,
            "thread_record_group_id": thread_rg_id,
            "thread_external_record_group_id": resolved.get("external_record_group_id"),
        }

    found_records: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for r in thread_records:
        meta = _model_to_dict(r)
        rid = meta.get("id") or meta.get("_key")
        if not rid:
            continue

        record = await _fetch_record_by_id(
            rid,
            graph_provider=graph_provider,
            blob_store=effective_blob,
            org_id=effective_org,
            virtual_record_id_to_result=virtual_record_id_to_result,
        )
        if record:
            found_records.append(record)
        else:
            skipped.append(rid)

    # Chronological order — easiest for the LLM to reason about a thread.
    found_records.sort(key=lambda r: r.get("source_created_at") or 0)

    logger.info(
        "Fetched Slack thread %s: %d records returned, %d skipped (input record_id=%s)",
        thread_rg_id, len(found_records), len(skipped), record_id,
    )

    return {
        "ok": True,
        "records": found_records,
        "record_count": len(found_records),
        "thread_record_group_id": thread_rg_id,
        "thread_external_record_group_id": resolved.get("external_record_group_id"),
        "skipped_record_ids": skipped,
    }


def create_fetch_slack_thread_tool(
    virtual_record_id_to_result: Dict[str, Any],
    org_id: Optional[str] = None,
    graph_provider: Optional["IGraphDBProvider"] = None,
    blob_store: Optional["BlobStorage"] = None,
    config_service: Optional["ConfigurationService"] = None,
) -> Callable:
    """Factory for the fetch_slack_thread tool with runtime deps injected.

    Args:
        virtual_record_id_to_result: Shared map of vrid -> built record dict.
            Newly fetched thread records are inserted here so subsequent tool
            calls (e.g. fetch_full_record) reuse the same instances.
        org_id: Organization ID for blob/graph lookups.
        graph_provider: Graph DB provider for record + RG resolution.
        blob_store: Blob storage for fetching record content.
        config_service: When ``blob_store`` is None (e.g. deep agent tools cached
            before retrieval), used to construct ``BlobStorage`` on each call.
    """

    @tool("fetch_slack_thread", args_schema=FetchSlackThreadArgs)
    async def fetch_slack_thread_tool(
        record_id: str,
        reason: str = "Fetching full Slack thread to see all replies in context",
    ) -> Dict[str, Any]:
        """Fetch every record belonging to a Slack thread.

        Use this when the conversation context shows a Slack thread record
        (recordGroupType='SLACK_THREAD') OR a channel-level Slack message
        that is itself a thread parent, and you need the full back-and-forth
        of replies + any files shared in the thread to answer the question.

        Pass the 'Record ID :' value from the context exactly. The tool will:
          1. Resolve the SLACK_THREAD RecordGroup the record belongs to.
          2. Return every record that BELONGS_TO that group (thread-burst
             MessageRecords + dependent FileRecords), in chronological order.
          3. Hydrate them through the same pipeline as first-pass retrieval,
             so block_containers, context_metadata and metadata fields all
             match the records you already see.

        Args:
            record_id: Record ID of a Slack thread / thread-parent record.
            reason: Brief explanation of why the thread expansion is needed.

        Returns:
            {
                "ok": true,
                "records": [...],
                "record_count": N,
                "thread_record_group_id": "...",
                "thread_external_record_group_id": "thread_<channel>_<ts>",
                "skipped_record_ids": [...]
            }
            or {"ok": false, "error": "..."}.
        """
        try:
            return await _fetch_thread_records_impl(
                record_id=record_id,
                virtual_record_id_to_result=virtual_record_id_to_result,
                graph_provider=graph_provider,
                blob_store=blob_store,
                org_id=org_id,
                config_service=config_service,
            )
        except Exception as e:
            logger.exception("fetch_slack_thread_tool failed")
            return {"ok": False, "error": f"Failed to fetch Slack thread: {e}"}

    return fetch_slack_thread_tool
