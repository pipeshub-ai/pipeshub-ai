"""Parsers and graph writers for agent attachments (models, toolsets, MCP
servers, knowledge, skills), moved verbatim out of `app/api/routes/agent.py`."""

import json
import uuid
from logging import Logger
from typing import Any

from app.config.constants.ai_models import (
    REASONING_EFFORT_VALUES,
    validate_reasoning_effort,
)
from app.config.constants.arangodb import CollectionNames
from app.modules.agents.service.errors import InvalidRequestError
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

SPLIT_PATH_EXPECTED_PARTS = 2  # Expected parts when splitting path with "/" separator


_SUPPORTED_WEB_SEARCH_PROVIDERS = {"duckduckgo", "serper", "tavily", "exa"}


def sa_forces_org_sharing() -> bool:
    """Whether service account agents are always forced to share with the org.
    """
    return True


def _parse_models(raw_models: list[Any], logger: Logger) -> tuple[list[str], bool]:
    """Parse and validate model entries"""
    model_entries = []
    has_reasoning_model = False

    if not raw_models or not isinstance(raw_models, list):
        return model_entries, has_reasoning_model

    for model in raw_models:
        if isinstance(model, dict):
            model_key = model.get("modelKey")
            model_name = model.get("modelName", "")

            if model_key:
                entry = f"{model_key}_{model_name}" if model_name else model_key
                model_entries.append(entry)

                if model.get("isReasoning", False):
                    has_reasoning_model = True
        elif isinstance(model, str):
            model_entries.append(model)

    return model_entries, has_reasoning_model


def _parse_default_reasoning_effort(raw_value: Any) -> str | None:
    """Validate the agent-level reasoning effort default.

    Returns ``None`` for an absent/blank value (no default configured — the
    per-request `reasoningEffort`, or DEFAULT_REASONING_EFFORT if neither is
    set, applies). Raises for any non-empty value outside the platform enum.
    """
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    try:
        validate_reasoning_effort(value)
    except ValueError as exc:
        raise InvalidRequestError(
            f"Invalid defaultReasoningEffort '{value}'. "
            f"Must be one of: {', '.join(sorted(REASONING_EFFORT_VALUES))}."
        ) from exc
    return value


def _parse_web_search(raw_web_search: Any) -> str | None:
    """Normalize the agent-level web-search attachment to a provider string.

    Accepts either:
    - a dict like {"provider": "serper", ...}
    - a provider string like "serper"

    Returns the sanitized provider (lowercase), or None if invalid/missing.
    """
    if not raw_web_search:
        return None

    provider = ""
    if isinstance(raw_web_search, dict):
        provider = str(raw_web_search.get("provider", "")).strip().lower()
    elif isinstance(raw_web_search, str):
        provider = raw_web_search.strip().lower()

    if not provider or provider not in _SUPPORTED_WEB_SEARCH_PROVIDERS:
        return None
    return provider


def _format_web_search_for_response(raw_web_search: Any) -> dict[str, Any] | None:
    """Normalize webSearch payloads to an API-friendly object shape."""
    provider = _parse_web_search(raw_web_search)
    if not provider:
        return None

    formatted: dict[str, Any] = {"provider": provider}
    if isinstance(raw_web_search, dict):
        provider_key = str(raw_web_search.get("providerKey", "")).strip()
        provider_label = str(raw_web_search.get("providerLabel", "")).strip()
        if provider_key:
            formatted["providerKey"] = provider_key
        if provider_label:
            formatted["providerLabel"] = provider_label
    return formatted


def _parse_toolsets(raw_toolsets: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse toolsets with their tools.

    The key of the returned dict is the toolset name (lowercase).
    Each value carries the parsed fields including optional instanceId.
    """
    toolsets_with_tools = {}

    if not raw_toolsets or not isinstance(raw_toolsets, list):
        return toolsets_with_tools

    for toolset_data in raw_toolsets:
        if not isinstance(toolset_data, dict):
            continue

        toolset_name = toolset_data.get("name", "").lower().strip()
        if not toolset_name:
            continue

        display_name = toolset_data.get("displayName", toolset_name.replace("_", " ").title())
        toolset_type = toolset_data.get("type", "app")
        tools_list = toolset_data.get("tools", [])
        # New field: admin-created instance UUID
        instance_id = toolset_data.get("instanceId", None)
        instance_name = toolset_data.get("instanceName", None)

        if toolset_name not in toolsets_with_tools:
            toolsets_with_tools[toolset_name] = {
                "displayName": display_name,
                "type": toolset_type,
                "tools": [],
                "instanceId": instance_id,
                "instanceName": instance_name,
            }
        elif instance_id and not toolsets_with_tools[toolset_name].get("instanceId"):
            # Update instanceId if not yet set
            toolsets_with_tools[toolset_name]["instanceId"] = instance_id
            toolsets_with_tools[toolset_name]["instanceName"] = instance_name

        for tool in tools_list:
            if isinstance(tool, dict):
                tool_name = tool.get("name", "")
                if tool_name:
                    toolsets_with_tools[toolset_name]["tools"].append({
                        "name": tool_name,
                        "fullName": tool.get("fullName", f"{toolset_name}.{tool_name}"),
                        "description": tool.get("description", "")
                    })

    return toolsets_with_tools


def _parse_knowledge_sources(raw_knowledge: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse knowledge sources"""
    knowledge_sources = {}

    if not raw_knowledge or not isinstance(raw_knowledge, list):
        return knowledge_sources

    for knowledge_data in raw_knowledge:
        if not isinstance(knowledge_data, dict):
            continue

        connector_id = knowledge_data.get("connectorId", "").strip()
        if not connector_id:
            continue

        filters = knowledge_data.get("filters", {})
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except json.JSONDecodeError:
                filters = {}

        knowledge_sources[connector_id] = {
            "connectorId": connector_id,
            "filters": filters
        }

    return knowledge_sources


async def _create_toolset_edges(
    agent_key: str,
    toolsets_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
    written_toolset_keys: list[str] | None = None,
    written_tool_keys: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create toolset nodes and edges for agent using batch operations.

    The agent is linked to the toolsets last, once each has its tools, so nothing reading
    the agent sees a toolset half built. ``written_toolset_keys`` and ``written_tool_keys``,
    when given, receive each node key before any write starts, so a caller can remove
    whatever this left behind if it fails midway.
    """
    from app.agents.constants.toolset_constants import normalize_app_name

    created_toolsets = []
    failed_toolsets = []
    time = get_epoch_timestamp_in_ms()

    if not toolsets_with_tools:
        return created_toolsets, failed_toolsets

    # Prepare all toolset nodes
    toolset_nodes = []
    toolset_mapping = {}  # Map toolset_name to toolset_key

    for toolset_name, toolset_data in toolsets_with_tools.items():
        toolset_key = str(uuid.uuid4())
        if written_toolset_keys is not None:
            written_toolset_keys.append(toolset_key)
        display_name = toolset_data["displayName"]
        toolset_type = toolset_data["type"]
        tools_list = toolset_data["tools"]
        instance_id = toolset_data.get("instanceId")
        instance_name = toolset_data.get("instanceName")

        toolset_node = {
            "_key": toolset_key,
            "name": normalize_app_name(toolset_name),
            "displayName": display_name,
            "type": toolset_type,
            "userId": user_info["userId"],
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }

        # Store instanceId in ArangoDB when provided (admin-created instances)
        if instance_id:
            toolset_node["instanceId"] = instance_id
        if instance_name:
            toolset_node["instanceName"] = instance_name

        toolset_nodes.append(toolset_node)
        toolset_mapping[toolset_name] = {
            "key": toolset_key,
            "displayName": display_name,
            "tools": tools_list
        }

    # Batch create all toolset nodes
    try:
        result = await graph_provider.batch_upsert_nodes(
            toolset_nodes, CollectionNames.AGENT_TOOLSETS.value, transaction=transaction
        )
        if not result:
            return created_toolsets, [{"name": "all", "error": "Failed to create toolset nodes"}]
    except Exception as e:
        logger.error(f"Failed to batch create toolset nodes: {e}")
        return created_toolsets, [{"name": "all", "error": action_failed("add these tools to the agent")}]

    # Prepare all tool nodes and edges
    tool_nodes = []
    toolset_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for toolset_name, toolset_info in toolset_mapping.items():
        for tool_data in toolset_info["tools"]:
            tool_name = tool_data["name"]
            full_name = tool_data["fullName"]
            description = tool_data["description"]

            tool_key = str(uuid.uuid4())
            if written_tool_keys is not None:
                written_tool_keys.append(tool_key)

            tool_node = {
                "_key": tool_key,
                "name": tool_name,
                "fullName": full_name,
                "toolsetName": toolset_name,
                "description": description,
                "createdBy": user_key,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time
            }

            tool_nodes.append(tool_node)
            tool_mapping[full_name] = {
                "key": tool_key,
                "name": tool_name,
                "toolset": toolset_name
            }

            # Prepare toolset -> tool edge
            toolset_tool_edges.append({
                "_from": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
                "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })

    # Batch create all tool nodes. Must raise on failure: edge creation below points at
    # these node keys, and some graph providers create edges via MATCH/MERGE that succeeds
    # silently even when the referenced node was never created.
    if tool_nodes:
        try:
            result = await graph_provider.batch_upsert_nodes(
                tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction
            )
            if not result:
                raise RuntimeError("Failed to create tool nodes")
        except Exception as e:
            logger.error(f"Failed to batch create tool nodes: {e}")
            raise

    # Batch create toolset -> tool edges. Re-raise for the same reason as above.
    if toolset_tool_edges:
        try:
            result = await graph_provider.batch_create_edges(
                toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=transaction
            )
            if not result:
                raise RuntimeError("Failed to link tools to their toolsets")
        except Exception as e:
            logger.error(f"Failed to create toolset-tool edges: {e}")
            raise

    agent_toolset_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for toolset_info in toolset_mapping.values()
    ]

    # Batch create agent -> toolset edges. Re-raise — mirrors `_create_mcp_server_edges`,
    # since every caller already wraps this in a transaction rollback or an HTTPException.
    try:
        result = await graph_provider.batch_create_edges(
            agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to link toolsets to the agent")
    except Exception as e:
        logger.error(f"Failed to create agent-toolset edges: {e}")
        raise

    # Build response with created toolsets and tools
    for toolset_name, toolset_info in toolset_mapping.items():
        created_tools = []
        for tool_data in toolset_info["tools"]:
            full_name = tool_data["fullName"]
            if full_name in tool_mapping:
                created_tools.append({
                    "name": tool_mapping[full_name]["name"],
                    "fullName": full_name,
                    "key": tool_mapping[full_name]["key"]
                })

        created_toolsets.append({
            "name": toolset_name,
            "displayName": toolset_info["displayName"],
            "key": toolset_info["key"],
            "tools": created_tools
        })

    return created_toolsets, failed_toolsets


def _parse_mcp_servers(raw_mcp_servers: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse attached MCP server references with their tools.

    Unlike `_parse_toolsets` (keyed by name — built-in toolset types have a
    single instance system-wide), MCP servers are keyed by `instanceId`: an
    agent can attach several distinct instances, but never two instances of
    the same `typeId` — that's enforced here so `mcp_{server_type}_{tool}`
    tool names stay unique at chat time (see `get_authenticated_mcp_servers`
    in `app/agents/mcp/service.py`).
    """
    mcp_servers_with_tools: dict[str, dict[str, Any]] = {}
    seen_type_ids: dict[str, str] = {}

    if not raw_mcp_servers or not isinstance(raw_mcp_servers, list):
        return mcp_servers_with_tools

    for mcp_data in raw_mcp_servers:
        if not isinstance(mcp_data, dict):
            continue

        instance_id = str(mcp_data.get("instanceId", "")).strip()
        if not instance_id:
            continue

        name = str(mcp_data.get("name", "")).strip()
        if not name:
            continue

        type_id = mcp_data.get("typeId") or None
        if type_id:
            existing_instance_id = seen_type_ids.get(type_id)
            if existing_instance_id and existing_instance_id != instance_id:
                raise InvalidRequestError(
                    f"Cannot attach two MCP server instances of the same type ('{type_id}') to one agent."
                )
            seen_type_ids[type_id] = instance_id

        display_name = mcp_data.get("displayName") or name.replace("_", " ").title()
        tools_list = mcp_data.get("tools", [])

        if instance_id not in mcp_servers_with_tools:
            mcp_servers_with_tools[instance_id] = {
                "name": name,
                "displayName": display_name,
                "typeId": type_id,
                "tools": [],
            }

        for tool in tools_list:
            if isinstance(tool, dict):
                tool_name = tool.get("name", "")
                if tool_name:
                    mcp_servers_with_tools[instance_id]["tools"].append({
                        "name": tool_name,
                        "fullName": tool.get("fullName", f"{name}.{tool_name}"),
                        "description": tool.get("description", "")
                    })

    return mcp_servers_with_tools


async def _create_mcp_server_edges(
    agent_key: str,
    mcp_servers_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create MCP server nodes and edges for an agent using batch operations.

    Mirrors `_create_toolset_edges`, keyed by `instanceId` instead of name.
    MCP server nodes never carry credentials — auth is resolved at chat time
    from `/services/mcp/credentials/{instanceId}/{ownerId}` (etcd), same as
    the attach-time Node/Python validation that already rejects secrets here.
    Accepts an optional `transaction` so callers can fold this into an
    existing agent-creation transaction (unlike the toolset equivalent,
    which create_agent re-implements inline for that reason).
    """
    created_mcp_servers: list[dict[str, Any]] = []
    failed_mcp_servers: list[dict[str, Any]] = []
    time = get_epoch_timestamp_in_ms()

    if not mcp_servers_with_tools:
        return created_mcp_servers, failed_mcp_servers

    # Prepare all MCP server nodes
    mcp_server_nodes = []
    mcp_server_mapping = {}  # Map instance_id to node key/tools

    for instance_id, mcp_data in mcp_servers_with_tools.items():
        mcp_server_key = str(uuid.uuid4())
        name = mcp_data["name"]
        display_name = mcp_data["displayName"]
        type_id = mcp_data.get("typeId")
        tools_list = mcp_data["tools"]

        mcp_server_node = {
            "_key": mcp_server_key,
            "instanceId": instance_id,
            "name": name,
            "displayName": display_name,
            "userId": user_info["userId"],
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }
        if type_id:
            mcp_server_node["typeId"] = type_id

        mcp_server_nodes.append(mcp_server_node)
        mcp_server_mapping[instance_id] = {
            "key": mcp_server_key,
            "name": name,
            "displayName": display_name,
            "tools": tools_list
        }

    # Batch create all MCP server nodes
    try:
        result = await graph_provider.batch_upsert_nodes(
            mcp_server_nodes, CollectionNames.AGENT_MCP_SERVERS.value, transaction=transaction
        )
        if not result:
            return created_mcp_servers, [{"name": "all", "error": "Failed to create MCP server nodes"}]
    except Exception as e:
        logger.error(f"Failed to batch create MCP server nodes: {e}")
        return created_mcp_servers, [{"name": "all", "error": action_failed("add these MCP servers to the agent")}]

    # Prepare agent -> mcpServer edges
    agent_mcp_server_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_MCP_SERVERS.value}/{mcp_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for mcp_info in mcp_server_mapping.values()
    ]

    # Batch create agent -> mcpServer edges. Re-raise (rather than log-and-continue) — every
    # caller already wraps this in a transaction rollback or an HTTPException, so swallowing
    # here would otherwise let create/update report "success" with MCP server nodes that were
    # never actually linked to the agent.
    try:
        await graph_provider.batch_create_edges(
            agent_mcp_server_edges, CollectionNames.AGENT_HAS_MCP_SERVER.value, transaction=transaction
        )
    except Exception as e:
        logger.error(f"Failed to create agent-mcpServer edges: {e}")
        raise

    # Prepare all tool nodes and edges (tools live in the shared AGENT_TOOLS
    # collection, same as toolset tools)
    tool_nodes = []
    mcp_server_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for mcp_info in mcp_server_mapping.values():
        for tool_data in mcp_info["tools"]:
            tool_name = tool_data["name"]
            full_name = tool_data["fullName"]
            description = tool_data["description"]

            tool_key = str(uuid.uuid4())

            tool_node = {
                "_key": tool_key,
                "name": tool_name,
                "fullName": full_name,
                "toolsetName": mcp_info["name"],
                "description": description,
                "createdBy": user_key,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time
            }

            tool_nodes.append(tool_node)
            tool_mapping[full_name] = {
                "key": tool_key,
                "name": tool_name,
                "mcpServer": mcp_info["name"]
            }

            # Prepare mcpServer -> tool edge
            mcp_server_tool_edges.append({
                "_from": f"{CollectionNames.AGENT_MCP_SERVERS.value}/{mcp_info['key']}",
                "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })

    # Batch create all tool nodes. Must raise on failure: edge creation below points at
    # these node keys, and some graph providers create edges via MATCH/MERGE that succeeds
    # silently even when the referenced node was never created.
    if tool_nodes:
        try:
            result = await graph_provider.batch_upsert_nodes(
                tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction
            )
            if not result:
                raise RuntimeError("Failed to create MCP tool nodes")
        except Exception as e:
            logger.error(f"Failed to batch create MCP tool nodes: {e}")
            raise

    # Batch create mcpServer -> tool edges. Re-raise for the same reason as the
    # agent->mcpServer edges above — a swallowed failure here leaves tools listed in the
    # response with no MCP_SERVER_HAS_TOOL edge actually connecting them.
    if mcp_server_tool_edges:
        try:
            await graph_provider.batch_create_edges(
                mcp_server_tool_edges, CollectionNames.MCP_SERVER_HAS_TOOL.value, transaction=transaction
            )
        except Exception as e:
            logger.error(f"Failed to create mcpServer-tool edges: {e}")
            raise

    # Build response with created MCP servers and tools
    for mcp_info in mcp_server_mapping.values():
        created_tools = []
        for tool_data in mcp_info["tools"]:
            full_name = tool_data["fullName"]
            if full_name in tool_mapping:
                created_tools.append({
                    "name": tool_mapping[full_name]["name"],
                    "fullName": full_name,
                    "key": tool_mapping[full_name]["key"]
                })

        created_mcp_servers.append({
            "name": mcp_info["name"],
            "displayName": mcp_info["displayName"],
            "key": mcp_info["key"],
            "tools": created_tools
        })

    return created_mcp_servers, failed_mcp_servers


async def _create_knowledge_edges(
    agent_key: str,
    knowledge_sources: dict[str, dict[str, Any]],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
    written_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Create knowledge nodes and edges for agent using batch operations.

    ``written_keys``, when given, receives each knowledge node key before any write
    starts, so a caller can remove whatever this left behind if it fails midway.
    """
    created_knowledge = []
    time = get_epoch_timestamp_in_ms()

    if not knowledge_sources:
        return created_knowledge

    # Prepare all knowledge nodes
    knowledge_nodes = []
    knowledge_mapping = {}

    for connector_id, knowledge_data in knowledge_sources.items():
        knowledge_key = str(uuid.uuid4())
        if written_keys is not None:
            written_keys.append(knowledge_key)
        filters = knowledge_data["filters"]

        # Schema expects filters as a stringified JSON, not a dict
        filters_str = json.dumps(filters) if isinstance(filters, dict) else str(filters)

        knowledge_node = {
            "_key": knowledge_key,
            "connectorId": connector_id,
            "filters": filters_str,
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }

        knowledge_nodes.append(knowledge_node)
        knowledge_mapping[connector_id] = {
            "key": knowledge_key,
            "filters": filters
        }

    # Raise, as the toolset and MCP helpers do: returning quietly reported success
    # for an agent whose previous knowledge update_agent had just removed.
    try:
        result = await graph_provider.batch_upsert_nodes(
            knowledge_nodes, CollectionNames.AGENT_KNOWLEDGE.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to create knowledge nodes")
    except Exception as e:
        logger.error(f"Failed to batch create knowledge nodes: {e}")
        raise

    # Prepare agent -> knowledge edges
    agent_knowledge_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{knowledge_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for knowledge_info in knowledge_mapping.values()
    ]

    # Batch create agent -> knowledge edges
    try:
        result = await graph_provider.batch_create_edges(
            agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to link knowledge to the agent")
    except Exception as e:
        logger.error(f"Failed to create agent-knowledge edges: {e}")
        raise

    # Build response
    created_knowledge.extend(
        {
            "connectorId": connector_id,
            "key": knowledge_info["key"],
            "filters": knowledge_info["filters"],
        }
        for knowledge_info in knowledge_mapping.values()
    )

    return created_knowledge


async def _remove_knowledge_nodes(keys: list[str], graph_provider: IGraphDBProvider, logger: Logger) -> None:
    """Best-effort removal of knowledge nodes and their agent links; failures are only logged."""
    for key in keys:
        try:
            await graph_provider.delete_all_edges_for_node(
                f"{CollectionNames.AGENT_KNOWLEDGE.value}/{key}", CollectionNames.AGENT_HAS_KNOWLEDGE.value,
            )
        except Exception as cleanup_error:
            logger.error(f"Failed to unlink knowledge node {key}: {cleanup_error}")
    try:
        await graph_provider.delete_nodes(keys, CollectionNames.AGENT_KNOWLEDGE.value)
    except Exception as cleanup_error:
        logger.error(f"Failed to remove knowledge nodes {keys}: {cleanup_error}")


async def _finish_unlinking_old(
    agent_full_id: str,
    edge_collection: str,
    old_ids: list[str],
    deleted_old_ids: list[str],
    new_ids: set[str],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> bool:
    """After a save failed partway through unlinking the agent's old attachments, finish
    unlinking them where the writes persisted (a rollback that undoes nothing). Returns
    whether it did, so the caller can remove the old nodes too.

    Acts only on evidence read back from the graph: a new link still present, or a
    removed old link still gone. A real rollback restores the old links and drops the
    new ones, and a failed read shows nothing, so both are left as they are. Best effort
    throughout; failures are logged.
    """
    try:
        edges = await graph_provider.get_edges_from_node(agent_full_id, edge_collection)
    except Exception as read_error:
        logger.error(f"Could not read the {edge_collection} links of {agent_full_id} after a failed save: {read_error}")
        return False
    linked = {edge.get("_to") for edge in edges or []}
    remaining_old = [old_id for old_id in old_ids if old_id in linked]
    if new_ids:
        persisted = bool(linked & new_ids)
    else:
        persisted = any(old_id not in linked for old_id in deleted_old_ids)
    if not persisted:
        return False
    for old_id in remaining_old:
        try:
            await graph_provider.delete_all_edges_for_node(old_id, edge_collection)
        except Exception as cleanup_error:
            logger.error(f"Failed to unlink {old_id}: {cleanup_error}")
    return True


async def _remove_toolsets(
    toolset_keys: list[str], tool_keys: list[str], graph_provider: IGraphDBProvider, logger: Logger,
) -> None:
    """Best-effort removal of toolset nodes, their tool nodes and every link to them;
    failures are only logged."""
    for key in toolset_keys:
        toolset_id = f"{CollectionNames.AGENT_TOOLSETS.value}/{key}"
        for edge_collection in (CollectionNames.AGENT_HAS_TOOLSET.value, CollectionNames.TOOLSET_HAS_TOOL.value):
            try:
                await graph_provider.delete_all_edges_for_node(toolset_id, edge_collection)
            except Exception as cleanup_error:
                logger.error(f"Failed to unlink toolset {key}: {cleanup_error}")
    for keys, collection in ((tool_keys, CollectionNames.AGENT_TOOLS.value), (toolset_keys, CollectionNames.AGENT_TOOLSETS.value)):
        if not keys:
            continue
        try:
            await graph_provider.delete_nodes(keys, collection)
        except Exception as cleanup_error:
            logger.error(f"Failed to remove {collection} nodes {keys}: {cleanup_error}")


async def _finish_removing_old_knowledge(
    agent_full_id: str,
    old_ids: list[str],
    old_keys: list[str],
    deleted_old_ids: list[str],
    new_keys: list[str],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> None:
    """After a knowledge save failed partway through removing the old links, finish
    removing them where the writes persisted (see `_finish_unlinking_old`)."""
    new_ids = {f"{CollectionNames.AGENT_KNOWLEDGE.value}/{key}" for key in new_keys}
    if not await _finish_unlinking_old(
        agent_full_id, CollectionNames.AGENT_HAS_KNOWLEDGE.value, old_ids, deleted_old_ids, new_ids,
        graph_provider, logger,
    ):
        return
    # Old knowledge nodes with no link are never read, so removing them is tidy-up only.
    if old_keys:
        try:
            await graph_provider.delete_nodes(old_keys, CollectionNames.AGENT_KNOWLEDGE.value)
        except Exception as cleanup_error:
            logger.error(f"Failed to remove old knowledge nodes {old_keys}: {cleanup_error}")


def _parse_skills(raw_skills: list[Any]) -> list[str]:
    """Parse the agent payload's `skills: [{name}] | [name, ...]` field into
    a de-duplicated, order-preserving list of skill names.

    Unlike `_parse_toolsets`/`_parse_knowledge_sources`, this never creates
    anything: skill NODES already exist in `agentSkills` (owned by the
    Skills management API — `api/routes/skills.py`), so agent create/update
    only ever links to a skill that's already there. `_create_skill_edges`
    below re-validates existence/ownership at write time regardless of
    what the client claims here.
    """
    names: list[str] = []
    seen: set[str] = set()
    if not raw_skills or not isinstance(raw_skills, list):
        return names
    for entry in raw_skills:
        name = entry.get("name") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


async def _create_skill_edges(
    agent_key: str,
    skill_names: list[str],
    org_id: str,
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
) -> list[str]:
    """Create `agentHasSkill` edges from an agent to each assigned skill —
    mirrors `_create_toolset_edges`/`_create_knowledge_edges` in shape, but
    never creates a skill node: skills are owned by the Skills management
    API, this only links to ones that already exist.

    Defense in depth (mirrors `GraphSkillStore._is_visible`): a name is
    only linked when it resolves to an existing skill in this org AND
    (the acting user created it OR it's a `builtin`-sourced skill) — a
    user can only assign their own skills (or org-wide builtins) to an
    agent, never a co-worker's. Any name that doesn't resolve is logged
    and skipped rather than failing the whole create/update — a stale
    skill reference in the payload should never block agent creation.
    """
    if not skill_names:
        return []

    skills_collection = CollectionNames.AGENT_SKILLS.value
    time = get_epoch_timestamp_in_ms()
    edges: list[dict[str, Any]] = []
    linked_names: list[str] = []

    for name in skill_names:
        skill_key = f"{org_id}_{name}"
        skill_doc = await graph_provider.get_document(skill_key, skills_collection, transaction=transaction)
        if not skill_doc or skill_doc.get("orgId") != org_id:
            logger.warning(f"Skipping unknown skill '{name}' for agent {agent_key}")
            continue
        if skill_doc.get("source") != "builtin" and skill_doc.get("createdBy") != user_key:
            logger.warning(f"Skipping skill '{name}' not owned by user {user_key} for agent {agent_key}")
            continue
        if (skill_doc.get("status") or "active") != "active":
            # Mirrors the picker (`GET /skills?status=active`, see
            # `SkillsApi.listAssignableSkills`) — closes the gap where a
            # direct API call could still newly assign a disabled or
            # deprecated skill the UI never offers.
            logger.warning(f"Skipping non-active skill '{name}' for agent {agent_key}")
            continue
        edges.append({
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{skills_collection}/{skill_key}",
            "skillName": name,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        })
        linked_names.append(name)

    if edges:
        await graph_provider.batch_create_edges(edges, CollectionNames.AGENT_HAS_SKILL.value, transaction=transaction)
    return linked_names
