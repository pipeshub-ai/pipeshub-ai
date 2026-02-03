"""
Async HTTP Client for ArangoDB REST API

This client provides fully async operations for ArangoDB using REST API,
replacing the synchronous python-arango SDK to avoid blocking the event loop.

ArangoDB REST API Documentation: https://www.arangodb.com/docs/stable/http/
"""

import asyncio
from logging import Logger
from typing import Any, Dict, List, Optional, Union

import aiohttp

from app.config.constants.http_status_code import HttpStatusCode

# ArangoDB Error Code Constants
ARANGO_ERROR_DOCUMENT_NOT_FOUND = 1202


class ArangoHTTPClient:
    """Fully async HTTP client for ArangoDB REST API

    Uses session-per-event-loop pattern to handle Windows async compatibility.
    Sessions are reused within the same event loop but recreated if the loop changes.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        database: str,
        logger: Logger
    ) -> None:
        """
        Initialize ArangoDB HTTP client.

        Args:
            base_url: ArangoDB server URL (e.g., http://localhost:8529)
            username: Database username
            password: Database password
            database: Database name
            logger: Logger instance
        """
        self.base_url = base_url.rstrip('/')
        self.database = database
        self.username = username
        self.password = password
        self.auth = aiohttp.BasicAuth(username, password)
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_loop: Optional[asyncio.AbstractEventLoop] = None
        self.logger = logger

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create a session for the current event loop.

        This handles Windows async compatibility by detecting event loop changes
        and creating new sessions when needed. Sessions are reused within the
        same event loop for efficiency.

        Returns:
            aiohttp.ClientSession: Session for the current event loop
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # Check if we need a new session (no session, or loop changed)
        if self._session is None or self._session_loop != current_loop:
            # Close old session if exists
            if self._session is not None:
                try:
                    await self._session.close()
                except Exception:
                    pass  # Ignore errors closing old session

            # Create new session for current loop
            self._session = aiohttp.ClientSession(auth=self.auth)
            self._session_loop = current_loop
            self.logger.debug("🔄 Created new HTTP session for current event loop")

        return self._session

    async def connect(self) -> bool:
        """
        Test connection to ArangoDB.

        Returns:
            bool: True if connection successful
        """
        try:
            session = await self._get_session()

            # Test connection
            async with session.get(f"{self.base_url}/_api/version") as resp:
                if resp.status == HttpStatusCode.OK.value:
                    version_info = await resp.json()
                    self.logger.info(f"✅ Connected to ArangoDB {version_info.get('version')}")
                    return True
                else:
                    self.logger.error(f"❌ Connection test failed: {resp.status}")
                    return False

        except Exception as e:
            self.logger.error(f"❌ Failed to connect to ArangoDB: {str(e)}")
            return False

    async def disconnect(self) -> None:
        """Close HTTP session"""
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
            self._session_loop = None
            self.logger.info("✅ Disconnected from ArangoDB")

    # ==================== Error Checking Helpers ====================

    def _check_response_for_errors(self, result: Union[Dict, List], operation: str = "operation") -> None:
        """
        Check ArangoDB response for error flags and raise exception if found.

        ArangoDB returns HTTP 202 even when operations fail, so we must check
        the response body for error flags.

        Args:
            result: Response JSON (can be dict, list, or other)
            operation: Description of the operation for error messages

        Raises:
            Exception: If error flag is found in response
        """
        # Check if result is a list (batch response)
        if isinstance(result, list):
            errors = []
            for idx, item in enumerate(result):
                if isinstance(item, dict) and item.get('error') is True:
                    error_msg = item.get('errorMessage', 'Unknown error')
                    error_num = item.get('errorNum', 'Unknown')
                    errors.append(f"Item {idx}: [{error_num}] {error_msg}")

            if errors:
                error_details = "; ".join(errors)
                self.logger.error(f"❌ {operation} had {len(errors)} error(s): {error_details}")
                raise Exception(f"{operation} failed with {len(errors)} error(s): {error_details}")

        # Check if result is a dict with error flag
        elif isinstance(result, dict) and result.get('error') is True:
            error_msg = result.get('errorMessage', 'Unknown error')
            error_num = result.get('errorNum', 'Unknown')
            self.logger.error(f"❌ {operation} failed: [{error_num}] {error_msg}")
            raise Exception(f"{operation} failed: [{error_num}] {error_msg}")

    # ==================== Database Management ====================

    async def database_exists(self, db_name: str) -> bool:
        """Check if database exists"""
        url = f"{self.base_url}/_api/database"
        session = await self._get_session()

        async with session.get(url) as resp:
            if resp.status == HttpStatusCode.OK.value:
                result = await resp.json()
                return db_name in result.get("result", [])
            return False

    async def create_database(self, db_name: str) -> bool:
        """Create database"""
        url = f"{self.base_url}/_api/database"

        payload = {
            "name": db_name,
            "users": [
                {
                    "username": self.username,
                    "passwd": self.password,
                    "active": True
                }
            ]
        }

        session = await self._get_session()
        async with session.post(url, json=payload) as resp:
            if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.CREATED.value]:
                self.logger.info(f"✅ Database '{db_name}' created")
                return True
            elif resp.status == HttpStatusCode.CONFLICT.value:
                self.logger.info(f"Database '{db_name}' already exists")
                return True
            else:
                error = await resp.text()
                self.logger.error(f"❌ Failed to create database: {error}")
                return False

    # ==================== Transaction Management ====================

    async def begin_transaction(self, read: List[str], write: List[str]) -> str:
        """
        Begin a database transaction.

        Args:
            read: Collections to read from
            write: Collections to write to

        Returns:
            str: Transaction ID
        """
        url = f"{self.base_url}/_db/{self.database}/_api/transaction/begin"

        payload = {
            "collections": {
                "read": read,
                "write": write,
                "exclusive": []
            }
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status != HttpStatusCode.CREATED.value:
                    error = await resp.text()
                    raise Exception(f"Failed to begin transaction: {error}")

                result = await resp.json()
                txn_id = result["result"]["id"]
                self.logger.debug(f"🔄 Transaction started: {txn_id}")
                return txn_id

        except Exception as e:
            self.logger.error(f"❌ Failed to begin transaction: {str(e)}")
            raise

    async def commit_transaction(self, txn_id: str) -> None:
        """
        Commit a transaction.

        Args:
            txn_id: Transaction ID returned by begin_transaction
        """
        url = f"{self.base_url}/_db/{self.database}/_api/transaction/{txn_id}"

        try:
            session = await self._get_session()
            async with session.put(url) as resp:
                if resp.status not in [200, 204]:
                    error = await resp.text()
                    raise Exception(f"Failed to commit transaction: {error}")

                self.logger.debug(f"✅ Transaction committed: {txn_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to commit transaction: {str(e)}")
            raise

    async def abort_transaction(self, txn_id: str) -> None:
        """
        Abort a transaction.

        Args:
            txn_id: Transaction ID returned by begin_transaction
        """
        url = f"{self.base_url}/_db/{self.database}/_api/transaction/{txn_id}"

        try:
            session = await self._get_session()
            async with session.delete(url) as resp:
                if resp.status not in [200, 204]:
                    error = await resp.text()
                    raise Exception(f"Failed to abort transaction: {error}")

                self.logger.debug(f"🔄 Transaction aborted: {txn_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to abort transaction: {str(e)}")
            raise

    # ==================== Document Operations ====================

    async def get_document(
        self,
        collection: str,
        key: str,
        txn_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a document by key.

        Args:
            collection: Collection name
            key: Document key
            txn_id: Optional transaction ID

        Returns:
            Optional[Dict]: Document data or None if not found
        """
        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}/{key}"

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == HttpStatusCode.NOT_FOUND.value:
                    return None
                elif resp.status == HttpStatusCode.OK.value:
                    return await resp.json()
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to get document: {error}")
                    return None

        except Exception as e:
            self.logger.error(f"❌ Error getting document: {str(e)}")
            return None

    async def create_document(
        self,
        collection: str,
        document: Dict,
        txn_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Create a document.

        Args:
            collection: Collection name
            document: Document data
            txn_id: Optional transaction ID

        Returns:
            Optional[Dict]: Created document metadata

        Raises:
            Exception: If document creation fails
        """
        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}"

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.post(url, json=document, headers=headers) as resp:
                if resp.status in [HttpStatusCode.CREATED.value, HttpStatusCode.ACCEPTED.value]:
                    result = await resp.json()
                    self._check_response_for_errors(result, "Create document")
                    return result
                elif resp.status == HttpStatusCode.CONFLICT.value:
                    # Document already exists, treat as success
                    return {"_key": document.get("_key")}
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to create document: {error}")
                    raise Exception(f"Failed to create document (status={resp.status}): {error}")

        except Exception as e:
            self.logger.error(f"❌ Error creating document: {str(e)}")
            raise

    async def update_document(
        self,
        collection: str,
        key: str,
        updates: Dict,
        txn_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Update a document.

        Args:
            collection: Collection name
            key: Document key
            updates: Fields to update
            txn_id: Optional transaction ID

        Returns:
            Optional[Dict]: Updated document metadata

        Raises:
            Exception: If document update fails
        """
        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}/{key}"

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.patch(url, json=updates, headers=headers) as resp:
                if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.CREATED.value, HttpStatusCode.ACCEPTED.value]:
                    result = await resp.json()
                    self._check_response_for_errors(result, "Update document")
                    return result
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to update document: {error}")
                    raise Exception(f"Failed to update document (status={resp.status}): {error}")

        except Exception as e:
            self.logger.error(f"❌ Error updating document: {str(e)}")
            raise

    async def delete_document(
        self,
        collection: str,
        key: str,
        txn_id: Optional[str] = None
    ) -> bool:
        """
        Delete a document.

        Args:
            collection: Collection name
            key: Document key
            txn_id: Optional transaction ID

        Returns:
            bool: True if successful

        Raises:
            Exception: If document deletion fails
        """
        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}/{key}"

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.delete(url, headers=headers) as resp:
                if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.ACCEPTED.value, HttpStatusCode.NO_CONTENT.value]:
                    # Try to parse response for error checking
                    try:
                        result = await resp.json()
                        self._check_response_for_errors(result, "Delete document")
                    except (ValueError, TypeError, aiohttp.ContentTypeError):
                        # Response might be empty for successful deletes
                        pass
                    return True
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to delete document: {error}")
                    raise Exception(f"Failed to delete document (status={resp.status}): {error}")

        except Exception as e:
            self.logger.error(f"❌ Error deleting document: {str(e)}")
            raise

    # ==================== Query Operations ====================

    async def execute_aql(
        self,
        query: str,
        bind_vars: Optional[Dict] = None,
        txn_id: Optional[str] = None,
        batch_size: int = 1000
    ) -> List[Dict]:
        """
        Execute AQL query.

        Args:
            query: AQL query string
            bind_vars: Query bind variables
            txn_id: Optional transaction ID
            batch_size: Batch size for cursor

        Returns:
            List[Dict]: Query results

        Raises:
            Exception: If query execution fails
        """
        url = f"{self.base_url}/_db/{self.database}/_api/cursor"

        payload = {
            "query": query,
            "bindVars": bind_vars or {},
            "count": True,
            "batchSize": batch_size
        }

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status not in [200, 201]:
                    error = await resp.text()
                    raise Exception(f"Query failed (status={resp.status}): {error}")

                result = await resp.json()
                self._check_response_for_errors(result, "Query execution")
                results = result.get("result", [])

                # Handle cursor for large result sets
                while result.get("hasMore"):
                    cursor_id = result.get("id")
                    cursor_url = f"{self.base_url}/_db/{self.database}/_api/cursor/{cursor_id}"

                    async with session.put(cursor_url, headers=headers) as cursor_resp:
                        if cursor_resp.status not in [200, 201]:
                            error = await cursor_resp.text()
                            raise Exception(f"Cursor fetch failed (status={cursor_resp.status}): {error}")

                        result = await cursor_resp.json()
                        self._check_response_for_errors(result, "Cursor fetch")
                        results.extend(result.get("result", []))

                return results

        except Exception as e:
            self.logger.error(f"❌ Query execution failed: {str(e)}")
            raise

    # ==================== Batch Operations ====================

    async def batch_insert_documents(
    self,
    collection: str,
    documents: List[Dict],
    txn_id: Optional[str] = None,
    overwrite: bool = True,
    overwrite_mode: str = "update"  # New parameter: "replace", "update", "ignore", or "conflict"
) -> Dict[str, Any]:
        """
        Batch insert/update documents.

        Args:
            collection: Collection name
            documents: List of documents
            txn_id: Optional transaction ID
            overwrite: Whether to overwrite existing documents (legacy param, use overwrite_mode instead)
            overwrite_mode: How to handle existing documents:
                - "replace": Replace entire document (old behavior, destructive)
                - "update": Merge/partial update, preserves fields not in input (recommended)
                - "ignore": Keep existing document, ignore new one
                - "conflict": Return error if document exists

        Returns:
            Dict: Result with created/updated counts

        Raises:
            Exception: If any document operation fails
        """
        if not documents:
            return {"created": 0, "updated": 0, "errors": 0}

        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}"

        # Build params - overwriteMode takes precedence over legacy overwrite param
        params = {}
        if overwrite:
            params["overwriteMode"] = overwrite_mode
        else:
            params["overwrite"] = "false"

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.post(
                url,
                json=documents,
                params=params,
                headers=headers
            ) as resp:
                if resp.status in [HttpStatusCode.CREATED.value, HttpStatusCode.ACCEPTED.value]:
                    result = await resp.json()
                    self.logger.debug(f"✅ Batch insert response: status={resp.status}, result={result}")

                    # Check for errors in response
                    self._check_response_for_errors(result, "Batch insert")

                    # Count created vs updated based on response
                    created_count = 0
                    updated_count = 0
                    error_count = 0

                    if isinstance(result, list):
                        for item in result:
                            if isinstance(item, dict):
                                if item.get("error"):
                                    error_count += 1
                                elif item.get("_oldRev"):
                                    # Document had a previous revision = updated
                                    updated_count += 1
                                else:
                                    # New document = created
                                    created_count += 1
                    else:
                        # Single document response
                        created_count = 1

                    return {
                        "created": created_count,
                        "updated": updated_count,
                        "errors": error_count,
                        "result": result
                    }
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Batch insert failed (status={resp.status}): {error}")
                    raise Exception(f"Batch insert failed (status={resp.status}): {error}")

        except Exception as e:
            self.logger.error(f"❌ Batch insert error: {str(e)}")
            raise

    async def batch_delete_documents(
        self,
        collection: str,
        keys: List[str],
        txn_id: Optional[str] = None
    ) -> int:
        """
        Batch delete documents using ArangoDB's batch deletion endpoint.

        Args:
            collection: Collection name
            keys: List of document keys
            txn_id: Optional transaction ID

        Returns:
            int: Number of documents successfully deleted

        Raises:
            Exception: If batch deletion fails with errors
        """
        if not keys:
            return 0

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        # ArangoDB batch delete endpoint
        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}"

        # Construct full document IDs
        document_ids = [f"{collection}/{key}" for key in keys]

        try:
            session = await self._get_session()
            async with session.delete(
                url,
                headers=headers,
                json=document_ids  # Send array of document IDs in request body
            ) as resp:
                if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.ACCEPTED.value]:
                    results = await resp.json()

                    # Results is an array of deletion results
                    deleted_count = 0
                    errors = []

                    for idx, result in enumerate(results):
                        # Check if this deletion was successful
                        if result.get("error"):
                            # Handle 404 as success (document already deleted)
                            error_num = result.get("errorNum")
                            if error_num == ARANGO_ERROR_DOCUMENT_NOT_FOUND:  # Document not found
                                deleted_count += 1
                                self.logger.debug(f"Document {document_ids[idx]} already deleted (404)")
                            else:
                                error_msg = result.get("errorMessage", "Unknown error")
                                errors.append(f"Document {document_ids[idx]}: (errorNum={error_num}) {error_msg}")
                        else:
                            deleted_count += 1

                    if errors:
                        error_details = "; ".join(errors)
                        self.logger.error(f"❌ Batch delete had {len(errors)} error(s): {error_details}")
                        raise Exception(f"Batch delete failed with {len(errors)} error(s): {error_details}")

                    self.logger.info(f"✅ Batch deleted {deleted_count} documents from {collection}")
                    return deleted_count

                else:
                    error_text = await resp.text()
                    self.logger.error(f"❌ Batch delete failed with status {resp.status}: {error_text}")
                    raise Exception(f"Batch delete failed: HTTP {resp.status} - {error_text}")

        except Exception as e:
            self.logger.error(f"❌ Batch delete failed: {str(e)}")
            raise

    # ==================== Edge Operations ====================

    async def create_edge(
        self,
        edge_collection: str,
        from_id: str,
        to_id: str,
        edge_data: Optional[Dict] = None,
        txn_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Create an edge.

        Args:
            edge_collection: Edge collection name
            from_id: Source document ID (e.g., "users/123")
            to_id: Target document ID (e.g., "records/456")
            edge_data: Additional edge properties
            txn_id: Optional transaction ID

        Returns:
            Optional[Dict]: Created edge metadata

        Raises:
            Exception: If edge creation fails
        """
        url = f"{self.base_url}/_db/{self.database}/_api/document/{edge_collection}"

        edge_doc = edge_data.copy() if edge_data else {}
        edge_doc["_from"] = from_id
        edge_doc["_to"] = to_id

        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            session = await self._get_session()
            async with session.post(url, json=edge_doc, headers=headers) as resp:
                if resp.status in [HttpStatusCode.CREATED.value, HttpStatusCode.ACCEPTED.value]:
                    result = await resp.json()
                    self._check_response_for_errors(result, "Create edge")
                    return result
                elif resp.status == HttpStatusCode.CONFLICT.value:
                    # Edge already exists
                    return {"_key": edge_doc.get("_key")}
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to create edge: {error}")
                    raise Exception(f"Failed to create edge (status={resp.status}): {error}")

        except Exception as e:
            self.logger.error(f"❌ Error creating edge: {str(e)}")
            raise

    async def delete_edge(
        self,
        edge_collection: str,
        from_id: str,
        to_id: str,
        txn_id: Optional[str] = None
    ) -> bool:
        """
        Delete an edge between two nodes.

        Args:
            edge_collection: Edge collection name
            from_id: Source document ID
            to_id: Target document ID
            txn_id: Optional transaction ID

        Returns:
            bool: True if successful
        """
        # First find the edge
        query = f"""
        FOR edge IN {edge_collection}
            FILTER edge._from == @from_id AND edge._to == @to_id
            RETURN edge._key
        """

        try:
            edge_keys = await self.execute_aql(query, {"from_id": from_id, "to_id": to_id}, txn_id)

            if edge_keys:
                url = f"{self.base_url}/_db/{self.database}/_api/document/{edge_collection}/{edge_keys[0]}"
                headers = {"x-arango-trx-id": txn_id} if txn_id else {}

                session = await self._get_session()
                async with session.delete(url, headers=headers) as resp:
                    return resp.status in [HttpStatusCode.OK.value, HttpStatusCode.ACCEPTED.value, HttpStatusCode.NO_CONTENT.value]

            return False

        except Exception as e:
            self.logger.error(f"❌ Error deleting edge: {str(e)}")
            return False

    # ==================== Collection Operations ====================

    async def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists"""
        url = f"{self.base_url}/_db/{self.database}/_api/collection/{collection_name}"

        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                return resp.status == HttpStatusCode.OK.value
        except Exception:
            return False

    async def create_collection(
        self,
        name: str,
        edge: bool = False,
        wait_for_sync: bool = False
    ) -> bool:
        """
        Create a collection.

        Args:
            name: Collection name
            edge: Whether this is an edge collection
            wait_for_sync: Wait for data to be synced to disk

        Returns:
            bool: True if successful
        """
        url = f"{self.base_url}/_db/{self.database}/_api/collection"

        payload = {
            "name": name,
            "type": 3 if edge else 2,  # 3 = edge, 2 = document
            "waitForSync": wait_for_sync
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.CREATED.value]:
                    self.logger.info(f"✅ Collection '{name}' created")
                    return True
                elif resp.status == HttpStatusCode.CONFLICT.value:
                    self.logger.debug(f"Collection '{name}' already exists")
                    return True
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to create collection: {error}")
                    return False

        except Exception as e:
            self.logger.error(f"❌ Error creating collection: {str(e)}")
            return False

    # ==================== Graph Operations ====================

    async def has_collection(self, name: str) -> bool:
        """
        Check if a collection exists.

        Args:
            name: Collection name

        Returns:
            bool: True if collection exists, False otherwise
        """
        return await self.collection_exists(name)

    async def has_graph(self, graph_name: str) -> bool:
        """
        Check if a graph exists.

        Args:
            graph_name: Graph name

        Returns:
            bool: True if graph exists, False otherwise
        """
        return (await self.get_graph(graph_name)) is not None

    async def create_graph(
        self,
        graph_name: str,
        edge_definitions: List[Dict[str, Any]],
    ) -> bool:
        """
        Create a named graph with the given edge definitions.

        Args:
            graph_name: Graph name
            edge_definitions: List of edge definitions. Each dict must have:
                - "collection": edge collection name
                - "from": list of vertex collection names
                - "to": list of vertex collection names

        Returns:
            bool: True if successful
        """
        url = f"{self.base_url}/_db/{self.database}/_api/gharial"

        # Map to ArangoDB REST format: collection, from, to
        # Support both schema keys (edge_collection, from_vertex_collections, to_vertex_collections) and REST keys
        payload_definitions = [
            {
                "collection": ed.get("edge_collection", ed.get("collection", "")),
                "from": ed.get("from_vertex_collections", ed.get("from", [])),
                "to": ed.get("to_vertex_collections", ed.get("to", [])),
            }
            for ed in edge_definitions
        ]

        payload = {"name": graph_name, "edgeDefinitions": payload_definitions}

        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.CREATED.value]:
                    self.logger.info(f"✅ Graph '{graph_name}' created")
                    return True
                elif resp.status == HttpStatusCode.CONFLICT.value:
                    self.logger.debug(f"Graph '{graph_name}' already exists")
                    return True
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to create graph: {error}")
                    return False
        except Exception as e:
            self.logger.error(f"❌ Error creating graph: {str(e)}")
            return False

    async def get_graph(self, graph_name: str) -> Optional[Dict]:
        """
        Get graph definition including edge definitions.

        Args:
            graph_name: Graph name

        Returns:
            Optional[Dict]: Graph definition with edgeDefinitions, or None if not found
        """
        url = f"{self.base_url}/_db/{self.database}/_api/gharial/{graph_name}"

        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status == HttpStatusCode.OK.value:
                    return await resp.json()
                elif resp.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"Graph '{graph_name}' not found")
                    return None
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to get graph: {error}")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Error getting graph: {str(e)}")
            return None

    # ==================== Helper Methods ====================

    async def _handle_response(self, resp: aiohttp.ClientResponse, operation: str) -> Optional[Dict]:
        """Helper to handle HTTP responses"""
        if resp.status in [HttpStatusCode.OK.value, HttpStatusCode.CREATED.value, HttpStatusCode.ACCEPTED.value]:
            return await resp.json()
        elif resp.status == HttpStatusCode.NOT_FOUND.value:
            return None
        else:
            error = await resp.text()
            self.logger.error(f"❌ {operation} failed ({resp.status}): {error}")
            return None

