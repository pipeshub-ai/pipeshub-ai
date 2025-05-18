
import grpc
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()


@router.post("/llm-health-check")
async def llm_health_check(request: Request, llm_configs: list[dict] = Body(...)):
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
async def embedding_health_check(request: Request, embedding_configs: list[dict] = Body(...)):
    try:
        app = request.app
        retrieval_service = await app.container.retrieval_service()
        await retrieval_service.get_embedding_model_instance(embedding_configs)
        sample_embedding = await retrieval_service.dense_embeddings.aembed_query("Test message to verify embedding model health.")
        if not sample_embedding or len(sample_embedding) == 0:
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
            if qdrant_vector_size != 0:
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "not healthy",
                        "error": f"Policy Rejection: Embedding model configuration cannot be changed or re-verified while a vector store collection (dimension: {qdrant_vector_size}) already exists. Please ensure you are using the original embedding configuration.",
                        "timestamp": get_epoch_timestamp_in_ms(),
                    },
                )
        except grpc._channel._InactiveRpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                # Collection doesn't exist; acceptable for health check
                print("Collection doesn't exist; acceptable for health check")
                pass
            else:
                # Re-raise unexpected gRPC errors
                raise
        except Exception as e:
            # Log or handle other unexpected exceptions
            print(f"Unexpected error checking Qdrant collection: {e}")
            raise


        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": f"Embedding model is responding. Sample embedding size: {len(sample_embedding)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"Embedding model health check failed: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
