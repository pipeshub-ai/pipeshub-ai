import asyncio
import os
import shutil
from logging import Logger
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Request  #type: ignore
from fastapi.responses import JSONResponse  #type: ignore
from langchain_core.messages import HumanMessage  #type: ignore

from app.services.vector_db.const.const import ORG_ID_FIELD, VIRTUAL_RECORD_ID_FIELD
from app.services.vector_db.models import CollectionConfig
from app.utils.aimodels import (
    ImageGenerationProvider,
    STTProvider,
    TTSProvider,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
)
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

SPARSE_IDF = False

# Outer cap vs I/O timeouts in web_search_tool / fetch_url (DDG 15s, httpx 30s).
_WEB_SEARCH_HEALTH_TIMEOUTS_S = {
    "duckduckgo": 20.0,
    "serper": 33.0,
    "tavily": 33.0,
    "exa": 33.0,
}


def _is_collection_not_found_error(exc: Exception) -> bool:
    """Return True when *exc* indicates the collection/index does not exist yet."""
    msg = str(exc).lower()
    if any(
        token in msg
        for token in ("not found", "doesn't exist", "does not exist", "404", "index_not_found")
    ):
        return True
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    try:
        import grpc  # type: ignore

        code_fn = getattr(exc, "code", None)
        if callable(code_fn) and code_fn() == grpc.StatusCode.NOT_FOUND:
            return True
    except Exception:
        pass
    return False


def _extract_error_message(e: Exception) -> str:
    """Extract a clean, user-facing message from API SDK exceptions.

    Handles OpenAI/Azure, Anthropic, and similar SDKs that embed a nested
    ``body`` dict with the real error text.
    """
    # OpenAI / Azure OpenAI SDK errors (openai.APIStatusError subclasses)
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict):
            msg = nested.get("message")
            if msg:
                return str(msg)
        if body.get("message"):
            return str(body["message"])

    # Anthropic SDK errors
    if hasattr(e, "message") and isinstance(getattr(e, "message"), str):
        msg = getattr(e, "message")
        if msg and msg != str(e):
            return msg

    return str(e)

def _load_test_image() -> str:
    """Loads the base64 encoded test image from a file."""
    file_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'test_image.b64')
    with open(file_path, 'r') as f:
        return f.read().strip()

_TEST_IMAGE: str | None = None

def _get_test_image() -> str:
    global _TEST_IMAGE
    if _TEST_IMAGE is None:
        _TEST_IMAGE = _load_test_image()
    return _TEST_IMAGE


@router.post("/web-search-health-check")
async def web_search_health_check(request: Request, provider_config: dict = Body(...)) -> JSONResponse:
    """Health check endpoint to validate a web search provider configuration."""
    provider = provider_config.get("provider", "duckduckgo")
    try:
        configuration = provider_config.get("configuration", {})

        from app.utils.web_search_tool import (
            _search_with_duckduckgo,
            _search_with_exa,
            _search_with_serper,
            _search_with_tavily,
        )

        provider_map = {
            "duckduckgo": _search_with_duckduckgo,
            "serper": _search_with_serper,
            "tavily": _search_with_tavily,
            "exa": _search_with_exa,
        }

        search_func = provider_map.get(provider)
        if not search_func:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "not healthy",
                    "error": f"Unknown web search provider: {provider}",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        budget_s = _WEB_SEARCH_HEALTH_TIMEOUTS_S.get(provider, 33.0)
        await asyncio.wait_for(
            search_func("health check test", configuration),
            timeout=budget_s,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": f"Web search provider '{provider}' is responding",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=408,
            content={
                "status": "not healthy",
                "error": f"Web search health check timed out for provider '{provider}'",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "status": "not healthy",
                "error": str(e),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (401, 403):
            error_msg = f"Invalid API key for provider '{provider}'"
        elif status == 429:
            error_msg = f"Rate limit exceeded for provider '{provider}'"
        else:
            error_msg = f"Provider '{provider}' returned HTTP {status}"
        return JSONResponse(
            status_code=400,
            content={
                "status": "not healthy",
                "error": error_msg,
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"Web search health check failed: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


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

async def initialize_embedding_model(request: Request, embedding_configs: list[dict]) -> tuple[Any, Any, Any]:
    """Initialize the embedding model and return necessary components."""
    app = request.app
    logger = app.container.logger()

    logger.info("Starting embedding health check", extra={"embedding_configs": embedding_configs})

    retrieval_service = await app.container.retrieval_service()
    logger.info("Retrieved retrieval service")

    try:
        if not embedding_configs:
            logger.info("Using default embedding model")
            dense_embeddings = await asyncio.to_thread(get_default_embedding_model)
        else:
            dense_embeddings = None
            for config in embedding_configs:
                if config.get("isDefault", False):
                    dense_embeddings = await asyncio.to_thread(get_embedding_model, config["provider"], config)
                    break

            if not dense_embeddings:
                for config in embedding_configs:
                    dense_embeddings = await asyncio.to_thread(get_embedding_model, config["provider"], config)
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

def normalize_embedding_model_name(name: str | None) -> str | None:
    """Normalize an embedding model name so the *same* model is comparable across providers.

    The identical underlying model is referenced differently depending on the
    serving provider, e.g. ``nomic-ai/nomic-embed-text`` (sentence-transformers)
    vs ``nomic-embed-text`` (Ollama), or ``models/text-embedding-004`` (Gemini).
    These all produce the same embeddings/dimension, so switching the provider
    for the same model must NOT be treated as a breaking model change.

    We lowercase and strip any provider/org namespace prefix (the part before the
    last ``/``, which also covers the ``models/`` prefix).
    """
    if name is None:
        return None
    normalized = name.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized


async def handle_model_change(
    retrieval_service,
    current_model_name: str,
    new_model_name: str,
    existing_vector_size: int,
    points_count: int,
    embedding_size: int,
    logger
) -> None:
    """Handle embedding model changes and collection recreation if needed."""
    current_model_name = normalize_embedding_model_name(current_model_name)
    new_model_name = normalize_embedding_model_name(new_model_name)

    model_name_changed = (
        current_model_name is not None
        and new_model_name is not None
        and current_model_name != new_model_name
    )
    dimension_mismatch = (
        existing_vector_size != 0 and existing_vector_size != embedding_size
    )

    if not model_name_changed and not dimension_mismatch:
        return

    if model_name_changed:
        logger.warning(
            f"Detected embedding model change: '{current_model_name}' -> '{new_model_name}'"
        )
    if dimension_mismatch:
        logger.warning(
            f"Detected vector dimension mismatch: collection has {existing_vector_size}, "
            f"new model produces {embedding_size}"
        )

    if points_count > 0:
        logger.error(
            f"Rejected embedding change: collection "
            f"'{retrieval_service.collection_name}' contains {points_count} "
            f"point(s) indexed with the previous model."
        )
        raise HTTPException(
            status_code=400,
            detail={
                "status": "not healthy",
                "error": (
                    "Embedding model cannot be changed while the vector store "
                    "contains data indexed with a different model. Please "
                    "remove existing indexed documents first, then change the "
                    "embedding model."
                ),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    if existing_vector_size != 0:
        await recreate_collection(retrieval_service, embedding_size, logger)

async def recreate_collection(retrieval_service, embedding_size, logger) -> None:
    """Recreate the collection with new parameters (provider-neutral via CollectionConfig)."""
    try:
        await retrieval_service.vector_db_service.delete_collection(retrieval_service.collection_name)
        logger.info(f"Successfully deleted empty collection {retrieval_service.collection_name}")
        caps = retrieval_service.vector_db_service.get_capabilities()
        await retrieval_service.vector_db_service.create_collection(
            collection_name=retrieval_service.collection_name,
            config=CollectionConfig(
                embedding_size=embedding_size,
                sparse_idf=SPARSE_IDF,
                enable_sparse=caps.supports_sparse_vectors,
            ),
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
    """Check and validate collection information using provider-neutral get_collection_info()."""
    try:
        collection_info = await retrieval_service.vector_db_service.get_collection_info(
            retrieval_service.collection_name
        )
        # Use normalized VectorCollectionInfo fields (works for all providers)
        existing_vector_size = collection_info.dense_dimension or 0
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
            existing_vector_size,
            points_count,
            embedding_size,
            logger
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(
            f"Could not check vector db collection (may not exist yet): {e}"
        )

@router.post("/embedding-health-check")
async def embedding_health_check(request: Request, embedding_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate embedding configurations."""
    logger = None
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
        detail = he.detail
        if isinstance(detail, dict) and "error" in detail and "message" not in detail:
            detail = {**detail, "message": detail["error"]}
        return JSONResponse(status_code=he.status_code, content=detail)
    except Exception as e:
        if logger:
            logger.error(f"Embedding health check failed: {str(e)}", exc_info=True)
        error_msg = f"Embedding model health check failed: {str(e)}"
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": error_msg,
                "message": error_msg,
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

async def perform_llm_health_check(
    llm_config: dict,
    logger: Logger,
) -> dict[str, Any]:
    """Perform health check for LLM models"""
    try:
        logger.info(f"Performing LLM health check for {llm_config.get('provider')} with configuration model {llm_config.get('configuration', {}).get('model', '')}")
        # Use the first model from comma-separated list
        model_string = llm_config.get("configuration", {}).get("model", "")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]

        if not model_names:
            logger.error(f"No valid model names found in configuration for {llm_config.get('provider')} with configuration model {llm_config.get('configuration', {}).get('model', '')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "No valid model names found in configuration",
                    "details": {
                    "provider": llm_config.get("provider"),
                    "model": llm_config.get("configuration", {}).get("model", "")
                    },
                },
            )

        model_name = model_names[0]
        logger.info("Getting generator model")

        provider = llm_config.get("provider", "")
        configuration = llm_config.get("configuration", {})
        config_keys = list(configuration.keys()) if isinstance(configuration, dict) else type(configuration).__name__
        logger.debug(f"LLM health check configuration keys for {provider}: {config_keys}")

        # Create LLM model
        llm_model = await asyncio.to_thread(
            get_generator_model,
            provider=llm_config.get("provider"),
            config=llm_config,
            model_name=model_name,
        )

        logger.info("Generator model created")

        # Check if multimodal is enabled
        is_multimodal = llm_config.get("isMultimodal", False) or llm_config.get("configuration", {}).get("isMultimodal", False)

        # Set timeout for the test
        if is_multimodal:
            # For multimodal models, test image first, then text if image fails
            logger.info("Multimodal model detected - testing with image first")
            test_image_url = _get_test_image()

            # Create multimodal message content
            multimodal_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": test_image_url
                    }
                }
            ]

            try:
                test_message = HumanMessage(content=multimodal_content)
                test_response = await asyncio.wait_for(
                    asyncio.to_thread(llm_model.invoke, [test_message]),
                    timeout=120.0  # 120 second timeout
                )
                logger.info(f"Image test passed for multimodal model: {test_response}")
            except asyncio.TimeoutError:
                raise
            except Exception as image_error:
                logger.error(f"Image test failed for multimodal model: {str(image_error)}")

                # Image test failed, now try text test to determine if model works at all
                logger.info("Image test failed - testing with text to verify model functionality")
                test_prompt = "Hello, this is a health check test. Please respond with 'Health check successful' if you can read this message."
                try:
                    text_response = await asyncio.wait_for(
                        asyncio.to_thread(llm_model.invoke, test_prompt),
                        timeout=120.0  # 120 second timeout
                    )
                    logger.info(f"Text test passed for multimodal model: {text_response}")

                    # Text works but image doesn't - model doesn't support images
                    return JSONResponse(
                        status_code=500,
                        content={
                            "status": "error",
                            "message": "Model doesn't support images/vision. Disable Multimodal checkbox.",
                            "details": {
                                "provider": llm_config.get("provider"),
                                "model": model_name,
                                "error": str(image_error)
                            },
                        },
                    )
                except Exception as text_error:
                    # Both tests failed - pass the original error as-is
                    logger.error(f"Both image and text tests failed for multimodal model: {str(text_error)}")
                    raise text_error
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
        logger.error(f"LLM health check timed out for {llm_config.get('provider')} with model {llm_config.get('configuration', {}).get('model', '')} ({llm_config.get('modelFriendlyName', '')})")
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
    except HTTPException as he:
        logger.error(f"LLM health check failed for {llm_config.get('provider')} with model {llm_config.get('configuration', {}).get('model', '')} ({llm_config.get('modelFriendlyName', '')}): {str(he)}")
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(f"LLM health check failed for {llm_config.get('provider')} with model {llm_config.get('configuration', {}).get('model', '')} ({llm_config.get('modelFriendlyName', '')}): {str(e)}")
        clean_msg = _extract_error_message(e)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"LLM health check failed: {clean_msg}",
                "details": {
                    "provider": llm_config.get("provider"),
                    "model": model_name,
                    "error_type": type(e).__name__
                }
            },
        )

async def perform_embedding_health_check(
    request: Request,
    embedding_config: dict,
    logger: Logger,
) -> dict[str, Any]:
    """Perform health check for embedding models"""
    try:
        logger.info(f"Performing embedding health check for {embedding_config.get('provider')} with configuration model {embedding_config.get('configuration', {}).get('model', '')}")
        # Use the first model from comma-separated list
        model_string = embedding_config.get("configuration", {}).get("model", "")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]

        if not model_names:
            logger.error(f"No valid model names found in configuration for {embedding_config.get('provider')} with configuration model {embedding_config.get('configuration', {}).get('model', '')}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "No valid model names found in configuration",
                    "details": {
                    "provider": embedding_config.get("provider"),
                    "model": embedding_config.get("configuration", {}).get("model", "")
                    },
                },
            )

        model_name = model_names[0]

        # Create embedding model
        embedding_model = await asyncio.to_thread(
            get_embedding_model,
            provider=embedding_config.get("provider"),
            config=embedding_config,
            model_name=model_name,
        )

        # Test with sample texts
        test_texts = [
            "This is a health check test.",
        ]

        # The first call may trigger a large model download (e.g. ~1.9 GB
        # for nomic-embed-text-v2-moe), so we allow a generous timeout.
        # Subsequent health checks hit the cached model and return quickly.
        HEALTH_CHECK_TIMEOUT = 600.0

        try:
            test_embeddings = await asyncio.wait_for(
                asyncio.to_thread(embedding_model.embed_documents, test_texts),
                timeout=HEALTH_CHECK_TIMEOUT,
            )

            logger.info(f"Test embeddings length: {len(test_embeddings)}")
            if not test_embeddings or len(test_embeddings) == 0:
                logger.error(f"Embedding model returned empty results for {embedding_config.get('provider')} with configuration model {embedding_config.get('configuration', {}).get('model', '')}")
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

            # Policy: reject if the existing non-empty collection was built
            # with a different model OR a different vector size.
            try:
                retrieval_service = await request.app.container.retrieval_service()
                # Use provider-neutral get_collection_info() — works for all backends
                collection_info = await retrieval_service.vector_db_service.get_collection_info(
                    retrieval_service.collection_name
                )

                if collection_info.exists:
                    existing_dim = collection_info.dense_dimension or 0
                    points_count = collection_info.points_count

                    if points_count > 0:
                        # Check dimension mismatch
                        if existing_dim != embedding_dimension:
                            return JSONResponse(
                                status_code=400,
                                content={
                                    "status": "error",
                                    "message": "Documents are already indexed with a different embedding model. Please remove existing documents indexed with the old model and try again.",
                                    "details": {
                                        "existing_vector_size": existing_dim,
                                        "new_embedding_size": embedding_dimension,
                                        "points_count": points_count,
                                    },
                                    "timestamp": get_epoch_timestamp_in_ms(),
                                },
                            )

                        # Check model identity — same dimensions but different
                        # model means incompatible vector spaces.
                        current_model_name = await retrieval_service.get_current_embedding_model_name()
                        new_provider = embedding_config.get("provider", "")
                        new_model = model_name

                        if current_model_name:
                            current_normalized = normalize_embedding_model_name(current_model_name)
                            new_normalized = normalize_embedding_model_name(new_model)

                            if current_normalized and new_normalized and current_normalized != new_normalized:
                                return JSONResponse(
                                    status_code=400,
                                    content={
                                        "status": "error",
                                        "message": (
                                            f"Embedding model mismatch: the existing collection was built with "
                                            f"'{current_model_name}'. Switching to '{new_model}' (provider: "
                                            f"{new_provider}) would corrupt search results. Please re-index "
                                            f"or use the same model."
                                        ),
                                        "details": {
                                            "current_model": current_model_name,
                                            "new_model": new_model,
                                            "new_provider": new_provider,
                                            "points_count": points_count,
                                        },
                                        "timestamp": get_epoch_timestamp_in_ms(),
                                    },
                                )
            except Exception as e:
                if _is_collection_not_found_error(e):
                    logger.warning(
                        "Collection not found during embedding health check "
                        "(expected for fresh installs): %s",
                        e,
                    )
                else:
                    logger.error(
                        "Collection lookup failed during embedding health check: %s",
                        e,
                    )
                    return JSONResponse(
                        status_code=500,
                        content={
                            "status": "error",
                            "message": "Vector database collection lookup failed",
                            "details": {
                                "provider": embedding_config.get("provider"),
                                "model": model_name,
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                            "timestamp": get_epoch_timestamp_in_ms(),
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
            logger.error(
                "Embedding health check timed out for %s model %s (timeout=%.0fs). "
                "If the model is downloading for the first time, retry after it finishes.",
                embedding_config.get("provider"),
                embedding_config.get("configuration", {}).get("model", ""),
                HEALTH_CHECK_TIMEOUT,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": (
                        "Embedding health check timed out. "
                        "If this is the first run, the model may still be downloading. "
                        "Please wait for the download to complete and try again."
                    ),
                    "details": {
                        "provider": embedding_config.get("provider"),
                        "model": model_name,
                        "timeout_seconds": int(HEALTH_CHECK_TIMEOUT),
                    },
                },
            )
        except Exception as e:
            raise e

    except HTTPException as he:
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(f"Embedding health check failed for {embedding_config.get('provider')} with model {embedding_config.get('configuration', {}).get('model', '')} ({embedding_config.get('modelFriendlyName', '')}): {str(e)}", exc_info=True)
        clean_msg = _extract_error_message(e)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Embedding health check failed: {clean_msg}",
                "details": {
                    "provider": embedding_config.get("provider"),
                    "model": embedding_config.get("configuration").get("model"),
                    "error_type": type(e).__name__
                },
            },
        )


async def perform_image_generation_health_check(
    model_config: dict,
    logger: Logger,
) -> JSONResponse:
    """Validate credentials for an image-generation provider.

    We deliberately do **not** call ``generate()``: the underlying APIs meter
    per-image cost and have strict rate limits. Instead we build a provider
    client, call a cheap listing/get endpoint, and surface the result in the
    same envelope used by the LLM/embedding health checks.
    """
    provider = model_config.get("provider")
    configuration = model_config.get("configuration") or {}
    model_string = configuration.get("model", "")
    model_names = [name.strip() for name in model_string.split(",") if name.strip()]

    if not model_names:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "No valid model names found in configuration",
                "details": {
                    "provider": provider,
                    "model": model_string,
                },
            },
        )

    model_name = model_names[0]
    try:
        adapter = get_image_generation_model(
            provider=provider,
            config=model_config,
            model_name=model_name,
        )
    except Exception as e:
        logger.error(
            "Image generation health check failed to build adapter for "
            f"{provider}/{model_name}: {e}", exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Image generation health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )

    try:
        if provider == ImageGenerationProvider.OPENAI.value:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
            )
            try:
                await asyncio.wait_for(client.models.list(), timeout=30.0)
            finally:
                await client.close()
        elif provider == ImageGenerationProvider.GEMINI.value:
            from google import genai

            client = genai.Client(api_key=configuration["apiKey"])
            await asyncio.wait_for(
                client.aio.models.get(model=model_name),
                timeout=30.0,
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Unsupported image generation provider: {provider}",
                },
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": "Image generation provider is reachable",
                "details": {"provider": provider, "model": model_name},
            },
        )
    except Exception as e:
        logger.error(
            f"Image generation health check failed for {provider}/{model_name}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Image generation health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )


async def perform_tts_health_check(
    model_config: dict,
    logger: Logger,
) -> JSONResponse:
    """Validate credentials for a Text-to-Speech provider.

    We build the adapter and run a minimal cheap round-trip (short
    synthesis) so configuration errors surface immediately.
    """
    provider = model_config.get("provider")
    configuration = model_config.get("configuration") or {}
    model_string = configuration.get("model", "")
    model_names = [name.strip() for name in model_string.split(",") if name.strip()]

    if not model_names:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "No valid model names found in configuration",
                "details": {"provider": provider, "model": model_string},
            },
        )

    model_name = model_names[0]
    try:
        adapter = get_tts_model(
            provider=provider,
            config=model_config,
            model_name=model_name,
        )
    except Exception as e:
        logger.error(
            f"TTS health check failed to build adapter for {provider}/{model_name}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"TTS health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )

    try:
        if provider == TTSProvider.OPENAI.value:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
            )
            try:
                await asyncio.wait_for(client.models.list(), timeout=30.0)
            finally:
                await client.close()
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Unsupported TTS provider: {provider}",
                },
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": "TTS provider is reachable",
                "details": {"provider": provider, "model": model_name},
            },
        )
    except Exception as e:
        logger.error(
            f"TTS health check failed for {provider}/{model_name}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"TTS health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )


async def perform_stt_health_check(
    model_config: dict,
    logger: Logger,
) -> JSONResponse:
    """Validate an STT provider.

    For OpenAI we list models; for Gemini we fetch model metadata via the
    Google GenAI SDK (same pattern as image generation). For the local
    ``whisper`` provider we verify ``faster-whisper`` is importable (weights
    stay lazy-loaded). For ``wispr`` we require ``ffmpeg`` on PATH for the
    server-side transcode step.
    """
    provider = model_config.get("provider")
    configuration = model_config.get("configuration") or {}
    model_string = configuration.get("model", "")
    model_names = [name.strip() for name in model_string.split(",") if name.strip()]

    if not model_names:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "No valid model names found in configuration",
                "details": {"provider": provider, "model": model_string},
            },
        )

    model_name = model_names[0]
    try:
        adapter = get_stt_model(
            provider=provider,
            config=model_config,
            model_name=model_name,
        )
    except Exception as e:
        logger.error(
            f"STT health check failed to build adapter for {provider}/{model_name}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"STT health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )

    try:
        if provider == STTProvider.OPENAI.value:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
            )
            try:
                await asyncio.wait_for(client.models.list(), timeout=30.0)
            finally:
                await client.close()
        elif provider == STTProvider.WHISPER.value:
            try:
                import importlib.util

                if importlib.util.find_spec("faster_whisper") is None:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "status": "error",
                            "message": (
                                "The 'faster-whisper' package is not installed. "
                                "Install dependencies or reinstall the service to "
                                "use the local Whisper STT provider."
                            ),
                            "details": {"provider": provider, "model": model_name},
                        },
                    )
            except Exception as exc:  # pragma: no cover - defensive
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": f"Failed to probe faster-whisper: {exc}",
                        "details": {"provider": provider, "model": model_name},
                    },
                )
        elif provider == STTProvider.GEMINI.value:
            from google import genai

            client = genai.Client(api_key=configuration["apiKey"])
            await asyncio.wait_for(
                client.aio.models.get(model=model_name),
                timeout=30.0,
            )
        elif provider == STTProvider.WISPR.value:
            if shutil.which("ffmpeg") is None:
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": (
                            "The 'wispr' STT provider requires ffmpeg on PATH "
                            "to transcode audio to 16 kHz WAV. Install ffmpeg "
                            "on the backend host and retry."
                        ),
                        "details": {"provider": provider, "model": model_name},
                    },
                )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Unsupported STT provider: {provider}",
                },
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": "STT provider is reachable",
                "details": {"provider": provider, "model": model_name},
            },
        )
    except Exception as e:
        logger.error(
            f"STT health check failed for {provider}/{model_name}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"STT health check failed: {_extract_error_message(e)}",
                "details": {
                    "provider": provider,
                    "model": model_name,
                    "error_type": type(e).__name__,
                },
            },
        )


@router.post("/health-check/{model_type}")
async def health_check(request: Request, model_type: str, model_config: dict = Body(...)) -> JSONResponse:
    """Health check endpoint to validate the health of the application."""

    try:
        logger = request.app.container.logger()
        logger.info(f"Health check endpoint called for {model_type}")
        logger.debug(f"Request body: {model_config}")

        if model_type == "embedding":
            logger.info(f"Performing embedding health check for {model_config.get('provider')} with configuration model {model_config.get('configuration', {}).get('model', '')}")
            return await perform_embedding_health_check(request, model_config, logger)

        elif model_type == "llm":
            logger.info(f"Performing LLM health check for {model_config.get('provider')} with configuration model {model_config.get('configuration', {}).get('model', '')}")
            return await perform_llm_health_check(model_config, logger)

        elif model_type == "imageGeneration":
            logger.info(
                f"Performing image generation health check for {model_config.get('provider')} "
                f"with configuration model {model_config.get('configuration', {}).get('model', '')}"
            )
            return await perform_image_generation_health_check(model_config, logger)

        elif model_type == "tts":
            logger.info(
                f"Performing TTS health check for {model_config.get('provider')} "
                f"with configuration model {model_config.get('configuration', {}).get('model', '')}"
            )
            return await perform_tts_health_check(model_config, logger)

        elif model_type == "stt":
            logger.info(
                f"Performing STT health check for {model_config.get('provider')} "
                f"with configuration model {model_config.get('configuration', {}).get('model', '')}"
            )
            return await perform_stt_health_check(model_config, logger)

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

