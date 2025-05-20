import grpc
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()


@router.post("/llm-health-check")
async def llm_health_check(request: Request, llm_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate user-provided LLM configurations"""
    try:
        app = request.app
        llm = await get_llm(app.container.logger(), app.container.config_service(), llm_configs)
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

@router.post("/embedding-health-check")
async def embedding_health_check(request: Request, embedding_configs: list[dict] = Body(...)) -> JSONResponse:
    try:
        app = request.app
        logger = app.container.logger()
        logger.info("Starting embedding health check", extra={"embedding_configs": embedding_configs})

        retrieval_service = await app.container.retrieval_service()
        logger.info("Retrieved retrieval service")

        dense_embeddings = await retrieval_service.get_embedding_model_instance(embedding_configs)
        if dense_embeddings is None:
            logger.error("Failed to initialize embedding model")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Failed to initialize embedding model",
                    "details": {
                        "embedding_model": "initialization_failed",
                        "vector_store": "unknown",  # Can't check vector store without embeddings
                        "llm": "unknown"  # Continue with other health checks if needed
                    }
                }
            )

        new_model_name = retrieval_service.get_embedding_model_name(dense_embeddings)
        sample_embedding = await dense_embeddings.aembed_query("Test message to verify embedding model health.")

        if not sample_embedding or len(sample_embedding) == 0:
            logger.error("Embedding model returned empty embedding")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "not healthy",
                    "error": "Embedding model returned empty embedding",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        # Check Qdrant collection vector size
        try:
            collection_info = retrieval_service.qdrant_client.get_collection(retrieval_service.collection_name)
            qdrant_vector_size = collection_info.config.params.vectors.get("dense").size

            current_model_name = await retrieval_service.get_current_embedding_model_name()
            logger.info(f"Current model name: {current_model_name}")
            logger.info(f"New model name: {new_model_name}")
            if (current_model_name is not None and
                new_model_name is not None and
                current_model_name.lower() != new_model_name.lower()):
                logger.warning(
                    "Detected embedding model change attempt",
                    extra={
                        "current_model": current_model_name,
                        "new_model": new_model_name,
                        "vector_size": qdrant_vector_size
                    }
                )
                if qdrant_vector_size != 0:
                    logger.error("Rejected embedding model change due to existing collection")
                    return JSONResponse(
                        status_code=500,
                        content={
                            "status": "not healthy",
                            "error": f"Policy Rejection: Embedding model configuration cannot be changed while a vector store collection (dimension: {qdrant_vector_size}) already exists. Please ensure you are using the original embedding configuration.",
                            "timestamp": get_epoch_timestamp_in_ms(),
                        },
                    )
        except grpc._channel._InactiveRpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                logger.info("Qdrant collection not found - acceptable for health check")
                pass
            else:
                logger.error(f"Unexpected gRPC error while checking Qdrant collection: {str(e)}", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Unexpected error checking Qdrant collection: {str(e)}", exc_info=True)
            raise

        logger.info("Embedding health check completed successfully")

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": f"Embedding model is responding. Sample embedding size: {len(sample_embedding)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

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
