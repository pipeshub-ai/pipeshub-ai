from app.services.graph_db.interface.graph_db import IGraphService
from app.config.configuration_service import ConfigurationService
from app.config.configuration_service import config_node_constants
from arango import ArangoClient # type: ignore
from typing import Optional
from logging import Logger

class ArangoService(IGraphService):
    def __init__(self, logger: Logger, config_service: ConfigurationService):
        self.logger = logger
        self.config_service = config_service
        self.client: Optional[ArangoClient] = None # type: ignore
        self.db = None

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

    async def get_service_name(self):
        return "arango"

    async def get_service_client(self):
        return self.client

    async def connect(self):
        """Connect to ArangoDB and initialize collections"""
        try:
            self.logger.info("🚀 Connecting to ArangoDB...")
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            arango_url = arangodb_config["url"]
            arango_user = arangodb_config["username"]
            arango_password = arangodb_config["password"]
            arango_db = arangodb_config["db"]

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
            self.logger.info("✅ Database created successfully")

            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("Our DB: %s", self.db)

            return True
        except Exception as e:
            self.logger.error("❌ Failed to connect to ArangoDB: %s", str(e))
            self.client = None
            self.db = None

            return False

    async def disconnect(self):
        """Disconnect from ArangoDB"""
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB")
            if self.client:
                self.client.close()
            self.client = None
            self.db = None
            self.logger.info("✅ Disconnected from ArangoDB successfully")
            return True
        except Exception as e:
            self.logger.error("❌ Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def __fetch_arango_host(self) -> str:
        """Fetch ArangoDB host URL from etcd asynchronously."""
        arango_config = await self.config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        return arango_config["url"]

    async def __create_arango_client(self) -> ArangoClient:
        """Async factory method to initialize ArangoClient."""
        hosts = await self.__fetch_arango_host()
        return ArangoClient(hosts=hosts)

    async def create_graph(self, graph_name: str):
        """Create a new graph"""
        pass

    async def create_node(self, node_type: str, node_id: str):
        """Create a new node"""
        pass

    async def create_edge(self, edge_type: str, from_node: str, to_node: str):
        """Create a new edge"""
        pass

    async def delete_graph(self, graph_name: str):
        """Delete a graph"""
        pass

    async def delete_node(self, node_type: str, node_id: str):
        """Delete a node"""
        pass

    async def delete_edge(self, edge_type: str, from_node: str, to_node: str):
        """Delete an edge"""
        pass

    async def get_node(self, node_type: str, node_id: str):
        """Get a node"""
        pass

    async def get_edge(self, edge_type: str, from_node: str, to_node: str):
        """Get an edge"""
        pass

    async def get_nodes(self, node_type: str):
        """Get all nodes of a given type"""
        pass

    async def get_edges(self, edge_type: str):
        """Get all edges of a given type"""
        pass