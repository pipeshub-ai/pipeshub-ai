"""
Tools Database Manager for ArangoDB
Manages tool storage and retrieval with ctag-based caching for efficient updates.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from app.agents.tools.models import Tool
from app.agents.tools.registry import ToolRegistry
from app.config.configuration_service import ConfigurationService
from app.services.graph_db.arango.config import ArangoConfig
from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.services.graph_db.interface.graph_db import IGraphService


class ToolNode:
    """Node representing a tool in the graph database"""

    def __init__(
        self,
        tool_id: str,
        app_name: str,
        tool_name: str,
        description: str,
        parameters: List[Dict],
        returns: Optional[str],
        examples: List[Dict],
        tags: List[str],
        ctag: str,
        created_at: str,
        updated_at: str
    ) -> None:
        self.tool_id = tool_id
        self.app_name = app_name
        self.tool_name = tool_name
        self.description = description
        self.parameters = parameters
        self.returns = returns
        self.examples = examples
        self.tags = tags
        self.ctag = ctag
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ArangoDB storage"""
        return {
            "_key": self.tool_id,
            "app_name": self.app_name,
            "tool_name": self.tool_name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
            "examples": self.examples,
            "tags": self.tags,
            "ctag": self.ctag,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ToolNode':
        """Create ToolNode from dictionary"""
        return cls(
            tool_id=data["_key"],
            app_name=data["app_name"],
            tool_name=data["tool_name"],
            description=data["description"],
            parameters=data["parameters"],
            returns=data.get("returns"),
            examples=data.get("examples", []),
            tags=data.get("tags", []),
            ctag=data["ctag"],
            created_at=data["created_at"],
            updated_at=data["updated_at"]
        )

    def validate(self) -> bool:
        """Validate the tool node"""
        return all([
            self.tool_id,
            self.app_name,
            self.tool_name,
            self.description,
            self.ctag,
            self.created_at,
            self.updated_at
        ])

    @property
    def key(self) -> str:
        """Get the unique key for this model"""
        return self.tool_id


class ConnectorCtag:
    """Manages ctags for connectors to track tool changes"""

    def __init__(self, connector_name: str, ctag: str, last_updated: str) -> None:
        self.connector_name = connector_name
        self.ctag = ctag
        self.last_updated = last_updated

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ArangoDB storage"""
        return {
            "_key": self.connector_name,
            "connector_name": self.connector_name,
            "ctag": self.ctag,
            "last_updated": self.last_updated
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConnectorCtag':
        """Create ConnectorCtag from dictionary"""
        return cls(
            connector_name=data["connector_name"],
            ctag=data["ctag"],
            last_updated=data["last_updated"]
        )

    def validate(self) -> bool:
        """Validate the connector ctag"""
        return all([
            self.connector_name,
            self.ctag,
            self.last_updated
        ])

    @property
    def key(self) -> str:
        """Get the unique key for this model"""
        return self.connector_name


class ToolsDBManager:
    """Manages tools storage and retrieval in ArangoDB with ctag support"""

    def __init__(self, graph_service: IGraphService, logger: logging.Logger) -> None:
        self.graph_service = graph_service
        self.logger = logger
        self.collection_name = "tools"
        self.ctag_collection_name = "tools_ctags"

    @classmethod
    async def create(cls, logger: logging.Logger, config_service: Union[ArangoConfig, ConfigurationService]) -> 'ToolsDBManager':
        """Create and initialize ToolsDBManager"""
        graph_service = await GraphDBFactory.create_service("arango", logger, config_service)
        if not graph_service:
            raise RuntimeError("Failed to create ArangoDB service")
        return cls(graph_service, logger)

    async def initialize_collections(self) -> None:
        """Initialize ArangoDB collections for tools and ctags"""
        try:
            # connect to db
            await self.graph_service.connect()

            # Create tools collection
            if not await self.graph_service.create_collection(self.collection_name):
                self.logger.warning(f"Collection {self.collection_name} already exists or failed to create")

            # Create ctag collection
            if not await self.graph_service.create_collection(self.ctag_collection_name):
                self.logger.warning(f"Collection {self.ctag_collection_name} already exists or failed to create")

            # Create indexes for better performance
            await self._create_indexes()

        except Exception as e:
            self.logger.error(f"Failed to initialize collections: {e}")
            raise

    async def _create_indexes(self) -> None:
        """Create database indexes for better performance"""
        try:
            # Index on app_name and tool_name for fast lookups
            await self.graph_service.create_index(
                self.collection_name,
                ["app_name", "tool_name"],
                "persistent"
            )

            # Index on ctag for change detection
            await self.graph_service.create_index(
                self.collection_name,
                ["ctag"],
                "persistent"
            )

            # Index on tags for tag-based searches
            await self.graph_service.create_index(
                self.collection_name,
                ["tags"],
                "persistent"
            )

        except Exception as e:
            self.logger.warning(f"Failed to create some indexes: {e}")

    def _generate_ctag(self, tool: Tool) -> str:
        """Generate a ctag (content tag) for a tool based on its content"""
        # Create a hash of the tool's content to detect changes
        content = json.dumps({
            "description": tool.description,
            "parameters": [param.to_json_serializable_dict() for param in tool.parameters],
            "returns": tool.returns,
            "examples": tool.examples,
            "tags": tool.tags
        }, sort_keys=True)

        return hashlib.md5(content.encode()).hexdigest()

    def _generate_tool_id(self, app_name: str, tool_name: str) -> str:
        """Generate a unique tool ID"""
        return f"{app_name}_{tool_name}"

    async def sync_tools_from_registry(self, tool_registry: ToolRegistry) -> None:
        """Sync all tools from the registry to ArangoDB"""
        try:
            all_tools = tool_registry.get_all_tools().values()
            if not all_tools:
                self.logger.info("No tools to sync")
                return

            for tool in all_tools:
                await self._sync_single_tool(tool)

            self.logger.info(f"Successfully synced {len(all_tools)} tools to ArangoDB")

        except Exception as e:
            self.logger.error(f"Failed to sync tools from registry: {e}")
            raise

    async def _sync_single_tool(self, tool: Tool) -> None:
        """Sync a single tool to ArangoDB"""
        try:
            tool_id = self._generate_tool_id(tool.app_name, tool.tool_name)
            new_ctag = self._generate_ctag(tool)
            # Check if tool exists and if ctag has changed
            existing_tool = await self.get_tool(tool.app_name, tool.tool_name)

            if existing_tool and existing_tool.ctag == new_ctag:
                # Tool hasn't changed, skip update
                return

            # Create or update tool
            tool_node = ToolNode(
                tool_id=tool_id,
                app_name=tool.app_name,
                tool_name=tool.tool_name,
                description=tool.description,
                parameters=[param.to_json_serializable_dict() for param in tool.parameters],
                returns=tool.returns,
                examples=tool.examples,
                tags=tool.tags,
                ctag=new_ctag,
                created_at=existing_tool.created_at if existing_tool else datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat()
            )

            await self._upsert_tool(tool_node)

            # Update connector ctag
            await self._update_connector_ctag(tool.app_name, new_ctag)

        except Exception as e:
            self.logger.error(f"Failed to sync tool {tool.app_name}.{tool.tool_name}: {e}")
            raise

    async def _upsert_tool(self, tool_node: ToolNode) -> None:
        """Insert or update a tool in ArangoDB"""
        try:
            # Use upsert operation (insert if not exists, update if exists)
            await self.graph_service.upsert_document(
                self.collection_name,
                tool_node.to_dict()
            )

        except Exception as e:
            self.logger.error(f"Failed to upsert tool {tool_node.tool_id}: {e}")
            raise

    async def _update_connector_ctag(self, connector_name: str, ctag: str) -> None:
        """Update the ctag for a connector"""
        try:
            ctag_node = ConnectorCtag(
                connector_name=connector_name,
                ctag=ctag,
                last_updated=datetime.utcnow().isoformat()
            )

            await self.graph_service.upsert_document(
                self.ctag_collection_name,
                ctag_node.to_dict()
            )

        except Exception as e:
            self.logger.error(f"Failed to update connector ctag for {connector_name}: {e}")
            raise

    async def get_tool(self, app_name: str, tool_name: str) -> Optional[ToolNode]:
        """Get a tool from ArangoDB"""
        try:
            tool_id = self._generate_tool_id(app_name, tool_name)

            result = await self.graph_service.get_document(
                self.collection_name,
                tool_id
            )

            if result:
                return ToolNode.from_dict(result)
            return None

        except Exception as e:
            self.logger.error(f"Failed to get tool {app_name}.{tool_name}: {e}")
            return None

    async def get_tools_by_app(self, app_name: str) -> List[ToolNode]:
        """Get all tools for a specific app"""
        try:
            query = f"""
            FOR tool IN {self.collection_name}
            FILTER tool.app_name == @app_name
            RETURN tool
            """

            result = await self.graph_service.execute_query(query, {"app_name": app_name})
            if result is None:
                return []
            return [ToolNode.from_dict(tool) for tool in result]

        except Exception as e:
            self.logger.error(f"Failed to get tools for app {app_name}: {e}")
            return []

    async def get_tools_by_tag(self, tag: str) -> List[ToolNode]:
        """Get all tools with a specific tag"""
        try:
            query = f"""
            FOR tool IN {self.collection_name}
            FILTER @tag IN tool.tags
            RETURN tool
            """

            result = await self.graph_service.execute_query(query, {"tag": tag})
            if result is None:
                return []
            return [ToolNode.from_dict(tool) for tool in result]

        except Exception as e:
            self.logger.error(f"Failed to get tools by tag {tag}: {e}")
            return []

    async def search_tools(self, search_term: str) -> List[ToolNode]:
        """Search tools by description, name, or tags"""
        try:
            query = f"""
            FOR tool IN {self.collection_name}
            FILTER CONTAINS(tool.description, @search_term, true) OR
                   CONTAINS(tool.tool_name, @search_term, true) OR
                   CONTAINS(tool.app_name, @search_term, true) OR
                   @search_term IN tool.tags
            RETURN tool
            """

            result = await self.graph_service.execute_query(query, {"search_term": search_term})
            if result is None:
                return []
            return [ToolNode.from_dict(tool) for tool in result]

        except Exception as e:
            self.logger.error(f"Failed to search tools with term {search_term}: {e}")
            return []

    async def get_connector_ctag(self, connector_name: str) -> Optional[str]:
        """Get the current ctag for a connector"""
        try:
            result = await self.graph_service.get_document(
                self.ctag_collection_name,
                connector_name
            )

            if result:
                return result.get("ctag")
            return None

        except Exception as e:
            self.logger.error(f"Failed to get ctag for connector {connector_name}: {e}")
            return None

    async def get_all_tools(self) -> List[ToolNode]:
        """Get all tools from ArangoDB"""
        try:
            query = f"""
            FOR tool IN {self.collection_name}
            RETURN tool
            """

            result = await self.graph_service.execute_query(query)
            if result is None:
                return []
            return [ToolNode.from_dict(tool) for tool in result]

        except Exception as e:
            self.logger.error(f"Failed to get all tools: {e}")
            return []

    async def delete_tool(self, app_name: str, tool_name: str) -> bool:
        """Delete a tool from ArangoDB"""
        try:
            tool_id = self._generate_tool_id(app_name, tool_name)

            await self.graph_service.delete_document(
                self.collection_name,
                tool_id
            )

            self.logger.info(f"Successfully deleted tool {app_name}.{tool_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to delete tool {app_name}.{tool_name}: {e}")
            return False
