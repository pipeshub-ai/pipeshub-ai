from typing import Any, Tuple

import grpc
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from qdrant_client.http import models

from app.utils.aimodels import get_default_embedding_model, get_embedding_model
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

SPARSE_IDF = False


@router.post("/llm-health-check")
async def llm_health_check(request: Request, llm_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate user-provided LLM configurations"""
    try:
        app = request.app
        llm = await get_llm(app.container.config_service(), llm_configs)
        # Make a simple test call to the LLM with the provided configurations
        await llm.ainvoke("Test message to verify LLM health.")

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": "LLM service is responding",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"LLM service health check failed: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

async def initialize_embedding_model(request: Request, embedding_configs: list[dict]) -> Tuple[Any, Any, Any]:
    """Initialize the embedding model and return necessary components."""
    app = request.app
    logger = app.container.logger()

    logger.info("Starting embedding health check", extra={"embedding_configs": embedding_configs})

    retrieval_service = await app.container.retrieval_service()
    logger.info("Retrieved retrieval service")

    try:
        if not embedding_configs:
            logger.info("Using default embedding model")
            dense_embeddings = get_default_embedding_model()
        else:

            if embedding_configs:
                config = embedding_configs[0] # Use the first configuration
                dense_embeddings = get_embedding_model(config["provider"], config)
    except Exception as e:
        logger.error(f"Failed to initialize embedding model: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "not healthy",
                "error": f"Failed to initialize embedding model: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            }
        )

    if dense_embeddings is None:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "not healthy",
                "error": "Failed to initialize embedding model",
                "details": {
                    "embedding_model": "initialization_failed",
                    "vector_store": "unknown",
                    "llm": "unknown"
                }
            }
        )

    return dense_embeddings, retrieval_service, logger

async def verify_embedding_health(dense_embeddings, logger) -> int:
    """Verify embedding model health by generating a test embedding."""
    sample_embedding = await dense_embeddings.aembed_query("Test message to verify embedding model health.")
    embedding_size = len(sample_embedding)

    if not sample_embedding or embedding_size == 0:
        logger.error("Embedding model returned empty embedding")
        raise HTTPException(
            status_code=500,
            detail={
                "status": "not healthy",
                "error": "Embedding model returned empty embedding",
                "timestamp": get_epoch_timestamp_in_ms(),
            }
        )

    return embedding_size

async def handle_model_change(
    retrieval_service,
    current_model_name: str,
    new_model_name: str,
    qdrant_vector_size: int,
    points_count: int,
    embedding_size: int,
    logger
) -> None:
    """Handle embedding model changes and collection recreation if needed."""
    if current_model_name is not None:
        current_model_name = current_model_name.removeprefix("models/")
    if new_model_name is not None:
        new_model_name = new_model_name.removeprefix("models/")

    if (current_model_name is not None and
        new_model_name is not None and
        current_model_name.lower() != new_model_name.lower()):



        logger.warning("Detected embedding model change attempt")

        if qdrant_vector_size != 0 and points_count > 0:
            logger.error("Rejected embedding model change due to non-empty existing collection")
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "not healthy",
                    "error": "Policy Rejection: Embedding model configuration cannot be changed while vector store collection contains data. Please ensure you are using the original embedding configuration.",
                    "timestamp": get_epoch_timestamp_in_ms(),
                }
            )

        if qdrant_vector_size != 0 and points_count == 0:
            await recreate_collection(retrieval_service, embedding_size, logger)

async def recreate_collection(retrieval_service, embedding_size, logger) -> None:
    """Recreate the Qdrant collection with new parameters."""
    try:
        retrieval_service.qdrant_client.delete_collection(retrieval_service.collection_name)
        logger.info(f"Successfully deleted empty collection {retrieval_service.collection_name}")

        retrieval_service.qdrant_client.create_collection(
            collection_name=retrieval_service.collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=embedding_size,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False),
                    modifier=models.Modifier.IDF if SPARSE_IDF else None,
                )
            },
            optimizers_config=models.OptimizersConfigDiff(default_segment_number=8),
            quantization_config=models.ScalarQuantization(
                                scalar=models.ScalarQuantizationConfig(
                                    type=models.ScalarType.INT8,
                                    quantile=0.95,
                                    always_ram=True,
                                ),
                            ),
        )

        retrieval_service.qdrant_client.create_payload_index(
            collection_name=retrieval_service.collection_name,
            field_name="metadata.virtualRecordId",
            field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD,
            ),
        )
        retrieval_service.qdrant_client.create_payload_index(
            collection_name=retrieval_service.collection_name,
            field_name="metadata.orgId",
            field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD,
            ),
        )
        logger.info(f"Successfully created new collection {retrieval_service.collection_name} with vector size {embedding_size}")
    except Exception as e:
        logger.error(f"Failed to recreate collection: {str(e)}", exc_info=True)
        raise

async def check_collection_info(
    retrieval_service,
    dense_embeddings,
    embedding_size,
    logger
) -> None:
    """Check and validate Qdrant collection information."""
    try:
        collection_info = retrieval_service.qdrant_client.get_collection(retrieval_service.collection_name)
        qdrant_vector_size = collection_info.config.params.vectors.get("dense").size
        points_count = collection_info.points_count

        current_model_name = await retrieval_service.get_current_embedding_model_name()
        new_model_name = retrieval_service.get_embedding_model_name(dense_embeddings)

        logger.info(f"Current model name: {current_model_name}")
        logger.info(f"New model name: {new_model_name}")
        logger.info(f"Collection points count: {points_count}")

        await handle_model_change(
            retrieval_service,
            current_model_name,
            new_model_name,
            qdrant_vector_size,
            points_count,
            embedding_size,
            logger
        )

    except grpc._channel._InactiveRpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            logger.info("Qdrant collection not found - acceptable for health check")
        else:
            logger.error(f"Unexpected gRPC error while checking Qdrant collection: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "not healthy",
                    "error": f"Unexpected gRPC error while checking Qdrant collection: {str(e)}",
                    "timestamp": get_epoch_timestamp_in_ms(),
                }
            )
    except HTTPException:
        # Re-raise HTTPException to be handled by the route handler
        raise
    except Exception as e:
        logger.error(f"Unexpected error checking Qdrant collection: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "not healthy",
                "error": f"Unexpected error checking Qdrant collection: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            }
        )

@router.post("/embedding-health-check")
async def embedding_health_check(request: Request, embedding_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate embedding configurations."""
    try:
        # Initialize components
        dense_embeddings, retrieval_service, logger = await initialize_embedding_model(request, embedding_configs)

        # Verify embedding health
        embedding_size = await verify_embedding_health(dense_embeddings, logger)

        # Check collection info and handle model changes
        await check_collection_info(retrieval_service, dense_embeddings, embedding_size, logger)

        # Initialize vector store as None
        retrieval_service.vector_store = None

        logger.info("Embedding health check completed successfully")

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": f"Embedding model is responding. Sample embedding size: {embedding_size}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    except HTTPException as he:
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(f"Embedding health check failed: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"Embedding model health check failed: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
