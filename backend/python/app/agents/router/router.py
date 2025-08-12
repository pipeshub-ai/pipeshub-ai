"""
Router for agent tools API endpoints
Provides endpoints for clients to retrieve tool information from ArangoDB
"""

import logging
from typing import Any, Dict, List, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.db.tools_db import ToolsDBManager
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.containers.connector import ConnectorAppContainer
from app.services.graph_db.arango.config import ArangoConfig

# Create router instance
router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

@inject
async def get_tools_db(
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
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
    app_name: Optional[str] = Query(None, description="Filter tools by app name"),
    tag: Optional[str] = Query(None, description="Filter tools by tag"),
    search: Optional[str] = Query(None, description="Search in tool names and descriptions"),
    tools_db: ToolsDBManager = Depends(get_tools_db),
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
        # Get tools from ArangoDB based on filters
        if app_name:
            all_tools = await tools_db.get_tools_by_app(app_name)
        elif tag:
            all_tools = await tools_db.get_tools_by_tag(tag)
        elif search:
            all_tools = await tools_db.search_tools(search)
        else:
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


@router.get("/{app_name}/{tool_name}", response_model=Dict[str, Any])
@inject
async def get_tool_by_name(
    app_name: str,
    tool_name: str,
    tools_db: ToolsDBManager = Depends(get_tools_db)
) -> Dict[str, Any]:
    """
    Get a specific tool by app name and tool name from ArangoDB
    Args:
        app_name: The name of the app
        tool_name: The name of the tool
        tools_db: Database manager dependency
    Returns:
        Complete tool information
    """
    try:
        tool_node = await tools_db.get_tool(app_name, tool_name)

        if not tool_node:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{app_name}.{tool_name}' not found"
            )

        # Convert ToolNode to dictionary with all information
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

        return tool_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve tool: {str(e)}"
        )


@router.get("/apps", response_model=List[str])
@inject
async def get_app_names(
    tools_db: ToolsDBManager = Depends(get_tools_db)
) -> List[str]:
    """
    Get list of all available app names from ArangoDB
    Args:
        tools_db: Database manager dependency
    Returns:
        List of unique app names
    """
    try:
        all_tools = await tools_db.get_all_tools()
        app_names = list(set(tool_node.app_name for tool_node in all_tools))
        return sorted(app_names)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve app names: {str(e)}"
        )


@router.get("/stats", response_model=Dict[str, Any])
@inject
async def get_tools_stats(
    tools_db: ToolsDBManager = Depends(get_tools_db)
) -> Dict[str, Any]:
    """
    Get statistics about available tools from ArangoDB
    Args:
        tools_db: Database manager dependency
    Returns:
        Dictionary with tool statistics
    """
    try:
        all_tools = await tools_db.get_all_tools()

        if not all_tools:
            return {
                "total_tools": 0,
                "total_apps": 0,
                "total_tags": 0,
                "total_parameters": 0
            }

        # Calculate statistics
        total_tools = len(all_tools)
        total_apps = len(set(tool_node.app_name for tool_node in all_tools))

        all_tags = []
        total_parameters = 0
        for tool_node in all_tools:
            all_tags.extend(tool_node.tags)
            total_parameters += len(tool_node.parameters)

        unique_tags = len(set(all_tags))

        # App-wise breakdown
        app_breakdown = {}
        for tool_node in all_tools:
            if tool_node.app_name not in app_breakdown:
                app_breakdown[tool_node.app_name] = 0
            app_breakdown[tool_node.app_name] += 1

        return {
            "total_tools": total_tools,
            "total_apps": total_apps,
            "total_tags": unique_tags,
            "total_parameters": total_parameters,
            "app_breakdown": app_breakdown,
            "average_parameters_per_tool": round(total_parameters / total_tools, 2) if total_tools > 0 else 0
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve tool statistics: {str(e)}"
        )
