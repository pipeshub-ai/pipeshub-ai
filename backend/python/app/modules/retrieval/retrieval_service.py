import asyncio
import time
from typing import Any, Dict, List, Optional, Union

from langchain.chat_models.base import BaseChatModel
from langchain.embeddings.base import Embeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.config.configuration_service import config_node_constants
from app.config.utils.named_constants.ai_models_named_constants import (
    AZURE_EMBEDDING_API_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    AzureOpenAILLM,
    EmbeddingProvider,
    LLMProvider,
)
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    Connectors,
    RecordTypes,
)
from app.core.embedding_service import (
    AzureEmbeddingConfig,
    CohereEmbeddingConfig,
    EmbeddingFactory,
    GeminiEmbeddingConfig,
    HuggingFaceEmbeddingConfig,
    OpenAICompatibleEmbeddingConfig,
    OpenAIEmbeddingConfig,
    SentenceTransformersEmbeddingConfig,
)
from app.core.llm_service import (
    AnthropicLLMConfig,
    AwsBedrockLLMConfig,
    AzureLLMConfig,
    GeminiLLMConfig,
    LLMFactory,
    OllamaConfig,
    OpenAICompatibleLLMConfig,
    OpenAILLMConfig,
)
from app.exceptions.embedding_exceptions import EmbeddingModelCreationError
from app.exceptions.fastapi_responses import Status
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.retrieval.retrieval_arango import ArangoService
from app.utils.embeddings import get_default_embedding_model


class RetrievalService:
    def __init__(
        self,
        logger,
        config_service,
        collection_name: str,
        qdrant_client: QdrantClient,
    ) -> None:
        """
        Initialize the retrieval service with necessary configurations.

        Args:
            collection_name: Name of the Qdrant collection
            qdrant_api_key: API key for Qdrant
            qdrant_host: Qdrant server host URL
        """

        self.logger = logger
        self.config_service = config_service
        self.llm = None

        # Initialize sparse embeddings
        try:
            self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
        except Exception as e:
            self.logger.error("Failed to initialize sparse embeddings: " + str(e))
            self.sparse_embeddings = None
            raise Exception(
                "Failed to initialize sparse embeddings: " + str(e),
            )
        self.qdrant_client = qdrant_client
        self.collection_name = collection_name
        self.logger.info(f"Retrieval service initialized with collection name: {self.collection_name}")
        self.vector_store = None
        self._embedding_model = None  # Cache embedding model

        # Optimize Qdrant client settings
        if hasattr(self.qdrant_client, 'http'):
            # Set connection pool size and timeout for better performance
            self.qdrant_client.http.timeout = 30.0
            self.qdrant_client.http.max_retries = 2

        # Cache for accessible records (user_id + org_id + filters_hash -> records)
        self._accessible_records_cache = {}
        self._cache_ttl = 300  # 5 minutes TTL
        self._cache_timestamps = {}

        # Cache for user info (user_id -> user_data)
        self._user_cache = {}
        self._user_cache_ttl = 600  # 10 minutes TTL
        self._user_cache_timestamps = {}



    async def get_llm_instance(self) -> Optional[BaseChatModel]:
        try:
            self.logger.info("Getting LLM")

            # TIMING: Start timing LLM instance creation
            start_time = time.time()

            # TIMING: Log the time before config retrieval
            pre_config_time = time.time()

            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value
            )

            # TIMING: Log the time after config retrieval
            post_config_time = time.time()
            config_time = post_config_time - pre_config_time
            self.logger.info(f"TIMING: Config retrieval took {config_time:.3f}s")

            llm_configs = ai_models["llm"]
            # For now, we'll use the first available provider that matches our supported types
            # We will add logic to choose a specific provider based on our needs
            llm_config = None

            # TIMING: Log the time before config parsing
            pre_parsing_time = time.time()

            for config in llm_configs:
                provider = config["provider"]
                if provider == LLMProvider.AZURE_OPENAI.value:
                    llm_config = AzureLLMConfig(
                        model=config["configuration"]["model"],
                        temperature=0.2,
                        api_key=config["configuration"]["apiKey"],
                        azure_endpoint=config["configuration"]["endpoint"],
                        azure_api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                        azure_deployment=config["configuration"]["deploymentName"],
                    )
                    break
                elif provider == LLMProvider.OPENAI.value:
                    llm_config = OpenAILLMConfig(
                        model=config["configuration"]["model"],
                        temperature=0.2,
                        api_key=config["configuration"]["apiKey"],
                    )
                    break
                elif provider == LLMProvider.GEMINI.value:
                    llm_config = GeminiLLMConfig(
                        model=config["configuration"]["model"],
                        temperature=0.2,
                        api_key=config["configuration"]["apiKey"],
                    )
                elif provider == LLMProvider.ANTHROPIC.value:
                    llm_config = AnthropicLLMConfig(
                        model=config["configuration"]["model"],
                        temperature=0.2,
                        api_key=config["configuration"]["apiKey"],
                    )
                elif provider == LLMProvider.AWS_BEDROCK.value:
                    llm_config = AwsBedrockLLMConfig(
                        model=config["configuration"]["model"],
                        temperature=0.2,
                        region=config["configuration"]["region"],
                        access_key=config["configuration"]["aws_access_key_id"],
                        access_secret=config["configuration"]["aws_access_secret_key"],
                        api_key=config["configuration"]["aws_access_secret_key"],
                    )
                elif provider == LLMProvider.OLLAMA.value:
                    llm_config = OllamaConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                    )
                elif provider == LLMProvider.OPENAI_COMPATIBLE.value:
                    llm_config = OpenAICompatibleLLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                        endpoint=config['configuration']['endpoint'],
                    )

            # TIMING: Log the time after config parsing
            post_parsing_time = time.time()
            parsing_time = post_parsing_time - pre_parsing_time
            self.logger.info(f"TIMING: Config parsing took {parsing_time:.3f}s")

            if not llm_config:
                raise ValueError("No supported LLM provider found in configuration")

            # TIMING: Log the time before LLM factory creation
            pre_factory_time = time.time()

            self.llm = LLMFactory.create_llm(self.logger, llm_config)

            # TIMING: Log the time after LLM factory creation
            post_factory_time = time.time()
            factory_time = post_factory_time - pre_factory_time
            self.logger.info(f"TIMING: LLM factory creation took {factory_time:.3f}s")

            # TIMING: Log total LLM instance creation time
            total_time = time.time() - start_time
            self.logger.info(f"TIMING: Total LLM instance creation took {total_time:.3f}s")

            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def get_embedding_model_instance(self, embedding_configs = None) -> Optional[Embeddings]:
        try:
            # Return cached embedding model if available
            if self._embedding_model is not None:
                self.logger.info("Using cached embedding model")
                return self._embedding_model

            self.logger.info("Getting embedding model")
            embedding_model = await self.get_embedding_model_instance_from_config(embedding_configs)

            try:
                if not embedding_model or embedding_model == DEFAULT_EMBEDDING_MODEL:
                    self.logger.info("Using default embedding model")
                    embedding_model = DEFAULT_EMBEDDING_MODEL
                    dense_embeddings = await get_default_embedding_model()
                else:
                    self.logger.info(f"Using embedding model: {getattr(embedding_model, 'model', embedding_model)}")
                    dense_embeddings = EmbeddingFactory.create_embedding_model(
                        embedding_model
                    )

            except Exception as e:
                self.logger.error(f"Error creating embedding model: {str(e)}")
                raise EmbeddingModelCreationError(
                    f"Failed to create embedding model: {str(e)}"
                ) from e

            # Get the embedding dimensions from the model
            try:
                sample_embedding = await dense_embeddings.aembed_query("test")
                embedding_size = len(sample_embedding)
            except Exception as e:
                self.logger.warning(
                    f"Error with configured embedding model: {str(e)}"
                )
                raise IndexingError(
                    "Failed to get embedding model: " + str(e),
                )

            self.logger.info(
                f"Using embedding model: {getattr(embedding_model, 'model', embedding_model)}, embedding_size: {embedding_size}"
            )

            # Cache the embedding model
            self._embedding_model = dense_embeddings
            return dense_embeddings
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    async def get_embedding_model_instance_from_config(
        self,
        embedding_configs: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Union[str, AzureEmbeddingConfig, OpenAIEmbeddingConfig,
                       HuggingFaceEmbeddingConfig, SentenceTransformersEmbeddingConfig,
                       GeminiEmbeddingConfig, CohereEmbeddingConfig]]:
        """
        Get embedding model configuration from provided configs or fetch from config service.

        Args:
            embedding_configs: Optional list of embedding configurations

        Returns:
            Either a string for default model, an embedding config object, or None if error occurs
        """
        try:
            if not embedding_configs:
                ai_models = await self.config_service.get_config(
                    config_node_constants.AI_MODELS.value
                )
                embedding_configs = ai_models["embedding"]
            embedding_model = None
            for config in embedding_configs:
                provider = config["provider"]
                if provider == EmbeddingProvider.AZURE_OPENAI.value:
                    embedding_model = AzureEmbeddingConfig(
                        model=config['configuration']['model'],
                        api_key=config['configuration']['apiKey'],
                        azure_endpoint=config['configuration']['endpoint'],
                        azure_api_version=AZURE_EMBEDDING_API_VERSION,
                    )
                elif provider == EmbeddingProvider.OPENAI.value:
                    embedding_model = OpenAIEmbeddingConfig(
                        model=config["configuration"]["model"],
                        api_key=config["configuration"]["apiKey"],
                    )
                elif provider == EmbeddingProvider.HUGGING_FACE.value:
                    embedding_model =   HuggingFaceEmbeddingConfig(
                      model=config['configuration']['model'],
                      api_key=config['configuration']['apiKey'],
                    )
                elif provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value:
                    embedding_model =   SentenceTransformersEmbeddingConfig(
                      model=config['configuration']['model'],
                    )
                elif provider == EmbeddingProvider.GEMINI.value:
                    embedding_model = GeminiEmbeddingConfig(
                      model=config['configuration']['model'],
                      api_key=config['configuration']['apiKey'],
                    )
                elif provider == EmbeddingProvider.COHERE.value:
                    embedding_model = CohereEmbeddingConfig(
                      model=config['configuration']['model'],
                      api_key=config['configuration']['apiKey'],
                    )
                elif provider == EmbeddingProvider.OPENAI_COMPATIBLE.value:
                    embedding_model = OpenAICompatibleEmbeddingConfig(
                      model=config['configuration']['model'],
                      api_key=config['configuration']['apiKey'],
                      organization_id=config['configuration'].get('organizationId', None),
                      endpoint=config['configuration']['endpoint'],
                    )
                elif provider == EmbeddingProvider.DEFAULT.value:
                    embedding_model = DEFAULT_EMBEDDING_MODEL

            return embedding_model
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    async def get_current_embedding_model_name(self) -> Optional[str]:
        """Get the current embedding model name from configuration or instance."""
        try:
            # First try to get from AI_MODELS config
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value
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
        Preprocess the query text with caching.

        Args:
            query: Raw query text

        Returns:
            Preprocessed query text
        """
        try:
            # Simple preprocessing without config lookup for speed
            query = query.strip()

            # Quick check for BGE model (cache this check)
            if not hasattr(self, '_is_bge_model'):
                try:
                    model_name = await self.get_current_embedding_model_name()
                    self._is_bge_model = model_name and "bge" in model_name.lower()
                except Exception:
                    self._is_bge_model = False

            # Add BGE prefix if needed
            if self._is_bge_model:
                return f"Represent this document for retrieval: {query}"
            return query
        except Exception as e:
            self.logger.error(f"Error in query preprocessing: {str(e)}")
            return query.strip()

    def _format_results(self, results: List[tuple]) -> List[Dict[str, Any]]:
        """Format search results into a consistent structure with flattened metadata."""
        formatted_results = []
        for doc, score in results:
            formatted_result = {
                "content": doc.page_content,
                "score": float(score),
                "citationType": "vectordb|document",
                "metadata": doc.metadata,
            }
            formatted_results.append(formatted_result)
        return formatted_results

    def _build_qdrant_filter(
        self, org_id: str, accessible_virtual_record_ids: List[str]
    ) -> Filter:
        """
        Build Qdrant filter for accessible records with both org_id and record_id conditions.

        Args:
            org_id: Organization ID to filter
            accessible_records: List of record IDs the user has access to

        Returns:
            Qdrant Filter object
        """
        return Filter(
            must=[
                FieldCondition(  # org_id condition
                    key="metadata.orgId", match=MatchValue(value=org_id)
                ),
                Filter(  # recordId must be one of the accessible_records
                    should=[
                        FieldCondition(
                            key="metadata.virtualRecordId", match=MatchValue(value=virtual_record_id)
                        )
                        for virtual_record_id in accessible_virtual_record_ids
                    ]
                ),
            ]
        )

    async def search_with_filters(
        self,
        queries: List[str],
        user_id: str,
        org_id: str,
        filter_groups: Optional[Dict[str, List[str]]] = None,
        limit: int = 20,
        arango_service: Optional[ArangoService] = None,
    ) -> List[Dict[str, Any]]:
        """Perform semantic search on accessible records with multiple queries."""

        start_time = time.time()
        self.logger.info(f"🔍 Starting search_with_filters for user {user_id}, org {org_id}, queries: {len(queries)}")

        try:
            # Get accessible records
            if not arango_service:
                raise ValueError("ArangoService is required for permission checking")

            filter_groups = filter_groups or {}

            # Convert filter_groups to format expected by get_accessible_records
            arango_filters = {}
            if filter_groups:  # Only process if filter_groups is not empty
                for key, values in filter_groups.items():
                    # Convert key to match collection naming
                    metadata_key = (
                        key.lower()
                    )  # e.g., 'departments', 'categories', etc.
                    arango_filters[metadata_key] = values

            # Measure initialization phase
            init_start = time.time()
            self.logger.info("🚀 Starting parallel initialization tasks...")

            init_tasks = [
                self._get_accessible_records_task(user_id, org_id, filter_groups, arango_service),
                self._get_vector_store_task(),
                self._get_user_task(user_id, arango_service)  # Get user info in parallel with caching
            ]

            accessible_records, vector_store, user = await asyncio.gather(*init_tasks)

            init_duration = (time.time() - init_start) * 1000
            self.logger.info(f"✅ Initialization completed in {init_duration:.2f}ms")
            self.logger.info(f"   - Accessible records: {len(accessible_records) if accessible_records else 0}")
            self.logger.info(f"   - Vector store initialized: {vector_store is not None}")
            self.logger.info(f"   - User info retrieved: {user is not None}")


            if not accessible_records:
                return self._create_empty_response("No accessible records found for this user with provided filters.")

            # Measure filter building and search execution
            search_start = time.time()
            self.logger.info("🔧 Building Qdrant filter...")

            accessible_virtual_record_ids = [
                record["virtualRecordId"] for record in accessible_records
                if record is not None and record.get("virtualRecordId") is not None
            ]
            self.logger.info(f"   - Virtual record IDs: {len(accessible_virtual_record_ids)}")

            # Build Qdrant filter
            qdrant_filter =  self._build_qdrant_filter(org_id, accessible_virtual_record_ids)

            filter_build_duration = (time.time() - search_start) * 1000
            self.logger.info(f"✅ Qdrant filter built in {filter_build_duration:.2f}ms")

            # Execute vector search
            vector_search_start = time.time()
            self.logger.info(f"🔍 Executing vector search with {len(queries)} queries...")

            search_results = await self._execute_parallel_searches(queries, qdrant_filter, limit, vector_store)

            vector_search_duration = (time.time() - vector_search_start) * 1000
            self.logger.info(f"✅ Vector search completed in {vector_search_duration:.2f}ms")
            self.logger.info(f"   - Search results: {len(search_results) if search_results else 0}")

            if not search_results:
                return self._create_empty_response("No search results found")

            # Measure post-processing phase
            post_process_start = time.time()
            self.logger.info("🔧 Starting post-processing of search results...")

            virtual_record_ids = list(
                set(result["metadata"]["virtualRecordId"] for result in search_results)
            )
            self.logger.info(f"   - Unique virtual record IDs: {len(virtual_record_ids)}")

            virtual_to_record_map = self._create_virtual_to_record_mapping(accessible_records, virtual_record_ids)
            unique_record_ids = set(virtual_to_record_map.values())
            self.logger.info(f"   - Unique record IDs: {len(unique_record_ids)}")

            if not unique_record_ids:
                return self._create_empty_response("No accessible records found for this user with provided filters.")

            # Replace virtualRecordId with first accessible record ID in search results
            metadata_update_start = time.time()
            self.logger.info("📝 Updating search result metadata...")

            for result in search_results:
                virtual_id = result["metadata"]["virtualRecordId"]
                if virtual_id in virtual_to_record_map:
                    record_id = virtual_to_record_map[virtual_id]
                    result["metadata"]["recordId"] = record_id
                    record = next((r for r in accessible_records if r["_key"] == record_id), None)
                    if record:
                        result["metadata"]["origin"] = record.get("origin")
                        result["metadata"]["connector"] = record.get("connectorName")
                        weburl = record.get("webUrl")
                        if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                            weburl = weburl.replace("{user.email}", user["email"])
                        result["metadata"]["webUrl"] = weburl

                        if not weburl and record.get("recordType", "") == RecordTypes.FILE.value:
                            files = await arango_service.get_document(
                                record_id, CollectionNames.FILES.value
                            )
                            weburl = files.get("webUrl")
                            if weburl and record.get("connectorName", "") == Connectors.GOOGLE_MAIL.value:
                                weburl = weburl.replace("{user.email}", user["email"])
                            result["metadata"]["webUrl"] = weburl

                        if not weburl and record.get("recordType", "") == RecordTypes.MAIL.value:
                            mail = await arango_service.get_document(
                                record_id, CollectionNames.MAILS.value
                            )
                            weburl = mail.get("webUrl")
                            if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                                weburl = weburl.replace("{user.email}", user["email"])
                            result["metadata"]["webUrl"] = weburl

            metadata_update_duration = (time.time() - metadata_update_start) * 1000
            self.logger.info(f"✅ Metadata update completed in {metadata_update_duration:.2f}ms")

            # Get full record documents from Arango
            records_build_start = time.time()
            self.logger.info("📋 Building records list...")

            records = []
            if unique_record_ids:
                for record_id in unique_record_ids:
                    record = next((r for r in accessible_records if r["_key"] == record_id), None)
                    records.append(record)

            records_build_duration = (time.time() - records_build_start) * 1000
            self.logger.info(f"✅ Records list built in {records_build_duration:.2f}ms")
            self.logger.info(f"   - Records count: {len(records)}")

            # Calculate total duration
            total_duration = (time.time() - start_time) * 1000
            post_process_duration = (time.time() - post_process_start) * 1000

            # Log cache statistics
            cache_stats = f"Cache stats - Records: {len(self._accessible_records_cache)}, Users: {len(self._user_cache)}"

            self.logger.info("📊 Performance Summary:")
            self.logger.info(f"   - Total duration: {total_duration:.2f}ms")
            self.logger.info(f"   - Initialization: {init_duration:.2f}ms ({(init_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - Filter building: {filter_build_duration:.2f}ms ({(filter_build_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - Vector search: {vector_search_duration:.2f}ms ({(vector_search_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - Post-processing: {post_process_duration:.2f}ms ({(post_process_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - Metadata update: {metadata_update_duration:.2f}ms ({(metadata_update_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - Records building: {records_build_duration:.2f}ms ({(records_build_duration/total_duration)*100:.1f}%)")
            self.logger.info(f"   - {cache_stats}")

            if search_results or records:
                return {
                    "searchResults": search_results,
                    "records": records,
                    "status": Status.SUCCESS.value,
                    "status_code": 200,
                    "message": "Query processed successfully. Relevant records retrieved.",
                }
            else:
                return {
                    "searchResults": [],
                    "records": [],
                    "status": Status.EMPTY_RESPONSE.value,
                    "status_code": 200,
                    "message": "Query processed, but no relevant results were found.",
                }

        except Exception as e:
            total_duration = (time.time() - start_time) * 1000
            self.logger.error(f"❌ Filtered search failed after {total_duration:.2f}ms: {str(e)}")
            return {
                "searchResults": [],
                "records": [],
                "status": Status.ERROR.value,
                "status_code": 500,
                "message": f"An error occurred during search: {str(e)}",
            }


    async def _get_accessible_records_task(self, user_id, org_id, filter_groups, arango_service) -> List[Dict[str, Any]]:
        """Separate task for getting accessible records with caching"""
        task_start = time.time()
        self.logger.info(f"🔍 Getting accessible records for user {user_id}...")

        # Cleanup expired cache entries
        self._cleanup_expired_cache()

        filter_groups = filter_groups or {}
        arango_filters = {}

        if filter_groups:
            for key, values in filter_groups.items():
                metadata_key = key.lower()
                arango_filters[metadata_key] = values

        # Check cache first
        cache_key = self._get_cache_key(user_id, org_id, arango_filters)
        if self._is_cache_valid(cache_key):
            cached_result = self._accessible_records_cache.get(cache_key)
            if cached_result is not None:
                task_duration = (time.time() - task_start) * 1000
                self.logger.info(f"✅ Accessible records retrieved from cache in {task_duration:.2f}ms: {len(cached_result) if cached_result else 0} records")
                return cached_result

        # Cache miss - fetch from database
        db_start = time.time()
        result = await arango_service.get_accessible_records(
            user_id=user_id, org_id=org_id, filters=arango_filters
        )

        # Cache the result
        self._accessible_records_cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()

        db_duration = (time.time() - db_start) * 1000
        task_duration = (time.time() - task_start) * 1000
        self.logger.info(f"✅ Accessible records retrieved from DB in {db_duration:.2f}ms, total {task_duration:.2f}ms: {len(result) if result else 0} records")
        self.logger.info(f"   - Cache key: {cache_key}")
        return result

    async def _get_user_task(self, user_id: str, arango_service) -> Optional[Dict]:
        """Get user info with caching"""
        task_start = time.time()
        self.logger.info(f"👤 Getting user info for {user_id}...")

        # Check cache first
        if user_id in self._user_cache:
            cache_time = self._user_cache_timestamps.get(user_id, 0)
            if (time.time() - cache_time) < self._user_cache_ttl:
                cached_user = self._user_cache[user_id]
                task_duration = (time.time() - task_start) * 1000
                self.logger.info(f"✅ User info retrieved from cache in {task_duration:.2f}ms")
                return cached_user

        # Cache miss - fetch from database
        db_start = time.time()
        user = await arango_service.get_user_by_user_id(user_id)

        # Cache the result
        if user:
            self._user_cache[user_id] = user
            self._user_cache_timestamps[user_id] = time.time()

        db_duration = (time.time() - db_start) * 1000
        task_duration = (time.time() - task_start) * 1000
        self.logger.info(f"✅ User info retrieved from DB in {db_duration:.2f}ms, total {task_duration:.2f}ms")
        return user


    async def _get_vector_store_task(self) -> QdrantVectorStore:
        """Cached vector store retrieval"""
        task_start = time.time()
        self.logger.info("🔧 Initializing vector store...")

        if not self.vector_store:
            # Check collection exists (cache this check)
            collection_check_start = time.time()
            collections = self.qdrant_client.get_collections()
            collection_info = (
                self.qdrant_client.get_collection(self.collection_name)
                if any(col.name == self.collection_name for col in collections.collections)
                else None
            )
            collection_check_duration = (time.time() - collection_check_start) * 1000
            self.logger.info(f"   - Collection check: {collection_check_duration:.2f}ms")

            if not collection_info or collection_info.points_count == 0:
                raise ValueError("Vector DB is empty or collection not found")

            # Get cached embedding model
            embedding_start = time.time()
            dense_embeddings = await self.get_embedding_model_instance()
            if not dense_embeddings:
                raise ValueError("No dense embeddings found")
            embedding_duration = (time.time() - embedding_start) * 1000
            self.logger.info(f"   - Embedding model: {embedding_duration:.2f}ms")

            # Create vector store
            store_creation_start = time.time()
            self.vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=self.collection_name,
                vector_name="dense",
                sparse_vector_name="sparse",
                embedding=dense_embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
            )
            store_creation_duration = (time.time() - store_creation_start) * 1000
            self.logger.info(f"   - Store creation: {store_creation_duration:.2f}ms")
            self.logger.info("   - Vector store created from scratch")
        else:
            self.logger.info("   - Using cached vector store")

        task_duration = (time.time() - task_start) * 1000
        self.logger.info(f"✅ Vector store ready in {task_duration:.2f}ms")
        return self.vector_store


    async def _execute_parallel_searches(self, queries, qdrant_filter, limit, vector_store) -> List[Dict[str, Any]]:
        """Execute all searches in parallel with optimizations"""
        search_start = time.time()
        self.logger.info(f"🔍 Executing {len(queries)} parallel searches...")

        # Preprocess all queries in parallel
        preprocess_start = time.time()
        preprocessed_queries = await asyncio.gather(*[
            self._preprocess_query(query) for query in queries
        ])
        preprocess_duration = (time.time() - preprocess_start) * 1000
        self.logger.info(f"   - Query preprocessing: {preprocess_duration:.2f}ms")

        # Execute searches with optimized parameters
        search_execution_start = time.time()

        # Log filter complexity and details
        filter_complexity = len(qdrant_filter.must) if hasattr(qdrant_filter, 'must') else 0
        virtual_record_count = len(qdrant_filter.must[1].should) if hasattr(qdrant_filter, 'must') and len(qdrant_filter.must) > 1 else 0
        self.logger.info(f"   - Filter complexity: {filter_complexity} conditions")
        self.logger.info(f"   - Virtual records in filter: {virtual_record_count}")
        self.logger.info(f"   - Search limit: {min(limit, 50)}")

        HIGH_FILTER_THRESHOLD = 1000
        HIGH_COMPLEXITY_THRESHOLD = 10

        # If filter is too complex, try to optimize it
        if virtual_record_count > HIGH_FILTER_THRESHOLD:
            self.logger.warning(f"⚠️ Large filter detected ({virtual_record_count} virtual records), this may cause slow searches")

        # Try different search strategies based on filter complexity
        if virtual_record_count > HIGH_FILTER_THRESHOLD:
            self.logger.info("   - Using large filter strategy (no score threshold, reduced limit)")
            search_strategy = "large_filter"
            score_threshold = None
            search_limit = min(limit, 20)  # Reduce limit for large filters
        elif filter_complexity > HIGH_COMPLEXITY_THRESHOLD:
            self.logger.info("   - Using complex filter strategy (no score threshold)")
            search_strategy = "complex_filter"
            score_threshold = None
            search_limit = min(limit, 50)
        else:
            self.logger.info("   - Using standard search strategy")
            search_strategy = "standard"
            score_threshold = 0.5
            search_limit = min(limit, 50)

        self.logger.info(f"   - Search strategy: {search_strategy}")
        self.logger.info(f"   - Score threshold: {score_threshold}")
        self.logger.info(f"   - Search limit: {search_limit}")

        # Try single search first to measure individual performance
        if len(preprocessed_queries) == 1:
            single_search_start = time.time()
            try:
                single_result = await vector_store.asimilarity_search_with_score(
                    query=preprocessed_queries[0],
                    k=search_limit,
                    filter=qdrant_filter,
                    score_threshold=score_threshold
                )
                single_search_duration = (time.time() - single_search_start) * 1000
                self.logger.info(f"   - Single search duration: {single_search_duration:.2f}ms")
                search_results = [single_result]
            except Exception as e:
                self.logger.warning(f"   - Single search failed, trying without score threshold: {str(e)}")
                # Fallback: try without score threshold
                single_search_start = time.time()
                single_result = await vector_store.asimilarity_search_with_score(
                    query=preprocessed_queries[0],
                    k=search_limit,
                    filter=qdrant_filter
                )
                single_search_duration = (time.time() - single_search_start) * 1000
                self.logger.info(f"   - Fallback search duration: {single_search_duration:.2f}ms")
                search_results = [single_result]
        else:
            # Execute multiple searches in parallel
            search_tasks = [
                vector_store.asimilarity_search_with_score(
                    query=preprocessed_query,
                    k=search_limit,  # Use optimized limit
                    filter=qdrant_filter,
                    score_threshold=score_threshold
                )
                for preprocessed_query in preprocessed_queries
            ]

            search_results = await asyncio.gather(*search_tasks)

        search_execution_duration = (time.time() - search_execution_start) * 1000
        total_search_duration = (time.time() - search_start) * 1000
        self.logger.info(f"✅ Vector searches completed in {search_execution_duration:.2f}ms (total: {total_search_duration:.2f}ms)")

        HIGH_SEARCH_THRESHOLD = 1000
        HIGH_COMPLEXITY_THRESHOLD = 10
        # If search is still too slow, try alternative approach
        if search_execution_duration > HIGH_SEARCH_THRESHOLD:  # If search takes more than 1 second
            self.logger.warning(f"⚠️ Vector search is slow ({search_execution_duration:.2f}ms), trying alternative approach")

            # Try with simpler filter or no filter
            if filter_complexity > HIGH_COMPLEXITY_THRESHOLD:
                self.logger.info("   - Trying search with simplified filter")
                try:
                    # Create a simpler filter with just org_id
                    simple_filter = Filter(
                        must=[
                            FieldCondition(
                                key="metadata.orgId", match=MatchValue(value=qdrant_filter.must[0].match.value)
                            )
                        ]
                    )

                    alt_search_start = time.time()
                    alt_results = await vector_store.asimilarity_search_with_score(
                        query=preprocessed_queries[0] if len(preprocessed_queries) == 1 else preprocessed_queries[0],
                        k=min(limit, 20),  # Even smaller limit
                        filter=simple_filter
                    )
                    alt_search_duration = (time.time() - alt_search_start) * 1000
                    self.logger.info(f"   - Alternative search duration: {alt_search_duration:.2f}ms")

                    if alt_search_duration < search_execution_duration * 0.5:  # If 2x faster
                        self.logger.info("   - Using alternative search results (faster)")
                        search_results = [alt_results]
                        search_execution_duration = alt_search_duration
                except Exception as e:
                    self.logger.warning(f"   - Alternative search failed: {str(e)}")

        # Optimized deduplication
        dedup_start = time.time()
        all_results = []
        seen_chunks = set()

        # Process results in batches for better performance
        for results in search_results:
            for doc, score in results:
                content_hash = hash(doc.page_content)  # Use hash for faster comparison
                if content_hash not in seen_chunks:
                    all_results.append((doc, score))
                    seen_chunks.add(content_hash)

        dedup_duration = (time.time() - dedup_start) * 1000
        self.logger.info(f"✅ Deduplication completed in {dedup_duration:.2f}ms")
        self.logger.info(f"   - Total results before dedup: {sum(len(results) for results in search_results)}")
        self.logger.info(f"   - Total results after dedup: {len(all_results)}")

        return self._format_results(all_results)


    def _create_empty_response(self, message: str) -> Dict[str, Any]:
        """Helper to create empty response"""
        return {
            "searchResults": [],
            "records": [],
            "status": Status.ACCESSIBLE_RECORDS_NOT_FOUND.value,
            "status_code": 200,
            "message": message,
        }


    def _get_cache_key(self, user_id: str, org_id: str, filters: dict) -> str:
        """Generate cache key for accessible records"""
        import hashlib
        import json

        # Create a stable representation of filters
        filters_str = json.dumps(filters, sort_keys=True) if filters else "{}"
        cache_key = f"{user_id}:{org_id}:{hashlib.md5(filters_str.encode()).hexdigest()}"
        return cache_key

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cache entry is still valid"""
        if cache_key not in self._cache_timestamps:
            return False

        current_time = time.time()
        cache_time = self._cache_timestamps[cache_key]
        return (current_time - cache_time) < self._cache_ttl

    def _cleanup_expired_cache(self) -> None:
        """Remove expired cache entries"""
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self._cache_timestamps.items()
            if (current_time - timestamp) >= self._cache_ttl
        ]

        for key in expired_keys:
            self._accessible_records_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)

        if expired_keys:
            self.logger.info(f"🧹 Cleaned up {len(expired_keys)} expired cache entries")

        # Cleanup user cache
        expired_user_keys = [
            key for key, timestamp in self._user_cache_timestamps.items()
            if (current_time - timestamp) >= self._user_cache_ttl
        ]

        for key in expired_user_keys:
            self._user_cache.pop(key, None)
            self._user_cache_timestamps.pop(key, None)

        if expired_user_keys:
            self.logger.info(f"🧹 Cleaned up {len(expired_user_keys)} expired user cache entries")

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
            virtual_id = record.get("virtualRecordId")
            record_id = record.get("_key")

            if virtual_id and record_id:
                if virtual_id not in virtual_to_records:
                    virtual_to_records[virtual_id] = []
                virtual_to_records[virtual_id].append(record_id)

        # Create the final mapping using only the virtual record IDs from search results
        # Use the first record ID for each virtual record ID
        mapping = {}
        for virtual_id in virtual_record_ids:
            if virtual_id in virtual_to_records and virtual_to_records[virtual_id]:
                mapping[virtual_id] = virtual_to_records[virtual_id][0]  # Use first record

        return mapping
