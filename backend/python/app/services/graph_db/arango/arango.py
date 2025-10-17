import logging
from logging import Logger
from typing import Any, Dict, List, Optional, Union

from arango.client import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.models.graph import Edge, Node
from app.services.graph_db.arango.config import ArangoConfig
from app.services.graph_db.interface.graph_db import IGraphService

logger = logging.getLogger(__name__)

# TODO: Remove Generic Exception Handling
class ArangoService(IGraphService):
    def __init__(self, logger: Logger, config_service: Union[ArangoConfig, ConfigurationService]) -> None:
        self.logger = logger
        self.config_service = config_service
        self.client: Optional[ArangoClient] = None
        self.db: Optional[Any] = None

    @classmethod
    async def create(cls, logger: Logger, config_service: Union[ArangoConfig, ConfigurationService]) -> 'ArangoService':
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
            if isinstance(self.config_service, ArangoConfig):
                arangodb_config = self.config_service.to_dict()
            else:
                arangodb_config = await self.config_service.get_config(
                    config_node_constants.ARANGODB.value
                )

            if not arangodb_config or not isinstance(arangodb_config, dict):
                raise ValueError("ArangoDB configuration not found or invalid")

            arango_url = arangodb_config.get("url")
            arango_user = arangodb_config.get("username")
            arango_password = arangodb_config.get("password")
            arango_db = arangodb_config.get("db")

            self.logger.info(f"Connecting to ArangoDB at: {arango_url}")

            if not all([arango_url, arango_user, arango_password, arango_db]):
                raise ValueError("Missing required ArangoDB configuration values")

            # Type assertion after validation
            arango_url = str(arango_url)
            arango_user = str(arango_user)
            arango_password = str(arango_password)
            arango_db = str(arango_db)

            if not self.client:
                self.logger.error("ArangoDB client not initialized")
                return False

            # Connect to system db to ensure our db exists
            sys_db = self.client.db(
                "_system", username=arango_user, password=arango_password, verify=False
            )
            self.logger.info("System database connected")

            # Check if our database exists, create it if it doesn't
            if not sys_db.has_database(arango_db):
                self.logger.info(f"Database '{arango_db}' does not exist, creating it...")
                sys_db.create_database(
                    name=arango_db,
                    users=[{"username": arango_user, "password": arango_password, "active": True}]
                )
                self.logger.info(f"✅ Database '{arango_db}' created successfully")
            else:
                self.logger.info(f"Database '{arango_db}' already exists")

            # Connect to our database
            self.logger.info(f"Connecting to database '{arango_db}'...")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=False
            )
            self.logger.info("✅ Database connected successfully")
            self.logger.debug(f"Database object: {self.db}")

            return True
        except Exception as e:
            self.logger.error("Failed to connect to ArangoDB: %s", str(e))
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
            self.logger.error("Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def __fetch_arango_host(self) -> str:
        """Fetch ArangoDB host URL from etcd asynchronously."""
        if isinstance(self.config_service, ArangoConfig):
            arango_config = self.config_service.to_dict()
        else:
            arango_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )

        if not arango_config or not isinstance(arango_config, dict):
            raise ValueError("ArangoDB configuration not found or invalid")

        url = arango_config.get('url')
        if not url:
            raise ValueError("ArangoDB URL not found in configuration")
        return url

    async def __create_arango_client(self) -> ArangoClient:
        """Async factory method to initialize ArangoClient."""
        hosts = await self.__fetch_arango_host()
        return ArangoClient(hosts=hosts)

    async def create_graph(self, graph_name: str) -> bool:
        """Create a new graph
        Args:
            graph_name: The name of the graph to create
        Returns:
            bool: True if the graph was created successfully, False otherwise
        """
        if not self.db:
            self.logger.error("Database not connected")
            return False

        self.db.create_graph(graph_name)
        self.logger.info(f"✅ Created graph: {graph_name}")
        return True

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

    # Implementation of new interface methods
    async def create_collection(self, collection_name: str) -> bool:
        """Create a new collection in ArangoDB"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return False

            # Check if collection already exists
            if self.db.has_collection(collection_name):
                self.logger.debug(f"Collection {collection_name} already exists")
                return True

            # Create the collection
            self.db.create_collection(collection_name)
            self.logger.info(f"✅ Created collection: {collection_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to create collection {collection_name}: {e}")
            return False

    async def upsert_document(self, collection_name: str, document: Dict[str, Any]) -> bool:
        """Insert or update a document in a collection using atomic AQL UPSERT"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return False

            # Ensure document has a _key for upsert operation
            if "_key" not in document:
                self.logger.error("Document must have a _key for upsert operation")
                return False

            # Use AQL UPSERT for atomic operation
            upsert_query = f"""
            UPSERT {{ _key: @_key }}
            INSERT @document
            UPDATE @document
            IN {collection_name}
            """

            bind_vars = {
                "_key": document["_key"],
                "document": document
            }

            result = await self.execute_query(upsert_query, bind_vars)

            # UPSERT without RETURN clause returns empty list on success
            if result is not None:
                self.logger.debug(f"Upserted document {document['_key']} in {collection_name}")
                return True
            else:
                self.logger.error(f"Upsert operation failed for document {document['_key']} in {collection_name}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to upsert document in {collection_name}: {e}")
            return False

    async def upsert_document_with_merge(self, collection_name: str, document: Dict[str, Any], merge_strategy: str = "merge") -> Optional[Dict[str, Any]]:
        """
        Insert or update a document with custom merge strategy using AQL UPSERT
        Args:
            collection_name: Name of the collection
            document: Document to upsert
            merge_strategy: Merge strategy - 'merge', 'replace', or 'keep'
        Returns:
            The upserted document or None if failed
        """
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return None

            # Ensure document has a _key for upsert operation
            if "_key" not in document:
                self.logger.error("Document must have a _key for upsert operation")
                return None

            # Build merge logic based on strategy
            if merge_strategy == "merge":
                # Merge: combine existing and new fields
                upsert_query = f"""
                UPSERT {{ _key: @_key }}
                INSERT @document
                UPDATE MERGE(OLD, @document)
                IN {collection_name}
                RETURN NEW
                """
            elif merge_strategy == "replace":
                # Replace: completely replace existing document
                upsert_query = f"""
                UPSERT {{ _key: @_key }}
                INSERT @document
                UPDATE @document
                IN {collection_name}
                RETURN NEW
                """
            elif merge_strategy == "keep":
                # Keep: only insert if doesn't exist, don't update
                upsert_query = f"""
                UPSERT {{ _key: @_key }}
                INSERT @document
                UPDATE OLD
                IN {collection_name}
                RETURN NEW
                """
            else:
                self.logger.error(f"Invalid merge strategy: {merge_strategy}")
                return None

            bind_vars = {
                "_key": document["_key"],
                "document": document
            }

            result = await self.execute_query(upsert_query, bind_vars)

            if result is not None and len(result) > 0:
                self.logger.debug(f"Upserted document {document['_key']} in {collection_name} with strategy '{merge_strategy}'")
                return result[0]  # Return the upserted document
            else:
                self.logger.error(f"Upsert operation failed for document {document['_key']} in {collection_name}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to upsert document in {collection_name}: {e}")
            return None

    async def batch_upsert_documents(self, collection_name: str, documents: List[Dict[str, Any]], merge_strategy: str = "merge") -> bool:
        """
        Batch upsert multiple documents using AQL UPSERT for better performance
        Args:
            collection_name: Name of the collection
            documents: List of documents to upsert
            merge_strategy: Merge strategy - 'merge', 'replace', or 'keep'
        Returns:
            True if all documents were upserted successfully, False otherwise
        """
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return False

            if not documents:
                self.logger.warning("No documents provided for batch upsert")
                return True

            # Validate all documents have _key
            for doc in documents:
                if "_key" not in doc:
                    self.logger.error(f"Document missing _key: {doc}")
                    return False

            # Build merge logic based on strategy
            if merge_strategy == "merge":
                update_logic = "UPDATE MERGE(OLD, doc)"
            elif merge_strategy == "replace":
                update_logic = "UPDATE doc"
            elif merge_strategy == "keep":
                update_logic = "UPDATE OLD"
            else:
                self.logger.error(f"Invalid merge strategy: {merge_strategy}")
                return False

            # Use AQL UPSERT with FOR loop for batch processing
            batch_upsert_query = f"""
            FOR doc IN @documents
            UPSERT {{ _key: doc._key }}
            INSERT doc
            {update_logic}
            IN {collection_name}
            """

            bind_vars = {
                "documents": documents
            }

            result = await self.execute_query(batch_upsert_query, bind_vars)

            if result is not None:
                self.logger.debug(f"Batch upserted {len(documents)} documents in {collection_name} with strategy '{merge_strategy}'")
                return True
            else:
                self.logger.error(f"Batch upsert operation failed for {len(documents)} documents in {collection_name}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to batch upsert documents in {collection_name}: {e}")
            return False

    async def get_document(self, collection_name: str, document_key: str) -> Optional[Dict[str, Any]]:
        """Get a document by key from a collection"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return None

            collection = self.db.collection(collection_name)

            try:
                document = collection.get(document_key)
                return document
            except Exception:
                # Document not found
                return None

        except Exception as e:
            self.logger.error(f"Failed to get document {document_key} from {collection_name}: {e}")
            return None

    async def delete_document(self, collection_name: str, document_key: str) -> bool:
        """Delete a document by key from a collection"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return False

            collection = self.db.collection(collection_name)

            try:
                collection.delete(document_key)
                self.logger.debug(f"Deleted document {document_key} from {collection_name}")
                return True
            except Exception as e:
                self.logger.error(f"Document {document_key} not found in {collection_name}: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to delete document {document_key} from {collection_name}: {e}")
            return False

    async def execute_query(self, query: str, bind_vars: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
        """Execute an AQL query"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return None

            if bind_vars is None:
                bind_vars = {}

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = [doc for doc in cursor]

            self.logger.debug(f"Executed query: {query}")
            return result

        except Exception as e:
            self.logger.error(f"Failed to execute query: {e}")
            return None

    async def create_index(self, collection_name: str, fields: List[str], index_type: str = "persistent") -> bool:
        """Create an index on a collection"""
        try:
            if not self.db:
                self.logger.error("Database not connected")
                return False

            collection = self.db.collection(collection_name)

            # Check if index already exists
            existing_indexes = collection.indexes()
            for index in existing_indexes:
                if index.get("fields") == fields and index.get("type") == index_type:
                    self.logger.debug(f"Index already exists on {fields} in {collection_name}")
                    return True

            # Create the index
            if index_type == "persistent":
                collection.ensure_persistent_index(fields)
            elif index_type == "hash":
                collection.ensure_hash_index(fields)
            elif index_type == "skiplist":
                collection.ensure_skiplist_index(fields)
            elif index_type == "ttl":
                collection.ensure_ttl_index(fields)
            else:
                self.logger.warning(f"Unsupported index type: {index_type}")
                return False

            self.logger.info(f"✅ Created {index_type} index on {fields} in {collection_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to create index on {fields} in {collection_name}: {e}")
            return False
