import asyncio
import inspect
import ipaddress
import os
import shutil
from logging import Logger
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, HTTPException, Request  #type: ignore
from fastapi.responses import JSONResponse  #type: ignore
from langchain_core.embeddings import Embeddings  #type: ignore
from langchain_core.language_models.chat_models import BaseChatModel  #type: ignore
from langchain_core.messages import BaseMessage, HumanMessage  #type: ignore
from langchain_core.tools import StructuredTool  #type: ignore
from pydantic import BaseModel, Field

from app.utils.aimodels import (
    ImageGenerationProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

SPARSE_IDF = False

# Cloud LLM health checks call external APIs; local runtimes do not need egress.
_LOCAL_LLM_PROVIDERS = frozenset({"ollama", "lmStudio"})
_OUTBOUND_PROBE_URL = "https://1.1.1.1/"
_OUTBOUND_PROBE_TIMEOUT_S = 5.0

# Model types `/health-check/{model_type}` can actually verify. Anything else
# is rejected rather than reported healthy -- Node's model-type validator is
# wider than this set (`ocr`, `slm`, `reasoning`, `multiModal`), and a type
# that falls through would register a model nothing had checked.
SUPPORTED_HEALTH_CHECK_TYPES = frozenset({"llm", "embedding", "imageGeneration", "tts", "stt"})

# Outer cap vs I/O timeouts in web_search_tool / fetch_url (DDG 15s, httpx 30s).
_WEB_SEARCH_HEALTH_TIMEOUTS_S = {
    "duckduckgo": 20.0,
    "serper": 33.0,
    "tavily": 33.0,
    "exa": 33.0,
}


def _endpoint_is_local(endpoint: str) -> bool:
    """True when *endpoint* is loopback, private, or a compose/k8s short name.

    Parses the hostname so ``::1`` is not a substring match against addresses
    like ``http://[fd00::1234]:8000``.
    """
    if not endpoint:
        return False
    raw = endpoint.strip()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        return False
    if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
        return True
    # Compose / Kubernetes short names: ``vllm``, ``pipeshub-ai``.
    if "." not in host and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _llm_health_check_needs_outbound(provider: str, configuration: dict[str, Any]) -> bool:
    if provider in _LOCAL_LLM_PROVIDERS:
        return False
    if provider in (
        LLMProvider.OPENAI_COMPATIBLE.value,
        LLMProvider.LITELLM_PROXY.value,
    ):
        endpoint = configuration.get("endpoint") or configuration.get("baseUrl") or ""
        return not _endpoint_is_local(str(endpoint))
    return True


def _looks_like_connectivity_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, ConnectionError, OSError),
    ):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "connection refused",
            "connecterror",
            "connect timeout",
            "connection timed out",
            "failed to establish",
            "name or service not known",
            "nameresolutionerror",
            "network is unreachable",
            "temporary failure in name resolution",
            "max retries exceeded",
            "all connection attempts failed",
        )
    )


async def _probe_outbound_connectivity(timeout: float = _OUTBOUND_PROBE_TIMEOUT_S) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(_OUTBOUND_PROBE_URL)
            return response.status_code < 600
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, OSError):
        return False


def _outbound_connectivity_error_response(
    provider: str,
    model_name: str,
) -> JSONResponse:
    message = (
        "Cannot reach cloud LLM providers from PipesHub. "
        "The container may not have outbound internet access. "
        "Cloud LLMs and external connectors require container egress; "
        "air-gapped installs should use local models (Ollama, LM Studio). "
        "See deployment/docker-compose/ADVANCED_DEPLOYMENT.md#container-outbound-connectivity."
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": message,
            "details": {
                "error_code": "outbound_connectivity",
                "provider": provider,
                "model": model_name,
            },
        },
    )


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

# HTTP statuses that mean "ask again later", never "this model cannot do it".
_TRANSIENT_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Substrings providers emit when a request asked for something the model or
# deployment genuinely does not support. Matched case-insensitively; the SDKs
# involved share no exception hierarchy, so this is the only portable signal.
_CAPABILITY_ERROR_MARKERS = (
    "does not support",
    "doesn't support",
    "not supported",
    "unsupported",
    "invalid content type",
    "only allowed for messages with role",
    "image input",
    "vision",
    "multimodal",
    "unrecognized request argument",
    "unknown parameter",
)


def _is_capability_error(exc: Exception) -> bool:
    """Whether `exc` says the model cannot do the thing, as opposed to the
    request having failed for a reason that says nothing about the model.

    A rate limit, a gateway 5xx, a timeout or a bad key must never be reported
    as "this model has no vision support" -- that verdict tells an admin to
    turn off a capability their model actually has.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        if status in _TRANSIENT_STATUSES or status in (401, 403):
            return False
        if status == 400:
            return True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _CAPABILITY_ERROR_MARKERS)


def _config_error(
    message: str, config: dict, model_name: str, **extra: object
) -> JSONResponse:
    """A rejected *configuration* -- the admin has to change something.

    400 rather than 500 so the caller can tell "you typed the wrong model
    name" apart from "the service broke"; `cm_controller.ts` surfaces this
    message verbatim in the model dialog.
    """
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": message,
            "details": {
                "provider": config.get("provider"),
                "model": model_name,
                **extra,
            },
        },
    )


def _response_text(response: object) -> str:
    """Readable text from a LangChain response, for a user-facing message.

    Interpolating the response object itself puts `additional_kwargs={...}
    usage_metadata={...}` in front of an admin.
    """
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    return text[:300]


async def _invoke_with_timeout(
    model: BaseChatModel, payload: str | list[BaseMessage], timeout: float,
) -> BaseMessage:
    """Call a chat model, preferring its async path.

    `asyncio.wait_for(asyncio.to_thread(...))` abandons the *await* on timeout
    while the worker thread keeps running until the provider's own timeout
    (360 s by default) -- a leaked thread per timed-out health check. The async
    client cancels for real.
    """
    if inspect.iscoroutinefunction(getattr(model, "ainvoke", None)):
        return await asyncio.wait_for(model.ainvoke(payload), timeout=timeout)
    return await asyncio.wait_for(asyncio.to_thread(model.invoke, payload), timeout=timeout)


async def _embed_with_timeout(
    model: Embeddings, texts: list[str], timeout: float,
) -> list[list[float]]:
    """Embed documents, preferring the async path -- see `_invoke_with_timeout`."""
    # `hasattr` is not enough: a wrapper can expose a non-async `aembed_documents`,
    # and awaiting its return value fails at runtime.
    if inspect.iscoroutinefunction(getattr(model, "aembed_documents", None)):
        return await asyncio.wait_for(model.aembed_documents(texts), timeout=timeout)
    return await asyncio.wait_for(
        asyncio.to_thread(model.embed_documents, texts), timeout=timeout,
    )


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
    """Validate a batch of LLM configurations (used by the bulk config write).

    Delegates to `perform_llm_health_check`, the same implementation the
    per-model route uses. These two used to be separate code paths that
    checked different things -- this one resolved a model through `get_llm`
    and sent one prompt, while the per-model route probed vision and
    capabilities -- so which checks ran depended on which screen the admin
    happened to use.
    """
    logger = request.app.container.logger()
    if not llm_configs:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "No LLM configurations provided",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    for llm_config in llm_configs:
        response = await perform_llm_health_check(llm_config, logger)
        if response.status_code != 200:
            return response

    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "message": f"All {len(llm_configs)} LLM configuration(s) are responding",
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
            f"Rejected embedding change: the managed collection(s) contain "
            f"{points_count} point(s) indexed with the previous model."
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
    """Rebuild every managed collection for the new embedding dimension.

    Routed through CollectionRegistry so each rebuilt collection gets the same
    config, payload indexes, and manifest entry as one created by the normal
    indexing write path — and so a multi-collection strategy rebuilds all of
    them, not just the one this service happens to name.
    """
    registry = retrieval_service.collection_registry
    try:
        recreated = await registry.recreate_all_collections(
            embedding_size, sparse_idf=SPARSE_IDF
        )
        if not recreated:
            # Nothing managed yet. There is no collection to rebuild, and
            # creating one here would have to invent a context — which under a
            # strategy that names collections per org or connector names a
            # collection belonging to nobody. The indexing write path pins the
            # dimension on first use, from the record that actually needs it.
            logger.info(
                "No managed collections to recreate; the indexing write path "
                "will create them at dimension %s on first use",
                embedding_size,
            )
            return
        logger.info(
            f"Successfully recreated collection(s) {recreated} with vector size {embedding_size}"
        )
    except Exception as e:
        logger.error(f"Failed to recreate collection: {str(e)}", exc_info=True)
        raise


class CollectionSurveyError(Exception):
    """The existing index state could not be established.

    Distinct from "there is nothing indexed", and the distinction decides
    whether an embedding-model change is allowed to drop collections. Treating
    an unreadable survey as an empty one is a fail-open on a destructive
    operation, so this propagates.
    """


async def survey_managed_collections(retrieval_service, logger) -> tuple[int, int]:
    """Aggregate (dense dimension, total points) over every managed collection.

    The embedding-model guard must reject a change while *any* managed
    collection still holds data, so the enumeration is read fresh: a cached
    view could miss a collection another service created since this process
    started, and the guard would wave the change through while that collection
    still holds vectors from the outgoing model.
    """
    registry = retrieval_service.collection_registry
    try:
        managed = await registry.list_managed_collections(fresh=True)
        existing_vector_size = 0
        points_count = 0
        for entry in managed:
            info = await retrieval_service.vector_db_service.get_collection_info(
                entry.name
            )
            if not info.exists:
                continue
            if not existing_vector_size:
                existing_vector_size = info.dense_dimension or 0
            points_count += info.points_count or 0
    except Exception as e:
        raise CollectionSurveyError(
            f"Could not determine what the vector store currently holds: {e}"
        ) from e

    logger.debug(
        f"Surveyed {len(managed)} managed collection(s): "
        f"dimension={existing_vector_size}, points={points_count}"
    )
    return existing_vector_size, points_count


async def check_collection_info(
    retrieval_service,
    dense_embeddings,
    embedding_size,
    logger
) -> None:
    """Check and validate collection information using provider-neutral get_collection_info()."""
    try:
        existing_vector_size, points_count = await survey_managed_collections(
            retrieval_service, logger
        )

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
    except CollectionSurveyError as e:
        # Fail closed. Proceeding here would let an embedding-model change be
        # accepted — and collections dropped — on the strength of a survey that
        # never ran, which is unrecoverable; refusing costs a retry.
        logger.error(f"Refusing to validate the embedding change: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not healthy",
                "error": (
                    "Could not verify what the vector store currently holds, so "
                    "an embedding model change cannot be validated. Check vector "
                    "store connectivity and retry."
                ),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        ) from e
    except Exception as e:
        # Connectivity / not-found errors during startup are non-fatal: log and
        # let the health check proceed rather than blocking the service from starting.
        logger.warning(f"Unexpected error checking vector db collection: {str(e)}", exc_info=True)

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

_LLM_HEALTH_TIMEOUT_S = 120.0
# A check now makes up to three calls per model (text, image, tools) and may
# cover several models, so each call having its own ceiling does not bound the
# request. Kept under the caller's own timeout (`ai.service.command.ts`).
_LLM_HEALTH_TOTAL_TIMEOUT_S = 420.0
# A stream is expected to start promptly even when the full answer is long;
# this only waits for the first content chunk.
_STREAM_PROBE_TIMEOUT_S = 60.0

# What the platform assumes when a model's context window is not configured
# (`chatbot.py`'s DEFAULT_CONTEXT_LENGTH). Worth reporting rather than
# assuming quietly: the value sizes how much of a document one read returns
# and which prompt scaffolding the model gets (`model_tier.py`), so an unset
# window silently caps a 1M-token model at the default.
ASSUMED_CONTEXT_LENGTH = 128_000
# Outside this range the value is a typo, not a window.
MIN_PLAUSIBLE_CONTEXT_LENGTH = 1_024
MAX_PLAUSIBLE_CONTEXT_LENGTH = 20_000_000
# The tool probe repeats a prompt the model has already answered once, so it
# gets a tighter budget: a model is now checked with up to three calls (text,
# image, tools) and the caller's own ceiling has to cover all of them.
_TOOL_PROBE_TIMEOUT_S = 60.0

_TEXT_PROBE = (
    "Hello, this is a health check test. Please respond with "
    "'Health check successful' if you can read this message."
)
# The image is a solid-colour square (see `assets/test_image.b64`); asking
# about it means a model that cannot actually see the image answers wrongly
# or not at all instead of returning something generic that still "passes".
_IMAGE_PROBE = "What is in this image? Answer in a few words."


class _HealthProbe(BaseModel):
    """Trivial tool used only to check that the model accepts bound tools."""

    query: str = Field(description="Anything at all; this tool is never run.")


async def _check_tool_calling(llm_model: BaseChatModel, logger: Logger) -> bool:
    """Whether this model accepts bound tools.

    Every agent turn binds tools, and `LangChainTransport._bind_tools` fails
    the turn rather than silently dropping them, so a model that cannot take
    them is unusable for agents even though it answers plain prompts. Reported
    rather than fatal: the same model may still be a fine choice for the
    indexing and image-description roles.
    """
    try:
        bound = llm_model.bind_tools([
            StructuredTool.from_function(
                func=lambda query: query,
                name="health_probe",
                description="A no-op probe used by the health check.",
                args_schema=_HealthProbe,
            ),
        ])
    except Exception as exc:
        logger.info("Model does not accept bound tools: %s", exc)
        return False

    try:
        await _invoke_with_timeout(bound, _TEXT_PROBE, _TOOL_PROBE_TIMEOUT_S)
    except Exception as exc:
        if _is_capability_error(exc):
            logger.info("Model rejected a request carrying tools: %s", exc)
            return False
        # A rate limit or a gateway blip says nothing about tool support;
        # the plain-text probe already proved the model answers.
        logger.warning("Tool-calling probe inconclusive, assuming supported: %s", exc)
    return True


def _configuration_warnings(llm_config: dict, model_names: list[str]) -> list[str]:
    """Settings that are wrong, or missing, but no longer fatal.

    An unset context length is the quiet one: the platform falls back to a
    default that decides how much of a document one read returns, so a 1M-token
    model configured without a window behaves like a 128k one.

    A Bedrock model id names its own provider, so a contradicting dropdown is
    corrected at request time rather than sent to Bedrock (which rejects it
    with "extraneous key [thinking] is not permitted" -- an error naming
    neither the setting at fault nor the model it was set on). The check still
    reports it: silently fixing a wrong setting leaves it wrong, and the next
    person to read the config sees a provider that is not what runs.

    Bedrock nests its providers -- the outer one is "bedrock", and
    `configuration.provider` names the foundation model's own vendor.
    """
    from app.utils.aimodels import bedrock_provider_mismatch

    warnings: list[str] = []
    if _configured_context_length(llm_config) is None:
        warnings.append(
            f"Note: this model's context length is not set, so "
            f"{ASSUMED_CONTEXT_LENGTH:,} tokens is assumed. That value decides how much "
            f"of a document a single read returns — set it to the model's real window."
        )

    if _normalize_provider_key(llm_config.get("provider")) != "bedrock":
        return warnings

    configured = (llm_config.get("configuration") or {}).get("provider")
    for model_name in model_names:
        identified = bedrock_provider_mismatch(configured, model_name)
        if identified:
            warnings.append(
                f"Note: this model's provider is set to '{configured}', but the model id "
                f"identifies it as '{identified}'. It was used as '{identified}' for this "
                f"check — update the provider on this model."
            )
    return warnings


def _normalize_provider_key(provider: str | None) -> str:
    return (provider or "").strip().lower().replace("-", "").replace("_", "")


async def _check_streaming(llm_model: BaseChatModel, logger: Logger) -> bool:
    """Whether this model streams.

    Every answer reaches the user through `astream`, so a model that only
    supports a blocking call still works but delivers the whole reply at once
    after a long silence. Reported rather than fatal: the indexing and
    image-description roles never stream.
    """
    if not hasattr(llm_model, "astream"):
        return False
    try:
        async with asyncio.timeout(_STREAM_PROBE_TIMEOUT_S):
            async for chunk in llm_model.astream(_TEXT_PROBE):
                if getattr(chunk, "content", None):
                    return True
        return False
    except Exception as exc:
        if _is_capability_error(exc):
            logger.info("Model does not support streaming: %s", exc)
            return False
        # A rate limit or a gateway blip says nothing about streaming, and the
        # plain probe already proved the model answers.
        logger.warning("Streaming probe inconclusive, assuming supported: %s", exc)
        return True


def _configured_context_length(llm_config: dict) -> int | None:
    """The window an admin set, in either shape the config manager sends."""
    for source in (llm_config, llm_config.get("configuration") or {}):
        raw = source.get("contextLength")
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1        # present and unusable: reported, never ignored
    return None


def _effective_context_length(llm_config: dict) -> int:
    configured = _configured_context_length(llm_config)
    return configured if configured and configured > 0 else ASSUMED_CONTEXT_LENGTH


def _validate_context_length(llm_config: dict, model_string: str) -> JSONResponse | None:
    """Reject a window that cannot be one, before spending a provider call."""
    configured = _configured_context_length(llm_config)
    if configured is None:
        return None
    if not (MIN_PLAUSIBLE_CONTEXT_LENGTH <= configured <= MAX_PLAUSIBLE_CONTEXT_LENGTH):
        return _config_error(
            f"Context length {configured} is not a usable window. Set it to the model's "
            f"context window in tokens (between {MIN_PLAUSIBLE_CONTEXT_LENGTH:,} and "
            f"{MAX_PLAUSIBLE_CONTEXT_LENGTH:,}), or leave it empty to assume "
            f"{ASSUMED_CONTEXT_LENGTH:,}.",
            llm_config, model_string,
        )
    return None


async def perform_llm_health_check(
    llm_config: dict,
    logger: Logger,
) -> JSONResponse:
    """Verify an LLM configuration against the real provider.

    Checks, in order: the model answers at all; if flagged multimodal, that it
    can actually read an image; and whether it accepts bound tools, which every
    agent turn requires.
    """
    provider = llm_config.get("provider", "") or ""
    configuration = llm_config.get("configuration") or {}
    if not isinstance(configuration, dict):
        configuration = {}
    model_string = configuration.get("model") or ""
    # Bound before anything can fail so the error handlers below always have
    # something to report.
    model_name = ""
    friendly_name = llm_config.get("modelFriendlyName", "")

    try:
        logger.info("Performing LLM health check for %s with model %s", provider, model_string)
        model_names = [name.strip() for name in str(model_string).split(",") if name.strip()]
        if not model_names:
            logger.error("No valid model names in configuration for %s", provider)
            return _config_error(
                "No valid model names found in configuration", llm_config, model_string,
            )

        context_error = _validate_context_length(llm_config, model_string)
        if context_error is not None:
            return context_error

        # Node registers every name in the list as its own model
        # (`cm_controller.ts`'s model flattening), so every name is checked.
        results: list[dict[str, Any]] = []
        async with asyncio.timeout(_LLM_HEALTH_TOTAL_TIMEOUT_S):
            for model_name in model_names:
                result = await _check_one_llm(llm_config, model_name, logger)
                if isinstance(result, JSONResponse):
                    return result
                results.append(result)

        tool_calling = all(r["tool_calling"] for r in results)
        streaming = all(r["streaming"] for r in results)
        message = f"LLM model is responding. Sample response: {results[0]['sample']}"
        for warning in _configuration_warnings(llm_config, model_names):
            message += f" {warning}"
        if not tool_calling:
            message += (
                " Note: this model did not accept bound tools, so it cannot be "
                "used for agents. It can still be used for indexing."
            )
        if not streaming:
            message += (
                " Note: this model did not stream a response. Answers are streamed to "
                "the user, so replies from this model may arrive only when complete."
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": message,
                "capabilities": {
                    "tool_calling": tool_calling,
                    "streaming": streaming,
                    "multimodal": bool(
                        llm_config.get("isMultimodal", False)
                        or configuration.get("isMultimodal", False)
                    ),
                    # What the platform will actually use: the configured
                    # window, or the assumption standing in for it.
                    "context_length": _effective_context_length(llm_config),
                },
                "models": [r["model"] for r in results],
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

    except asyncio.TimeoutError:
        logger.error(
            "LLM health check timed out for %s model %s (%s)", provider, model_string, friendly_name,
        )
        return JSONResponse(
            status_code=504,
            content={
                "status": "error",
                "message": (
                    "LLM health check timed out. For cloud providers, verify your API key "
                    "and that PipesHub containers can reach the internet. "
                    "See deployment/docker-compose/ADVANCED_DEPLOYMENT.md#container-outbound-connectivity."
                ),
                "details": {
                    "error_code": "health_check_timeout",
                    "provider": provider,
                    "model": model_name or model_string,
                    "timeout_seconds": int(_LLM_HEALTH_TIMEOUT_S),
                },
            },
        )
    except HTTPException as he:
        logger.error("LLM health check failed for %s model %s: %s", provider, model_string, he)
        return JSONResponse(status_code=he.status_code, content=he.detail)
    except Exception as e:
        logger.error(
            "LLM health check failed for %s model %s (%s): %s",
            provider, model_string, friendly_name, e,
        )
        # A refused connection from a cloud provider usually means the
        # container has no egress at all, which no amount of re-typing the API
        # key will fix -- so say that instead of relaying the socket error.
        if (
            _looks_like_connectivity_error(e)
            and _llm_health_check_needs_outbound(provider, configuration)
            and not await _probe_outbound_connectivity()
        ):
            return _outbound_connectivity_error_response(provider, model_name or model_string)
        clean_msg = _extract_error_message(e)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"LLM health check failed: {clean_msg}",
                "details": {
                    "provider": provider,
                    "model": model_name or model_string,
                    "error_type": type(e).__name__,
                },
            },
        )


async def _check_one_llm(
    llm_config: dict, model_name: str, logger: Logger,
) -> "dict[str, Any] | JSONResponse":
    """Probe a single model. Returns its capabilities, or the JSONResponse to
    return to the caller when the model itself is the problem."""
    provider = llm_config.get("provider", "")
    configuration = llm_config.get("configuration") or {}
    is_multimodal = bool(
        llm_config.get("isMultimodal", False) or configuration.get("isMultimodal", False)
    )

    llm_model = await asyncio.to_thread(
        get_generator_model, provider=provider, config=llm_config, model_name=model_name,
    )
    logger.debug("Generator model created for %s", model_name)

    # Text first: it establishes that the model answers at all, which is what
    # makes a later image failure interpretable.
    text_response = await _invoke_with_timeout(llm_model, _TEXT_PROBE, _LLM_HEALTH_TIMEOUT_S)
    sample = _response_text(text_response)
    if not sample:
        return _config_error(
            "Model accepted the request but returned an empty response",
            llm_config, model_name,
        )

    if is_multimodal:
        image_error = await _probe_vision(llm_model, logger)
        if image_error is not None:
            return _config_error(
                image_error, llm_config, model_name,
                hint="Uncheck Multimodal for this model, or choose a vision model.",
            )

    return {
        "model": model_name,
        "sample": sample,
        "tool_calling": await _check_tool_calling(llm_model, logger),
        "streaming": await _check_streaming(llm_model, logger),
    }


async def _probe_vision(llm_model: BaseChatModel, logger: Logger) -> str | None:
    """None when the model demonstrably read the image, else why not.

    The probe carries a question alongside the image: a bare image block lets a
    model that ignored it still return something, and some gateways reject an
    image-only user turn outright.
    """
    message = HumanMessage(content=[
        {"type": "text", "text": _IMAGE_PROBE},
        {"type": "image_url", "image_url": {"url": _get_test_image()}},
    ])
    try:
        response = await _invoke_with_timeout(llm_model, [message], _LLM_HEALTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise
    except Exception as image_error:
        if _is_capability_error(image_error):
            logger.info("Model rejected image input: %s", image_error)
            return f"Model doesn't support images/vision: {_extract_error_message(image_error)}"
        # Rate limit, gateway 5xx, auth: says nothing about vision support, so
        # reporting "no vision" here would tell the admin to disable a
        # capability the model may well have.
        logger.error("Image probe failed for a reason unrelated to vision: %s", image_error)
        raise

    if not _response_text(response):
        return "Model accepted the image but returned an empty response"
    logger.info("Image probe passed")
    return None


def _is_multimodal(config: dict) -> bool:
    """The deployment's own multimodal flag, in either of the two shapes the
    Node config manager sends it."""
    return bool(
        config.get("isMultimodal", False)
        or (config.get("configuration") or {}).get("isMultimodal", False)
    )


async def _probe_image_embedding(
    embedding_config: dict, model_name: str, text_dimension: int, logger: Logger,
) -> str | None:
    """None when this model really can embed an image, else why not.

    Uses the same `MultimodalEmbeddingFactory` the indexing pipeline uses, so a
    provider with no implementation is caught here rather than by images
    quietly missing from the index.
    """
    from app.services.embeddings.multimodal.config import MultimodalProviderConfig
    from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory

    provider = embedding_config.get("provider", "")
    configuration = embedding_config.get("configuration") or {}
    try:
        multimodal_provider = MultimodalEmbeddingFactory.create(
            MultimodalProviderConfig(
                provider=provider,
                model_name=model_name,
                api_key=configuration.get("apiKey"),
                base_url=configuration.get("endpoint") or configuration.get("baseUrl"),
                region_name=configuration.get("region"),
                aws_access_key_id=configuration.get("awsAccessKeyId"),
                aws_secret_access_key=configuration.get("awsSecretAccessKey"),
                embedding_size=text_dimension,
                logger=logger,
            )
        )
    except Exception as exc:
        logger.warning("Could not build a multimodal embedding provider: %s", exc)
        return f"This provider cannot embed images: {_extract_error_message(exc)}"

    if multimodal_provider is None or not multimodal_provider.supports_multimodal():
        return (
            f"Provider '{provider}' has no image-embedding support in PipesHub, "
            "so images would never be indexed for this model."
        )

    try:
        results = await asyncio.wait_for(
            multimodal_provider.embed_images([_get_test_image()]),
            timeout=_LLM_HEALTH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise
    except Exception as exc:
        if _is_capability_error(exc):
            return f"Model cannot embed images: {_extract_error_message(exc)}"
        raise

    first = results[0] if results else None
    embedding = getattr(first, "embedding", None)
    if not embedding:
        error = getattr(first, "error", None)
        return f"Image embedding returned nothing{f': {error}' if error else ''}"
    if len(embedding) != text_dimension:
        # A collection holds one vector width; text and image points must agree.
        return (
            f"Image embeddings are {len(embedding)}-dimensional but text embeddings "
            f"are {text_dimension}-dimensional; both share one collection."
        )
    return None


async def _check_collection_compatibility(
    request: Request, dense_embeddings: Embeddings, embedding_dimension: int, logger: Logger,
) -> JSONResponse | None:
    """Run the bulk route's collection guard for a single-model check.

    Returns the response to send when the change is refused, else None. A
    vector store that is unreachable is not this check's problem -- the guard
    itself already treats connectivity errors as non-fatal.
    """
    try:
        retrieval_service = await request.app.container.retrieval_service()
    except Exception:
        logger.debug("No retrieval service available; skipping collection check", exc_info=True)
        return None

    try:
        await check_collection_info(
            retrieval_service, dense_embeddings, embedding_dimension, logger,
        )
    except HTTPException as he:
        detail = he.detail if isinstance(he.detail, dict) else {"message": str(he.detail)}
        detail.setdefault("message", detail.get("error", "Embedding model change refused"))
        return JSONResponse(status_code=he.status_code, content=detail)
    return None


async def perform_embedding_health_check(
    request: Request,
    embedding_config: dict,
    logger: Logger,
) -> JSONResponse:
    """Perform health check for embedding models"""
    try:
        logger.info(f"Performing embedding health check for {embedding_config.get('provider')} with configuration model {embedding_config.get('configuration', {}).get('model', '')}")
        # Use the first model from comma-separated list
        model_string = embedding_config.get("configuration", {}).get("model", "")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]

        if not model_names:
            logger.error("No valid model names in configuration for %s", embedding_config.get("provider"))
            return _config_error(
                "No valid model names found in configuration", embedding_config, model_string,
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
            test_embeddings = await _embed_with_timeout(
                embedding_model, test_texts, HEALTH_CHECK_TIMEOUT,
            )

            logger.info(f"Test embeddings length: {len(test_embeddings)}")
            if not test_embeddings:
                logger.error("Embedding model returned empty results for %s", embedding_config.get("provider"))
                return _config_error(
                    "Embedding model returned empty results", embedding_config, model_name,
                )

            # Validate embedding dimensions. The result of this comparison
            # used to be discarded, which made it look like a check while
            # letting a ragged response through to the vector store.
            embedding_dimension = len(test_embeddings[0])
            if any(len(emb) != embedding_dimension for emb in test_embeddings):
                return _config_error(
                    "Embedding model returned vectors of differing sizes",
                    embedding_config, model_name,
                )

            # A provider that silently ignores a `dimensions` override would
            # otherwise build a collection of the wrong width, discovered only
            # when the first query returns nothing.
            requested = embedding_config.get("configuration", {}).get("dimensions")
            if isinstance(requested, int) and requested > 0 and requested != embedding_dimension:
                return _config_error(
                    f"Model ignored the requested dimensions: asked for {requested}, "
                    f"got {embedding_dimension}",
                    embedding_config, model_name,
                )

            # `isMultimodal` on an embedding model is what makes indexing send
            # images down the image-embedding path at all, and only a handful
            # of providers implement one. Claiming it without checking means
            # images silently never get indexed
            # (`vectorstore._process_image_embeddings` warns and returns []).
            if _is_multimodal(embedding_config):
                image_error = await _probe_image_embedding(
                    embedding_config, model_name, embedding_dimension, logger,
                )
                if image_error is not None:
                    return _config_error(
                        image_error, embedding_config, model_name,
                        hint="Uncheck Multimodal for this model, or choose one that embeds images.",
                    )

            # The same collection-compatibility guard the bulk route runs. Without
            # it, changing dimensions from the model dialog reports healthy and is
            # discovered when queries start returning nothing.
            collection_error = await _check_collection_compatibility(
                request, embedding_model, embedding_dimension, logger,
            )
            if collection_error is not None:
                return collection_error

            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "message": f"Embedding model is responding. Sample embedding size: {embedding_dimension}",
                    "capabilities": {
                        "multimodal": _is_multimodal(embedding_config),
                        "dimensions": embedding_dimension,
                    },
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
                status_code=504,
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
                    "model": embedding_config.get("configuration", {}).get("model"),
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
        elif provider == ImageGenerationProvider.OPENROUTER.value:
            from app.config.constants.ai_models import OPENROUTER_BASE_URL

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(
                    f"{OPENROUTER_BASE_URL}/auth/key",
                    headers={"Authorization": f"Bearer {configuration['apiKey']}"},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"OpenRouter credential check returned HTTP {resp.status_code}"
                    )
        elif provider == ImageGenerationProvider.LITELLM_PROXY.value:
            endpoint = configuration.get("endpoint", "").rstrip("/")
            headers: dict[str, str] = {}
            api_key = configuration.get("apiKey")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(f"{endpoint}/health", headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"LiteLLM Proxy health check returned HTTP {resp.status_code}"
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
            pass  # Gemini TTS uses a REST endpoint; no dedicated health probe needed.
        elif provider == TTSProvider.OPENROUTER.value:
            from app.config.constants.ai_models import OPENROUTER_BASE_URL

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(
                    f"{OPENROUTER_BASE_URL}/auth/key",
                    headers={"Authorization": f"Bearer {configuration['apiKey']}"},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"OpenRouter credential check returned HTTP {resp.status_code}"
                    )
        elif provider == TTSProvider.LITELLM_PROXY.value:
            endpoint = configuration.get("endpoint", "").rstrip("/")
            headers = {}
            api_key = configuration.get("apiKey")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(f"{endpoint}/health", headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"LiteLLM Proxy health check returned HTTP {resp.status_code}"
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
        elif provider == STTProvider.OPENROUTER.value:
            from app.config.constants.ai_models import OPENROUTER_BASE_URL

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(
                    f"{OPENROUTER_BASE_URL}/auth/key",
                    headers={"Authorization": f"Bearer {configuration['apiKey']}"},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"OpenRouter credential check returned HTTP {resp.status_code}"
                    )
        elif provider == STTProvider.LITELLM_PROXY.value:
            endpoint = configuration.get("endpoint", "").rstrip("/")
            headers = {}
            api_key = configuration.get("apiKey")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.get(f"{endpoint}/health", headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"LiteLLM Proxy health check returned HTTP {resp.status_code}"
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

    logger = request.app.container.logger()
    try:
        logger.info(f"Health check endpoint called for {model_type}")

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

        logger.error("No health check implemented for model type %r", model_type)
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": (
                    f"No health check exists for model type '{model_type}'. "
                    f"Supported types: {', '.join(sorted(SUPPORTED_HEALTH_CHECK_TYPES))}."
                ),
                "details": {"modelType": model_type},
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

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

