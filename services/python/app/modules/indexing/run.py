from langchain_experimental.text_splitter import SemanticChunker
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain.schema import Document as LangchainDocument
from qdrant_client import QdrantClient
from qdrant_client.http import models
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from app.config.arangodb_constants import CollectionNames
from app.utils.logger import create_logger
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from typing import List, Optional
from dotenv import load_dotenv
from app.exceptions.indexing_exceptions import *
from datetime import datetime, timezone

logger = create_logger(__name__)

@dataclass
class Document(LangchainDocument):
    """Extended Document class that supports bounding box"""
    bounding_box: Optional[List[Dict[str, float]]] = None

    def __init__(self, page_content: str, metadata: dict = None, bounding_box: Optional[List[Dict[str, float]]] = None):
        """Initialize document with page content, metadata, and bounding box"""
        super().__init__(page_content=page_content, metadata=metadata)
        self.bounding_box = bounding_box

    def __repr__(self):
        return f"Document(page_content={self.page_content[:50]}..., metadata={self.metadata}, bounding_box={self.bounding_box})"


class CustomChunker(SemanticChunker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.number_of_chunks = None
        self.breakpoint_threshold_type: str = "percentile"
        self.breakpoint_threshold_amount: float = 1

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """Override split_documents to use our custom merging logic"""
        try:
            logger.info("Splitting documents")
            if len(documents) <= 1:
                return documents

            # Calculate distances between adjacent documents
            try:
                distances, sentences = self._calculate_sentence_distances(
                    [doc.page_content for doc in documents]
                )
            except Exception as e:
                raise ChunkingError(
                    "Failed to calculate sentence distances",
                    details={"error": str(e)}
                )

            # Get breakpoint threshold
            try:
                if self.number_of_chunks is not None:
                    breakpoint_distance_threshold = self._threshold_from_clusters(distances)
                    breakpoint_array = distances
                else:
                    breakpoint_distance_threshold, breakpoint_array = self._calculate_breakpoint_threshold(
                        distances)
            except Exception as e:
                raise ChunkingError(
                    "Failed to calculate breakpoint threshold",
                    details={"error": str(e)}
                )

            # Find indices where we should NOT merge (where distance is too high)
            indices_above_thresh = [
                i for i, x in enumerate(breakpoint_array)
                if x > breakpoint_distance_threshold
            ]

            merged_documents = []
            start_index = 0

            # Merge documents between breakpoints
            try:
                for index in indices_above_thresh:
                    # Get group of documents to merge
                    group = documents[start_index:index + 1]
                    logger.info("Group: %s", group)
                    
                    # Merge text content
                    merged_text = " ".join(doc.page_content for doc in group)
                    
                    # Get bounding boxes directly from documents
                    bboxes = [doc.bounding_box for doc in group if doc.bounding_box]
                    metadata_list = [doc.metadata for doc in group]
                    
                    # Create merged metadata
                    merged_metadata = self._merge_metadata(metadata_list)
                    
                    # Update block numbers to reflect merged state
                    if len(group) > 1:
                        merged_metadata['blockNum'] = [doc.metadata.get('blockNum', 0) for doc in group]

                    # Create merged document with separate bounding_box attribute
                    merged_documents.append(Document(
                        page_content=merged_text,
                        metadata=merged_metadata,
                        bounding_box=self._merge_bboxes(bboxes) if bboxes else None
                    ))

                    start_index = index + 1

                # Handle the last group
                if start_index < len(documents):
                    group = documents[start_index:]
                    logger.debug("Group: %s", group)
                
                    merged_text = " ".join(doc.page_content for doc in group)
                    
                    bboxes = [doc.bounding_box for doc in group if doc.bounding_box]
                    metadata_list = [doc.metadata for doc in group]
                    
                    try:
                        merged_metadata = self._merge_metadata(metadata_list)
                        if len(group) > 1:
                            merged_metadata['blockNum'] = f"{group[0].metadata.get('blockNum', 0)}-{group[-1].metadata.get('blockNum', 0)}"

                        merged_documents.append(Document(
                            page_content=merged_text,
                            metadata=merged_metadata,
                            bounding_box=self._merge_bboxes(bboxes) if bboxes else None
                        ))
                    except MetadataProcessingError as e:
                        raise ChunkingError(
                            "Failed to process metadata during document merge",
                            details={"error": str(e)}
                        )
                    except Exception as e:
                        raise ChunkingError(
                            "Failed to merge document groups",
                            details={"error": str(e)}
                        )

                return merged_documents
            except Exception as e:
                raise ChunkingError(
                    "Failed to merge document groups",
                    details={"error": str(e)}
                )

        except ChunkingError:
            raise
        except Exception as e:
            raise ChunkingError(
                "Unexpected error during document splitting",
                details={"error": str(e)}
            )

    def _merge_bboxes(self, bboxes: List[List[dict]]) -> List[dict]:
        """Merge multiple bounding boxes into one encompassing box"""
        try:
            if not bboxes:
                return []

            if not all(isinstance(bbox, list) for bbox in bboxes):
                raise MetadataProcessingError(
                    "Invalid bounding box format",
                    details={"bboxes": bboxes}
                )

            try:
                # Get the extremes of all coordinates
                leftmost_x = min(point['x'] for bbox in bboxes for point in bbox)
                topmost_y = min(point['y'] for bbox in bboxes for point in bbox)
                rightmost_x = max(point['x'] for bbox in bboxes for point in bbox)
                bottommost_y = max(point['y'] for bbox in bboxes for point in bbox)

            except (KeyError, TypeError) as e:
                raise MetadataProcessingError(
                    "Invalid bounding box coordinate format",
                    details={"error": str(e)}
                )

            # Create new bounding box
            return [
                {'x': leftmost_x, 'y': topmost_y},
                {'x': rightmost_x, 'y': topmost_y},
                {'x': rightmost_x, 'y': bottommost_y},
                {'x': leftmost_x, 'y': bottommost_y}
            ]

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                "Failed to merge bounding boxes",
                details={"error": str(e)}
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
                    "Invalid metadata_list format",
                    details={"received_type": type(metadata_list).__name__}
                )

            if not metadata_list:
                return {}

            # Start with the first metadata as base
            merged_metadata = {}

            try:
                # Get all possible fields from all metadata dictionaries
                all_fields = set().union(*(meta.keys() for meta in metadata_list))

                for field in all_fields:
                    # Collect all non-None values for this field
                    field_values = [
                        meta[field] for meta in metadata_list 
                        if field in meta and meta[field] is not None
                    ]
                    
                    if not field_values:
                        continue

                    # Handle list fields - flatten and get unique values
                    if isinstance(field_values[0], list):
                        unique_values = set()
                        for value_list in field_values:
                            unique_values.update(value_list)
                        merged_metadata[field] = list(unique_values)
                
                    # Handle confidence score - keep maximum
                    elif field == 'confidence_score':
                        merged_metadata[field] = max(field_values)
                        
                    # Handle block numbers - keep range if different
                    elif field == 'blockNum':
                        if len(set(field_values)) > 1:
                            merged_metadata[field] = f"{field_values[0]}-{field_values[-1]}"
                        else:
                            merged_metadata[field] = field_values[0]
                            
                    # For all other fields
                    else:
                        unique_values = set(
                            str(v) if isinstance(v, (int, float)) else v 
                            for v in field_values
                        )
                        # If all values are the same, keep single value
                        if len(unique_values) == 1:
                            merged_metadata[field] = field_values[0]
                        # If values differ, keep all unique values in a list
                        else:
                            merged_metadata[field] = list(unique_values)

                return merged_metadata
            except Exception as e:
                raise MetadataProcessingError(
                    "Failed to merge metadata",
                    details={"error": str(e)}
                )

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                "Unexpected error during metadata merging",
                details={"error": str(e)}
            )


    
    def split_text(self, text: str) -> List[str]:
        """This method won't be used but needs to be implemented"""
        return [text]  # Return as is since we're not using this method


class IndexingPipeline:
    def __init__(
        self,
        arango_service,
        collection_name: str,
        qdrant_api_key: str,
        qdrant_host,
        grpc_port,
    ):
        """
        Initialize the indexing pipeline with necessary configurations.

        Args:
            collection_name: Name for the Qdrant collection
            qdrant_host: Qdrant server host URL
            qdrant_api_key: Optional API key for Qdrant
        """
        try:
            load_dotenv()  # Load environment variables

            # Initialize dense embeddings with BGE
            try:
                model_name = "BAAI/bge-large-en-v1.5"
                # Recommended by model authors
                encode_kwargs = {'normalize_embeddings': True}

                self.dense_embeddings = HuggingFaceEmbeddings(
                    model_name=model_name,
                    model_kwargs={'device': 'cpu'},
                    encode_kwargs=encode_kwargs
                )
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize dense embeddings",
                    details={"error": str(e)}
                )
            
            self.arango_service = arango_service

            # Initialize custom semantic chunker with BGE embeddings
            try:
                self.text_splitter = CustomChunker(
                    embeddings=self.dense_embeddings,
                    breakpoint_threshold_type="percentile",
                    breakpoint_threshold_amount=95,
                )
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize text splitter",
                    details={"error": str(e)}
                )

            # Initialize sparse embeddings
            try:
                self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize sparse embeddings",
                    details={"error": str(e)}
                )

            # Initialize Qdrant client and collection
            try:
                self.qdrant_client = QdrantClient(
                    host=qdrant_host,
                    grpc_port=grpc_port,
                    api_key=qdrant_api_key,
                    prefer_grpc=True,
                    https=False,
                )
            except Exception as e:
                raise VectorStoreError(
                    "Failed to initialize Qdrant client",
                    details={"error": str(e)}
                )

            self.collection_name = collection_name
            self._initialize_collection()

            # Initialize vector store
            try:
                self.vector_store = QdrantVectorStore(
                    client=self.qdrant_client,
                    collection_name=collection_name,
                    vector_name="dense",
                    sparse_vector_name="sparse",
                    embedding=self.dense_embeddings,
                    sparse_embedding=self.sparse_embeddings,
                    retrieval_mode=RetrievalMode.HYBRID,
                )
            except Exception as e:
                raise VectorStoreError(
                    "Failed to initialize vector store",
                    details={"error": str(e)}
                )

        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline",
                details={"error": str(e)}
            )

    def _initialize_collection(self, sparse_idf: bool = False):
        """Initialize Qdrant collection with proper configuration."""
        try:
            self.qdrant_client.get_collection(self.collection_name)
        except Exception as e:
            logger.info(f"Collection {self.collection_name} not found, creating new collection")
            try:
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={'dense': models.VectorParams(
                        size=1024, on_disk=False, distance=models.Distance.COSINE)},
                    sparse_vectors_config={
                        "sparse": models.SparseVectorParams(
                            index=models.SparseIndexParams(
                                on_disk=False
                            ),
                            modifier=models.Modifier.IDF if sparse_idf else None,
                        )
                    }
                )
                logger.info(f"✅ Successfully created collection {self.collection_name}")
            except Exception as e:
                logger.error(f"❌ Error creating collection {self.collection_name}: {str(e)}")
                raise VectorStoreError(
                    "Failed to create collection",
                    details={
                        "collection": self.collection_name,
                        "error": str(e)
                    }
                )

    async def _create_embeddings(self, chunks: List[Document]):
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
                    meta = chunk.metadata
                    enhanced_metadata = self._process_metadata(meta)
                    chunk.metadata = enhanced_metadata
                    
                    if chunk.bounding_box:
                        chunk.metadata['bounding_box'] = chunk.bounding_box
                        
                except Exception as e:
                    raise MetadataProcessingError(
                        f"Failed to process metadata for chunk",
                        details={"error": str(e), "metadata": meta}
                    )

            # Store in vector store
            try:
                await self.vector_store.aadd_documents(chunks)
            except Exception as e:
                raise VectorStoreError(
                    "Failed to store documents in vector store",
                    details={"error": str(e)}
                )

            logger.info(f"✅ Successfully added {len(chunks)} documents to vector store")
            
            # Update record with indexing status
            try:
                record = await self.arango_service.get_document(meta['recordId'], CollectionNames.RECORDS.value)
                if not record:
                    raise DocumentProcessingError(
                        "Record not found in database",
                        doc_id=meta['recordId']
                    )
                
                doc = dict(record)
                doc.update({"indexingStatus": "COMPLETED", "lastIndexTimestamp": int(datetime.now(timezone.utc).timestamp())})
                docs = [doc]
                
                success = await self.arango_service.batch_upsert_nodes(docs, CollectionNames.RECORDS.value)
                if not success:
                    raise DocumentProcessingError(
                        "Failed to update indexing status",
                        doc_id=meta['recordId']
                    )
                return
                
            except DocumentProcessingError:
                raise
            except Exception as e:
                raise DocumentProcessingError(
                    "Error updating record status",
                    doc_id=meta.get('recordId'),
                    details={"error": str(e)}
                )
            
        except (EmbeddingError, VectorStoreError, MetadataProcessingError, DocumentProcessingError):
            raise
        except Exception as e:
            raise IndexingError(
                "Unexpected error during embedding creation",
                details={"error": str(e)}
            )

    async def delete_embeddings(self, record_id: str):
        """
        Delete all embeddings associated with a record ID from the vector store

        Args:
            record_id (str): ID of the record whose embeddings should be deleted
            
        Raises:
            EmbeddingDeletionError: If there's an error during the deletion process
        """
        try:
            if not record_id:
                raise EmbeddingDeletionError(
                    "No record ID provided for deletion",
                    record_id=record_id
                )

            logger.info(f"🗑️ Deleting embeddings for record {record_id}")
            
            try:
                filter_dict = Filter(
                    should=[
                        FieldCondition(
                            key="metadata.recordId",
                            match=MatchValue(value=record_id)
                        )
                    ]
                )
                
                result = self.qdrant_client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_dict,
                    limit=1000000
                )
                
                ids = [point.id for point in result[0]]
                logger.info(f"🎯 Filter: {filter_dict}")
                logger.info(f"🎯 Ids: {ids}")
                
                if ids:
                    await self.vector_store.adelete(ids=ids)
                
                logger.info(f"✅ Successfully deleted embeddings for record {record_id}")
                
            except Exception as e:
                raise EmbeddingDeletionError(
                    "Failed to delete embeddings from vector store",
                    record_id=record_id,
                    details={"error": str(e)}
                )
                
        except EmbeddingDeletionError:
            raise
        except Exception as e:
            raise EmbeddingDeletionError(
                "Unexpected error during embedding deletion",
                record_id=record_id,
                details={"error": str(e)}
            )

    async def index_documents(self, sentences: List[Dict[str, Any]]):
        """
        Main method to index documents through the entire pipeline.

        Args:
            sentences: List of dictionaries containing text, bounding_box, and metadata
                    Each dict should have 'text', 'bounding_box', and 'metadata' keys
                    
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
                        page_content=sentence['text'],
                        metadata=sentence.get('metadata', {}),
                        bounding_box=sentence.get('bounding_box')
                    )
                    for sentence in sentences
                ]
            except Exception as e:
                raise DocumentProcessingError(
                    "Failed to create document objects",
                    details={"error": str(e)}
                )

            # Process documents into chunks
            try:
                chunks = self.text_splitter.split_documents(documents)
                if not chunks:
                    raise ChunkingError("No chunks were generated from the documents")
            except Exception as e:
                raise ChunkingError(
                    "Failed to split documents into chunks",
                    details={"error": str(e)}
                )

            # Create and store embeddings
            try:
                await self._create_embeddings(chunks)
            except Exception as e:
                raise EmbeddingError(
                    "Failed to create or store embeddings",
                    details={"error": str(e)}
                )

            return chunks

        except IndexingError:
            # Re-raise any of our custom exceptions
            raise
        except Exception as e:
            # Catch any unexpected errors
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__}
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
            # Create a copy to avoid modifying the original
            enhanced_metadata = {
                'orgId': meta.get('orgId', ''),
                'recordId': meta.get('recordId', ''),
                'recordName': meta.get('recordName', ''),
                'recordType': meta.get('recordType', ''),
                'recordVersion': meta.get('version', ''),
                'origin': meta.get('origin', ''),
                'connector': meta.get('connectorName', ''),
                'blockNum': meta.get('blockNum', 0),
                'blockText': meta.get('blockText', ''),
                'blockType': meta.get('blockType', 'paragraph'),
                'departments': meta.get('departments', []),
                'topics': meta.get('topics', []),
                'categories': meta.get('categories', []),
                'subcategoryLevel1': meta.get('subcategoryLevel1', []),
                'subcategoryLevel2': meta.get('subcategoryLevel2', []),
                'subcategoryLevel3': meta.get('subcategoryLevel3', []),
                'languages': meta.get('languages', []),
                'extension': meta.get('extension', ''),
                'mimeType': meta.get('mimeType', ''),
            }
            if meta.get('sheetName'):
                enhanced_metadata['sheetName'] = meta.get('sheetName')
            if meta.get('sheetNum'):
                enhanced_metadata['sheetNum'] = meta.get('sheetNum')
            if meta.get('pageNum'):
                enhanced_metadata['pageNum'] = meta.get('pageNum')

            return enhanced_metadata

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                f"Unexpected error processing metadata: {str(e)}",
                details={"error_type": type(e).__name__}
            )