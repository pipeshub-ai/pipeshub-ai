import asyncio
import time
from typing import Any, Dict, List, Optional

from langchain.chat_models.base import BaseChatModel
from langchain.embeddings.base import Embeddings
from langchain.schema import Document
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import models

from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
)

# from langchain_cohere import CohereEmbeddings
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    RecordTypes,
)
from app.config.constants.service import config_node_constants
from app.connectors.services.base_arango_service import BaseArangoService
from app.exceptions.embedding_exceptions import EmbeddingModelCreationError
from app.exceptions.fastapi_responses import Status
from app.models.blocks import GroupType
from app.modules.transformers.blob_storage import BlobStorage
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.sources.client.http.exception.exception import VectorDBEmptyError
from app.utils.aimodels import (
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
)
from app.utils.chat_helpers import (
    get_flattened_results,
    get_record,
)
from app.utils.mimetype_to_extension import get_extension_from_mimetype

# OPTIMIZATION: User data cache with TTL
_user_cache: Dict[str, tuple] = {}  # {user_id: (user_data, timestamp)}
USER_CACHE_TTL = 300  # 5 minutes
MAX_USER_CACHE_SIZE = 1000  # Max number of users to keep in cache


class RetrievalService:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        collection_name: str,
        vector_db_service: IVectorDBService,
        arango_service: BaseArangoService,
        blob_store: BlobStorage,
    ) -> None:
        """
        Initialize the retrieval service with necessary configurations.

        Args:
            collection_name: Name of the collection
            vector_db_service: Vector DB service
            config_service: Configuration service
        """

        self.logger = logger
        self.config_service = config_service
        self.llm = None
        self.arango_service = arango_service
        self.blob_store = blob_store
        # Initialize sparse embeddings
        try:
            self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
        except Exception as e:
            self.logger.error("Failed to initialize sparse embeddings: " + str(e))
            self.sparse_embeddings = None
            raise Exception(
                "Failed to initialize sparse embeddings: " + str(e),
            )
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        self.logger.info(f"Retrieval service initialized with collection name: {self.collection_name}")
        self.vector_store = None
        self.embedding_model = None
        self.embedding_size = None
        self.embedding_model_instance = None

    async def get_llm_instance(self, use_cache: bool = True) -> Optional[BaseChatModel]:
        try:
            self.logger.info("Getting LLM")
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache
            )
            llm_configs = ai_models["llm"]

            # For now, we'll use the first available provider that matches our supported types
            # We will add logic to choose a specific provider based on our needs

            for config in llm_configs:
                if config.get("isDefault", False):
                    provider = config["provider"]
                    self.llm = get_generator_model(provider, config)
                if self.llm:
                    break

            if not self.llm:
                self.logger.info("No default LLM found, using first available provider")

            if not self.llm:
                for config in llm_configs:
                    provider = config["provider"]
                    self.llm = get_generator_model(provider, config)
                    if self.llm:
                        break
                if not self.llm:
                    raise ValueError("No supported LLM provider found in configuration")

            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def get_embedding_model_instance(self, use_cache: bool = True) -> Optional[Embeddings]:
        try:
            embedding_model = await self.get_current_embedding_model_name(use_cache)
            if self.embedding_model == embedding_model:
                return self.embedding_model_instance
            self.embedding_model = embedding_model
            try:
                if not embedding_model or embedding_model == DEFAULT_EMBEDDING_MODEL:
                    self.logger.info("Using default embedding model")
                    embedding_model = DEFAULT_EMBEDDING_MODEL
                    dense_embeddings = get_default_embedding_model()

                else:
                    self.logger.info(f"Using embedding model: {getattr(embedding_model, 'model', embedding_model)}")
                    ai_models = await self.config_service.get_config(
                        config_node_constants.AI_MODELS.value
                    )
                    dense_embeddings = None
                    if ai_models["embedding"]:
                        self.logger.info("No default embedding model found, using first available provider")
                        configs = ai_models["embedding"]
                        # Try to find the default config
                        selected_config = next((c for c in configs if c.get("isDefault", False)), None)
                        # If no default, take the first one
                        if not selected_config and configs:
                            selected_config = configs[0]

                        if selected_config:
                            dense_embeddings = get_embedding_model(selected_config["provider"], selected_config)
                            self.logger.info(f"Embedding provider: {selected_config['provider']}")


            except Exception as e:
                self.logger.error(f"Error creating embedding model: {str(e)}")
                raise EmbeddingModelCreationError(
                    f"Failed to create embedding model: {str(e)}"
                ) from e

            # Get the embedding dimensions from the model

            self.logger.info(
                f"Using embedding model: {getattr(embedding_model, 'model', embedding_model)}"
            )
            self.embedding_model_instance = dense_embeddings
            return dense_embeddings
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    async def get_current_embedding_model_name(self, use_cache: bool = True) -> Optional[str]:
        """Get the current embedding model name from configuration or instance."""
        try:
            # First try to get from AI_MODELS config
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache
            )
            if ai_models and "embedding" in ai_models and ai_models["embedding"]:
                for config in ai_models["embedding"]:
                    # Only one embedding model is supported
                    if "configuration" in config and "model" in config["configuration"]:
                        return config["configuration"]["model"]

            # Return default model if no embedding config found
            return DEFAULT_EMBEDDING_MODEL
        except Exception as e:
            self.logger.error(f"Error getting current embedding model name: {str(e)}")
            return DEFAULT_EMBEDDING_MODEL

    def get_embedding_model_name(self, dense_embeddings: Embeddings) -> Optional[str]:
        if hasattr(dense_embeddings, "model_name"):
            return dense_embeddings.model_name
        elif hasattr(dense_embeddings, "model"):
            return dense_embeddings.model
        else:
            return None

    async def _preprocess_query(self, query: str) -> str:
        """
        Preprocess the query text.

        Args:
            query: Raw query text

        Returns:
            Preprocessed query text
        """
        try:
            # Get current model name from config
            model_name = await self.get_current_embedding_model_name(use_cache=False)

            # Check if using BGE model before adding the prefix
            if model_name and "bge" in model_name.lower():
                return f"Represent this document for retrieval: {query.strip()}"
            return query.strip()
        except Exception as e:
            self.logger.error(f"Error in query preprocessing: {str(e)}")
            return query.strip()

    def _format_results(self, results: List[tuple]) -> List[Dict[str, Any]]:
        """Format search results into a consistent structure with flattened metadata."""
        formatted_results = []
        for doc, score in results:
            formatted_result = {
                "score": float(score),
                "citationType": "vectordb|document",
                "metadata": doc.metadata,
                "content": doc.page_content
            }
            formatted_results.append(formatted_result)
        return formatted_results

    async def search_with_filters(
        self,
        queries: List[str],
        user_id: str,
        org_id: str,
        filter_groups: Optional[Dict[str, List[str]]] = None,
        limit: int = 20,
        virtual_record_ids_from_tool: Optional[List[str]] = None,
        arango_service: Optional[BaseArangoService] = None,
        knowledge_search:bool = False,
    ) -> Dict[str, Any]:
        """Perform semantic search on accessible records with multiple queries."""

        try:
            # Get accessible records
            if not self.arango_service:
                raise ValueError("ArangoService is required for permission checking")

            filter_groups = filter_groups or {}

            kb_ids = filter_groups.get('kb', None) if filter_groups else None
            # Convert filter_groups to format expected by get_accessible_records
            arango_filters = {}
            if filter_groups:  # Only process if filter_groups is not empty
                for key, values in filter_groups.items():
                    # Convert key to match collection naming
                    metadata_key = (
                        key.lower()
                    )  # e.g., 'departments', 'categories', etc.
                    arango_filters[metadata_key] = values

            init_tasks = [
                self._get_accessible_records_task(user_id, org_id, filter_groups, self.arango_service),
                self._get_vector_store_task(),
                self._get_user_cached(user_id)  # Get user info in parallel with caching
            ]

            accessible_records, vector_store, user = await asyncio.gather(*init_tasks)

            if not accessible_records:
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            # FIX: Filter out None records before processing
            record_id_to_record_map = {}
            for r in accessible_records:
                if r:
                    record_id_to_record_map[r["_key"]] = r

            accessible_virtual_record_ids = [
                record["virtualRecordId"] for record in record_id_to_record_map.values()
                if record.get("virtualRecordId") is not None
            ]

            self.logger.debug(f"Accessible virtual record ids: {accessible_virtual_record_ids}")

            if virtual_record_ids_from_tool:
                filter  = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id,"virtualRecordId": virtual_record_ids_from_tool},
                    )
            else:
                filter = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id},
                        should={"virtualRecordId": accessible_virtual_record_ids}  # Pass as should condition
                    )
            search_results = await self._execute_parallel_searches(queries, filter, limit, vector_store)

            if not search_results:
                self.logger.debug("No search results found")
                return self._create_empty_response("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)

            self.logger.info(f"Search results count: {len(search_results) if search_results else 0}")

            # OPTIMIZATION: Extract virtual_record_ids using set comprehension (faster than loop)
            self.logger.debug("Starting to extract virtual_record_ids")
            virtual_record_ids = list({
                result["metadata"]["virtualRecordId"]
                for result in search_results
                if result
                and isinstance(result, dict)
                and result.get("metadata")
                and result["metadata"].get("virtualRecordId") is not None
            })

            virtual_record_ids = list(set(virtual_record_ids))
            self.logger.debug(f"Extracted virtual_record_ids: {virtual_record_ids}")

            virtual_to_record_map = {}
            try:
                self.logger.debug("About to call _create_virtual_to_record_mapping")
                virtual_to_record_map = self._create_virtual_to_record_mapping(record_id_to_record_map.values(), virtual_record_ids)
                self.logger.info(f"Virtual to record map size: {len(virtual_to_record_map)}")
            except Exception as e:
                self.logger.error(f"Error in _create_virtual_to_record_mapping: {e}")
                import traceback
                self.logger.error(f"Traceback: {traceback.format_exc()}")
                raise

            unique_record_ids = set(virtual_to_record_map.values())

            if not unique_record_ids:
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)
            self.logger.info(f"Unique record IDs count: {len(unique_record_ids)}")

            # OPTIMIZATION: First pass - enrich metadata and collect IDs that need additional fetching
            file_record_ids_to_fetch = []
            mail_record_ids_to_fetch = []
            result_to_record_map = {}  # Map result index to record_id for later URL assignment
            virtual_record_id_to_record = {}
            new_type_results = []
            final_search_results = []
            for idx, result in enumerate(search_results):
                if not result or not isinstance(result, dict):
                    continue
                if not result.get("metadata"):
                    self.logger.warning(f"Result has no metadata: {result}")
                    continue
                virtual_id = result["metadata"].get("virtualRecordId")
                if virtual_id is not None and virtual_id in virtual_to_record_map:
                    record_id = virtual_to_record_map[virtual_id]
                    result["metadata"]["recordId"] = record_id
                    record = record_id_to_record_map.get(record_id, None)
                    if record:
                        result["metadata"]["origin"] = record.get("origin")
                        result["metadata"]["connector"] = record.get("connectorName", None)
                        result["metadata"]["kbId"] = record.get("kbId", None)
                        weburl = record.get("webUrl")
                        if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                            user_email = user.get("email") if user else None
                            if user_email:
                                weburl = weburl.replace("{user.email}", user_email)
                        result["metadata"]["webUrl"] = weburl
                        result["metadata"]["recordName"] = record.get("recordName")

                        mime_type = record.get("mimeType")
                        if not mime_type:
                            if record.get("recordType", "") == RecordTypes.FILE.value:
                                file_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "file")
                            elif record.get("recordType", "") == RecordTypes.MAIL.value:
                                mail_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "mail")
                            continue
                        else:
                            result["metadata"]["mimeType"] = record.get("mimeType")
                            ext =  get_extension_from_mimetype(record.get("mimeType"))
                            if ext:
                                result["metadata"]["extension"] = ext

                        # Collect IDs that need additional fetching (instead of fetching immediately)
                        if not weburl:
                            if record.get("recordType", "") == RecordTypes.FILE.value:
                                file_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "file")
                            elif record.get("recordType", "") == RecordTypes.MAIL.value:
                                mail_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "mail")
                            continue

                        if knowledge_search:
                            meta = result.get("metadata")
                            is_block_group = meta.get("isBlockGroup")
                            if is_block_group is not None:
                                if virtual_id not in virtual_record_id_to_record:
                                    await get_record(meta,virtual_id,virtual_record_id_to_record,self.blob_store,org_id)
                                    record = virtual_record_id_to_record[virtual_id]
                                    if record is None:
                                        continue
                                    new_type_results.append(result)
                                    continue

                final_search_results.append(result)

            # OPTIMIZATION: Batch fetch all files and mails in parallel
            files_map = {}
            mails_map = {}

            async def fetch_files() -> Dict:
                if not file_record_ids_to_fetch:
                    return {}
                try:
                    # Fetch files in parallel
                    file_results = await asyncio.gather(*[
                        self.arango_service.get_document(record_id, CollectionNames.FILES.value)
                        for record_id in file_record_ids_to_fetch
                    ], return_exceptions=True)
                    return {
                        record_id: result
                        for record_id, result in zip(file_record_ids_to_fetch, file_results)
                        if result and not isinstance(result, Exception)
                    }
                except Exception as e:
                    self.logger.warning(f"Failed to batch fetch files: {str(e)}")
                    return {}

            async def fetch_mails() -> Dict:
                if not mail_record_ids_to_fetch:
                    return {}
                try:
                    # Fetch mails in parallel
                    mail_results = await asyncio.gather(*[
                        self.arango_service.get_document(record_id, CollectionNames.MAILS.value)
                        for record_id in mail_record_ids_to_fetch
                    ], return_exceptions=True)
                    return {
                        record_id: result
                        for record_id, result in zip(mail_record_ids_to_fetch, mail_results)
                        if result and not isinstance(result, Exception)
                    }
                except Exception as e:
                    self.logger.warning(f"Failed to batch fetch mails: {str(e)}")
                    return {}

            if file_record_ids_to_fetch or mail_record_ids_to_fetch:
                files_map, mails_map = await asyncio.gather(fetch_files(), fetch_mails())

            # Second pass - apply fetched URLs to results
            for idx, (record_id, record_type) in result_to_record_map.items():
                result = search_results[idx]
                record = record_id_to_record_map.get(record_id)
                if not record:
                    continue

                weburl = None
                fallback_mimetype= None
                if record_type == "file" and record_id in files_map:
                    files = files_map[record_id]
                    weburl = files.get("webUrl")
                    if weburl and record.get("connectorName", "") == Connectors.GOOGLE_MAIL.value:
                        user_email = user.get("email") if user else None
                        if user_email:
                            weburl = weburl.replace("{user.email}", user_email)
                    fallback_mimetype = files.get("mimeType")
                elif record_type == "mail" and record_id in mails_map:
                    mail = mails_map[record_id]
                    weburl = mail.get("webUrl")
                    if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                        user_email = user.get("email") if user else None
                        if user_email:
                            weburl = weburl.replace("{user.email}", user_email)
                    fallback_mimetype = "text/html"

                if weburl:
                    result["metadata"]["webUrl"] = weburl

                if fallback_mimetype:
                    result["metadata"]["mimeType"] = fallback_mimetype
                    fallback_ext =  get_extension_from_mimetype(fallback_mimetype)
                    if fallback_ext:
                        result["metadata"]["extension"] = fallback_ext

                final_search_results.append(result)

            # OPTIMIZATION: Get full record documents from Arango using list comprehension
            records = [
                record_id_to_record_map[record_id]
                for record_id in unique_record_ids
                if record_id in record_id_to_record_map
            ]

            if new_type_results:
                is_multimodal_llm = False   #doesn't matter for retrieval service
                flattened_results = await get_flattened_results(new_type_results,self.blob_store,org_id,is_multimodal_llm,virtual_record_id_to_record,from_retrieval_service=True)
                for result in flattened_results:
                    block_type = result.get("block_type")
                    if block_type == GroupType.TABLE.value:
                        _,child_results = result.get("content")
                        for child in child_results:
                            final_search_results.append(child)
                    else:
                        final_search_results.append(result)

            final_search_results = sorted(
                final_search_results,
                key=lambda x: x.get("score") or 0,
                reverse=True,
            )

            # Filter out incomplete results to prevent citation validation failures
            required_fields = ['origin', 'recordName', 'recordId', 'mimeType',"orgId"]
            complete_results = []

            for result in final_search_results:
                if result.get("content") is None or result.get("content") == "":
                    continue
                metadata = result.get('metadata', {})
                if all(field in metadata and metadata[field] is not None for field in required_fields):
                    complete_results.append(result)
                else:
                    self.logger.warning(f"Filtering out result with incomplete metadata. Virtual ID: {metadata.get('virtualRecordId')}, Missing fields: {[f for f in required_fields if f not in metadata]}")

            search_results = complete_results
            if search_results or records:
                response_data = {
                    "searchResults": search_results,
                    "records": records,
                    "status": Status.SUCCESS.value,
                    "status_code": 200,
                    "message": "Query processed successfully. Relevant records retrieved.",
                }

                # Add KB filtering info to response if KB filtering was applied
                if kb_ids:
                    response_data["appliedFilters"] = {
                        "kb": kb_ids,
                        "kb_count": len(kb_ids)
                    }

                return response_data
            else:
                return self._create_empty_response("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)
        except VectorDBEmptyError:
            self.logger.error("VectorDBEmptyError")
            return self._create_empty_response(
                    "No records indexed yet. Please upload documents or enable connectors to index content",
                    Status.VECTOR_DB_EMPTY,
                )
        except ValueError as e:
            self.logger.error(f"ValueError: {e}")
            return self._create_empty_response(f"Bad request: {str(e)}", Status.ERROR)
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            self.logger.error(f"Filtered search failed: {str(e)}")
            self.logger.error(f"Full traceback:\n{tb_str}")
            if virtual_record_ids_from_tool:
                return {}
            return self._create_empty_response("Unexpected server error during search.", Status.ERROR)

    async def _get_accessible_records_task(self, user_id, org_id, filter_groups, arango_service: BaseArangoService) -> List[Dict[str, Any]]:
        """Separate task for getting accessible records"""
        filter_groups = filter_groups or {}
        arango_filters = {}

        if filter_groups:
            for key, values in filter_groups.items():
                metadata_key = key.lower()
                arango_filters[metadata_key] = values

        return await arango_service.get_accessible_records(
            user_id=user_id, org_id=org_id, filters=arango_filters
        )

    async def _get_user_cached(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        OPTIMIZATION: Get user data with caching to avoid repeated DB calls.
        Cache expires after USER_CACHE_TTL seconds (default 5 minutes).
        """
        global _user_cache

        # Check cache
        if user_id in _user_cache:
            user_data, timestamp = _user_cache[user_id]
            if time.time() - timestamp < USER_CACHE_TTL:
                self.logger.debug(f"User cache hit for user_id: {user_id}")
                return user_data
            else:
                # Cache expired, remove it
                del _user_cache[user_id]

        # Cache miss - fetch from database
        self.logger.debug(f"User cache miss for user_id: {user_id}")
        user_data = await self.arango_service.get_user_by_user_id(user_id)

        # Store in cache
        _user_cache[user_id] = (user_data, time.time())

        # Simple cache size management - keep only last MAX_USER_CACHE_SIZE users
        if len(_user_cache) > MAX_USER_CACHE_SIZE:
            # Remove oldest entry
            oldest_key = min(_user_cache.keys(), key=lambda k: _user_cache[k][1])
            del _user_cache[oldest_key]

        return user_data


    async def _get_vector_store_task(self) -> QdrantVectorStore:
        """Cached vector store retrieval"""
        if not self.vector_store:
            # Check collection exists
            collections = await self.vector_db_service.get_collections()
            self.logger.info(f"Collections: {collections}")
            collection_info = (
                await self.vector_db_service.get_collection(self.collection_name)
                if any(col.name == self.collection_name for col in collections.collections) # type: ignore
                else None
            )
            if not collection_info or collection_info.points_count == 0: # type: ignore
                raise VectorDBEmptyError("Vector DB is empty or collection not found")

            # Get cached embedding model
            dense_embeddings = await self.get_embedding_model_instance()
            self.logger.info(f"Dense embeddings: {dense_embeddings}")
            if not dense_embeddings:
                raise ValueError("No dense embeddings found")

            self.vector_store = QdrantVectorStore(
                client=self.vector_db_service.get_service_client(),
                collection_name=self.collection_name,
                vector_name="dense",
                sparse_vector_name="sparse",
                embedding=dense_embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
            )
            self.logger.info(f"Vector store: {self.vector_store}")
        return self.vector_store


    async def _execute_parallel_searches(self, queries, filter, limit, vector_store) -> List[Dict[str, Any]]:
        """Execute all searches in parallel"""
        all_results = []

        dense_embeddings = await self.get_embedding_model_instance()
        if not dense_embeddings:
            raise ValueError("No dense embeddings found")

        # OPTIMIZATION: Parallelize embedding generation for multiple queries
        query_embeddings = await asyncio.gather(*[
            dense_embeddings.aembed_query(query) for query in queries
        ])

        query_requests = [models.QueryRequest(
            query=query_embedding,
            with_payload=True,
            limit=limit,
            using="dense",
            filter=filter,
        ) for query_embedding in query_embeddings]
        search_results = self.vector_db_service.query_nearest_points(
            collection_name=self.collection_name,
            requests=query_requests,
        )
        seen_points = set()
        for r in search_results:
                points = r.points
                for point in points:
                    if point.id in seen_points:
                        continue
                    seen_points.add(point.id)
                    metadata=point.payload.get("metadata", {})
                    metadata.update({"point_id": point.id})
                    doc = Document(
                        page_content=point.payload.get("page_content", ""),
                        metadata=metadata
                    )
                    score = point.score
                    all_results.append((doc, score))

        return self._format_results(all_results)

    def _create_empty_response(self, message: str, status: Status) -> Dict[str, Any]:
        """Helper to create empty response with appropriate HTTP status codes"""
        # Map status types to appropriate HTTP status codes
        status_code_mapping = {
            Status.SUCCESS: 200,
            Status.ERROR: 500,
            Status.ACCESSIBLE_RECORDS_NOT_FOUND: 404,  # Not Found - no accessible records
            Status.VECTOR_DB_EMPTY: 503,  # Service Unavailable - vector DB is empty
            Status.VECTOR_DB_NOT_READY: 503,  # Service Unavailable - vector DB not ready
            Status.EMPTY_RESPONSE: 200,  # OK but no results found
        }

        status_code = status_code_mapping.get(status, 500)  # Default to 500 for unknown status

        return {
            "searchResults": [],
            "records": [],
            "status": status.value,
            "status_code": status_code,
            "message": message,
        }


    def _create_virtual_to_record_mapping(
        self,
        accessible_records: List[Dict[str, Any]],
        virtual_record_ids: List[str]
    ) -> Dict[str, str]:
        """
        Create virtual record ID to record ID mapping from already fetched accessible_records.
        This eliminates the need for an additional database query.
        Args:
            accessible_records: List of accessible record documents (already fetched)
            virtual_record_ids: List of virtual record IDs from search results
        Returns:
            Dict[str, str]: Mapping of virtual_record_id -> first accessible record_id
        """
        # Create a mapping from virtualRecordId to list of record IDs
        virtual_to_records = {}
        for record in accessible_records:
            if record and isinstance(record, dict):
                virtual_id = record.get("virtualRecordId", None)
                record_id = record.get("_key", None)
                # self.logger.info(f"Virtual ID: {virtual_id}, Record ID: {record_id}")
                if virtual_id and record_id:
                    if virtual_id not in virtual_to_records:
                        virtual_to_records[virtual_id] = []
                    virtual_to_records[virtual_id].append(record_id)


        # Create the final mapping using only the virtual record IDs from search results
        # Use the first record ID for each virtual record ID
        mapping = {}
        for virtual_id in virtual_record_ids:
            # Skip None values and ensure virtual_id exists in virtual_to_records
            if virtual_id is not None and virtual_id in virtual_to_records and virtual_to_records[virtual_id]:
                mapping[virtual_id] = virtual_to_records[virtual_id][0]  # Use first record

        return mapping
