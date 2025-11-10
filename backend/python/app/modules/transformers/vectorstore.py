import asyncio
import re
import time
import uuid
from typing import List, Optional

import httpx
import spacy
from langchain.chat_models.base import BaseChatModel
from langchain.schema import Document, HumanMessage
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client.http.models import PointStruct
from spacy.language import Language
from spacy.tokens import Doc

from app.config.constants.arangodb import CollectionNames, MimeTypes
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    EmbeddingError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.models.blocks import BlocksContainer
from app.modules.extraction.prompt_template import prompt_for_image_description
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.utils.aimodels import (
    EmbeddingProvider,
    get_default_embedding_model,
    get_embedding_model,
)
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Module-level shared spaCy pipeline to avoid repeated heavy loads
_SHARED_NLP: Optional[Language] = None

def _get_shared_nlp() -> Language:
    # Avoid global mutation; attach cache to function attribute
    cached = getattr(_get_shared_nlp, "_cached_nlp", None)
    if cached is None:
        nlp = spacy.load("en_core_web_sm")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")
        if "custom_sentence_boundary" not in nlp.pipe_names:
            try:
                nlp.add_pipe("custom_sentence_boundary", after="sentencizer")
            except Exception:
                pass
        setattr(_get_shared_nlp, "_cached_nlp", nlp)
        return nlp
    return cached

LENGTH_THRESHOLD = 2
OUTPUT_DIMENSION = 1536
HTTP_OK = 200
_DEFAULT_DOCUMENT_BATCH_SIZE = 50
_DEFAULT_CONCURRENCY_LIMIT = 5

class VectorStore(Transformer):

    def __init__(
        self,
        logger,
        config_service,
        arango_service,
        collection_name: str,
        vector_db_service: IVectorDBService,
    ) -> None:
        super().__init__()
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service
        # Reuse a single spaCy pipeline across instances to avoid memory bloat
        self.nlp = _get_shared_nlp()
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        self.vector_store = None
        self.dense_embeddings = None
        self.api_key = None
        self.model_name = None
        self.embedding_provider = None
        self.is_multimodal_embedding = False
        self.region_name = None
        self.aws_access_key_id = None
        self.aws_secret_access_key = None

        try:
            # Initialize sparse embeddings
            try:
                self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize sparse embeddings: " + str(e),
                    details={"error": str(e)},
                )



        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline: " + str(e),
                details={"error": str(e)},
            )

    async def _normalize_image_to_base64(self, image_uri: str) -> str | None:
        """
        Normalize an image reference into a raw base64-encoded string (no data: prefix).
        - data URLs (data:image/...;base64,xxxxx) -> returns the part after the comma
        - http/https URLs -> downloads bytes then base64-encodes
        - raw base64 strings -> returns as-is (after trimming/padding)

        Returns None if normalization fails.
        """
        try:
            if not image_uri or not isinstance(image_uri, str):
                return None

            uri = image_uri.strip()

            # data URL
            if uri.startswith("data:"):
                comma_index = uri.find(",")
                if comma_index == -1:
                    return None
                b64_part = uri[comma_index + 1 :].strip()
                # fix padding
                missing = (-len(b64_part)) % 4
                if missing:
                    b64_part += "=" * missing
                return b64_part



            # Assume raw base64

            candidate = uri
            candidate = candidate.replace("\n", "").replace("\r", "").replace(" ", "")
            if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", candidate):
                return None
            missing = (-len(candidate)) % 4
            if missing:
                candidate += "=" * missing
            return candidate
        except Exception:
            return None

    async def apply(self, ctx: TransformContext) -> bool|None:
        record = ctx.record
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        block_containers = record.block_containers
        org_id = record.org_id
        mime_type = record.mime_type
        result = await self.index_documents(block_containers, org_id,record_id,virtual_record_id,mime_type)
        return result

    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc) -> Doc:
        for token in doc[:-1]:  # Avoid out-of-bounds errors
            next_token = doc[token.i + 1]

            # If token is a number and followed by a period, don't treat it as a sentence boundary
            if token.like_num and next_token.text == ".":
                next_token.is_sent_start = False
            # Handle common abbreviations
            elif (
                token.text.lower()
                in [
                    "mr",
                    "mrs",
                    "dr",
                    "ms",
                    "prof",
                    "sr",
                    "jr",
                    "inc",
                    "ltd",
                    "co",
                    "etc",
                    "vs",
                    "fig",
                    "et",
                    "al",
                    "e.g",
                    "i.e",
                    "vol",
                    "pg",
                    "pp",
                    "pvt",
                    "llc",
                    "llp",
                    "lp",
                    "ll",
                    "ltd",
                    "inc",
                    "corp",
                ]
                and next_token.text == "."
            ):
                next_token.is_sent_start = False
            # Handle bullet points and list markers
            elif (
                # Numeric bullets with period (1., 2., etc)
                (
                    token.like_num and next_token.text == "." and len(token.text) <= LENGTH_THRESHOLD
                )  # Limit to 2 digits
                or
                # Letter bullets with period (a., b., etc)
                (
                    len(token.text) == 1
                    and token.text.isalpha()
                    and next_token.text == "."
                )
                or
                # Common bullet point markers
                token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
            ):
                next_token.is_sent_start = False

            # Check for potential headings (all caps or title case without period)
            elif (
                # All caps text likely a heading
                token.text.isupper()
                and len(token.text) > 1  # Avoid single letters
                and not any(c.isdigit() for c in token.text)  # Avoid serial numbers
            ):
                if next_token.i < len(doc) - 1:
                    next_token.is_sent_start = False

            # Handle ellipsis (...) - don't split
            elif token.text == "." and next_token.text == ".":
                next_token.is_sent_start = False
        return doc

    def _create_custom_tokenizer(self, nlp) -> Language:
        """
        Creates a custom tokenizer that handles special cases for sentence boundaries.
        """
        # Add the custom rule to the pipeline
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")

        # Add custom sentence boundary detection
        if "custom_sentence_boundary" not in nlp.pipe_names:
            nlp.add_pipe("custom_sentence_boundary", after="sentencizer")

        # Configure the tokenizer to handle special cases
        special_cases = {
            "e.g.": [{"ORTH": "e.g."}],
            "i.e.": [{"ORTH": "i.e."}],
            "etc.": [{"ORTH": "etc."}],
            "...": [{"ORTH": "..."}],
        }

        for case, mapping in special_cases.items():
            nlp.tokenizer.add_special_case(case, mapping)
        return nlp

    async def _initialize_collection(
        self, embedding_size: int = 1024, sparse_idf: bool = False
    ) -> None:
        """Initialize Qdrant collection with proper configuration."""
        try:
            collection_info = await self.vector_db_service.get_collection(self.collection_name)
            current_vector_size = collection_info.config.params.vectors["dense"].size
            # current_vector_size_2 = collection_info.config.params.vectors["dense-1536"].size

            if current_vector_size != embedding_size:
                self.logger.warning(
                    f"Collection {self.collection_name} has size {current_vector_size}, but {embedding_size} is required."
                    " Recreating collection."
                )
                await self.vector_db_service.delete_collection(self.collection_name)
                raise Exception(
                    "Recreating collection due to vector dimension mismatch."
                )
        except Exception:
            self.logger.info(
                f"Collection {self.collection_name} not found, creating new collection"
            )
            try:
                await self.vector_db_service.create_collection(
                    embedding_size=embedding_size,
                    collection_name=self.collection_name,
                    sparse_idf=sparse_idf,
                )
                self.logger.info(
                    f"✅ Successfully created collection {self.collection_name}"
                )
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name="metadata.virtualRecordId",
                    field_schema={
                        "type": "keyword",
                    },
                )
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name="metadata.orgId",
                    field_schema={
                        "type": "keyword",
                    },
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Error creating collection {self.collection_name}: {str(e)}"
                )
                raise VectorStoreError(
                    "Failed to create collection",
                    details={"collection": self.collection_name, "error": str(e)},
                )



    async def get_embedding_model_instance(self) -> bool:
        try:
            self.logger.info("Getting embedding model")
            # Return cached configuration if already initialized
            # if getattr(self, "vector_store", None) is not None and getattr(self, "dense_embeddings", None) is not None:
                # return bool(getattr(self, "is_multimodal_embedding", False))

            dense_embeddings = None
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,use_cache=False
            )
            embedding_configs = ai_models["embedding"]
            is_multimodal = False
            provider = None
            model_name = None
            configuration = None
            if not embedding_configs:
                dense_embeddings = get_default_embedding_model()
                self.logger.info("Using default embedding model")
            else:
                config = embedding_configs[0]
                provider = config["provider"]
                configuration = config["configuration"]
                model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
                model_name = model_names[0]
                dense_embeddings = get_embedding_model(provider, config)
                is_multimodal = config.get("isMultimodal")
            # Get the embedding dimensions from the model
            try:
                sample_embedding = dense_embeddings.embed_query("test")
                embedding_size = len(sample_embedding)
            except Exception as e:
                self.logger.warning(
                    f"Error with configured embedding model, falling back to default: {str(e)}"
                )
                raise IndexingError(
                    "Failed to get embedding model: " + str(e),
                    details={"error": str(e)},
                )

            # Get model name safely
            model_name = None
            if hasattr(dense_embeddings, "model_name"):
                model_name = dense_embeddings.model_name
            elif hasattr(dense_embeddings, "model"):
                model_name = dense_embeddings.model
            elif hasattr(dense_embeddings, "model_id"):
                model_name = dense_embeddings.model_id
            else:
                model_name = "unknown"

            self.logger.info(
                f"Using embedding model: {model_name}, embedding_size: {embedding_size}"
            )

            # Initialize collection with correct embedding size
            await self._initialize_collection(embedding_size=embedding_size)

            # Initialize vector store with same configuration

            self.vector_store: QdrantVectorStore = QdrantVectorStore(
                client=self.vector_db_service.get_service_client(),
                collection_name=self.collection_name,
                vector_name="dense",
                sparse_vector_name="sparse",
                embedding=dense_embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
            )

            self.dense_embeddings = dense_embeddings
            self.embedding_provider = provider
            self.api_key = configuration["apiKey"] if configuration and "apiKey" in configuration else None
            self.model_name = model_name
            self.region_name = configuration["region"] if configuration and "region" in configuration else None
            # Persist AWS credentials when using Bedrock so we can call image embedding runtime directly
            if provider == EmbeddingProvider.AWS_BEDROCK.value and configuration:
                self.aws_access_key_id = configuration.get("awsAccessKeyId")
                self.aws_secret_access_key = configuration.get("awsAccessSecretKey")
            self.is_multimodal_embedding = bool(is_multimodal)
            return self.is_multimodal_embedding
        except IndexingError as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            raise IndexingError(
                "Failed to get embedding model: " + str(e), details={"error": str(e)}
            )

    async def delete_embeddings(self, virtual_record_id: str) -> None:
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"virtualRecordId": virtual_record_id}
            )

            self.vector_db_service.delete_points(self.collection_name, filter_dict)

            self.logger.info(f"✅ Successfully deleted embeddings for record {virtual_record_id}")
        except Exception as e:
            self.logger.error(f"Error deleting embeddings: {str(e)}")
            raise EmbeddingError(f"Failed to delete embeddings: {str(e)}")

    async def _process_image_embeddings_cohere(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Cohere API."""
        import cohere

        co = cohere.ClientV2(api_key=self.api_key)
        points = []

        async def embed_single_image(i: int, image_base64: str) -> Optional[PointStruct]:
            """Embed a single image with Cohere API."""
            image_input = {
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_base64}
                    }
                ]
            }

            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: co.embed(
                        model=self.model_name,
                        input_type="image",
                        embedding_types=["float"],
                        inputs=[image_input],
                    )
                )
                chunk = image_chunks[i]
                embedding = response.embeddings.float[0]
                return PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"dense": embedding},
                    payload={
                        "metadata": chunk.get("metadata", {}),
                        "page_content": chunk.get("image_uri", ""),
                    },
                )
            except Exception as cohere_error:
                error_text = str(cohere_error)
                if "image size must be at most" in error_text:
                    self.logger.warning(
                        f"Skipping image {i} embedding due to size limit: {error_text}"
                    )
                    return None
                raise

        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_embed(i: int, image_base64: str) -> Optional[PointStruct]:
            async with semaphore:
                return await embed_single_image(i, image_base64)

        tasks = [limited_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, PointStruct):
                points.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image: {str(result)}")

        return points

    async def _process_image_embeddings_voyage(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Voyage AI."""
        batch_size = getattr(self.dense_embeddings, 'batch_size', 7)
        points = []

        async def process_voyage_batch(batch_start: int, batch_images: List[str]) -> List[PointStruct]:
            """Process a single batch of images with Voyage AI."""
            try:
                embeddings = await self.dense_embeddings.aembed_documents(batch_images)
                batch_points = []
                for i, embedding in enumerate(embeddings):
                    chunk_idx = batch_start + i
                    image_chunk = image_chunks[chunk_idx]
                    point = PointStruct(
                        id=str(uuid.uuid4()),
                        vector={"dense": embedding},
                        payload={
                            "metadata": image_chunk.get("metadata", {}),
                            "page_content": image_chunk.get("image_uri", ""),
                        },
                    )
                    batch_points.append(point)
                self.logger.info(
                    f"✅ Processed Voyage batch starting at {batch_start}: {len(embeddings)} image embeddings"
                )
                return batch_points
            except Exception as voyage_error:
                self.logger.warning(
                    f"Failed to process Voyage batch starting at {batch_start}: {str(voyage_error)}"
                )
                return []

        batches = []
        for batch_start in range(0, len(image_base64s), batch_size):
            batch_end = min(batch_start + batch_size, len(image_base64s))
            batch_images = image_base64s[batch_start:batch_end]
            batches.append((batch_start, batch_images))

        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_voyage_batch(batch_start: int, batch_images: List[str]) -> List[PointStruct]:
            async with semaphore:
                return await process_voyage_batch(batch_start, batch_images)

        tasks = [limited_voyage_batch(start, imgs) for start, imgs in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                points.extend(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Voyage batch processing exception: {str(result)}")

        return points

    async def _process_image_embeddings_bedrock(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using AWS Bedrock."""
        import json

        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        try:
            client_kwargs = {
                "service_name": "bedrock-runtime",
            }
            if self.aws_access_key_id and self.aws_secret_access_key and self.region_name:
                client_kwargs.update({
                    "aws_access_key_id": self.aws_access_key_id,
                    "aws_secret_access_key": self.aws_secret_access_key,
                    "region_name": self.region_name,
                })
            bedrock = boto3.client(**client_kwargs)
        except NoCredentialsError as cred_err:
            raise EmbeddingError(
                "AWS credentials not found for Bedrock image embeddings. Provide awsAccessKeyId/awsAccessSecretKey or configure a credential source."
            ) from cred_err

        points = []

        async def embed_single_bedrock_image(i: int, image_ref: str) -> Optional[PointStruct]:
            """Embed a single image with AWS Bedrock."""
            normalized_b64 = await self._normalize_image_to_base64(image_ref)
            if not normalized_b64:
                self.logger.warning("Skipping image: unable to normalize to base64 (index=%s)", i)
                return None

            request_body = {
                "inputImage": normalized_b64,
                "embeddingConfig": {
                    "outputEmbeddingLength": 1024
                }
            }

            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: bedrock.invoke_model(
                        modelId=self.model_name,
                        body=json.dumps(request_body),
                        contentType='application/json',
                        accept='application/json'
                    )
                )
                response_body = json.loads(response['body'].read())
                image_embedding = response_body['embedding']

                image_chunk = image_chunks[i]
                return PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"dense": image_embedding},
                    payload={
                        "metadata": image_chunk.get("metadata", {}),
                        "page_content": image_chunk.get("image_uri", ""),
                    },
                )
            except NoCredentialsError as cred_err:
                raise EmbeddingError(
                    "AWS credentials not found while invoking Bedrock model."
                ) from cred_err
            except ClientError as client_err:
                self.logger.warning("Bedrock image embedding failed for index=%s: %s", i, str(client_err))
                return None
            except Exception as bedrock_err:
                self.logger.warning("Unexpected Bedrock error for image index=%s: %s", i, str(bedrock_err))
                return None

        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_bedrock_embed(i: int, image_ref: str) -> Optional[PointStruct]:
            async with semaphore:
                return await embed_single_bedrock_image(i, image_ref)

        tasks = [limited_bedrock_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, PointStruct):
                points.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image with Bedrock: {str(result)}")

        return points

    async def _process_image_embeddings_jina(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Jina AI."""

        batch_size = 32
        points = []

        async def process_jina_batch(client: httpx.AsyncClient, batch_start: int, batch_images: List[str]) -> List[PointStruct]:
            """Process a single batch of images with Jina AI."""
            try:
                url = 'https://api.jina.ai/v1/embeddings'
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + self.api_key
                }
                normalized_images = await asyncio.gather(*[
                    self._normalize_image_to_base64(image_base64)
                    for image_base64 in batch_images
                ])
                # Track which original indices correspond to successfully normalized images
                valid_indices = []
                valid_normalized_images = []
                for idx, normalized_b64 in enumerate(normalized_images):
                    if normalized_b64 is not None:
                        valid_indices.append(batch_start + idx)
                        valid_normalized_images.append(normalized_b64)

                if not valid_normalized_images:
                    self.logger.warning(
                        f"No valid images in Jina AI batch starting at {batch_start} after normalization"
                    )
                    return []

                data = {
                    "model": self.model_name,
                    "input": [
                        {"image": normalized_b64}
                        for normalized_b64 in valid_normalized_images
                    ]
                }

                response = await client.post(url, headers=headers, json=data)
                response_body = response.json()
                embeddings = [r["embedding"] for r in response_body["data"]]

                batch_points = []
                for i, embedding in enumerate(embeddings):
                    # Use the tracked valid index instead of simple increment
                    chunk_idx = valid_indices[i]
                    image_chunk = image_chunks[chunk_idx]
                    point = PointStruct(
                        id=str(uuid.uuid4()),
                        vector={"dense": embedding},
                        payload={
                            "metadata": image_chunk.get("metadata", {}),
                            "page_content": image_chunk.get("image_uri", ""),
                        },
                    )
                    batch_points.append(point)
                self.logger.info(
                    f"✅ Processed Jina AI batch starting at {batch_start}: {len(embeddings)} image embeddings"
                )
                return batch_points
            except Exception as jina_error:
                self.logger.warning(
                    f"Failed to process Jina AI batch starting at {batch_start}: {str(jina_error)}"
                )
                return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = []
            for batch_start in range(0, len(image_base64s), batch_size):
                batch_end = min(batch_start + batch_size, len(image_base64s))
                batch_images = image_base64s[batch_start:batch_end]
                batches.append((batch_start, batch_images))

            concurrency_limit = 5
            semaphore = asyncio.Semaphore(concurrency_limit)

            async def limited_process_batch(batch_start: int, batch_images: List[str]) -> List[PointStruct]:
                async with semaphore:
                    return await process_jina_batch(client, batch_start, batch_images)

            tasks = [limited_process_batch(start, imgs) for start, imgs in batches]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, list):
                    points.extend(result)
                elif isinstance(result, Exception):
                    self.logger.warning(f"Jina AI batch processing exception: {str(result)}")

        return points

    async def _process_image_embeddings(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings based on the configured provider."""
        if self.embedding_provider == EmbeddingProvider.COHERE.value:
            return await self._process_image_embeddings_cohere(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.VOYAGE.value:
            return await self._process_image_embeddings_voyage(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.AWS_BEDROCK.value:
            return await self._process_image_embeddings_bedrock(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.JINA_AI.value:
            return await self._process_image_embeddings_jina(image_chunks, image_base64s)
        else:
            self.logger.warning(f"Unsupported embedding provider for images: {self.embedding_provider}")
            return []

    async def _store_image_points(self, points: List[PointStruct]) -> None:
        """Store image embedding points in the vector database."""
        if points:
            start_time = time.perf_counter()
            self.logger.info(f"⏱️ Starting image embeddings insertion for {len(points)} points")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.vector_db_service.upsert_points(
                    collection_name=self.collection_name, points=points
                ),
            )

            elapsed_time = time.perf_counter() - start_time
            self.logger.info(
                f"✅ Successfully added {len(points)} image embeddings to vector store in {elapsed_time:.2f}s"
            )
        else:
            self.logger.info(
                "No image embeddings to upsert; all images were skipped or failed to embed"
            )

    async def _process_document_chunks(self, langchain_document_chunks: List[Document]) -> None:
        """Process and store document chunks in the vector store."""
        time.perf_counter()
        self.logger.info(f"⏱️ Starting langchain document embeddings insertion for {len(langchain_document_chunks)} documents")

        batch_size = _DEFAULT_DOCUMENT_BATCH_SIZE

        async def process_document_batch(batch_start: int, batch_documents: List[Document]) -> int:
            """Process a single batch of documents."""
            try:
                await self.vector_store.aadd_documents(batch_documents)
                self.logger.info(
                    f"✅ Processed document batch starting at {batch_start}: {len(batch_documents)} documents"
                )
                return len(batch_documents)
            except Exception as batch_error:
                self.logger.warning(
                    f"Failed to process document batch starting at {batch_start}: {str(batch_error)}"
                )
                raise

        batches = []
        for batch_start in range(0, len(langchain_document_chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(langchain_document_chunks))
            batch_documents = langchain_document_chunks[batch_start:batch_end]
            batches.append((batch_start, batch_documents))

        concurrency_limit = _DEFAULT_CONCURRENCY_LIMIT
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_process_batch(batch_start: int, batch_documents: List[Document]) -> int:
            async with semaphore:
                return await process_document_batch(batch_start, batch_documents)

        tasks = [limited_process_batch(start, docs) for start, docs in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Document batch {i} failed: {str(result)}")
                raise VectorStoreError(
                    f"Failed to store document batch {i} in vector store: {str(result)}",
                    details={"error": str(result), "batch_index": i},
                )

    async def _update_record_status(
        self, chunks: List[Document], record_id: str
    ) -> None:
        """Update record indexing status in the database."""
        if not chunks:
            return

        meta = chunks[0].metadata if isinstance(chunks[0], Document) else chunks[0].get("metadata", {})
        record = await self.arango_service.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if not record:
            raise DocumentProcessingError(
                "Record not found in database",
                doc_id=record_id,
            )

        doc = dict(record)
        doc.update(
            {
                "indexingStatus": "COMPLETED",
                "isDirty": False,
                "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                "virtualRecordId": meta.get("virtualRecordId"),
            }
        )

        docs = [doc]
        success = await self.arango_service.batch_upsert_nodes(
            docs, CollectionNames.RECORDS.value
        )
        if not success:
            raise DocumentProcessingError(
                "Failed to update indexing status", doc_id=record_id
            )

    async def _create_embeddings(
        self, chunks: List[Document], record_id: str, virtual_record_id: str
    ) -> None:
        """
        Create both sparse and dense embeddings for document chunks and store them in vector store.
        Handles both text and image embeddings.

        Args:
            chunks: List of document chunks to embed
            record_id: Record ID for status updates
            virtual_record_id: Virtual record ID for filtering embeddings

        Raises:
            EmbeddingError: If there's an error creating embeddings
            VectorStoreError: If there's an error storing embeddings
            MetadataProcessingError: If there's an error processing metadata
            DocumentProcessingError: If there's an error updating document status
        """
        try:
            if not chunks:
                raise EmbeddingError("No chunks provided for embedding creation")

            # Separate chunks by type
            langchain_document_chunks = []
            image_chunks = []
            for chunk in chunks:
                if isinstance(chunk, Document):
                    langchain_document_chunks.append(chunk)
                else:
                    image_chunks.append(chunk)

            await self.delete_embeddings(virtual_record_id)

            self.logger.info(
                f"📊 Processing {len(langchain_document_chunks)} langchain document chunks and {len(image_chunks)} image chunks"
            )

            # Process image chunks if any
            if image_chunks:
                image_base64s = [chunk.get("image_uri") for chunk in image_chunks]
                points = await self._process_image_embeddings(image_chunks, image_base64s)
                await self._store_image_points(points)

            # Process document chunks if any
            if langchain_document_chunks:
                try:
                    await self._process_document_chunks(langchain_document_chunks)
                except Exception as e:
                    raise VectorStoreError(
                        "Failed to store langchain documents in vector store: " + str(e),
                        details={"error": str(e)},
                    )

            # Update record status
            await self._update_record_status(chunks, record_id)

        except (
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            DocumentProcessingError,
        ):
            raise
        except Exception as e:
            raise IndexingError(
                "Unexpected error during embedding creation: " + str(e),
                details={"error": str(e)},
            )

    async def describe_image_async(self, base64_string: str, vlm: BaseChatModel) -> str:
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_for_image_description},
                {"type": "image_url", "image_url": {"url": base64_string}},
            ]
        )
        response = await vlm.ainvoke([message])
        return response.content

    async def describe_images(self, base64_images: List[str],vlm:BaseChatModel) -> List[dict]:

        async def describe(i: int, base64_string: str) -> dict:
            try:
                description = await self.describe_image_async(base64_string, vlm)
                return {"index": i, "success": True, "description": description.strip()}
            except Exception as e:
                return {"index": i, "success": False, "error": str(e)}

        # Limit concurrency to avoid memory growth when many images
        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_describe(i: int, base64_string: str) -> dict:
            async with semaphore:
                return await describe(i, base64_string)

        tasks = [limited_describe(i, img) for i, img in enumerate(base64_images)]
        results = await asyncio.gather(*tasks)
        return results

    async def index_documents(
        self,
        block_containers: BlocksContainer,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        mime_type: str,
    ) -> List[Document]|None|bool:
        """
        Main method to index documents through the entire pipeline.
        Args:
            sentences: List of dictionaries containing text and metadata
                    Each dict should have 'text' and 'metadata' keys

        Raises:
            DocumentProcessingError: If there's an error processing the documents
            ChunkingError: If there's an error during document chunking
            EmbeddingError: If there's an error creating embeddings
        """

        try:
          is_multimodal_embedding = await self.get_embedding_model_instance()
        except Exception as e:
                raise IndexingError(
                    "Failed to get embedding model instance: " + str(e),
                    details={"error": str(e)},
                )

        try:
            llm, config = await get_llm(self.config_service)
            is_multimodal_llm = config.get("isMultimodal")
        except Exception as e:
            raise IndexingError(
                "Failed to get LLM: " + str(e),
                details={"error": str(e)},
            )

        blocks = block_containers.blocks
        block_groups = block_containers.block_groups
        try:
            if not blocks and not block_groups:
                return None

            # Separate blocks by type
            text_blocks = []
            image_blocks = []
            table_blocks = []

            for block in blocks:
                block_type = block.type

                if block_type.lower() in [
                    "text",
                    "paragraph",
                    "textsection",
                    "heading",
                    "quote",
                ]:
                    text_blocks.append(block)
                elif block_type.lower() in ["image", "drawing"]:
                    image_blocks.append(block)
                elif block_type.lower() in ["table", "table_row", "table_cell"]:
                    table_blocks.append(block)

            for block_group in block_groups:
                if block_group.type.lower() in ["table"]:
                    table_blocks.append(block_group)


            documents_to_embed = []

            # Process text blocks - create sentence embeddings
            if text_blocks:
                try:
                    for block in text_blocks:
                        block_text = block.data
                        metadata = {
                            "virtualRecordId": virtual_record_id,
                            "blockIndex": block.index,
                            "orgId": org_id,
                            "isBlockGroup": False,
                        }
                        doc = self.nlp(block_text)
                        sentences = [sent.text for sent in doc.sents]
                        if len(sentences) > 1:
                            for sentence in sentences:
                                documents_to_embed.append(
                                    Document(
                                        page_content=sentence,
                                        metadata={
                                            **metadata,
                                            "isBlock": False,
                                        },
                                    )
                                )
                        documents_to_embed.append(
                            Document(page_content=block_text, metadata={
                                        **metadata,
                                        "isBlock": True,
                                    },)
                        )

                    self.logger.info("✅ Added text documents for embedding")
                except Exception as e:
                    raise DocumentProcessingError(
                        "Failed to create text document objects: " + str(e),
                        details={"error": str(e)},
                    )

            # Process image blocks - create image embeddings
            if image_blocks:
                try:
                    images_uris = []
                    for block in image_blocks:
                        # Get image data from metadata
                        image_data = block.data
                        if image_data:
                            image_uri = image_data.get("uri")
                            images_uris.append(image_uri)

                    if images_uris:
                        if is_multimodal_embedding:
                            for block in image_blocks:
                                metadata = {
                                    "virtualRecordId": virtual_record_id,
                                    "blockIndex": block.index,
                                    "orgId": org_id,
                                    "isBlock": True,
                                    "isBlockGroup": False,
                                }
                                image_data = block.data
                                image_uri = image_data.get("uri")
                                documents_to_embed.append(
                                    {"image_uri": image_uri, "metadata": metadata}
                                )
                        elif is_multimodal_llm:
                            description_results = await self.describe_images(
                                images_uris,llm
                            )
                            for result, block in zip(description_results, image_blocks):
                                if result["success"]:
                                    metadata = {
                                        "virtualRecordId": virtual_record_id,
                                        "blockIndex": block.index,
                                        "orgId": org_id,
                                        "isBlock": True,
                                        "isBlockGroup": False,
                                    }
                                    description = result["description"]
                                    documents_to_embed.append(
                                        Document(
                                            page_content=description, metadata=metadata
                                        )
                                    )
                        elif mime_type in {MimeTypes.PNG.value, MimeTypes.JPG.value, MimeTypes.JPEG.value, MimeTypes.WEBP.value, MimeTypes.SVG.value, MimeTypes.HEIC.value, MimeTypes.HEIF.value}:
                            try:
                                record = await self.arango_service.get_document(
                                    record_id, CollectionNames.RECORDS.value
                                )
                                if not record:
                                    raise DocumentProcessingError(
                                        "Record not found in database",
                                        doc_id=record_id,
                                    )
                                doc = dict(record)
                                doc.update(
                                    {
                                        "indexingStatus": "ENABLE_MULTIMODAL_MODELS",
                                        "isDirty": True,
                                        "virtualRecordId": virtual_record_id,
                                    }
                                )

                                docs = [doc]

                                success = await self.arango_service.batch_upsert_nodes(
                                    docs, CollectionNames.RECORDS.value
                                )
                                if not success:
                                    raise DocumentProcessingError(
                                        "Failed to update indexing status", doc_id=record_id
                                    )

                                return False

                            except DocumentProcessingError:
                                raise
                            except Exception as e:
                                raise DocumentProcessingError(
                                    "Error updating record status: " + str(e),
                                    doc_id=record_id,
                                    details={"error": str(e)},
                                )
                except Exception as e:
                    raise DocumentProcessingError(
                        "Failed to create image document objects: " + str(e),
                        details={"error": str(e)},
                    )

            # Skip table blocks - no embedding creation
            if table_blocks:
                for block in table_blocks:
                    block_type = block.type
                    if block_type.lower() in ["table"]:
                        table_data = block.data
                        if table_data:
                            table_summary = table_data.get("table_summary","")
                            documents_to_embed.append(Document(page_content=table_summary, metadata={
                                "virtualRecordId": virtual_record_id,
                                "blockIndex": block.index,
                                "orgId": org_id,
                                "isBlock": False,
                                "isBlockGroup": True,
                            }))
                    elif block_type.lower() in ["table_row"]:
                        table_data = block.data
                        table_row_text = table_data.get("row_natural_language_text")
                        documents_to_embed.append(Document(page_content=table_row_text, metadata={
                            "virtualRecordId": virtual_record_id,
                            "blockIndex": block.index,
                            "orgId": org_id,
                            "isBlock": True,
                            "isBlockGroup": False,
                        }))

            if not documents_to_embed:
                self.logger.warning(
                    "⚠️ No documents to embed after filtering by block type"
                )
                return True

            # Create and store embeddings
            try:
                await self._create_embeddings(documents_to_embed, record_id, virtual_record_id)
            except Exception as e:
                raise EmbeddingError(
                    "Failed to create or store embeddings: " + str(e),
                    details={"error": str(e)},
                )

            return True

        except IndexingError:
            # Re-raise any of our custom exceptions
            raise
        except Exception as e:
            # Catch any unexpected errors
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__},
            )

