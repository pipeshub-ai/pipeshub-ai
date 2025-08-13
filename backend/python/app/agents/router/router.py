"""
Router for agent tools API endpoints
Provides endpoints for clients to retrieve tool information from ArangoDB
"""

import logging
from typing import Any, Dict, List

from dependency_injector.wiring import inject
from fastapi import APIRouter, HTTPException, Request

from app.agents.db.tools_db import ToolsDBManager
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.graph_db.arango.config import ArangoConfig

router = APIRouter(prefix="/tools", tags=["tools"])

logger = logging.getLogger(__name__)


async def get_tools_db(
    config_service: ConfigurationService
) -> ToolsDBManager:
    """
    Dependency provider for ToolsDBManager
    Args:
        config_service: Configuration service dependency
    Returns:
        ToolsDBManager instance
    """
    arangodb_config = await config_service.get_config(
        config_node_constants.ARANGODB.value
    )
    if not arangodb_config:
        raise HTTPException(
            status_code=500,
            detail="ArangoDB configuration not found"
        )

    if not arangodb_config or not isinstance(arangodb_config, dict):
                raise ValueError("ArangoDB configuration not found or invalid")

    arango_url = str(arangodb_config.get("url"))
    arango_user = str(arangodb_config.get("username"))
    arango_password = str(arangodb_config.get("password"))
    arango_db = str(arangodb_config.get("db"))

    arango_config = ArangoConfig(
        url=arango_url,
        username=arango_user,
        password=arango_password,
        db=arango_db
    )
    return await ToolsDBManager.create(logging.getLogger(__name__), arango_config)

@router.get("/", response_model=List[Dict[str, Any]])
@inject
async def get_all_tools(
    request: Request,
) -> List[Dict[str, Any]]:
    """
    Get all available tools with complete information from ArangoDB
    Args:
        app_name: Optional filter by app name
        tag: Optional filter by tag
        search: Optional search term for tool names and descriptions
        tools_db: Database manager dependency
    Returns:
        List of tools with complete information including parameters, examples, and tags
    """
    try:
        container = request.app.container
        # Get tools from ArangoDB
        tools_db = await get_tools_db(container.config_service())
        await tools_db.graph_service.connect()
        all_tools = await tools_db.get_all_tools()

        if not all_tools:
            return []

        # Convert ToolNode objects to serializable format
        tools_data = []
        for tool_node in all_tools:
            tool_data = {
                "tool_id": tool_node.tool_id,
                "app_name": tool_node.app_name,
                "tool_name": tool_node.tool_name,
                "full_name": f"{tool_node.app_name}.{tool_node.tool_name}",
                "description": tool_node.description,
                "parameters": tool_node.parameters,
                "returns": tool_node.returns,
                "examples": tool_node.examples,
                "tags": tool_node.tags,
                "parameter_count": len(tool_node.parameters),
                "required_parameters": [param["name"] for param in tool_node.parameters if param.get("required", False)],
                "optional_parameters": [param["name"] for param in tool_node.parameters if not param.get("required", False)],
                "ctag": tool_node.ctag,
                "created_at": tool_node.created_at,
                "updated_at": tool_node.updated_at
            }

            tools_data.append(tool_data)

        return tools_data

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve tools: {str(e)}"
        )
