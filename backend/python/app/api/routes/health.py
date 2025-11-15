import asyncio
import os
from logging import Logger
from typing import Any, Dict, Tuple

import grpc  #type: ignore
from fastapi import APIRouter, Body, HTTPException, Request  #type: ignore
from fastapi.responses import JSONResponse  #type: ignore
from langchain_core.messages import HumanMessage  #type: ignore

from app.services.vector_db.const.const import ORG_ID_FIELD, VIRTUAL_RECORD_ID_FIELD
from app.utils.aimodels import (
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
)
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

SPARSE_IDF = False

def _load_test_image() -> str:
    """Loads the base64 encoded test image from a file."""
    # Path is relative to this file. Adjust if you place the asset elsewhere.
    file_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'test_image.b64')
    with open(file_path, 'r') as f:
        return f.read().strip()

# Then, you can define your constant like this:
TEST_IMAGE = _load_test_image()


@router.post("/llm-health-check")
async def llm_health_check(request: Request, llm_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate user-provided LLM configurations"""
    try:
        app = request.app
        llm, _ = await get_llm(app.container.config_service(), llm_configs)

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
            dense_embeddings = None
            for config in embedding_configs:
                if config.get("isDefault", False):
                    dense_embeddings = get_embedding_model(config["provider"], config)
                    break

            if not dense_embeddings:
                for config in embedding_configs:
                    dense_embeddings = get_embedding_model(config["provider"], config)
                    break

            if not dense_embeddings:
                raise HTTPException(status_code=500, detail="No default embedding model found")
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
    """Recreate the collection with new parameters."""
    try:
        await retrieval_service.vector_db_service.delete_collection(retrieval_service.collection_name)
        logger.info(f"Successfully deleted empty collection {retrieval_service.collection_name}")
        await retrieval_service.vector_db_service.create_collection(
            collection_name=retrieval_service.collection_name,
            embedding_size=embedding_size,
            sparse_idf=SPARSE_IDF,
        )

        await retrieval_service.vector_db_service.create_index(
            collection_name=retrieval_service.collection_name,
            field_name=VIRTUAL_RECORD_ID_FIELD,
            field_schema={
                "type": "keyword",
            }
        )
        await retrieval_service.vector_db_service.create_index(
            collection_name=retrieval_service.collection_name,
            field_name=ORG_ID_FIELD,
            field_schema={
                "type": "keyword",
            }
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
    """Check and validate collection information."""
    try:
        collection_info = await retrieval_service.vector_db_service.get_collection(retrieval_service.collection_name)
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
            logger.info("collection not found - acceptable for health check")
        else:
            logger.error(f"Unexpected gRPC error while checking vector db collection: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "not healthy",
                    "error": f"Unexpected gRPC error while checking vector db collection: {str(e)}",
                    "timestamp": get_epoch_timestamp_in_ms(),
                }
            )
    except HTTPException:
        # Re-raise HTTPException to be handled by the route handler
        raise
    except Exception as e:
        logger.error(f"Unexpected error checking vector db collection: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "not healthy",
                "error": f"Unexpected error checking vector db collection: {str(e)}",
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



async def perform_llm_health_check(
    llm_config: dict,
    logger: Logger,
) -> Dict[str, Any]:
    """Perform health check for LLM models"""
    try:
        logger.info(f"Performing LLM health check for {llm_config.get('provider')} with configuration {llm_config.get('configuration')}")
        # Use the first model from comma-separated list
        model_string = llm_config.get("configuration").get("model", "")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]

        if not model_names:
            logger.error(f"No valid model names found in configuration for {llm_config.get('provider')} with configuration {llm_config.get('configuration')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "No valid model names found in configuration",
                    "details": {
                    "provider": llm_config.get("provider"),
                    "model": llm_config.get("configuration").get("model")
                    },
                },
            )

        model_name = model_names[0]

        # Create LLM model
        llm_model = get_generator_model(
            provider=llm_config.get("provider"),
            config=llm_config,
            model_name=model_name
        )

        # Check if multimodal is enabled
        is_multimodal = llm_config.get("isMultimodal", False) or llm_config.get("configuration", {}).get("isMultimodal", False)

        # Set timeout for the test
        try:
            if is_multimodal:
                # Test with multimodal input (text + small image)
                logger.info("Multimodal model detected - testing with text and small image")
                test_image_url = TEST_IMAGE

                # Create multimodal message content
                multimodal_content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": test_image_url
                        }
                    }
                ]

                test_message = HumanMessage(content=multimodal_content)
                test_response = await asyncio.wait_for(
                    asyncio.to_thread(llm_model.invoke, [test_message]),
                    timeout=120.0  # 120 second timeout
                )
            else:
                # Test with a simple text prompt
                test_prompt = "Hello, this is a health check test. Please respond with 'Health check successful' if you can read this message."
                test_response = await asyncio.wait_for(
                    asyncio.to_thread(llm_model.invoke, test_prompt),
                    timeout=120.0  # 120 second timeout
                )

            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "message": f"LLM model is responding. Sample response: {test_response}",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        except asyncio.TimeoutError:
            logger.error(f"LLM health check timed out for {llm_config.get('provider')} with configuration {llm_config.get('configuration')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "LLM health check timed out",
                    "details": {
                        "provider": llm_config.get("provider"),
                        "model": model_name,
                        "timeout_seconds": 120
                    },
                },
            )

    except Exception as e:
        logger.error(f"LLM health check failed for {llm_config.get('provider')} with configuration {llm_config.get('configuration')}: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"LLM health check failed: {str(e)}",
                "details": {
                    "provider": llm_config.get("provider"),
                    "model": llm_config.get("configuration").get("model"),
                    "error_type": type(e).__name__
                }
            },
        )
    except HTTPException as he:
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(f"LLM health check failed for {llm_config.get('provider')} with configuration {llm_config.get('configuration')}: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"LLM health check failed: {str(e)}",
                "details": {
                    "provider": llm_config.get("provider"),
                    "model": llm_config.get("configuration").get("model"),
                    "error_type": type(e).__name__
                },
            },
        )

async def perform_embedding_health_check(
    request: Request,
    embedding_config: dict,
    logger: Logger,
) -> Dict[str, Any]:
    """Perform health check for embedding models"""
    try:
        logger.info(f"Performing embedding health check for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')}")
        # Use the first model from comma-separated list
        model_string = embedding_config.get("configuration").get("model", "")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]

        if not model_names:
            logger.error(f"No valid model names found in configuration for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "No valid model names found in configuration",
                    "details": {
                    "provider": embedding_config.get("provider"),
                    "model": embedding_config.get("configuration").get("model")
                    },
                },
            )

        model_name = model_names[0]

        # Create embedding model
        embedding_model = get_embedding_model(
            provider=embedding_config.get("provider"),
            config=embedding_config,
            model_name=model_name,
        )

        # Test with sample texts
        test_texts = [
            "This is a health check test.",
        ]

        # Set timeout for the test
        try:
            test_embeddings = await asyncio.wait_for(
                asyncio.to_thread(embedding_model.embed_documents, test_texts),
                timeout=120.0  # 120 second timeout
            )

            logger.info(f"Test embeddings length: {len(test_embeddings)}")
            if not test_embeddings or len(test_embeddings) == 0:
                logger.error(f"Embedding model returned empty results for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": "Embedding model returned empty results",
                        "details": {
                        "provider": embedding_config.get("provider"),
                        "model": model_name
                        },
                    },
                )

            # Validate embedding dimensions
            embedding_dimension = len(test_embeddings[0]) if test_embeddings else 0
            all(len(emb) == embedding_dimension for emb in test_embeddings)

            # Additional policy: If existing collection has points and vector size differs, reject
            try:
                retrieval_service = await request.app.container.retrieval_service()
                collection_info = await retrieval_service.vector_db_service.get_collection(retrieval_service.collection_name)

                if collection_info:
                    dense_vector = collection_info.config.params.vectors.get("dense")
                    qdrant_vector_size = getattr(dense_vector, "size", None) if dense_vector else None

                    if qdrant_vector_size is None:
                        raise Exception("Qdrant vector size not found")

                    points_count = getattr(collection_info, "points_count", 0)

                    if points_count>0 and qdrant_vector_size != embedding_dimension:
                        return JSONResponse(
                            status_code=400,
                            content={
                                "status": "error",
                                "message": "Embedding model dimension mismatch with existing non-empty collection",
                                "details": {
                                    "existing_vector_size": qdrant_vector_size,
                                    "new_embedding_size": embedding_dimension,
                                    "points_count": points_count,
                                },
                                "timestamp": get_epoch_timestamp_in_ms(),
                            },
                        )
            except grpc._channel._InactiveRpcError as e:
                if e.code() == grpc.StatusCode.NOT_FOUND:
                    logger.info("Collection not found - acceptable for health check")
                else:
                    raise
            except Exception as e:
                logger.error(f"Collection lookup failed: {str(e)}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": "Something went wrong! Please try again.",
                    },
                )

            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "message": f"Embedding model is responding. Sample embedding size: {embedding_dimension}",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        except asyncio.TimeoutError:
            logger.error(f"Embedding health check timed out for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Embedding health check timed out",
                    "details": {
                        "provider": embedding_config.get("provider"),
                    "model": model_name,
                    "timeout_seconds": 120
                },
            },
        )

    except Exception as e:
        logger.error(f"Embedding health check failed for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')   }: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
            "status": "error",
            "message": f"Embedding health check failed: {str(e)}",
            "details": {
                "provider": embedding_config.get("provider"),
                "model": embedding_config.get("configuration").get("model"),
                "error_type": type(e).__name__
            },
            },
        )

    except HTTPException as he:
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(f"Embedding health check failed for {embedding_config.get('provider')} with configuration {embedding_config.get('configuration')}: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Embedding health check failed: {str(e)}",
                "details": {
                    "provider": embedding_config.get("provider"),
                    "model": embedding_config.get("configuration").get("model"),
                    "error_type": type(e).__name__
                },
            },
        )


@router.post("/health-check/{model_type}")
async def health_check(request: Request, model_type: str, model_config: dict = Body(...)) -> JSONResponse:
    """Health check endpoint to validate the health of the application."""

    try:
        logger = request.app.container.logger()
        logger.info(f"Health check endpoint called for {model_type}")
        logger.info(f"Request body: {model_config}")

        if model_type == "embedding":
            logger.info(f"Performing embedding health check for {model_config.get('provider')} with configuration {model_config.get('configuration')}")
            return await perform_embedding_health_check(request, model_config, logger)

        elif model_type == "llm":
            logger.info(f"Performing LLM health check for {model_config.get('provider')} with configuration {model_config.get('configuration')}")
            return await perform_llm_health_check(model_config, logger)

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"Health check failed: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    return JSONResponse(
        status_code=200,
        content={"status": "healthy", "message": "Application is responding"}
    )

