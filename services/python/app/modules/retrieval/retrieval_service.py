from typing import Any, Dict, List, Optional

from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.config.utils.named_constants.ai_models_named_constants import (
    AZURE_EMBEDDING_API_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    AzureOpenAILLM,
    EmbeddingProvider,
    LLMProvider,
)
from app.config.utils.named_constants.arangodb_constants import CollectionNames, RecordTypes
from app.config.configuration_service import config_node_constants
from app.core.embedding_service import (
    AzureEmbeddingConfig,
    EmbeddingFactory,
    HuggingFaceEmbeddingConfig,
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
    OpenAILLMConfig,
)
from app.modules.retrieval.retrieval_arango import ArangoService
from app.utils.embeddings import get_default_embedding_model


class RetrievalService:
    def __init__(
        self,
        logger,
        config_service,
        collection_name: str,
        qdrant_api_key: str,
        qdrant_host: str,
        grpc_port: int
    ):
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
            self.dense_embeddings = None
            self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
        except Exception as e:
            self.logger.error("Failed to initialize sparse embeddings: " + str(e))
            self.sparse_embeddings = None
            raise Exception(
                "Failed to initialize sparse embeddings: " + str(e),
                details={"error": str(e)}
            )
        # Initialize Qdrant client
        self.qdrant_client = QdrantClient(
            host=qdrant_host,
            grpc_port=grpc_port,
            api_key=qdrant_api_key,
            prefer_grpc=True,
            https=False,
            timeout=60
        )
        self.collection_name = collection_name
        self.vector_store = None


    async def get_llm_instance(self):
        try:
            self.logger.info("Getting LLM")
            ai_models = await self.config_service.get_config(config_node_constants.AI_MODELS.value)
            llm_configs = ai_models['llm']
            # For now, we'll use the first available provider that matches our supported types
            # We will add logic to choose a specific provider based on our needs
            llm_config = None

            for config in llm_configs:
                provider = config['provider']
                if provider == LLMProvider.AZURE_OPENAI_PROVIDER.value:
                    llm_config = AzureLLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                        azure_endpoint=config['configuration']['endpoint'],
                        azure_api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                        azure_deployment=config['configuration']['deploymentName'],
                    )
                    break
                elif provider == LLMProvider.OPENAI_PROVIDER.value:
                    llm_config = OpenAILLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                    )
                    break
                elif provider == LLMProvider.GEMINI_PROVIDER.value:
                    llm_config = GeminiLLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                    )
                elif provider == LLMProvider.ANTHROPIC_PROVIDER.value:
                    llm_config = AnthropicLLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                    )
                elif provider == LLMProvider.AWS_BEDROCK_PROVIDER.value:
                    llm_config = AwsBedrockLLMConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        region=config['configuration']['region'],
                        access_key=config['configuration']['aws_access_key_id'],
                        access_secret=config['configuration']['aws_access_secret_key'],
                        api_key=config['configuration']['aws_access_secret_key'],
                    )
                elif provider == LLMProvider.OLLAMA_PROVIDER.value:
                    llm_config = OllamaConfig(
                        model=config['configuration']['model'],
                        temperature=0.2,
                        api_key=config['configuration']['apiKey'],
                    )
            if not llm_config:
                raise ValueError("No supported LLM provider found in configuration")

            self.llm = LLMFactory.create_llm(self.logger, llm_config)
            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def get_embedding_model_instance(self):
        try:
            self.logger.info("Getting embedding model")
            ai_models = await self.config_service.get_config(config_node_constants.AI_MODELS.value)
            embedding_configs = ai_models['embedding']
            embedding_model = None
            for config in embedding_configs:
                provider = config['provider']
                if provider == EmbeddingProvider.AZURE_OPENAI_PROVIDER.value:
                    embedding_model = AzureEmbeddingConfig(
                        model=config['configuration']['model'],
                        api_key=config['configuration']['apiKey'],
                        azure_endpoint=config['configuration']['endpoint'],
                        azure_api_version=AZURE_EMBEDDING_API_VERSION,
                    )
                elif provider == EmbeddingProvider.OPENAI_PROVIDER.value:
                    embedding_model = OpenAIEmbeddingConfig(
                        model=config['configuration']['model'],
                        api_key=config['configuration']['apiKey'],
                    )
                elif provider == EmbeddingProvider.HUGGING_FACE_PROVIDER.value:
                    embedding_model =   HuggingFaceEmbeddingConfig(
                      model=config['configuration']['model'],
                      api_key=config['configuration']['apiKey'],
                    )
                elif provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value:
                    embedding_model =   SentenceTransformersEmbeddingConfig(
                      model=config['configuration']['model'],
                    )

            if not embedding_model:
                self.logger.info("No embedding model found in configuration, using default embedding model")
                embedding_model = DEFAULT_EMBEDDING_MODEL
                self.dense_embeddings = await get_default_embedding_model()
            else:
                self.logger.info(f"Using embedding model: {embedding_model}")
                self.dense_embeddings = EmbeddingFactory.create_embedding_model(embedding_model)

            self.logger.info(f"Embedding model: {embedding_model}")
            return self.dense_embeddings
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    def _preprocess_query(self, query: str) -> str:
        """
        Preprocess the query text.

        Args:
            query: Raw query text

        Returns:
            Preprocessed query text
        """
        # Check if using BGE model before adding the prefix
        if hasattr(self.dense_embeddings, 'model_name') and 'bge' in self.dense_embeddings.model_name.lower():
            return f"Represent this document for retrieval: {query.strip()}"
        return query.strip()

    def _format_results(self, results: List[tuple]) -> List[Dict[str, Any]]:
        """Format search results into a consistent structure with flattened metadata."""
        formatted_results = []
        for doc, score in results:
            formatted_result = {
                "content": doc.page_content,
                "score": float(score),
                "citationType": "vectordb|document",
                "metadata": doc.metadata
            }
            formatted_results.append(formatted_result)
        return formatted_results

    def _build_qdrant_filter(self, org_id: str, accessible_records: List[str]) -> Filter:
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
                FieldCondition(   # org_id condition
                    key="metadata.orgId",
                    match=MatchValue(value=org_id)
                ),
                Filter(   # recordId must be one of the accessible_records
                    should=[
                        FieldCondition(
                            key="metadata.recordId",
                            match=MatchValue(value=record_id)
                        ) for record_id in accessible_records
                    ]
                )
            ]
        )

    async def search_with_filters(
        self,
        queries: List[str],
        user_id: str,
        org_id: str,
        filter_groups: Optional[Dict[str, List[str]]] = None,
        limit: int = 20,
        arango_service: Optional[ArangoService] = None
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
                    metadata_key = key.lower()  # e.g., 'departments', 'categories', etc.
                    arango_filters[metadata_key] = values

            accessible_records = await arango_service.get_accessible_records(
                user_id=user_id,
                org_id=org_id,
                filters=arango_filters
            )

            if not accessible_records:
                return []

            # Extract record IDs from accessible records
            record_ids = [record['_key'] for record in accessible_records if record is not None]
            # Build Qdrant filter
            qdrant_filter = self._build_qdrant_filter(org_id, record_ids)

            all_results = []
            seen_chunks = set()

            if not self.vector_store:
                # Check if collection exists and is not empty in Qdrant
                collections = self.qdrant_client.get_collections()
                collection_info = self.qdrant_client.get_collection(self.collection_name) if any(col.name == self.collection_name for col in collections.collections) else None
                if not collection_info or collection_info.points_count == 0:
                    self.logger.info(f"Collection {self.collection_name} not found in Qdrant or is empty. Indexing may not be complete.")
                    return {"searchResults": [], "records": []}

                if not self.dense_embeddings:
                    self.logger.info("No dense embeddings found, using default embedding model")
                    self.dense_embeddings = await self.get_embedding_model_instance()
                if not self.dense_embeddings:
                    raise ValueError("No dense embeddings found, please configure an embedding model or ensure indexing is complete")

                self.logger.info("Dense embeddings: %s", self.dense_embeddings)
                self.vector_store = QdrantVectorStore(
                    client=self.qdrant_client,
                    collection_name=self.collection_name,
                    vector_name="dense",
                    sparse_vector_name="sparse",
                    embedding=self.dense_embeddings,
                    sparse_embedding=self.sparse_embeddings,
                    retrieval_mode=RetrievalMode.HYBRID,
                )

            # Process each query
            for query in queries:
                # Perform similarity search
                processed_query = self._preprocess_query(query)
                results = await self.vector_store.asimilarity_search_with_score(
                    query=processed_query,
                    k=limit,
                    filter=qdrant_filter
                )
                self.logger.info(f"Results: {results}")
                # Add to results if content not already seen
                for doc, score in results:
                   if doc.page_content not in seen_chunks:
                        all_results.append((doc, score))
                        seen_chunks.add(doc.page_content)


            search_results = self._format_results(all_results)
            record_ids = list(set(result['metadata']['recordId'] for result in search_results))
            user = await arango_service.get_user_by_user_id(user_id)

            # Get full record documents from Arango
            records = []
            if record_ids:
                for record_id in record_ids:
                    record = await arango_service.get_document(record_id, CollectionNames.RECORDS.value)
                    if record['recordType'] == RecordTypes.FILE.value:
                        files = await arango_service.get_document(record_id, CollectionNames.FILES.value)
                        record = {**record, **files}
                    if record['recordType'] == RecordTypes.MAIL.value:
                        mail = await arango_service.get_document(record_id, CollectionNames.MAILS.value)
                        message_id = record['externalRecordId']
                        # Format the webUrl with the user's email
                        mail['webUrl'] = f"https://mail.google.com/mail?authuser={user['email']}#all/{message_id}"
                        record = {**record, **mail}
                    records.append(record)

            return {
                "searchResults": search_results,
                "records": records
            }

        except Exception as e:
            raise ValueError(f"Filtered search failed: {str(e)}")
