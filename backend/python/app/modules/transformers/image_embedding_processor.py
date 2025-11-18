import asyncio
import re
import time
import uuid
from typing import List, Optional

import httpx
from langchain.chat_models.base import BaseChatModel
from langchain.schema import HumanMessage
from qdrant_client.http.models import PointStruct

from app.exceptions.indexing_exceptions import EmbeddingError
from app.modules.extraction.prompt_template import prompt_for_image_description
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.utils.aimodels import EmbeddingProvider


class ImageEmbeddingProcessor:
    """Handles all image embedding related tasks and functions."""

    def __init__(
        self,
        logger,
        embedding_provider: Optional[str],
        model_name: Optional[str],
        dense_embeddings,
        vector_db_service: Optional[IVectorDBService]=None,
        collection_name: Optional[str]=None,
        api_key: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> None:
        """
        Initialize the ImageEmbeddingProcessor.

        Args:
            logger: Logger instance
            embedding_provider: The embedding provider name (e.g., "cohere", "voyage", etc.)
            model_name: The model name to use for embeddings
            dense_embeddings: Dense embedding model instance
            vector_db_service: Vector database service instance
            collection_name: Name of the collection to store embeddings
            api_key: API key for the embedding service (for Cohere, Jina AI)
            aws_access_key_id: AWS access key ID (for Bedrock)
            aws_secret_access_key: AWS secret access key (for Bedrock)
            region_name: AWS region name (for Bedrock)
        """
        self.logger = logger
        self.embedding_provider = embedding_provider
        self.model_name = model_name
        self.api_key = api_key
        self.dense_embeddings = dense_embeddings
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.region_name = region_name

    async def normalize_image_to_base64(self, image_uri: str) -> str | None:
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

    def _create_point_structs(
        self, embeddings: List[List[float]], image_chunks: List[dict], start_index: int = 0
    ) -> List[PointStruct]:
        """
        Create PointStruct objects from embeddings and image chunks.

        Args:
            embeddings: List of embedding vectors
            image_chunks: List of image chunk dictionaries
            start_index: Starting index for matching chunks to embeddings

        Returns:
            List of PointStruct objects
        """
        points = []
        for i, embedding in enumerate(embeddings):
            chunk_idx = start_index + i
            if chunk_idx < len(image_chunks):
                image_chunk = image_chunks[chunk_idx]
                point = PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"dense": embedding},
                    payload={
                        "metadata": image_chunk.get("metadata", {}),
                        "page_content": image_chunk.get("image_uri", ""),
                    },
                )
                points.append(point)
        return points

    async def _create_cohere_embeddings(
        self, image_base64s: List[str]
    ) -> List[Optional[List[float]]]:
        """Create embeddings using Cohere API."""
        if self.api_key is None:
            raise EmbeddingError("API key not found for Cohere")
        import cohere

        co = cohere.ClientV2(api_key=self.api_key)

        async def embed_single_image(i: int, image_base64: str) -> Optional[List[float]]:
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
                embedding = response.embeddings.float[0]
                return embedding
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

        async def limited_embed(i: int, image_base64: str) -> Optional[List[float]]:
            async with semaphore:
                return await embed_single_image(i, image_base64)

        tasks = [limited_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        embeddings = []
        for result in results:
            if isinstance(result, list):
                embeddings.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image: {str(result)}")
                embeddings.append(None)
            else:
                embeddings.append(None)

        return embeddings

    async def process_image_embeddings_cohere(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Cohere API."""
        embeddings = await self._create_cohere_embeddings(image_base64s)

        # Filter out None embeddings and create points
        points = []
        for i, embedding in enumerate(embeddings):
            if embedding is not None:
                point_structs = self._create_point_structs([embedding], image_chunks, start_index=i)
                points.extend(point_structs)

        return points

    async def _create_voyage_embeddings(
        self, image_base64s: List[str], batch_size: int = 32
    ) -> List[Optional[List[float]]]:
        """Create embeddings using Voyage AI."""
        embeddings = [None] * len(image_base64s)

        async def process_voyage_batch(batch_start: int, batch_images: List[str]) -> tuple[int, List[List[float]]]:
            """Process a single batch of images with Voyage AI."""
            try:
                batch_embeddings = await self.dense_embeddings.aembed_documents(batch_images)
                self.logger.info(
                    f"✅ Processed Voyage batch starting at {batch_start}: {len(batch_embeddings)} image embeddings"
                )
                return batch_start, batch_embeddings
            except Exception as voyage_error:
                self.logger.warning(
                    f"Failed to process Voyage batch starting at {batch_start}: {str(voyage_error)}"
                )
                return batch_start, []

        batches = []
        for batch_start in range(0, len(image_base64s), batch_size):
            batch_end = min(batch_start + batch_size, len(image_base64s))
            batch_images = image_base64s[batch_start:batch_end]
            batches.append((batch_start, batch_images))

        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_voyage_batch(batch_start: int, batch_images: List[str]) -> tuple[int, List[List[float]]]:
            async with semaphore:
                return await process_voyage_batch(batch_start, batch_images)

        tasks = [limited_voyage_batch(start, imgs) for start, imgs in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple):
                batch_start, batch_embeddings = result
                for i, embedding in enumerate(batch_embeddings):
                    if batch_start + i < len(embeddings):
                        embeddings[batch_start + i] = embedding
            elif isinstance(result, Exception):
                self.logger.warning(f"Voyage batch processing exception: {str(result)}")

        return embeddings

    async def process_image_embeddings_voyage(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Voyage AI."""
        embeddings = await self._create_voyage_embeddings(image_base64s)

        # Filter out None embeddings and create points
        points = []
        for i, embedding in enumerate(embeddings):
            if embedding is not None:
                point_structs = self._create_point_structs([embedding], image_chunks, start_index=i)
                points.extend(point_structs)

        return points

    async def _create_bedrock_embeddings(
        self, image_base64s: List[str]
    ) -> List[Optional[List[float]]]:
        """Create embeddings using AWS Bedrock."""
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

        async def embed_single_bedrock_image(i: int, image_ref: str) -> Optional[List[float]]:
            """Embed a single image with AWS Bedrock."""
            normalized_b64 = await self.normalize_image_to_base64(image_ref)
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
                return image_embedding
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

        async def limited_bedrock_embed(i: int, image_ref: str) -> Optional[List[float]]:
            async with semaphore:
                return await embed_single_bedrock_image(i, image_ref)

        tasks = [limited_bedrock_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        embeddings = []
        for result in results:
            if isinstance(result, list):
                embeddings.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image with Bedrock: {str(result)}")
                embeddings.append(None)
            else:
                embeddings.append(None)

        return embeddings

    async def process_image_embeddings_bedrock(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using AWS Bedrock."""
        embeddings = await self._create_bedrock_embeddings(image_base64s)

        # Filter out None embeddings and create points
        points = []
        for i, embedding in enumerate(embeddings):
            if embedding is not None:
                point_structs = self._create_point_structs([embedding], image_chunks, start_index=i)
                points.extend(point_structs)

        return points

    async def _create_jina_embeddings(
        self, image_base64s: List[str], batch_size: int = 32
    ) -> List[Optional[List[float]]]:
        """Create embeddings using Jina AI."""
        embeddings = [None] * len(image_base64s)
        if self.api_key is None:
            raise EmbeddingError("API key not found for Jina AI")
        async def process_jina_batch(
            client: httpx.AsyncClient, batch_start: int, batch_images: List[str]
        ) -> tuple[List[int], List[List[float]]]:
            """Process a single batch of images with Jina AI."""
            try:
                url = 'https://api.jina.ai/v1/embeddings'
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + self.api_key
                }
                normalized_images = await asyncio.gather(*[
                    self.normalize_image_to_base64(image_base64)
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
                    return [], []

                data = {
                    "model": self.model_name,
                    "input": [
                        {"image": normalized_b64}
                        for normalized_b64 in valid_normalized_images
                    ]
                }

                response = await client.post(url, headers=headers, json=data)
                response_body = response.json()
                batch_embeddings = [r["embedding"] for r in response_body["data"]]

                self.logger.info(
                    f"✅ Processed Jina AI batch starting at {batch_start}: {len(batch_embeddings)} image embeddings"
                )
                return valid_indices, batch_embeddings
            except Exception as jina_error:
                self.logger.warning(
                    f"Failed to process Jina AI batch starting at {batch_start}: {str(jina_error)}"
                )
                return [], []

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = []
            for batch_start in range(0, len(image_base64s), batch_size):
                batch_end = min(batch_start + batch_size, len(image_base64s))
                batch_images = image_base64s[batch_start:batch_end]
                batches.append((batch_start, batch_images))

            concurrency_limit = 5
            semaphore = asyncio.Semaphore(concurrency_limit)

            async def limited_process_batch(
                batch_start: int, batch_images: List[str]
            ) -> tuple[List[int], List[List[float]]]:
                async with semaphore:
                    return await process_jina_batch(client, batch_start, batch_images)

            tasks = [limited_process_batch(start, imgs) for start, imgs in batches]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, tuple):
                    valid_indices, batch_embeddings = result
                    for idx, embedding in zip(valid_indices, batch_embeddings):
                        if idx < len(embeddings):
                            embeddings[idx] = embedding
                elif isinstance(result, Exception):
                    self.logger.warning(f"Jina AI batch processing exception: {str(result)}")

        return embeddings

    async def process_image_embeddings_jina(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Jina AI."""
        embeddings = await self._create_jina_embeddings(image_base64s)

        # Filter out None embeddings and create points
        points = []
        for i, embedding in enumerate(embeddings):
            if embedding is not None:
                point_structs = self._create_point_structs([embedding], image_chunks, start_index=i)
                points.extend(point_structs)

        return points

    async def create_embeddings(
        self, image_base64s: List[str], batch_size: Optional[int] = None
    ) -> List[Optional[List[float]]]:
        """
        Generic method to create embeddings for images based on the configured provider.

        Args:
            image_base64s: List of base64-encoded image strings
            batch_size: Optional batch size for providers that support batching (Voyage, Jina)

        Returns:
            List of embedding vectors (or None for failed embeddings)
        """
        if self.embedding_provider == EmbeddingProvider.COHERE.value:
            return await self._create_cohere_embeddings(image_base64s)
        elif self.embedding_provider == EmbeddingProvider.VOYAGE.value:
            batch_size = batch_size or 32
            return await self._create_voyage_embeddings(image_base64s, batch_size=batch_size)
        elif self.embedding_provider == EmbeddingProvider.AWS_BEDROCK.value:
            return await self._create_bedrock_embeddings(image_base64s)
        elif self.embedding_provider == EmbeddingProvider.JINA_AI.value:
            batch_size = batch_size or 32
            return await self._create_jina_embeddings(image_base64s, batch_size=batch_size)
        else:
            self.logger.warning(f"Unsupported embedding provider for images: {self.embedding_provider}")
            return []

    async def process_image_embeddings(
        self, image_base64s: List[str], image_chunks: Optional[List[dict]]=None
    ) -> List[PointStruct]:
        """Process image embeddings based on the configured provider."""
        if self.embedding_provider == EmbeddingProvider.COHERE.value:
            return await self.process_image_embeddings_cohere(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.VOYAGE.value:
            return await self.process_image_embeddings_voyage(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.AWS_BEDROCK.value:
            return await self.process_image_embeddings_bedrock(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.JINA_AI.value:
            return await self.process_image_embeddings_jina(image_chunks, image_base64s)
        else:
            self.logger.warning(f"Unsupported embedding provider for images: {self.embedding_provider}")
            return []

    async def store_image_points(self, points: List[PointStruct]) -> None:
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

    async def describe_image_async(self, base64_string: str, vlm: BaseChatModel) -> str:
        """Describe a single image using a vision language model."""
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_for_image_description},
                {"type": "image_url", "image_url": {"url": base64_string}},
            ]
        )
        response = await vlm.ainvoke([message])
        return response.content

    async def describe_images(self, base64_images: List[str], vlm: BaseChatModel) -> List[dict]:
        """Describe multiple images using a vision language model."""
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



