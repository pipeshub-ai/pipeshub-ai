"""
Async HTTP Client for ArangoDB REST API

This client provides fully async operations for ArangoDB using REST API,
replacing the synchronous python-arango SDK to avoid blocking the event loop.

ArangoDB REST API Documentation: https://www.arangodb.com/docs/stable/http/
"""

from logging import Logger
from typing import Any, Dict, List, Optional, Union

import aiohttp

# HTTP Status Code Constants
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_ACCEPTED = 202
HTTP_NO_CONTENT = 204
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409


class ArangoHTTPClient:
    """Fully async HTTP client for ArangoDB REST API"""

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
        self.session: Optional[aiohttp.ClientSession] = None
        self.logger = logger

    async def connect(self) -> bool:
        """
        Create HTTP session and test connection.

        Returns:
            bool: True if connection successful
        """
        try:
            self.session = aiohttp.ClientSession(auth=self.auth)

            # Test connection
            async with self.session.get(f"{self.base_url}/_api/version") as resp:
                if resp.status == HTTP_OK:
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
        if self.session:
            await self.session.close()
            self.session = None
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

        async with self.session.get(url) as resp:
            if resp.status == HTTP_OK:
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

        async with self.session.post(url, json=payload) as resp:
            if resp.status in [HTTP_OK, HTTP_CREATED]:
                self.logger.info(f"✅ Database '{db_name}' created")
                return True
            elif resp.status == HTTP_CONFLICT:
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
            async with self.session.post(url, json=payload) as resp:
                if resp.status != HTTP_CREATED:
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
            async with self.session.put(url) as resp:
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
            async with self.session.delete(url) as resp:
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
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == HTTP_NOT_FOUND:
                    return None
                elif resp.status == HTTP_OK:
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
            async with self.session.post(url, json=document, headers=headers) as resp:
                if resp.status in [HTTP_CREATED, HTTP_ACCEPTED]:
                    result = await resp.json()
                    self._check_response_for_errors(result, "Create document")
                    return result
                elif resp.status == HTTP_CONFLICT:
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
            async with self.session.patch(url, json=updates, headers=headers) as resp:
                if resp.status in [HTTP_OK, HTTP_CREATED, HTTP_ACCEPTED]:
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
            async with self.session.delete(url, headers=headers) as resp:
                if resp.status in [HTTP_OK, HTTP_ACCEPTED, HTTP_NO_CONTENT]:
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
            async with self.session.post(url, json=payload, headers=headers) as resp:
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

                    async with self.session.put(cursor_url, headers=headers) as cursor_resp:
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
        overwrite: bool = True
    ) -> Dict[str, Any]:
        """
        Batch insert/update documents.

        Args:
            collection: Collection name
            documents: List of documents
            txn_id: Optional transaction ID
            overwrite: Whether to overwrite existing documents

        Returns:
            Dict: Result with created/updated counts

        Raises:
            Exception: If any document operation fails
        """
        if not documents:
            return {"created": 0, "updated": 0, "errors": 0}

        url = f"{self.base_url}/_db/{self.database}/_api/document/{collection}"
        params = {"overwrite": "true" if overwrite else "false"}
        headers = {"x-arango-trx-id": txn_id} if txn_id else {}

        try:
            async with self.session.post(
                url,
                json=documents,
                params=params,
                headers=headers
            ) as resp:
                if resp.status in [HTTP_CREATED, HTTP_ACCEPTED]:
                    result = await resp.json()
                    self.logger.debug(f"✅ Batch insert response: status={resp.status}, result={result}")

                    # Check for errors in response
                    self._check_response_for_errors(result, "Batch insert")

                    return {
                        "created": len(documents),
                        "updated": 0,
                        "errors": 0,
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
            async with self.session.delete(
                url,
                headers=headers,
                json=document_ids  # Send array of document IDs in request body
            ) as resp:
                if resp.status in [HTTP_OK, HTTP_ACCEPTED]:
                    results = await resp.json()
                    
                    # Results is an array of deletion results
                    deleted_count = 0
                    errors = []
                    
                    for idx, result in enumerate(results):
                        # Check if this deletion was successful
                        if result.get("error"):
                            # Handle 404 as success (document already deleted)
                            error_num = result.get("errorNum")
                            if error_num == 1202:  # Document not found
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
            async with self.session.post(url, json=edge_doc, headers=headers) as resp:
                if resp.status in [HTTP_CREATED, HTTP_ACCEPTED]:
                    result = await resp.json()
                    self._check_response_for_errors(result, "Create edge")
                    return result
                elif resp.status == HTTP_CONFLICT:
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

                async with self.session.delete(url, headers=headers) as resp:
                    return resp.status in [HTTP_OK, HTTP_ACCEPTED, HTTP_NO_CONTENT]

            return False

        except Exception as e:
            self.logger.error(f"❌ Error deleting edge: {str(e)}")
            return False

    # ==================== Collection Operations ====================

    async def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists"""
        url = f"{self.base_url}/_db/{self.database}/_api/collection/{collection_name}"

        try:
            async with self.session.get(url) as resp:
                return resp.status == HTTP_OK
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
            async with self.session.post(url, json=payload) as resp:
                if resp.status in [HTTP_OK, HTTP_CREATED]:
                    self.logger.info(f"✅ Collection '{name}' created")
                    return True
                elif resp.status == HTTP_CONFLICT:
                    self.logger.debug(f"Collection '{name}' already exists")
                    return True
                else:
                    error = await resp.text()
                    self.logger.error(f"❌ Failed to create collection: {error}")
                    return False

        except Exception as e:
            self.logger.error(f"❌ Error creating collection: {str(e)}")
            return False

    # ==================== Helper Methods ====================

    async def _handle_response(self, resp: aiohttp.ClientResponse, operation: str) -> Optional[Dict]:
        """Helper to handle HTTP responses"""
        if resp.status in [HTTP_OK, HTTP_CREATED, HTTP_ACCEPTED]:
            return await resp.json()
        elif resp.status == HTTP_NOT_FOUND:
            return None
        else:
            error = await resp.text()
            self.logger.error(f"❌ {operation} failed ({resp.status}): {error}")
            return None

