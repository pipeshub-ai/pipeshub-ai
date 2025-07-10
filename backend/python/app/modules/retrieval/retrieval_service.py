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

    async def get_llm_instance(self) -> Optional[BaseChatModel]:
        try:
            self.logger.info("Getting LLM")
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value
            )
            llm_configs = ai_models["llm"]
            # For now, we'll use the first available provider that matches our supported types
            # We will add logic to choose a specific provider based on our needs
            llm_config = None

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

            if not llm_config:
                raise ValueError("No supported LLM provider found in configuration")

            self.llm = LLMFactory.create_llm(self.logger, llm_config)
            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def get_embedding_model_instance(self, embedding_configs = None) -> Optional[Embeddings]:
        try:
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
        Preprocess the query text.

        Args:
            query: Raw query text

        Returns:
            Preprocessed query text
        """
        try:
            # Get current model name from config
            model_name = await self.get_current_embedding_model_name()

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


            init_tasks = [
                self._get_accessible_records_task(user_id, org_id, filter_groups, arango_service),
                self._get_vector_store_task(),
                arango_service.get_user_by_user_id(user_id)  # Get user info in parallel
            ]

            accessible_records, vector_store, user = await asyncio.gather(*init_tasks)


            if not accessible_records:
                return self._create_empty_response("No accessible records found for this user with provided filters.")

            accessible_virtual_record_ids = [
                record["virtualRecordId"] for record in accessible_records
                if record is not None and record.get("virtualRecordId") is not None
            ]
            # Build Qdrant filter
            qdrant_filter =  self._build_qdrant_filter(org_id, accessible_virtual_record_ids)

            search_results = await self._execute_parallel_searches(queries, qdrant_filter, limit, vector_store)

            if not search_results:
                return self._create_empty_response("No search results found")


            virtual_record_ids = list(
                set(result["metadata"]["virtualRecordId"] for result in search_results)
            )
            virtual_to_record_map = self._create_virtual_to_record_mapping(accessible_records, virtual_record_ids)
            unique_record_ids = set(virtual_to_record_map.values())

            if not unique_record_ids:
                return self._create_empty_response("No accessible records found for this user with provided filters.")

            # Replace virtualRecordId with first accessible record ID in search results
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

            # Get full record documents from Arango
            records = []
            if unique_record_ids:
                for record_id in unique_record_ids:
                    record = next((r for r in accessible_records if r["_key"] == record_id), None)
                    records.append(record)

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
            self.logger.error(f"Filtered search failed: {str(e)}")
            return {
                "searchResults": [],
                "records": [],
                "status": Status.ERROR.value,
                "status_code": 500,
                "message": f"An error occurred during search: {str(e)}",
            }


    async def _get_accessible_records_task(self, user_id, org_id, filter_groups, arango_service) -> List[Dict[str, Any]]:
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


    async def _get_vector_store_task(self) -> QdrantVectorStore:
        """Cached vector store retrieval"""
        if not self.vector_store:
            # Check collection exists
            collections = self.qdrant_client.get_collections()
            collection_info = (
                self.qdrant_client.get_collection(self.collection_name)
                if any(col.name == self.collection_name for col in collections.collections)
                else None
            )

            if not collection_info or collection_info.points_count == 0:
                raise ValueError("Vector DB is empty or collection not found")

            # Get cached embedding model
            dense_embeddings = await self.get_embedding_model_instance()
            if not dense_embeddings:
                raise ValueError("No dense embeddings found")

            self.vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=self.collection_name,
                vector_name="dense",
                sparse_vector_name="sparse",
                embedding=dense_embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
            )

        return self.vector_store


    async def _execute_parallel_searches(self, queries, qdrant_filter, limit, vector_store) -> List[Dict[str, Any]]:
        """Execute all searches in parallel"""
        all_results = []
        seen_chunks = set()

        # Process all queries in parallel
        search_tasks = [
            vector_store.asimilarity_search_with_score(
                query=await self._preprocess_query(query),
                k=limit,
                filter=qdrant_filter
            )
            for query in queries
        ]

        start_time = time.monotonic()
        search_results = await asyncio.gather(*search_tasks)
        elapsed = time.monotonic() - start_time
        self.logger.debug(f"VectorDB lookup for {len(queries)} queries took {elapsed:.3f} seconds.")

        # Deduplicate results
        for results in search_results:
            for doc, score in results:
                if doc.page_content not in seen_chunks:
                    all_results.append((doc, score))
                    seen_chunks.add(doc.page_content)

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
