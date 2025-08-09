from logging import Logger
from typing import Any, List, Optional

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.models.graph import Edge, Node
from app.services.graph_db.interface.graph_db import IGraphService


# TODO: Remove Generic Exception Handling
class ArangoService(IGraphService):
    def __init__(self, logger: Logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.client: Optional[ArangoClient] = None # type: ignore
        self.db: Optional[Any] = None # type: ignore

    @classmethod
    async def create(cls, logger: Logger, config_service: ConfigurationService) -> 'ArangoService':
        """
        Factory method to create and initialize an ArangoService instance.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            ArangoService: Initialized ArangoService instance
        """
        service = cls(logger, config_service)
        service.client = await service.__create_arango_client()
        return service

    async def get_service_name(self) -> str:
        return "arango"

    async def get_service_client(self) -> object:
        return self.client

    async def connect(self) -> bool:
        """Connect to ArangoDB and initialize collections"""
        try:
            self.logger.info("🚀 Connecting to ArangoDB...")
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            if not arangodb_config or not isinstance(arangodb_config, dict):
                raise ValueError("ArangoDB configuration not found or invalid")

            arango_url = arangodb_config.get("url")
            arango_user = arangodb_config.get("username")
            arango_password = arangodb_config.get("password")
            arango_db = arangodb_config.get("db")

            if not all([arango_url, arango_user, arango_password, arango_db]):
                raise ValueError("Missing required ArangoDB configuration values")

            if not isinstance(arango_url, str):
                raise ValueError("ArangoDB URL must be a string")
            if not self.client:
                self.logger.error("ArangoDB client not initialized")
                return False

            # Connect to system db to ensure our db exists
            self.logger.debug("Connecting to system db")
            sys_db = self.client.db(
                "_system", username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("System DB: %s", sys_db)
            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=True
            )
            self.logger.info("✅ Database connected")
            self.logger.debug("Our DB: %s", self.db)

            return True
        except Exception as e:
            self.logger.error("❌ Failed to connect to ArangoDB: %s", str(e))
            self.client = None
            self.db = None

            return False

    async def disconnect(self) -> bool:
        """Disconnect from ArangoDB"""
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB")
            if self.client:
                self.client.close()
            self.client = None
            self.db = None
            self.logger.info("✅ Disconnected from ArangoDB")
            return True
        except Exception as e:
            self.logger.error("❌ Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def __fetch_arango_host(self) -> str:
        """Fetch ArangoDB host URL from etcd asynchronously."""
        arango_config = await self.config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        if not arango_config or not isinstance(arango_config, dict):
            raise ValueError("ArangoDB configuration not found or invalid")

        url = arango_config.get("url")
        if not url:
            raise ValueError("ArangoDB URL not found in configuration")
        return url

    async def __create_arango_client(self) -> ArangoClient:  # type: ignore
        """Async factory method to initialize ArangoClient."""
        hosts = await self.__fetch_arango_host()
        return ArangoClient(hosts=hosts)  # type: ignore

    async def create_graph(self, graph_name: str) -> bool:
        """Create a new graph"""
        return False

    async def create_node(self, node_type: str, node_id: str) -> bool:
        """Create a new node"""
        return False

    async def create_edge(self, edge_type: str, from_node: str, to_node: str) -> bool:
        """Create a new edge"""
        return False

    async def delete_graph(self) -> bool:
        """Delete a graph"""
        return False

    async def delete_node(self, node_type: str, node_id: str) -> bool:
        """Delete a node"""
        return False

    async def delete_edge(self, edge_type: str, from_node: str, to_node: str) -> bool:
        """Delete an edge"""
        return False

    async def get_node(self, node_type: str, node_id: str) -> Node | None:
        """Get a node"""
        return None

    async def get_edge(self, edge_type: str, from_node: str, to_node: str) -> Edge | None:
        """Get an edge"""
        return None # type: ignore

    async def get_nodes(self, node_type: str) -> List[Node]:
        """Get all nodes of a given type"""
        return []

    async def get_edges(self, edge_type: str) -> List[Edge]:
        """Get all edges of a given type"""
        return []
