import asyncio
import os
from logging import Logger
from typing import Any, Dict, Optional, Tuple

import grpc  # type: ignore

try:
    from grpc._channel import _InactiveRpcError as _GrpcInactiveRpcError
except ImportError:  # pragma: no cover
    _GrpcInactiveRpcError = grpc.RpcError  # type: ignore[misc,assignment]

from fastapi import APIRouter, Body, HTTPException, Request  #type: ignore
from fastapi.responses import JSONResponse  #type: ignore
from langchain_core.messages import HumanMessage  #type: ignore

from app.config.collection_spec import (
    default_collection_spec,
    delete_collection_spec,
    get_collection_spec,
    normalize_identity as _normalize_identity,
    set_collection_spec,
)
from app.services.vector_db.const.const import (
    ORG_ID_FIELD,
    VIRTUAL_RECORD_ID_FIELD,
)
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


async def _resolve_stored_embedding_identity(
    retrieval_service,
    logger: Logger,
) -> Tuple[Optional[dict], str]:
    """Resolve the embedding identity that was used to build the existing
    collection.

    Returns ``(identity, source)`` where:
      * ``identity`` is a dict with ``embedding_provider`` and
        ``embedding_model`` keys (normalized), or ``None`` if it can't be
        determined.
      * ``source`` is one of:
          - ``"collection_spec"`` — spec stored in the ConfigurationService
            under ``/services/vectorCollections/<name>`` (authoritative).
          - ``"default"``         — no spec stored; assume the collection
            was built with the built-in default embedding model. Matches
            how pre-migration collections are handled everywhere else.
          - ``"config"``          — legacy fallback: read the currently
            configured model from AI_MODELS. Only meaningful when neither
            the collection spec nor the default fallback is applicable.
          - ``"unknown"``         — nothing we can say about the identity.

    The collection spec is the authoritative source when present; the
    config-based fallback is kept only so legacy flows that never wrote
    a spec still line up with what the user has configured.
    """
    config_service = getattr(retrieval_service, "config_service", None)
    collection_name = getattr(retrieval_service, "collection_name", None)

    if config_service is not None and collection_name:
        try:
            stored = await get_collection_spec(
                config_service, collection_name, use_cache=False
            )
            # Only accept a proper dict payload. Guard against AsyncMock-
            # style test doubles returning a MagicMock for any attribute.
            if isinstance(stored, dict) and stored:
                identity = {
                    "embedding_provider": _normalize_identity(
                        stored.get("embedding_provider")
                    ),
                    "embedding_model": _normalize_identity(
                        stored.get("embedding_model")
                    ),
                    "embedding_dimension": stored.get("embedding_dimension"),
                }
                # ``is_multimodal`` is only present on specs written by
                # spec-aware code. Surface it so the same-dimension
                # identity check can catch a flip of the ``isMultimodal``
                # toggle on the same model.
                if "is_multimodal" in stored:
                    identity["is_multimodal"] = bool(stored.get("is_multimodal"))
                return identity, "collection_spec"
        except Exception as e:
            logger.warning(
                f"Stored collection spec lookup failed: {e}"
            )

    # No spec on record. Treat pre-migration collections as if they were
    # built with the default embedding model. This matches the fallback
    # used in the vectorstore/indexing paths and keeps health checks
    # consistent for legacy deployments.
    default_spec = default_collection_spec(None)
    if default_spec.get("embedding_model"):
        return (
            {
                "embedding_provider": default_spec.get("embedding_provider"),
                "embedding_model": default_spec.get("embedding_model"),
                "embedding_dimension": default_spec.get("embedding_dimension"),
            },
            "default",
        )

    # Legacy fallback retained for the edge case where even the default
    # model name is unavailable: read the default entry from AI_MODELS.
    current_model: Optional[str] = None
    current_provider: Optional[str] = None

    get_default_cfg = getattr(
        retrieval_service, "get_current_embedding_config", None
    )
    if callable(get_default_cfg):
        try:
            default_cfg = await get_default_cfg()
            if isinstance(default_cfg, dict):
                current_model = (
                    (default_cfg.get("configuration") or {}).get("model")
                )
                current_provider = default_cfg.get("provider")
        except Exception as e:
            logger.warning(f"get_current_embedding_config failed: {e}")

    if not current_model:
        try:
            current_model = await retrieval_service.get_current_embedding_model_name()
        except Exception as e:
            logger.warning(f"get_current_embedding_model_name failed: {e}")
            current_model = None

    if isinstance(current_model, str) and current_model:
        return (
            {
                "embedding_provider": _normalize_identity(current_provider)
                or None,
                "embedding_model": _normalize_identity(current_model),
                "embedding_dimension": None,
            },
            "config",
        )

    return None, "unknown"


async def _count_user_points(
    retrieval_service, raw_points_count, logger: Optional[Logger] = None
) -> int:
    """Return the user-point count for the retrieval collection.

    With the embedding-model spec now living in the ConfigurationService
    (see ``app.config.collection_spec``) there is no sentinel point to
    exclude, so the raw Qdrant ``points_count`` is the user count. Kept as
    a helper (rather than inlining) so the strict ``isinstance`` guard
    against ``MagicMock.__int__`` returning ``1`` stays centralized — tests
    rely on this to distinguish "no user data" from "one user point".
    """
    # ``retrieval_service`` and ``logger`` are intentionally accepted for
    # backwards compatibility with existing call sites and tests.
    del retrieval_service, logger

    if isinstance(raw_points_count, int) and not isinstance(raw_points_count, bool):
        return raw_points_count
    return 0


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
    current_model_name: Optional[str],
    new_model_name: Optional[str],
    qdrant_vector_size: int,
    points_count: int,
    embedding_size: int,
    logger,
    *,
    current_provider: Optional[str] = None,
    new_provider: Optional[str] = None,
    identity_source: str = "config",
) -> None:
    """Decide whether an embedding-model change is compatible with the
    existing collection, and act on it.

    Policy:
      * If both current and new identity are known and **differ** (either
        by provider or by normalized model name), and the collection has
        user data → reject: cross-model mixing silently corrupts search.
      * If identities differ but the collection has no user data → drop
        and recreate so the new model can index cleanly.
      * If identities match → nothing to do.
      * ``identity_source="collection_spec"`` means the "current" identity
        came from the ConfigurationService spec node and is authoritative;
        ``"default"`` means we assumed the built-in default model (pre-
        migration fallback); otherwise it came from config (best-effort,
        legacy fallback).

    Historical args ``current_model_name`` / ``new_model_name`` are kept
    for backward compatibility; when ``current_provider`` and
    ``new_provider`` are supplied they additionally participate in the
    identity comparison — which is the whole point of this refactor,
    since two different providers can ship same-named models that produce
    incompatible vector spaces.
    """
    current_normalized = _normalize_identity(current_model_name)
    new_normalized = _normalize_identity(new_model_name)
    current_provider_norm = _normalize_identity(current_provider)
    new_provider_norm = _normalize_identity(new_provider)

    # Compute a tri-state for identity comparison:
    #   True  -> known difference (block/recreate)
    #   False -> known match
    #   None  -> insufficient info to decide (treat as match = no action)
    identity_differs: Optional[bool]
    if not current_normalized or not new_normalized:
        identity_differs = None
    else:
        model_differs = current_normalized != new_normalized
        provider_differs = bool(
            current_provider_norm
            and new_provider_norm
            and current_provider_norm != new_provider_norm
        )
        identity_differs = model_differs or provider_differs

    if identity_differs is None:
        # Missing either the current or the new model name. Log explicitly
        # so an operator can tell this apart from a genuine "identities
        # match" no-op — the two look identical from the outside and the
        # first is almost always a caller bug (e.g. a legacy call site
        # forgot to plumb through the new model name).
        logger.info(
            "handle_model_change: insufficient info to compare identities "
            f"(source={identity_source}, "
            f"current={current_provider_norm or '?'}/{current_normalized or '?'}, "
            f"new={new_provider_norm or '?'}/{new_normalized or '?'}). "
            "Skipping identity check."
        )
        return

    if not identity_differs:
        return

    logger.warning(
        "Detected embedding model change attempt "
        f"(source={identity_source}, "
        f"current={current_provider_norm or '?'}/{current_normalized}, "
        f"new={new_provider_norm or '?'}/{new_normalized})"
    )

    if qdrant_vector_size != 0 and points_count > 0:
        logger.error(
            "Rejected embedding model change due to non-empty existing collection"
        )
        # 400 Bad Request: this is a client-side configuration conflict
        # (the proposed embedding model is incompatible with the data that
        # already lives in the collection), not an internal server fault.
        # Kept consistent with ``perform_embedding_health_check`` which
        # returns 400 for the same class of rejection.
        raise HTTPException(
            status_code=400,
            detail={
                "status": "not healthy",
                "error": (
                    "Policy Rejection: Embedding model configuration cannot be "
                    "changed while vector store collection contains data. Even "
                    "when vector dimensions match, different embedding models "
                    "produce incompatible vector spaces and mixing them will "
                    "silently corrupt search results. Please re-index or use "
                    "the original embedding configuration."
                ),
                "details": {
                    "existing_provider": current_provider_norm or None,
                    "existing_model": current_normalized or None,
                    "new_provider": new_provider_norm or None,
                    "new_model": new_normalized or None,
                    "points_count": points_count,
                    "identity_source": identity_source,
                },
                "timestamp": get_epoch_timestamp_in_ms(),
            }
        )

    if qdrant_vector_size != 0 and points_count == 0:
        await recreate_collection(
            retrieval_service,
            embedding_size,
            logger,
            new_provider=new_provider,
            new_model_name=new_model_name,
        )

async def recreate_collection(
    retrieval_service,
    embedding_size: int,
    logger,
    *,
    new_provider: Optional[str] = None,
    new_model_name: Optional[str] = None,
    new_is_multimodal: Optional[bool] = None,
) -> None:
    """Recreate the collection with new parameters. If provider and model
    are supplied, also write a fresh collection spec to the Configuration
    Service so future health checks can authoritatively tell which model
    built this collection."""
    try:
        # Drop the old spec node first so a transient failure after this
        # point can never leave a stale spec pointing at a dropped
        # collection. Best-effort; recreated below on success.
        config_service = getattr(retrieval_service, "config_service", None)
        if config_service is not None:
            try:
                await delete_collection_spec(
                    config_service, retrieval_service.collection_name
                )
            except Exception as del_err:
                logger.warning(
                    f"Failed to clear collection spec before recreate: {del_err}"
                )

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

        if new_provider and new_model_name and config_service is not None:
            try:
                await set_collection_spec(
                    config_service,
                    retrieval_service.collection_name,
                    provider=new_provider,
                    model=new_model_name,
                    dimension=int(embedding_size),
                    is_multimodal=new_is_multimodal,
                )
            except Exception as sig_err:
                # Don't fail the recreate just because the spec write
                # failed; log loudly so it's discoverable.
                logger.error(
                    f"Failed to write collection spec after recreate: {sig_err}",
                    exc_info=True,
                )

        logger.info(f"Successfully created new collection {retrieval_service.collection_name} with vector size {embedding_size}")
    except Exception as e:
        logger.error(f"Failed to recreate collection: {str(e)}", exc_info=True)
        raise

async def check_collection_info(
    retrieval_service,
    dense_embeddings,
    embedding_size,
    logger,
    *,
    new_provider: Optional[str] = None,
    new_model_name: Optional[str] = None,
) -> None:
    """Check and validate collection information.

    Resolution order for "which model built the existing collection":
      1. Signature stored on the collection (authoritative).
      2. AI_MODELS config (legacy fallback).
      3. Unknown (treated as legacy; no identity check performed).

    ``new_provider`` / ``new_model_name`` come from the incoming request
    config. If omitted (legacy callers / tests), we fall back to
    ``retrieval_service.get_embedding_model_name(dense_embeddings)``, which
    cannot supply a provider and so behaves exactly like before.
    """
    try:
        collection_info = await retrieval_service.vector_db_service.get_collection(retrieval_service.collection_name)
        qdrant_vector_size = collection_info.config.params.vectors.get("dense").size
        raw_points_count = collection_info.points_count
        points_count = await _count_user_points(
            retrieval_service, raw_points_count, logger=logger
        )

        if not new_model_name:
            new_model_name = retrieval_service.get_embedding_model_name(dense_embeddings)

        current_identity, identity_source = await _resolve_stored_embedding_identity(
            retrieval_service, logger
        )
        current_model_name = (
            current_identity.get("embedding_model") if current_identity else None
        )
        current_provider = (
            current_identity.get("embedding_provider") if current_identity else None
        )

        logger.info(
            f"Embedding identity: source={identity_source}, "
            f"current_provider={current_provider}, current_model={current_model_name}, "
            f"new_provider={new_provider}, new_model={new_model_name}"
        )
        logger.info(
            f"Collection points: raw={raw_points_count}, user={points_count}"
        )

        # Spec-source path: we KNOW what built the collection from the
        # ConfigurationService record. No need to defer to the legacy
        # "current model from config" heuristic.
        if identity_source == "collection_spec":
            await handle_model_change(
                retrieval_service,
                current_model_name,
                new_model_name,
                qdrant_vector_size,
                points_count,
                embedding_size,
                logger,
                current_provider=current_provider,
                new_provider=new_provider,
                identity_source="collection_spec",
            )
            return

        # Legacy / default-fallback path: "current" came from the default
        # embedding model or from AI_MODELS. Keep the historical behaviour
        # so pre-migration collections keep working.
        await handle_model_change(
            retrieval_service,
            current_model_name,
            new_model_name,
            qdrant_vector_size,
            points_count,
            embedding_size,
            logger,
            current_provider=current_provider,
            new_provider=new_provider,
            identity_source=identity_source,
        )

    except _GrpcInactiveRpcError as e:
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

def _pick_new_embedding_identity(embedding_configs: list[dict]) -> Tuple[Optional[str], Optional[str]]:
    """Choose the (provider, model_name) the caller is asking us to validate.

    Mirrors ``initialize_embedding_model``'s "default first, else first"
    selection so the identity we compare against matches the embedding we
    actually built.
    """
    if not embedding_configs:
        return None, None
    chosen = next(
        (c for c in embedding_configs if c.get("isDefault", False)),
        embedding_configs[0],
    )
    provider = chosen.get("provider")
    model_str = (chosen.get("configuration") or {}).get("model", "")
    # AI_MODELS allows a comma-separated list for some providers; take the
    # first non-empty entry, matching the runtime behaviour elsewhere.
    model_name = next(
        (n.strip() for n in str(model_str).split(",") if n.strip()),
        None,
    )
    return provider, model_name


@router.post("/embedding-health-check")
async def embedding_health_check(request: Request, embedding_configs: list[dict] = Body(...)) -> JSONResponse:
    """Health check endpoint to validate embedding configurations."""
    try:
        # Initialize components
        dense_embeddings, retrieval_service, logger = await initialize_embedding_model(request, embedding_configs)

        # Verify embedding health
        embedding_size = await verify_embedding_health(dense_embeddings, logger)

        # Pull out provider+model from the incoming config so the identity
        # check compares against (provider, model), not just model name.
        new_provider, new_model_name = _pick_new_embedding_identity(embedding_configs)

        # Check collection info and handle model changes
        await check_collection_info(
            retrieval_service,
            dense_embeddings,
            embedding_size,
            logger,
            new_provider=new_provider,
            new_model_name=new_model_name,
        )

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
        # Create LLM model
        llm_model = get_generator_model(
            provider=llm_config.get("provider"),
            config=llm_config,
            model_name=model_name
        )

        logger.info("Generator model created")

        # Check if multimodal is enabled
        is_multimodal = llm_config.get("isMultimodal", False) or llm_config.get("configuration", {}).get("isMultimodal", False)

        # Set timeout for the test
        if is_multimodal:
            # For multimodal models, test image first, then text if image fails
            logger.info("Multimodal model detected - testing with image first")
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
) -> Dict[str, Any]:
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
            #
            # Identity resolution order (authoritative -> heuristic):
            #   1. Signature stored on the Qdrant collection itself.
            #   2. AI_MODELS config (legacy; correlated with what the user is
            #      trying to change, so not reliable on its own).
            # When neither is available and the collection has user data,
            # we conservatively refuse because a silent pass here can
            # corrupt an existing index on a same-dimension model swap.
            try:
                retrieval_service = await request.app.container.retrieval_service()
                collection_info = await retrieval_service.vector_db_service.get_collection(retrieval_service.collection_name)

                if collection_info:
                    dense_vector = collection_info.config.params.vectors.get("dense")
                    qdrant_vector_size = getattr(dense_vector, "size", None) if dense_vector else None

                    if qdrant_vector_size is None:
                        raise Exception("Qdrant vector size not found")

                    raw_points_count = getattr(collection_info, "points_count", 0)
                    points_count = await _count_user_points(
                        retrieval_service, raw_points_count, logger=logger
                    )

                    if points_count > 0:
                        # Resolve the stored embedding identity up-front so
                        # *both* the dimension-mismatch branch and the
                        # same-dimension branch can report which model built
                        # the existing collection. Without this, a dimension
                        # mismatch error can only say "a different model",
                        # which isn't actionable.
                        new_provider = embedding_config.get("provider", "")
                        new_model = model_name
                        new_provider_norm = _normalize_identity(new_provider)
                        new_model_norm = _normalize_identity(new_model)
                        # Read isMultimodal from the request config so we
                        # can compare against a stored ``is_multimodal``
                        # signature. When the saved config is text-only
                        # this reliably returns False.
                        new_is_multimodal = bool(
                            embedding_config.get("isMultimodal")
                            or (embedding_config.get("configuration") or {}).get(
                                "isMultimodal"
                            )
                        )

                        current_identity, identity_source = (
                            await _resolve_stored_embedding_identity(
                                retrieval_service, logger
                            )
                        )
                        cur_provider = (
                            (current_identity or {}).get("embedding_provider") or ""
                        )
                        cur_model = (
                            (current_identity or {}).get("embedding_model") or ""
                        )
                        # Only populated when the collection was written
                        # by signature-aware code that knew the flag.
                        cur_is_multimodal = (
                            (current_identity or {}).get("is_multimodal")
                            if current_identity
                            else None
                        )

                        # Check dimension mismatch first. Dimensions differing
                        # is an unambiguous "cannot coexist" signal.
                        if qdrant_vector_size != embedding_dimension:
                            existing_desc = (
                                f"provider='{cur_provider}', model='{cur_model}'"
                                if cur_model
                                else "an unknown embedding model"
                            )
                            details = {
                                "existing_vector_size": qdrant_vector_size,
                                "new_embedding_size": embedding_dimension,
                                "points_count": points_count,
                                "identity_source": identity_source,
                            }
                            if cur_model:
                                details["existing_model"] = cur_model
                            if cur_provider:
                                details["existing_provider"] = cur_provider
                            details["new_model"] = new_model_norm
                            details["new_provider"] = new_provider_norm

                            return JSONResponse(
                                status_code=400,
                                content={
                                    "status": "error",
                                    "message": (
                                        f"{points_count} chunk(s) are already indexed using {existing_desc} "
                                        f"(vector dimension {qdrant_vector_size}). The selected model "
                                        f"'{new_model_norm or model_name}' (provider: "
                                        f"'{new_provider_norm or 'unknown'}') produces vectors of dimension "
                                        f"{embedding_dimension}, which is incompatible. Please either switch "
                                        f"back to the exact embedding model that was originally used to build "
                                        f"this index, or re-index your documents with the new model. Note: "
                                        f"matching the vector dimension alone is not sufficient — different "
                                        f"embedding models produce incompatible vector spaces even at the same "
                                        f"dimension."
                                    ),
                                    "details": details,
                                    "timestamp": get_epoch_timestamp_in_ms(),
                                },
                            )

                        # Same dimension — the case the original code missed.
                        # Two different models can share a dimension but produce
                        # incompatible vector spaces. Use the stored spec
                        # when available; fall back to config otherwise.
                        if identity_source == "collection_spec" and current_identity:
                            # Authoritative: compare (provider, model, and
                            # — when known on both sides — is_multimodal).
                            # Any differ blocks the change. (``cur_provider``
                            # / ``cur_model`` were resolved above for the
                            # dimension-mismatch branch.)
                            multimodal_flip = (
                                cur_is_multimodal is not None
                                and bool(cur_is_multimodal) != bool(new_is_multimodal)
                            )
                            mismatch = (
                                cur_model != new_model_norm
                                or (cur_provider and new_provider_norm and cur_provider != new_provider_norm)
                                or multimodal_flip
                            )
                            if mismatch:
                                if multimodal_flip and cur_model == new_model_norm:
                                    # Same provider/model but the user
                                    # toggled ``isMultimodal``. Make the
                                    # message explicit so the fix is
                                    # obvious (re-index, or un-toggle).
                                    message = (
                                        f"Embedding identity mismatch: the existing collection was built with "
                                        f"provider='{cur_provider}', model='{cur_model}', "
                                        f"isMultimodal={bool(cur_is_multimodal)}. The new configuration sets "
                                        f"isMultimodal={bool(new_is_multimodal)} on the same model — text-only "
                                        f"and multimodal modes of the same model produce incompatible vector "
                                        f"spaces. Please re-index, or leave isMultimodal unchanged for this "
                                        f"collection."
                                    )
                                else:
                                    message = (
                                        f"Embedding model mismatch: the existing collection was built with "
                                        f"provider='{cur_provider}', model='{cur_model}'. Switching to "
                                        f"provider='{new_provider_norm}', model='{new_model_norm}' would "
                                        f"corrupt search results even though vector dimensions match. "
                                        f"Please re-index or use the same model."
                                    )
                                details = {
                                    "existing_provider": cur_provider,
                                    "existing_model": cur_model,
                                    "new_provider": new_provider_norm,
                                    "new_model": new_model_norm,
                                    "points_count": points_count,
                                    "identity_source": "collection_spec",
                                }
                                if cur_is_multimodal is not None:
                                    details["existing_is_multimodal"] = bool(
                                        cur_is_multimodal
                                    )
                                    details["new_is_multimodal"] = bool(
                                        new_is_multimodal
                                    )
                                return JSONResponse(
                                    status_code=400,
                                    content={
                                        "status": "error",
                                        "message": message,
                                        "details": details,
                                        "timestamp": get_epoch_timestamp_in_ms(),
                                    },
                                )
                        elif identity_source in ("config", "default") and current_identity:
                            # Best-effort identity check used when no per-
                            # collection spec has been stamped yet. ``config``
                            # means we read the default AI_MODELS entry;
                            # ``default`` means we assumed the built-in
                            # default embedding model (pre-migration
                            # collections). Both are correlated, not causal,
                            # so we compare model + provider strings without
                            # the ``isMultimodal`` refinement.
                            model_differs = (
                                bool(cur_model)
                                and bool(new_model_norm)
                                and cur_model != new_model_norm
                            )
                            provider_differs = (
                                bool(cur_provider)
                                and bool(new_provider_norm)
                                and cur_provider != new_provider_norm
                            )
                            if model_differs or provider_differs:
                                return JSONResponse(
                                    status_code=400,
                                    content={
                                        "status": "error",
                                        "message": (
                                            f"Embedding model mismatch: the existing collection appears to have "
                                            f"been built with provider='{cur_provider or 'unknown'}', "
                                            f"model='{cur_model or 'unknown'}'. Switching to provider='"
                                            f"{new_provider_norm or 'unknown'}', model='{new_model_norm or 'unknown'}' "
                                            f"would corrupt search results even though vector dimensions match. "
                                            f"Please re-index or use the same model."
                                        ),
                                        "details": {
                                            "existing_provider": cur_provider or None,
                                            "existing_model": cur_model,
                                            "new_provider": new_provider_norm,
                                            "new_model": new_model_norm,
                                            "points_count": points_count,
                                            "identity_source": identity_source,
                                        },
                                        "timestamp": get_epoch_timestamp_in_ms(),
                                    },
                                )
                        else:
                            # No authoritative source and no config either,
                            # but the collection has user data. A silent pass
                            # here is exactly the bug we're fixing: we cannot
                            # rule out a same-dim model swap, so refuse.
                            return JSONResponse(
                                status_code=400,
                                content={
                                    "status": "error",
                                    "message": (
                                        f"Cannot verify that '{new_model_norm}' (provider: "
                                        f"{new_provider_norm}) matches the embedding model that built the "
                                        f"existing collection ({points_count} indexed chunks). Dimension "
                                        f"alone is insufficient — different models with the same dimension "
                                        f"produce incompatible vector spaces. Please re-index before "
                                        f"switching embedding models."
                                    ),
                                    "details": {
                                        "new_provider": new_provider_norm,
                                        "new_model": new_model_norm,
                                        "points_count": points_count,
                                        "identity_source": "unknown",
                                    },
                                    "timestamp": get_epoch_timestamp_in_ms(),
                                },
                            )
            except _GrpcInactiveRpcError as e:
                if e.code() == grpc.StatusCode.NOT_FOUND:
                    logger.info("Collection not found - acceptable for health check")
                else:
                    raise
            except HTTPException:
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
            logger.error(f"Embedding health check timed out for {embedding_config.get('provider')} with configuration model {embedding_config.get('configuration', {}).get('model', '')}")
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
        elif provider == TTSProvider.GEMINI.value:
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

    For cloud providers we call a cheap listing endpoint; for the local
    ``whisper`` provider we only instantiate the adapter and verify the
    optional runtime dependency is importable (model weights are lazy-loaded
    at first use to avoid multi-GB downloads during a UI health check).
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
                                "Install the optional extra to use the local "
                                "Whisper STT provider."
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

