import time
from typing import Any, Dict, List

from langchain.schema import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
)
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    ChunkingError,
    DocumentProcessingError,
    EmbeddingDeletionError,
    EmbeddingError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.services.vector_db.const.const import ORG_ID_FIELD, VIRTUAL_RECORD_ID_FIELD
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.utils.aimodels import get_default_embedding_model, get_embedding_model
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class CustomChunker(SemanticChunker):
    def __init__(self, logger, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.logger = logger
        self.number_of_chunks = None
        self.breakpoint_threshold_type: str = "percentile"
        self.breakpoint_threshold_amount: float = 1

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """Override split_documents to use our custom merging logic"""
        try:
            self.logger.info("Splitting documents")
            if len(documents) <= 1:
                return documents

            # Calculate distances between adjacent documents
            try:
                distances, sentences = self._calculate_sentence_distances(
                    [doc.page_content for doc in documents]
                )
            except Exception as e:
                raise ChunkingError(
                    "Failed to calculate sentence distances: " + str(e),
                    details={"error": str(e)},
                )

            # Get breakpoint threshold
            try:
                if self.number_of_chunks is not None:
                    breakpoint_distance_threshold = self._threshold_from_clusters(
                        distances
                    )
                    breakpoint_array = distances
                else:
                    breakpoint_distance_threshold, breakpoint_array = (
                        self._calculate_breakpoint_threshold(distances)
                    )
            except Exception as e:
                raise ChunkingError(
                    "Failed to calculate breakpoint threshold: " + str(e),
                    details={"error": str(e)},
                )

            # Find indices where we should NOT merge (where distance is too high)
            indices_above_thresh = [
                i
                for i, x in enumerate(breakpoint_array)
                if x > breakpoint_distance_threshold
            ]

            merged_documents = []
            start_index = 0

            # Merge documents between breakpoints
            try:
                for index in indices_above_thresh:
                    # Get group of documents to merge
                    group = documents[start_index : index + 1]

                    # Merge text content
                    merged_text = " ".join(doc.page_content for doc in group)
                    # Get bounding boxes directly from metadata
                    bboxes = [
                        doc.metadata.get("bounding_box", [])
                        for doc in group
                        if doc.metadata.get("bounding_box")
                    ]
                    metadata_list = [doc.metadata for doc in group]

                    # Create merged metadata
                    merged_metadata = self._merge_metadata(metadata_list)

                    # Update block numbers to reflect merged state
                    if len(group) > 1:
                        block_nums = []
                        for doc in group:
                            nums = doc.metadata.get("blockNum", [])
                            if isinstance(nums, list):
                                block_nums.extend(nums)
                            else:
                                block_nums.append(nums)
                        merged_metadata["blockNum"] = sorted(
                            list(set(block_nums))
                        )  # Remove duplicates and sort

                    # Merge bounding boxes and add to metadata
                    merged_metadata["bounding_box"] = (
                        self._merge_bboxes(bboxes) if bboxes else None
                    )
                    # Create merged document
                    merged_documents.append(
                        Document(
                            page_content=merged_text,
                            metadata=merged_metadata,
                        )
                    )

                    start_index = index + 1

                # Handle the last group
                if start_index < len(documents):
                    group = documents[start_index:]

                    merged_text = " ".join(doc.page_content for doc in group)

                    # Get bounding boxes from metadata
                    bboxes = [
                        doc.metadata.get("bounding_box", [])
                        for doc in group
                        if doc.metadata.get("bounding_box")
                    ]
                    metadata_list = [doc.metadata for doc in group]

                    try:
                        merged_metadata = self._merge_metadata(metadata_list)
                        if len(group) > 1:
                            block_nums = []
                            for doc in group:
                                nums = doc.metadata.get("blockNum", [])
                                if isinstance(nums, list):
                                    block_nums.extend(nums)
                                else:
                                    block_nums.append(nums)
                            merged_metadata["blockNum"] = sorted(
                                list(set(block_nums))
                            )  # Remove duplicates and sort

                        # Merge bounding boxes and add to metadata
                        merged_metadata["bounding_box"] = (
                            self._merge_bboxes(bboxes) if bboxes else None
                        )

                        merged_documents.append(
                            Document(
                                page_content=merged_text,
                                metadata=merged_metadata,
                            )
                        )
                    except MetadataProcessingError as e:
                        raise ChunkingError(
                            "Failed to process metadata during document merge: "
                            + str(e),
                            details={"error": str(e)},
                        )
                    except Exception as e:
                        raise ChunkingError(
                            "Failed to merge document groups: " + str(e),
                            details={"error": str(e)},
                        )

                return merged_documents
            except Exception as e:
                raise ChunkingError(
                    "Failed to merge document groups: " + str(e),
                    details={"error": str(e)},
                )

        except ChunkingError:
            raise
        except Exception as e:
            raise ChunkingError(
                "Unexpected error during document splitting: " + str(e),
                details={"error": str(e)},
            )

    def _merge_bboxes(self, bboxes: List[List[dict]]) -> List[dict]:
        """Merge multiple bounding boxes into one encompassing box"""
        try:
            if not bboxes:
                return []

            if not all(isinstance(bbox, list) for bbox in bboxes):
                raise MetadataProcessingError(
                    "Invalid bounding box format.", details={"bboxes": bboxes}
                )

            try:
                # Get the extremes of all coordinates
                leftmost_x = min(point["x"] for bbox in bboxes for point in bbox)
                topmost_y = min(point["y"] for bbox in bboxes for point in bbox)
                rightmost_x = max(point["x"] for bbox in bboxes for point in bbox)
                bottommost_y = max(point["y"] for bbox in bboxes for point in bbox)

            except (KeyError, TypeError) as e:
                raise MetadataProcessingError(
                    "Invalid bounding box coordinate format: " + str(e),
                    details={"error": str(e)},
                )

            # Create new bounding box
            return [
                {"x": leftmost_x, "y": topmost_y},
                {"x": rightmost_x, "y": topmost_y},
                {"x": rightmost_x, "y": bottommost_y},
                {"x": leftmost_x, "y": bottommost_y},
            ]

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                "Failed to merge bounding boxes: " + str(e), details={"error": str(e)}
            )

    def _merge_metadata(self, metadata_list: List[dict]) -> dict:
        """
        Merge metadata from multiple documents.
        For each field:
        - If all values are the same, keep single value
        - If values differ, keep all unique values in a list
        """
        try:
            if not isinstance(metadata_list, list):
                raise MetadataProcessingError(
                    "Invalid metadata_list format.",
                    details={"received_type": type(metadata_list).__name__},
                )

            if not metadata_list:
                return {}

            merged_metadata = {}

            try:
                all_fields = set().union(*(meta.keys() for meta in metadata_list))

                for field in all_fields:
                    # Collect all non-None values for this field
                    field_values = [
                        meta[field]
                        for meta in metadata_list
                        if field in meta and meta[field] is not None
                    ]

                    if not field_values:
                        continue

                    # Handle list fields - flatten and get unique values
                    if isinstance(field_values[0], list):
                        unique_values = []
                        seen = set()
                        for value_list in field_values:
                            for value in value_list:
                                value_str = str(value)
                                if value_str not in seen:
                                    seen.add(value_str)
                                    unique_values.append(value)
                        merged_metadata[field] = unique_values

                    # Handle confidence score - keep maximum
                    elif field == "confidence_score":
                        merged_metadata[field] = max(field_values)

                    # For all other fields
                    else:
                        # Convert values to strings for comparison
                        str_values = [str(v) for v in field_values]
                        # If all values are the same, keep single value
                        if len(set(str_values)) == 1:
                            merged_metadata[field] = field_values[0]
                        # If values differ, keep all unique values in a list
                        else:
                            # Keep original values but ensure uniqueness
                            unique_values = []
                            seen = set()
                            for value in field_values:
                                value_str = str(value)
                                if value_str not in seen:
                                    seen.add(value_str)
                                    unique_values.append(value)
                            merged_metadata[field] = unique_values

                return merged_metadata
            except Exception as e:
                raise MetadataProcessingError(
                    "Failed to merge metadata: " + str(e), details={"error": str(e)}
                )

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                "Unexpected error during metadata merging: " + str(e),
                details={"error": str(e)},
            )

    def split_text(self, text: str) -> List[str]:
        """This method won't be used but needs to be implemented"""
        return [text]  # Return as is since we're not using this method


class IndexingPipeline:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        arango_service,
        collection_name: str,
        vector_db_service: IVectorDBService,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service
        """
        Initialize the indexing pipeline with necessary configurations.

        Args:
            config_service: Configuration service
            arango_service: Arango service
            collection_name: Name for the collection
            vector_db_service: Vector DB service
        """
        try:
            # Initialize sparse embeddings
            try:
                self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize sparse embeddings: " + str(e),
                    details={"error": str(e)},
                )

            self.vector_db_service = vector_db_service
            self.collection_name = collection_name
            self.vector_store = None

        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline: " + str(e),
                details={"error": str(e)},
            )

    async def _initialize_collection(
        self, embedding_size: int = 1024, sparse_idf: bool = False
    ) -> None:
        """Initialize collection with proper configuration."""
        try:
            collection_info = await self.vector_db_service.get_collection(self.collection_name)
            if not collection_info:
                self.logger.info(
                    f"Collection {self.collection_name} not found, creating new collection"
                )
                raise Exception("Collection not found")
            current_vector_size = collection_info.config.params.vectors["dense"].size #type: ignore

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
                # create the collection
                await self.vector_db_service.create_collection(
                    collection_name=self.collection_name,
                    embedding_size=embedding_size,
                    sparse_idf=sparse_idf,
                )
                self.logger.info(
                    f"✅ Successfully created collection {self.collection_name}"
                )
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name=VIRTUAL_RECORD_ID_FIELD,
                    field_schema={
                        "type": "keyword",
                    }
                )
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name=ORG_ID_FIELD,
                    field_schema={
                        "type": "keyword",
                    }
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
            dense_embeddings = None
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value
            )
            embedding_configs = ai_models.get("embedding", [])
            if not embedding_configs:
                dense_embeddings = get_default_embedding_model()
            else:
                dense_embeddings = None
                for config in embedding_configs:
                    if config.get("isDefault", False):
                        provider = config["provider"]
                        dense_embeddings = get_embedding_model(provider, config)
                        break

                if not dense_embeddings:
                    self.logger.info("No default embedding model found, using first available provider")
                    for config in embedding_configs:
                        provider = config["provider"]
                        dense_embeddings = get_embedding_model(provider, config)
                        break

                if not dense_embeddings:
                    raise IndexingError("No default embedding model found")

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

            # Initialize custom semantic chunker with BGE embeddings
            try:
                self.text_splitter = CustomChunker(
                    logger=self.logger,
                    embeddings=dense_embeddings,
                    breakpoint_threshold_type="percentile",
                    breakpoint_threshold_amount=95,
                )
            except IndexingError as e:
                raise IndexingError(
                    "Failed to initialize text splitter: " + str(e),
                    details={"error": str(e)},
                )

            return True
        except IndexingError as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            raise IndexingError(
                "Failed to get embedding model: " + str(e), details={"error": str(e)}
            )

    async def _create_embeddings(self, chunks: List[Document]) -> None:
        """
        Create both sparse and dense embeddings for document chunks and store them in vector store.

        Args:
            chunks: List of document chunks to embed

        Raises:
            EmbeddingError: If there's an error creating embeddings
            VectorStoreError: If there's an error storing embeddings
            MetadataProcessingError: If there's an error processing metadata
            DocumentProcessingError: If there's an error updating document status
        """
        try:
            # Validate input
            if not chunks:
                raise EmbeddingError("No chunks provided for embedding creation")

            # Process metadata for each chunk
            for chunk in chunks:
                try:
                    virtual_record_id = chunk.metadata["virtualRecordId"]
                    meta = chunk.metadata
                    enhanced_metadata = self._process_metadata(meta)
                    chunk.metadata = enhanced_metadata

                except Exception as e:
                    raise MetadataProcessingError(
                        "Failed to process metadata for chunk: " + str(e),
                        details={"error": str(e), "metadata": meta},
                    )

            self.logger.debug("Enhanced metadata processed")

            # Store in vector store
            try:
                start_time = time.perf_counter()
                self.logger.info(f"⏱️ Starting embeddings insertion for {len(chunks)} chunks into vector store")

                await self.vector_store.aadd_documents(chunks)

                elapsed_time = time.perf_counter() - start_time
                if len(chunks) > 0:
                    self.logger.info(
                        f"✅ Successfully added {len(chunks)} documents to vector store in {elapsed_time:.2f}s (avg: {elapsed_time/len(chunks)*1000:.2f}ms per document)"
                    )
                else:
                    self.logger.info(
                        f"✅ Successfully added 0 documents to vector store in {elapsed_time:.2f}s"
                    )
            except Exception as e:
                raise VectorStoreError(
                    "Failed to store documents in vector store: " + str(e),
                    details={"error": str(e)},
                )

            # Update record with indexing status
            try:
                record = await self.arango_service.get_document(
                    meta["recordId"], CollectionNames.RECORDS.value
                )
                if not record:
                    raise DocumentProcessingError(
                        "Record not found in database",
                        doc_id=meta["recordId"],
                    )

                doc = dict(record)
                doc.update(
                    {
                        "indexingStatus": "COMPLETED",
                        "isDirty": False,
                        "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                        "virtualRecordId": virtual_record_id,
                    }
                )

                docs = [doc]

                success = await self.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )
                if not success:
                    raise DocumentProcessingError(
                        "Failed to update indexing status", doc_id=meta["recordId"]
                    )
                return

            except DocumentProcessingError:
                raise
            except Exception as e:
                raise DocumentProcessingError(
                    "Error updating record status: " + str(e),
                    doc_id=meta.get("recordId"),
                    details={"error": str(e)},
                )

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

    async def delete_embeddings(self, record_id: str, virtual_record_id: str) -> None:
        """
        Delete embeddings only if this is the last record with this virtual_record_id.
        If other records exist with the same virtual_record_id, skip deletion.

        Args:
            record_id (str): ID of the record whose embeddings should be deleted
            virtual_record_id (str): Virtual record ID to check for other records

        Raises:
            EmbeddingDeletionError: If there's an error during the deletion process
        """
        try:
            if not record_id:
                raise EmbeddingDeletionError(
                    "No record ID provided for deletion", record_id=record_id
                )

            if not virtual_record_id:
                self.logger.info(f"No virtual record ID provided for deletion, skipping embedding deletion for record {record_id}")
                return

            self.logger.info(f"🔍 Checking other records with virtual_record_id {virtual_record_id}")

            # Get other records with same virtual_record_id
            other_records = await self.arango_service.get_records_by_virtual_record_id(
                virtual_record_id=virtual_record_id
            )

            # Filter out the current record
            other_records = [r for r in other_records if r != record_id]

            if other_records:
                self.logger.info(
                    f"⏭️ Skipping embedding deletion for record {record_id} as other records "
                    f"exist with same virtual_record_id: {other_records}"
                )
                return

            self.logger.info("🗑️ Proceeding with deletion as no other records exist")

            try:
                filter_dict = await self.vector_db_service.filter_collection(
                    must={"virtualRecordId": virtual_record_id}
                )

                result = await self.vector_db_service.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_dict,
                    limit=1000000,
                )

                if not result:
                    self.logger.info(f"No embeddings found for record {record_id}")
                    return

                ids = [point.id for point in result[0]] #type: ignore
                self.logger.info(f"🎯 Filter: {filter_dict}")

                try:
                    await self.get_embedding_model_instance()
                except Exception as e:
                    raise IndexingError(
                        "Failed to get embedding model instance: " + str(e),
                        details={"error": str(e)},
                    )

                if ids:
                    await self.vector_store.adelete(ids=ids)

                self.logger.info(
                    f"✅ Successfully deleted embeddings for record {record_id}"
                )

            except Exception as e:
                raise EmbeddingDeletionError(
                    "Failed to delete embeddings from vector store: " + str(e),
                    record_id=record_id,
                    details={"error": str(e)},
                )

        except EmbeddingDeletionError:
            raise
        except Exception as e:
            raise EmbeddingDeletionError(
                "Unexpected error during embedding deletion: " + str(e),
                record_id=record_id,
                details={"error": str(e)},
            )

    async def index_documents(
        self, sentences: List[Dict[str, Any]], merge_documents: bool = False
    ) -> List[Document]:
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
            if not sentences:
                raise DocumentProcessingError("No sentences provided for indexing")

            # Convert sentences to custom Document class
            try:
                documents = [
                    Document(
                        page_content=sentence["text"],
                        metadata=sentence.get("metadata", {}),
                    )
                    for sentence in sentences
                ]
            except Exception as e:
                raise DocumentProcessingError(
                    "Failed to create document objects: " + str(e),
                    details={"error": str(e)},
                )

            try:
                await self.get_embedding_model_instance()
            except Exception as e:
                raise IndexingError(
                    "Failed to get embedding model instance: " + str(e),
                    details={"error": str(e)},
                )

            # Create and store embeddings
            try:
                await self._create_embeddings(documents)
            except Exception as e:
                raise EmbeddingError(
                    "Failed to create or store embeddings: " + str(e),
                    details={"error": str(e)},
                )

            return documents

        except IndexingError:
            # Re-raise any of our custom exceptions
            raise
        except Exception as e:
            # Catch any unexpected errors
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__},
            )

    def _process_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and enhance document metadata.

        Args:
            metadata: Original metadata dictionary

        Returns:
            Dict[str, Any]: Enhanced metadata

        Raises:
            MetadataProcessingError: If there's an error processing the metadata
        """
        try:
            block_type = meta.get("blockType", "text")
            virtual_record_id = meta.get("virtualRecordId", "")
            record_name = meta.get("recordName", "")
            if isinstance(block_type, list):
                block_type = block_type[0]

            enhanced_metadata = {
                "orgId": meta.get("orgId", ""),
                "virtualRecordId": virtual_record_id,
                "recordName": record_name,
                "recordType": meta.get("recordType", ""),
                "recordVersion": meta.get("version", ""),
                "origin": meta.get("origin", ""),
                "connector": meta.get("connectorName", ""),
                "blockNum": meta.get("blockNum", [0]),
                "blockText": meta.get("blockText", ""),
                "blockType": str(block_type),
                "departments": meta.get("departments", ""),
                "topics": meta.get("topics", ""),
                "categories": meta.get("categories", ""),
                "subcategoryLevel1": meta.get("subcategoryLevel1", ""),
                "subcategoryLevel2": meta.get("subcategoryLevel2", ""),
                "subcategoryLevel3": meta.get("subcategoryLevel3", ""),
                "languages": meta.get("languages", ""),
                "extension": meta.get("extension", ""),
                "mimeType": meta.get("mimeType", ""),
            }

            if meta.get("bounding_box"):
                enhanced_metadata["bounding_box"] = meta.get("bounding_box")
            if meta.get("sheetName"):
                enhanced_metadata["sheetName"] = meta.get("sheetName")
            if meta.get("sheetNum"):
                enhanced_metadata["sheetNum"] = meta.get("sheetNum")
            if meta.get("pageNum"):
                enhanced_metadata["pageNum"] = meta.get("pageNum")

            return enhanced_metadata

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                f"Unexpected error processing metadata: {str(e)}",
                details={"error_type": type(e).__name__},
            )
