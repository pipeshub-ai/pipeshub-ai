
from __future__ import annotations

import asyncio
import os
import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict
from urllib.parse import urlparse

if TYPE_CHECKING:
    from botocore.client import BaseClient

from langchain_core.embeddings.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config.constants.ai_models import (
    AZURE_EMBEDDING_API_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_REASONING_EFFORT,
    OPENROUTER_BASE_URL,
    AzureOpenAILLM,
)
from app.utils.embedding_server_client import get_embedding_server_embeddings
from app.utils.llm_api_mode_store import (
    REASONING_MANDATORY_FALLBACK_EFFORT,
    LLMApiMode,
    get_llm_api_mode_store,
)
from app.utils.logger import create_logger


def coerce_message_content_to_text(content: Any) -> str:
    """Normalize a LangChain message ``content`` value to plain text.

    Most providers return ``content`` as a string, but some (e.g. Gemini) return
    a list of content blocks — strings and/or ``{"type": "text", "text": ...}``
    dicts. Concatenate the textual parts so downstream string handling does not
    break with ``'list' object has no attribute 'strip'``. Non-text blocks (such
    as image parts) are ignored rather than stringified.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif block is not None:
                parts.append(str(block))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


class ModelType(str, Enum):
    LLM = "llm"
    EMBEDDING = "embedding"
    OCR = "ocr"
    SLM = "slm"
    REASONING = "reasoning"
    MULTIMODAL = "multiModal"
    IMAGE_GENERATION = "imageGeneration"
    TTS = "tts"
    STT = "stt"

class EmbeddingProvider(Enum):
    ANTHROPIC = "anthropic"
    AWS_BEDROCK = "bedrock"
    AZURE_AI = "azureAI"
    AZURE_OPENAI = "azureOpenAI"
    COHERE = "cohere"
    DEFAULT = "default"
    FIREWORKS = "fireworks"
    GEMINI = "gemini"
    HUGGING_FACE = "huggingFace"
    JINA_AI = "jinaAI"
    LITELLM_PROXY = "litellmProxy"
    LM_STUDIO = "lmStudio"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    OPENROUTER = "openRouter"
    SENTENCE_TRANSFOMERS = "sentenceTransformers"
    TOGETHER = "together"
    VERTEX_AI = "vertexAI"
    VOYAGE = "voyage"

LOCAL_CPU_EMBEDDING_PROVIDERS = frozenset({
    EmbeddingProvider.DEFAULT.value,
    EmbeddingProvider.HUGGING_FACE.value,
    EmbeddingProvider.SENTENCE_TRANSFOMERS.value,
})


def is_local_cpu_embedding_provider(provider: str | None) -> bool:
    """Whether *provider* embeds on local CPU via the embedding server.

    ``None`` counts as local: an unconfigured deployment falls back to
    ``get_default_embedding_model()``.
    """
    return provider is None or provider in LOCAL_CPU_EMBEDDING_PROVIDERS


class LLMProvider(Enum):
    ANTHROPIC = "anthropic"
    AWS_BEDROCK = "bedrock"
    AZURE_AI = "azureAI"
    AZURE_OPENAI = "azureOpenAI"
    COHERE = "cohere"
    FIREWORKS = "fireworks"
    GEMINI = "gemini"
    GROQ = "groq"
    LITELLM_PROXY = "litellmProxy"
    LM_STUDIO = "lmStudio"
    MINIMAX = "minimax"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    OPENROUTER = "openRouter"
    TOGETHER = "together"
    VERTEX_AI = "vertexAI"
    XAI = "xai"


class ImageGenerationProvider(Enum):
    LITELLM_PROXY = "litellmProxy"
    OPENAI = "openAI"
    GEMINI = "gemini"
    OPENROUTER = "openRouter"


class TTSProvider(Enum):
    LITELLM_PROXY = "litellmProxy"
    OPENAI = "openAI"
    GEMINI = "gemini"
    OPENROUTER = "openRouter"


class STTProvider(Enum):
    LITELLM_PROXY = "litellmProxy"
    OPENAI = "openAI"
    WHISPER = "whisper"
    WISPR = "wispr"
    GEMINI = "gemini"
    OPENROUTER = "openRouter"

MAX_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS_CLAUDE_MODERN = 16384
MAX_OUTPUT_TOKENS_CLAUDE_4_5 = 64000

def get_default_embedding_model() -> Embeddings:
    return get_embedding_server_embeddings(DEFAULT_EMBEDDING_MODEL)

logger = create_logger("aimodels")

def _create_bedrock_client(configuration: dict[str, Any], service_name: str = "bedrock-runtime") -> BaseClient:
    """Create a boto3 Bedrock client with proper credential handling.

    Tries credentials in this order:
      1. Explicit keys from configuration (awsAccessKeyId / awsAccessSecretKey)
      2. boto3 default credential chain (env vars, ~/.aws, EC2 IAM role, ECS task role)
    """
    import boto3

    region = configuration.get("region") or os.environ.get("AWS_DEFAULT_REGION")
    aws_access_key = (configuration.get("awsAccessKeyId") or "").strip()
    aws_secret_key = (configuration.get("awsAccessSecretKey") or "").strip()

    if aws_access_key and aws_secret_key:
        logger.info("Creating Bedrock client with explicit AWS credentials")
        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region,
        )
    else:
        logger.info(
            "No explicit AWS credentials provided for Bedrock; "
            "using default credential chain (env vars AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY, ~/.aws/credentials, EC2 IAM role, ECS task role)"
        )
        session = boto3.Session(region_name=region)

    return session.client(service_name)


def _create_vertex_credentials(service_account_json: str) -> Any:
    """Build GCP credentials from a service-account JSON string (from UI config).

    Credentials are never read from environment variables; callers must pass
    the JSON key material stored in the model configuration.
    """
    import json

    from google.oauth2 import service_account

    info = json.loads(service_account_json)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def is_multimodal_llm(config: dict[str, Any]) -> bool:
    """
    Check if an LLM configuration supports multimodal capabilities.

    Args:
        config: LLM configuration dictionary

    Returns:
        bool: True if the LLM supports multimodal capabilities
    """
    return (
        config.get("isMultimodal", False) or
        config.get("configuration", {}).get("isMultimodal", False)
    )


def _set_embedding_dimensions_kwarg(
    kwargs: Dict[str, Any],
    dimensions: int | None,
    *,
    key: str = "dimensions",
) -> None:
    if dimensions is not None:
        kwargs[key] = dimensions


# `check_embedding_ctx_length=True` makes langchain-openai tiktoken-encode each
# text and send `input` as arrays of token IDs. Only OpenAI's own embeddings
# endpoint accepts that shape; every other server speaking the OpenAI protocol
# (routers such as OpenRouter/Requesty, and providers proxied behind them —
# Google, Cohere, Voyage, Nvidia, ...) requires plain strings and rejects token
# arrays with a 400. tiktoken's vocabulary is OpenAI-specific anyway, so the
# token IDs would be meaningless to a non-OpenAI model even where accepted.
_TOKEN_ARRAY_EMBEDDING_HOSTS = frozenset({"api.openai.com"})


def _accepts_token_array_embedding_input(base_url: str | None) -> bool:
    if not base_url:
        return False
    host = urlparse(base_url).hostname
    return bool(host) and host.lower() in _TOKEN_ARRAY_EMBEDDING_HOSTS


def get_embedding_model(provider: str, config: dict[str, Any], model_name: str | None = None) -> Embeddings:
    configuration = config['configuration']
    is_default = config.get("isDefault")
    if is_default and model_name is None:
        model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
        model_name = model_names[0]
    elif not is_default and model_name is None:
        model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
        model_name = model_names[0]
    elif not is_default and model_name is not None:
        model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
        if model_name not in model_names:
            raise ValueError(f"Model name {model_name} not found in {configuration['model']}")

    logger.info(f"Getting embedding model: provider={provider}, model_name={model_name}")

    raw_dims = configuration.get("dimensions")
    dimensions: int | None = None
    if raw_dims not in (None, "", 0):
        try:
            dimensions = int(raw_dims)
        except (ValueError, TypeError):
            logger.warning(f"Non-numeric dimensions value ignored: {raw_dims!r}")

    if provider == EmbeddingProvider.AZURE_AI.value:
        from langchain_openai.embeddings import OpenAIEmbeddings
        if model_name and ("cohere" in model_name.lower() or "embed-v" in model_name.lower()):
            check_embedding_ctx_length = False
        else:
            check_embedding_ctx_length = True
        kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
            check_embedding_ctx_length=check_embedding_ctx_length,
        )
        _set_embedding_dimensions_kwarg(kwargs, dimensions)
        return OpenAIEmbeddings(**kwargs)

    elif provider == EmbeddingProvider.AZURE_OPENAI.value:
        from langchain_openai.embeddings import AzureOpenAIEmbeddings

        kwargs = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            api_version=AZURE_EMBEDDING_API_VERSION,
            azure_endpoint=configuration['endpoint'],
        )
        _set_embedding_dimensions_kwarg(kwargs, dimensions)
        return AzureOpenAIEmbeddings(**kwargs)

    elif provider == EmbeddingProvider.COHERE.value:
        from langchain_cohere import CohereEmbeddings

        return CohereEmbeddings(
            model=model_name,
            cohere_api_key=configuration['apiKey'],
        )


    elif provider == EmbeddingProvider.DEFAULT.value:
        return get_default_embedding_model()

    elif provider == EmbeddingProvider.FIREWORKS.value:
        from langchain_fireworks import FireworksEmbeddings
        return FireworksEmbeddings(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
        )

    elif provider == EmbeddingProvider.GEMINI.value:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        gemini_kwargs: Dict[str, Any] = dict(
            model=model_name,
            google_api_key=configuration['apiKey'],
        )
        _set_embedding_dimensions_kwarg(
            gemini_kwargs, dimensions, key="output_dimensionality"
        )
        return GoogleGenerativeAIEmbeddings(**gemini_kwargs)

    elif provider == EmbeddingProvider.HUGGING_FACE.value:
        return get_embedding_server_embeddings(
            model_name,
            trust_remote_code=bool(configuration.get("trustRemoteCode")),
        )

    elif provider == EmbeddingProvider.JINA_AI.value:
        from langchain_community.embeddings.jina import JinaEmbeddings
        return JinaEmbeddings(
            model_name=model_name,
            jina_api_key=configuration['apiKey'],
        )

    elif provider == EmbeddingProvider.MISTRAL.value:
        from langchain_mistralai import MistralAIEmbeddings

        mistral_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
        )
        _set_embedding_dimensions_kwarg(mistral_kwargs, dimensions)
        return MistralAIEmbeddings(**mistral_kwargs)


    elif provider == EmbeddingProvider.OLLAMA.value:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model_name,
            base_url=configuration['endpoint']
        )

    elif provider == EmbeddingProvider.OPENAI.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        openai_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
        )
        _set_embedding_dimensions_kwarg(openai_kwargs, dimensions)
        return OpenAIEmbeddings(**openai_kwargs)

    elif provider == EmbeddingProvider.AWS_BEDROCK.value:
        from langchain_aws import BedrockEmbeddings

        bedrock_client = _create_bedrock_client(configuration)
        return BedrockEmbeddings(
            model_id=model_name,
            client=bedrock_client,
            region_name=configuration.get("region"),
        )

    elif provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value:
        return get_embedding_server_embeddings(
            model_name,
            trust_remote_code=bool(configuration.get("trustRemoteCode")),
        )

    elif provider == EmbeddingProvider.OPENAI_COMPATIBLE.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        base_url = configuration['endpoint']
        compat_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=base_url,
            check_embedding_ctx_length=_accepts_token_array_embedding_input(base_url),
        )
        _set_embedding_dimensions_kwarg(compat_kwargs, dimensions)
        return OpenAIEmbeddings(**compat_kwargs)

    elif provider == EmbeddingProvider.OPENROUTER.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        or_emb_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=OPENROUTER_BASE_URL,
            check_embedding_ctx_length=_accepts_token_array_embedding_input(OPENROUTER_BASE_URL),
        )
        _set_embedding_dimensions_kwarg(or_emb_kwargs, dimensions)
        return OpenAIEmbeddings(**or_emb_kwargs)

    elif provider == EmbeddingProvider.LM_STUDIO.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        lms_emb_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration.get("apiKey") or "lm-studio",
            base_url=configuration["endpoint"],
            check_embedding_ctx_length=False,
        )
        _set_embedding_dimensions_kwarg(lms_emb_kwargs, dimensions)
        return OpenAIEmbeddings(**lms_emb_kwargs)

    elif provider == EmbeddingProvider.LITELLM_PROXY.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        llp_emb_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration.get("apiKey"),
            base_url=configuration["endpoint"],
            check_embedding_ctx_length=False,
        )
        _set_embedding_dimensions_kwarg(llp_emb_kwargs, dimensions)
        return OpenAIEmbeddings(**llp_emb_kwargs)

    elif provider == EmbeddingProvider.TOGETHER.value:
        from app.utils.custom_embeddings import TogetherEmbeddings

        together_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
        )
        _set_embedding_dimensions_kwarg(together_kwargs, dimensions)
        return TogetherEmbeddings(**together_kwargs)

    elif provider == EmbeddingProvider.VOYAGE.value:
        from app.utils.custom_embeddings import VoyageEmbeddings

        return VoyageEmbeddings(
            model=model_name,
            voyage_api_key=configuration['apiKey'],
        )

    elif provider == EmbeddingProvider.VERTEX_AI.value:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        sa_json = configuration.get("serviceAccountJson")
        if not sa_json:
            raise ValueError(
                "Vertex AI requires a service account JSON. "
                "Please upload a valid service account JSON key file."
            )
        project = configuration.get("project")
        if not project:
            raise ValueError(
                "Vertex AI requires a GCP Project ID. "
                "Please provide the Google Cloud project that hosts Vertex AI."
            )
        creds = _create_vertex_credentials(sa_json)
        vertex_emb_kwargs: Dict[str, Any] = dict(
            model=model_name,
            project=project,
            location=configuration.get("location") or "us-central1",
            credentials=creds,
        )
        _set_embedding_dimensions_kwarg(vertex_emb_kwargs, dimensions, key="output_dimensionality")
        return GoogleGenerativeAIEmbeddings(**vertex_emb_kwargs)

    raise ValueError(f"Unsupported embedding config type: {provider}")

# Bedrock/Anthropic snapshot suffixes (e.g. 20250219) are not x.y minors.
_CLAUDE_SNAPSHOT_DATE_MIN = 100

_CLAUDE_TIER_PATTERN = r"([a-z]+)"


def _claude_version_minor(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    if value >= _CLAUDE_SNAPSHOT_DATE_MIN:
        return None
    return value


def _parse_claude_version(
    model_name: str | None,
) -> tuple[str | None, int, int | None] | None:
    """Parse Claude tier/major/minor from API, Bedrock, and Vertex IDs.

    Handles both ``claude-sonnet-4-5`` and dated ``claude-3-7-sonnet-20250219``
    shapes. Snapshot dates are not treated as minor versions.
    """
    if not model_name:
        return None
    lowered = model_name.lower()
    if "claude" not in lowered:
        return None

    match = re.search(
        rf"claude[-_]?{_CLAUDE_TIER_PATTERN}[-_]?(\d+)(?:[-_.](\d+))?",
        lowered,
    )
    if match:
        return match.group(1), int(match.group(2)), _claude_version_minor(match.group(3))

    match = re.search(
        rf"claude[-_]?(\d+)(?:[-_.](\d+))?[-_]?{_CLAUDE_TIER_PATTERN}",
        lowered,
    )
    if match:
        return match.group(3), int(match.group(1)), _claude_version_minor(match.group(2))

    return None


def _get_anthropic_max_tokens(model_name: str) -> int:
    """Gets the max output tokens for an Anthropic model based on its name.

    Claude 3.7, Claude 4, and Claude 4.5 support 64K output tokens (GA
    thinking cap on 3.7). Claude 4.6+ and Claude 5.x use 16K. Legacy or
    unrecognised models fall back to 4096.
    """
    lowered = model_name.lower() if model_name else ""
    parsed = _parse_claude_version(model_name)
    if parsed is not None:
        _tier, major, minor = parsed
        if major >= 5:
            return MAX_OUTPUT_TOKENS_CLAUDE_MODERN
        if major == 4:
            if minor is not None and minor >= 6:
                return MAX_OUTPUT_TOKENS_CLAUDE_MODERN
            return MAX_OUTPUT_TOKENS_CLAUDE_4_5
        if major == 3 and minor is not None and minor >= 7:
            return MAX_OUTPUT_TOKENS_CLAUDE_4_5
    if "4.5" in lowered:
        return MAX_OUTPUT_TOKENS_CLAUDE_4_5
    return MAX_OUTPUT_TOKENS


def _anthropic_supports_sampling_params(model_name: str | None) -> bool:
    """Whether the given Claude model accepts ``temperature``/``top_p``/``top_k``.

    Anthropic removed sampling parameters starting with Claude Opus 4.7:
    any non-default value returns a 400 "deprecated for this model" error.
    The same direction is expected for future Claude families, so we also
    disable sampling params for any Claude major version >= 5.

    Matches model IDs like ``claude-opus-4-7``, ``claude-opus-4.7``,
    ``claude-sonnet-5``, and Bedrock/Vertex variants that embed the same
    version suffix.
    """
    if not model_name:
        return True

    lowered = model_name.lower()
    if "claude" not in lowered:
        return True

    parsed = _parse_claude_version(model_name)
    if parsed is None:
        return True

    tier, major, minor = parsed

    if major >= 5:
        return False

    if tier == "opus" and major == 4 and minor is not None and minor >= 7:
        return False

    return True

_OPENAI_EFFORT_MAP: Dict[str, str] = {
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "xhigh",
}

# xAI's Grok reasoning models (grok-4.3, grok-4.5) only document
# none/low/medium/high for `reasoning_effort` — there is no 'max' or 'xhigh'
# tier, so 'max' is clamped to 'high' instead of reusing OpenAI's map.
# See https://docs.x.ai/developers/model-capabilities/text/reasoning.
_XAI_EFFORT_MAP: Dict[str, str] = {
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

# MiniMax's OpenAI-compatible endpoint doesn't document a 'max' or 'xhigh'
# reasoning tier (its M2/M3 docs only mention low/medium/high, or
# minimal/low/medium/high/none for the newer Responses-style API), so 'max'
# is clamped to 'high' rather than sending OpenAI's 'xhigh'.
# See https://platform.minimax.io/docs/api-reference/text-openai-api.
_MINIMAX_EFFORT_MAP: Dict[str, str] = {
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

# Fireworks' `reasoning_effort` documents none/low/medium/high/max as the
# OpenAI-compatible string values it accepts — 'max' is a real supported
# tier there, unlike OpenAI where our platform's 'max' maps to 'xhigh', so
# Fireworks is left as an identity mapping instead of reusing OpenAI's map.
# See https://docs.fireworks.ai/api-reference/post-chatcompletions.
_FIREWORKS_EFFORT_MAP: Dict[str, str] = {
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "max",
}

_GEMINI_EFFORT_MAP: Dict[str, str] = {
    "none": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

_ANTHROPIC_EFFORT_MAP: Dict[str, str] = {
    "none": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "max",
}

# LM Studio's OpenAI-compatible `/v1/chat/completions` only documents
# low/medium/high for `reasoning_effort` (see lmstudio.ai/docs/developer/rest/chat
# and lmstudio-ai/lmstudio-bug-tracker#1250) — unlike OpenAI itself it has no
# 'none'/'xhigh' tier, so both ends of our platform range are clamped to the
# nearest value LM Studio actually accepts rather than reusing the OpenAI map.
_LM_STUDIO_EFFORT_MAP: Dict[str, str] = {
    "none": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

# Ollama's `think` field (surfaced by langchain-ollama's `reasoning` constructor
# kwarg) accepts `False` to disable thinking or one of "low"/"medium"/"high" to
# tune it — no "none" string (use `False`) and no "max" tier yet, so we clamp
# 'max' to 'high'. See https://docs.ollama.com/capabilities/thinking.
_OLLAMA_EFFORT_MAP: Dict[str, bool | str] = {
    "none": False,
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

# Providers that speak OpenAI's `reasoning_effort` string values as-is
# (none/low/medium/high/xhigh). OpenRouter and LiteLLM proxy are passthrough
# layers so they get the OpenAI value set; providers with their own
# documented reasoning tiers (Fireworks, MiniMax, XAI) use dedicated maps
# below instead, since they reject 'xhigh'.
_OPENAI_FAMILY = frozenset({
    LLMProvider.OPENAI.value,
    LLMProvider.AZURE_OPENAI.value,
    LLMProvider.AZURE_AI.value,
    LLMProvider.OPENAI_COMPATIBLE.value,
    LLMProvider.LITELLM_PROXY.value,
    LLMProvider.OPENROUTER.value,
})

_GEMINI_FAMILY = frozenset({
    LLMProvider.GEMINI.value,
    LLMProvider.VERTEX_AI.value,
})

# Direct OpenAI and Azure OpenAI reject `reasoning_effort` combined with
# function tools on /v1/chat/completions for the entire gpt-5.x family
# ("Function tools with reasoning_effort are not supported ... in
# /v1/chat/completions. To use function tools, use /v1/responses ...").
# These two providers must be routed through the Responses API instead.
# Third-party OpenAI-compatible providers (OpenRouter, LiteLLM proxy,
# Fireworks, MiniMax, XAI, LM Studio) don't expose /v1/responses, so they
# keep using `reasoning_effort` as-is — UNLESS they're proxying an actual
# OpenAI gpt-5.x model (see `_is_openai_gpt5_model` below), in which case
# they hit this same restriction and must also switch.
_RESPONSES_API_PROVIDERS = frozenset({
    LLMProvider.OPENAI.value,
    LLMProvider.AZURE_OPENAI.value,
})

# OpenAI's own API is also reachable through the generic OpenAI-compatible
# provider (endpoint set to https://api.openai.com/v1), which hits the same
# Chat Completions restriction, so the decision is endpoint-aware and not
# provider-only. Only OpenAI's official host is matched — a self-hosted or
# proxy endpoint that merely speaks the OpenAI protocol has no /v1/responses.
_OPENAI_RESPONSES_API_HOSTS = frozenset({"api.openai.com"})


def _targets_openai_responses_api(base_url: str | None) -> bool:
    if not base_url:
        return False
    host = urlparse(base_url).hostname
    return bool(host) and host.lower() in _OPENAI_RESPONSES_API_HOSTS


# LLM routers/gateways (Requesty, OpenRouter, LiteLLM proxy, and similar)
# commonly proxy requests for OpenAI's own "gpt-5.x" models straight through
# to OpenAI's real backend under an `openAICompatible`/`litellmProxy`/
# `openRouter` config entry, without the caller ever pointing `base_url` at
# `api.openai.com` directly. Those requests still hit the exact same Chat
# Completions restriction as the direct provider — and, since these routers
# exist specifically to be OpenAI-protocol compatible, they expose a
# matching `/v1/responses` endpoint at the same base URL for this reason
# (confirmed for Requesty and OpenRouter). Detecting by model name (rather
# than by host) is what makes this work regardless of which router/proxy
# sits in front of the real OpenAI model.
#
# o-series reasoning models (o3, o4-mini, ...) are intentionally excluded:
# they support `reasoning_effort` + bound tools on Chat Completions today
# (with a documented, non-fatal loss of cross-turn reasoning persistence),
# unlike gpt-5.x which hard-rejects the combination with a 400.
_OPENAI_GPT5_MODEL_PATTERN = re.compile(r"(?:^|/)gpt-5(?:\.\d+)?(?:[-_]|$)", re.IGNORECASE)

# gpt-5-chat / gpt-5.x-chat-latest are non-reasoning chat variants: they
# don't accept a `reasoning`/`reasoning_effort` value at all and must never
# be routed to the Responses API or forced onto temperature=1 for that
# reason — langchain-openai itself special-cases them the same way
# (`"chat" not in model_lower`, `validate_temperature`/
# `_construct_responses_api_payload` in langchain_openai's chat_models/base.py).
_OPENAI_GPT5_CHAT_PATTERN = re.compile(r"(?:^|/)gpt-5(?:\.\d+)?-chat(?:[-_]|$)", re.IGNORECASE)


def _is_openai_gpt5_model(model_name: str | None) -> bool:
    if not model_name:
        return False
    if _OPENAI_GPT5_CHAT_PATTERN.search(model_name):
        return False
    return bool(_OPENAI_GPT5_MODEL_PATTERN.search(model_name))


def _default_temperature(
    configuration: dict[str, Any], config: dict[str, Any], model_name: str | None,
    *, provider: str = "",
) -> float:
    """Default ``temperature`` for a provider's constructor kwargs.

    OpenAI gpt-5.x (and o-series) reasoning models only accept
    ``temperature=1`` (any other value 400s on both Chat Completions and the
    Responses API) — this is the one check shared verbatim by every provider
    branch below that constructs a LangChain client, so it lives here once
    instead of being copy-pasted per branch (it previously was, with a plain
    ``"gpt-5" in model_name`` substring check that also mis-fired on
    ``gpt-5-chat-latest``, which has no such restriction).

    ``config["isReasoning"]`` alone is deliberately NOT enough to force
    ``temperature=1`` — it's a platform flag meaning "this model reasons",
    not "this model is OpenAI's own and has OpenAI's specific sampling
    restriction". Providers/gateways such as Azure AI Foundry, LM Studio,
    LiteLLM proxy, and OpenRouter route ``isReasoning=True`` requests to
    plenty of non-OpenAI reasoning models (Llama, Mistral, DeepSeek, Qwen,
    Gemini, ...) that have no such restriction and whose configured
    temperature should be respected. It's only trusted as a fallback signal
    (alongside ``_is_openai_gpt5_model``, which can miss a custom deployment
    alias that doesn't literally contain "gpt-5", or an o-series model,
    which that name check doesn't match at all) when ``provider`` guarantees
    the backend actually is OpenAI's own API (``_RESPONSES_API_PROVIDERS``:
    direct OpenAI or Azure OpenAI).
    """
    is_reasoning_model = _is_openai_gpt5_model(model_name) or (
        provider in _RESPONSES_API_PROVIDERS and bool(config.get("isReasoning", False))
    )
    return 1 if is_reasoning_model else configuration.get("temperature", 0.2)


def _reasoning_effort_kwargs(
    reasoning_effort: str | None,
    config: dict[str, Any],
    *,
    provider: str = "",
    base_url: str | None = None,
    model_name: str | None = None,
    api_mode: str | None = None,
) -> Dict[str, Any]:
    """Build the constructor kwarg for LangChain's standard ``reasoning_effort``
    parameter (langchain-core>=1.5.2), gated on the model being flagged as
    reasoning-capable in its configuration.

    Returns an empty dict when the model isn't reasoning-capable — passing the
    kwarg to a non-reasoning model integration raises ``UnsupportedParamsError``
    in LangChain, so callers must never pass it unconditionally.

    When ``reasoning_effort`` is absent (no explicit user choice and no agent
    default), a reasoning-capable model defaults to ``DEFAULT_REASONING_EFFORT``
    ("high") rather than silently omitting the parameter and letting each
    provider fall back to its own default — those vary per provider/model and
    are often a lower, cheaper tier than a user picking a "reasoning" model
    would expect.

    An explicit ``"none"`` is silently floored to ``REASONING_MANDATORY_FALLBACK_EFFORT``
    ("low") rather than actually disabling reasoning. Fully disabling reasoning
    makes some models prone to hallucinating malformed/oversized tool-call
    names once reasoning stops constraining their output (see
    ``app/agent_loop_lib/tools/executor.py::_collapse_repeated_name`` and
    ``app/agents/agent_loop/converters.py::_clamp_tool_call_name`` for the
    defenses against that once it happens anyway). The frontend no longer
    offers "none" as a choice (see ``REASONING_EFFORT_OPTIONS`` in
    ``model-selector-panel.tsx``); this floor is what makes it safe to still
    accept "none" on the wire from already-persisted conversations/agents
    instead of rejecting them.

    Provider-specific translation is applied because each provider accepts
    different value sets:
    - OpenAI/Azure/OpenRouter/LiteLLM proxy: none, low, medium, high, xhigh
      (no 'max' on older models)
    - Fireworks: none, low, medium, high, max (no 'xhigh')
    - MiniMax, XAI: none, low, medium, high (no 'max' or 'xhigh'; 'max' is
      clamped to 'high')
    - Gemini: minimal, low, medium, high (no 'none' or 'max')
    - Anthropic: low, medium, high, xhigh, max (no 'none')
    - LM Studio: low, medium, high (no 'none' or 'max')
    - Ollama: False, low, medium, high (no 'none' string or 'max')

    Requests that reach an actual OpenAI gpt-5.x model with reasoning
    genuinely turned on additionally route through the Responses API
    (``reasoning`` + ``use_responses_api=True``) rather than the legacy
    ``reasoning_effort`` kwarg, because Chat Completions rejects reasoning
    effort combined with bound tools for that model family. This is detected
    two ways, since the model can be reached several ways:
    - Provider is direct OpenAI or Azure OpenAI (``_RESPONSES_API_PROVIDERS``).
    - The configured ``base_url`` is OpenAI's own host, or ``model_name``
      itself matches the ``gpt-5.x`` family — the latter covers routers/
      gateways (Requesty, OpenRouter, LiteLLM proxy, a generic
      OpenAI-compatible entry, ...) that transparently forward to OpenAI's
      real backend under a different host.
    The post-mapping ``effort != "none"`` guard on this switch is now purely
    defensive: since ``"none"`` is floored to ``REASONING_MANDATORY_FALLBACK_EFFORT``
    up front (see above), no provider map below ever actually produces
    ``"none"`` as its output. It's kept because OpenAI's own error for this
    restriction states Chat Completions works fine with reasoning effort set
    to ``"none"`` even with bound tools — if a future caller legitimately
    needs that combination again, the guard is already in place to keep
    reasoning-off on the plain ``reasoning_effort`` kwarg for every provider
    rather than routing it through the newer Responses API code path.

    The Responses API ``reasoning`` dict is deliberately kept to just
    ``{"effort": ...}`` — no ``"summary"`` key. Requesting a summary needs
    the caller's OpenAI organization to be Verified and 400s otherwise, and
    some Azure OpenAI / gateway Responses API implementations reject the
    field outright even when verified upstream, so it's opt-in territory we
    don't control from here rather than something safe to default to.

    Ollama is a special case: LangChain's ``ChatOllama`` doesn't expose a
    ``reasoning_effort`` constructor kwarg at all — it uses ``reasoning``
    (bool | str), which maps onto Ollama's native ``think`` field rather than
    the OpenAI-style ``reasoning_effort`` body parameter. Callers must read
    the ``"reasoning"`` key from the returned dict for this provider instead.

    ``api_mode`` overrides the heuristic above with a fact *learned* at
    runtime by ``LangChainTransport`` from an actual provider rejection
    (see ``app/utils/llm_api_mode_store.py``), rather than guessed from the
    model name / base_url:
    - ``LLMApiMode.NO_REASONING_WITH_TOOLS``: this model's provider/gateway
      rejects reasoning together with bound tools on *both* API shapes —
      skip reasoning entirely (identical to ``isReasoning`` being off).
    - ``LLMApiMode.RESPONSES``: this model needs the Responses API even
      though the name/host heuristic didn't detect it — take that branch
      whenever ``provider in _OPENAI_FAMILY`` (still gated on
      ``effort != "none"`` like the heuristic path, since reasoning-off
      never needs it). Gated on the provider family, not applied
      unconditionally, because ``api_mode`` is looked up only by
      ``(model_key, model_name)`` with no provider check — an unrelated
      provider reusing the same pair (e.g. after reconfiguring a model
      entry) must not have ``use_responses_api``/``reasoning`` (ChatOpenAI-
      only kwargs) forced onto a non-OpenAI-family constructor like
      ``ChatAnthropic`` or ``ChatGoogleGenerativeAI``.
    - ``LLMApiMode.REASONING_MANDATORY``: this model/gateway rejects an
      explicit ``"none"`` effort outright (some OpenRouter-proxied Gemini
      models: "Reasoning is mandatory for this endpoint and cannot be
      disabled."). In practice this is now a defensive no-op for calls that
      go through this function, since ``"none"`` is already floored to
      ``REASONING_MANDATORY_FALLBACK_EFFORT`` up front regardless of
      ``api_mode`` — it's kept in case something outside this function ever
      hands a model an unfloored ``"none"``.
    """
    if not config.get("isReasoning"):
        return {}

    if api_mode == LLMApiMode.NO_REASONING_WITH_TOOLS.value:
        return {}

    effort_input = reasoning_effort or DEFAULT_REASONING_EFFORT
    if effort_input == "none":
        # "none" is no longer offered as a UI choice (see the docstring
        # above) — floor it unconditionally rather than only when a
        # REASONING_MANDATORY fact happens to be learned for this model.
        effort_input = REASONING_MANDATORY_FALLBACK_EFFORT

    if provider == LLMProvider.OLLAMA.value:
        return {"reasoning": _OLLAMA_EFFORT_MAP.get(effort_input, effort_input)}

    effort = effort_input
    if provider in _OPENAI_FAMILY:
        effort = _OPENAI_EFFORT_MAP.get(effort_input, effort_input)
    elif provider == LLMProvider.FIREWORKS.value:
        effort = _FIREWORKS_EFFORT_MAP.get(effort_input, effort_input)
    elif provider == LLMProvider.MINIMAX.value:
        effort = _MINIMAX_EFFORT_MAP.get(effort_input, effort_input)
    elif provider == LLMProvider.XAI.value:
        effort = _XAI_EFFORT_MAP.get(effort_input, effort_input)
    elif provider == LLMProvider.LM_STUDIO.value:
        effort = _LM_STUDIO_EFFORT_MAP.get(effort_input, effort_input)
    elif provider in _GEMINI_FAMILY:
        effort = _GEMINI_EFFORT_MAP.get(effort_input, effort_input)
    elif provider == LLMProvider.ANTHROPIC.value:
        effort = _ANTHROPIC_EFFORT_MAP.get(effort_input, effort_input)

    needs_responses_api = effort != "none" and (
        provider in _RESPONSES_API_PROVIDERS
        or (
            provider in _OPENAI_FAMILY
            and (
                api_mode == LLMApiMode.RESPONSES.value
                or _targets_openai_responses_api(base_url)
                or _is_openai_gpt5_model(model_name)
            )
        )
    )
    if needs_responses_api:
        # No `"summary"` key: requesting a reasoning summary requires the
        # caller's OpenAI organization to be Verified (platform.openai.com
        # settings) and 400s otherwise ("Your organization must be verified
        # to generate reasoning summaries..."), and some Azure OpenAI /
        # gateway (Requesty et al.) Responses API implementations reject the
        # field outright ("Unknown parameter: 'reasoning_summary'.") even
        # when verified upstream. Reasoning effort/depth still fully applies
        # without it — only the optional textual reasoning-summary blocks
        # LangChain would otherwise surface via `content_blocks` are absent,
        # same as every other provider that doesn't emit reasoning text.
        return {
            "reasoning": {"effort": effort},
            "use_responses_api": True,
        }

    return {"reasoning_effort": effort}


# Bedrock Converse: gpt-oss only accepts low/medium/high for
# additionalModelRequestFields.reasoning_effort (no none/max/xhigh).
# GPT-5.6 Sol/Terra/Luna accept a wider set (including max); do not clamp.
_BEDROCK_GPT_OSS_EFFORT_MAP: Dict[str, str] = {
    "none": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}
_BEDROCK_OPENAI_EFFORT_MAP = _BEDROCK_GPT_OSS_EFFORT_MAP

# Same gpt-5 family as ``_OPENAI_GPT5_MODEL_PATTERN``, but Bedrock IDs are
# dot-delimited (``us.openai.gpt-5.6-luna``) so the prefix must also allow
# ``.``. A substring ``gpt[-_.]?5`` would false-positive ``gpt-50`` and
# mid-token names like ``my-gpt-5-compatible``.
_GPT5_MODEL_RE = re.compile(r"(?:^|[./])gpt-5(?:\.\d+)?(?:[-_]|$)", re.IGNORECASE)

# Nova 2 maxReasoningEffort is low|medium|high only.
_BEDROCK_NOVA_EFFORT_MAP: Dict[str, str] = {
    "none": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}

# LiteLLM-style budget_tokens map for Claude manual extended thinking on Bedrock.
# Bedrock rejects budget_tokens < 1024.
_BEDROCK_ANTHROPIC_THINKING_BUDGETS: Dict[str, int] = {
    "low": 1024,
    "medium": 2048,
    "high": 4096,
    "max": 4096,
}

_BEDROCK_MIN_THINKING_BUDGET_TOKENS = 1024
# Visible-reply headroom so budget_tokens stays strictly below max_tokens.
_BEDROCK_THINKING_OUTPUT_RESERVE_TOKENS = 1024


def _bedrock_is_nova_2(model_name: str | None) -> bool:
    """True for Nova 2 ids such as ``amazon.nova-2-lite-v1:0`` and ``us.amazon.nova-2-lite-v1:0``."""
    if not model_name:
        return False
    return "nova-2" in model_name.lower()


def _bedrock_is_gpt_oss(model_name: str | None) -> bool:
    return bool(model_name) and "gpt-oss" in model_name.lower()


def _bedrock_is_openai_gpt5(model_name: str | None) -> bool:
    """GPT-5.x on Bedrock Converse (Sol/Terra/Luna), not gpt-oss."""
    if not model_name:
        return False
    lowered = model_name.lower()
    if "gpt-oss" in lowered:
        return False
    return bool(_GPT5_MODEL_RE.search(lowered))


def _bedrock_is_openai(provider: str, model_name: str | None) -> bool:
    if (provider or "").lower() == "openai":
        return True
    if not model_name:
        return False
    lowered = model_name.lower()
    return (
        "openai" in lowered
        or "gpt-oss" in lowered
        or bool(_GPT5_MODEL_RE.search(lowered))
    )


def _bedrock_is_deepseek_r1(model_name: str | None) -> bool:
    if not model_name:
        return False
    lowered = model_name.lower()
    return "deepseek" in lowered and "r1" in lowered


def _bedrock_anthropic_uses_adaptive_thinking(model_name: str | None) -> bool:
    """Claude models that reject budget_tokens and require thinking.type=adaptive.

    Adaptive thinking is required for Claude 4.6+, Opus 4.7+, and Claude 5
    families (including Fable/Mythos). Manual ``enabled`` + budget_tokens
    returns 400 on those models.
    """
    if not model_name:
        return False

    lowered = model_name.lower()
    if "claude" not in lowered:
        return False

    if any(token in lowered for token in ("fable", "mythos")):
        return True

    parsed = _parse_claude_version(model_name)
    if parsed is not None:
        _tier, major, minor = parsed
        if major >= 5:
            return True
        if major == 4 and minor is not None and minor >= 6:
            return True
        return False

    # IDs like ``claude-4-6`` with no tier token.
    return bool(re.search(r"claude.*4[-_.]([67]|[6-9]\d)", lowered))


def _resolve_bedrock_effort_input(reasoning_effort: str | None) -> str:
    """Normalize UI effort for Bedrock, flooring explicit ``none`` to low.

    Matches the platform-wide policy in ``_reasoning_effort_kwargs``: ``none``
    is no longer offered in the UI and must not fully disable reasoning when
    the model is flagged ``isReasoning``.
    """
    effort_input = reasoning_effort or DEFAULT_REASONING_EFFORT
    if effort_input == "none":
        return REASONING_MANDATORY_FALLBACK_EFFORT
    return effort_input


def _bedrock_additional_model_request_fields(
    reasoning_effort: str | None,
    config: dict[str, Any],
    *,
    provider_in_bedrock: str,
    model_name: str | None,
) -> Dict[str, Any]:
    """Build Converse ``additional_model_request_fields`` for Bedrock reasoning.

    Provider shapes differ (OpenAI ``reasoning_effort``, Anthropic ``thinking``,
    Nova ``reasoningConfig``). DeepSeek R1 always reasons and rejects any
    reasoning request fields.
    """
    if not config.get("isReasoning"):
        return {}

    if _bedrock_is_deepseek_r1(model_name):
        return {}

    effort_input = _resolve_bedrock_effort_input(reasoning_effort)
    provider = (provider_in_bedrock or "").lower()

    if _bedrock_is_openai(provider, model_name):
        if _bedrock_is_gpt_oss(model_name):
            effort = _BEDROCK_GPT_OSS_EFFORT_MAP.get(effort_input, effort_input)
        else:
            # GPT-5.6 (and future gpt-5* Converse models): pass UI effort
            # through. ``none`` is already floored to low above.
            effort = effort_input
        return {"reasoning_effort": effort}

    if provider == LLMProvider.ANTHROPIC.value or (
        model_name and ("claude" in model_name.lower() or "anthropic" in model_name.lower())
    ):
        if _bedrock_anthropic_uses_adaptive_thinking(model_name):
            effort = _ANTHROPIC_EFFORT_MAP.get(effort_input, effort_input)
            return {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": effort},
            }
        budget = _BEDROCK_ANTHROPIC_THINKING_BUDGETS.get(
            effort_input, _BEDROCK_ANTHROPIC_THINKING_BUDGETS["high"]
        )
        budget = max(budget, _BEDROCK_MIN_THINKING_BUDGET_TOKENS)
        return {
            "thinking": {
                "type": "enabled",
                "budget_tokens": budget,
            },
        }

    if _bedrock_is_nova_2(model_name):
        effort = _BEDROCK_NOVA_EFFORT_MAP.get(effort_input, effort_input)
        return {
            "reasoningConfig": {
                "type": "enabled",
                "maxReasoningEffort": effort,
            },
        }

    return {}


def _bedrock_max_tokens_for_thinking(
    model_name: str | None,
    thinking: dict[str, Any] | None,
) -> int:
    """max_tokens for Bedrock Converse, with budget_tokens < max_tokens.

    AWS and Anthropic require ``1024 <= budget_tokens < max_tokens`` when
    ``thinking.type`` is ``enabled``. Unrecognised Claude IDs fall back to
    4096, which collides with the high/max thinking budgets, so those
    requests are lifted to at least the modern Claude output floor.
    """
    max_tokens = _get_anthropic_max_tokens(model_name or "")
    if not isinstance(thinking, dict) or thinking.get("type") != "enabled":
        return max_tokens
    budget = thinking.get("budget_tokens")
    if not isinstance(budget, int):
        return max_tokens
    if max_tokens > budget:
        return max_tokens
    return max(
        MAX_OUTPUT_TOKENS_CLAUDE_MODERN,
        budget + _BEDROCK_THINKING_OUTPUT_RESERVE_TOKENS,
    )


def _bedrock_temperature(
    configuration: dict[str, Any],
    *,
    provider_in_bedrock: str,
    model_name: str | None,
    additional_fields: dict[str, Any],
) -> float | None:
    """Return Converse temperature, or ``None`` when the param must be omitted.

    Rules (AWS / Anthropic / Nova / OpenAI):
    - Claude models that dropped sampling params → omit.
    - Claude with thinking / adaptive thinking enabled → omit (non-1 values 400).
    - Nova 2 with ``maxReasoningEffort=high`` → omit (also forbids maxTokens).
    - GPT-5.x on Bedrock typically rejects temperature → omit.
    - Otherwise use configured temperature (default 0.2). Never force 1 for
      Bedrock OpenAI gpt-oss (unlike direct OpenAI gpt-5.x).
    """
    provider = (provider_in_bedrock or "").lower()
    is_anthropic = provider == LLMProvider.ANTHROPIC.value or (
        model_name is not None
        and ("claude" in model_name.lower() or "anthropic" in model_name.lower())
    )

    if _bedrock_is_openai_gpt5(model_name):
        return None

    if is_anthropic and not _anthropic_supports_sampling_params(model_name):
        return None

    thinking = additional_fields.get("thinking")
    if is_anthropic and isinstance(thinking, dict) and thinking.get("type") in (
        "enabled",
        "adaptive",
    ):
        return None

    reasoning_config = additional_fields.get("reasoningConfig")
    if (
        isinstance(reasoning_config, dict)
        and reasoning_config.get("type") == "enabled"
        and reasoning_config.get("maxReasoningEffort") == "high"
    ):
        return None

    return configuration.get("temperature", 0.2)


def _detect_bedrock_provider(model_name: str | None) -> str:
    """Infer the Bedrock foundation-model provider from a model id."""
    if not model_name:
        return LLMProvider.ANTHROPIC.value

    lowered = model_name.lower()
    if "mistral" in lowered:
        return LLMProvider.MISTRAL.value
    if "claude" in lowered or "anthropic" in lowered:
        return LLMProvider.ANTHROPIC.value
    if "openai" in lowered or "gpt-oss" in lowered or _GPT5_MODEL_RE.search(lowered):
        return "openai"
    if "llama" in lowered or "meta" in lowered:
        return "meta"
    if "deepseek" in lowered:
        return "deepseek"
    if "titan" in lowered or "amazon" in lowered or "nova" in lowered:
        return "amazon"
    if "cohere" in lowered:
        return "cohere"
    if "ai21" in lowered or "jamba" in lowered:
        return "ai21"
    if "qwen" in lowered:
        return "qwen"
    return LLMProvider.ANTHROPIC.value


def get_generator_model(
    provider: str,
    config: dict[str, Any],
    model_name: str | None = None,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    configuration = config['configuration']
    is_default = config.get("isDefault")
    configured_model = configuration.get("model")
    if not configured_model:
        raise ValueError(f"Provider '{provider}' configuration is missing a 'model' name.")
    model_names = [name.strip() for name in configured_model.split(",") if name.strip()]
    if not model_names:
        raise ValueError(f"Provider '{provider}' configuration has an empty 'model' field.")

    if model_name is None:
        model_name = model_names[0]
    elif not is_default and model_name not in model_names:
        raise ValueError(f"Model name {model_name} not found in {configured_model}")

    # Resolved once per call from the process-wide learned-facts snapshot
    # (see `app/utils/llm_api_mode_store.py`) and threaded explicitly into
    # every `_reasoning_effort_kwargs()` call below, so that function stays
    # a pure, synchronously-testable computation with no knowledge of the
    # store. `None` (store not yet loaded, or nothing learned for this
    # model) falls through to today's name/host heuristic unchanged.
    api_mode_store = get_llm_api_mode_store()
    api_mode = api_mode_store.get(config.get("modelKey"), model_name) if api_mode_store else None

    logger.info(
        f"Getting generator model: provider={provider}, model_name={model_name}, "
        f"reasoning_effort={reasoning_effort}, api_mode={api_mode}"
    )

    DEFAULT_LLM_TIMEOUT = 360.0
    if provider == LLMProvider.ANTHROPIC.value:
        from langchain_anthropic import ChatAnthropic

        max_tokens = _get_anthropic_max_tokens(model_name)
        anthropic_kwargs: Dict[str, Any] = dict(
            model=model_name,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            max_retries=2,
            api_key=configuration["apiKey"],
            max_tokens=max_tokens,
        )
        if _anthropic_supports_sampling_params(model_name):
            anthropic_kwargs["temperature"] = 0.2
        anthropic_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatAnthropic(**anthropic_kwargs)

    elif provider == LLMProvider.AWS_BEDROCK.value:
        from langchain_aws import ChatBedrockConverse

        # Determine the actual provider based on model name if not explicitly set
        provider_in_bedrock = configuration.get("provider")

        # Handle custom provider (when user selects "other")
        if provider_in_bedrock == "other":
            custom_provider = configuration.get("customProvider")
            if custom_provider:
                provider_in_bedrock = custom_provider
                logger.info(f"Using custom provider: {provider_in_bedrock}")
            else:
                # Fall back to auto-detection if custom provider is not provided
                provider_in_bedrock = None

        if not provider_in_bedrock:
            provider_in_bedrock = _detect_bedrock_provider(model_name)

        logger.info(f"Provider in Bedrock: {provider_in_bedrock} for model: {model_name}")

        additional_fields = _bedrock_additional_model_request_fields(
            reasoning_effort,
            config,
            provider_in_bedrock=provider_in_bedrock,
            model_name=model_name,
        )
        temperature = _bedrock_temperature(
            configuration,
            provider_in_bedrock=provider_in_bedrock,
            model_name=model_name,
            additional_fields=additional_fields,
        )

        bedrock_client = _create_bedrock_client(configuration)

        bedrock_kwargs: Dict[str, Any] = dict(
            model_id=model_name,
            client=bedrock_client,
            region_name=configuration.get("region"),
            provider=provider_in_bedrock,
        )
        if additional_fields:
            bedrock_kwargs["additional_model_request_fields"] = additional_fields

        # Anthropic needs an explicit max_tokens on Converse. Any path that
        # emits a thinking block must set it too (budget_tokens < max_tokens).
        # Nova 2 with maxReasoningEffort=high forbids maxTokens — skip then.
        thinking = additional_fields.get("thinking")
        nova_high = (
            isinstance(additional_fields.get("reasoningConfig"), dict)
            and additional_fields["reasoningConfig"].get("maxReasoningEffort") == "high"
        )
        if not nova_high and (
            isinstance(thinking, dict)
            or provider_in_bedrock == LLMProvider.ANTHROPIC.value
        ):
            bedrock_kwargs["max_tokens"] = _bedrock_max_tokens_for_thinking(
                model_name, thinking if isinstance(thinking, dict) else None
            )

        if temperature is not None:
            bedrock_kwargs["temperature"] = temperature

        return ChatBedrockConverse(**bedrock_kwargs)
    elif provider == LLMProvider.AZURE_AI.value:
        from langchain_anthropic import ChatAnthropic
        from langchain_openai import ChatOpenAI

        temperature = _default_temperature(configuration, config, model_name, provider=provider)

        is_claude_model = "claude" in model_name
        if is_claude_model:
            max_tokens = _get_anthropic_max_tokens(model_name)
            azure_claude_kwargs: Dict[str, Any] = dict(
                model=model_name,
                base_url=configuration.get("endpoint"),
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration.get("apiKey"),
                max_tokens=configuration.get("maxTokens", max_tokens),
            )
            if _anthropic_supports_sampling_params(model_name):
                azure_claude_kwargs["temperature"] = temperature
            azure_claude_kwargs.update(
                _reasoning_effort_kwargs(
                    reasoning_effort, config, provider=LLMProvider.ANTHROPIC.value,
                    model_name=model_name, api_mode=api_mode,
                )
            )
            return ChatAnthropic(**azure_claude_kwargs)
        else:
            azure_ai_openai_kwargs: Dict[str, Any] = dict(
                model=model_name,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration.get("apiKey"),
                base_url=configuration.get("endpoint"),
                stream_usage=True,  # Enable token usage tracking for Opik
            )
            # `provider` here (not `LLMProvider.OPENAI.value`) matters: Azure
            # AI Foundry also hosts non-OpenAI models (Llama, Mistral,
            # DeepSeek, ...) under `isReasoning=True`, and those have no
            # `/v1/responses` endpoint at all. Passing the real `azureAI`
            # provider keeps it in `_OPENAI_FAMILY` (so effort-value mapping
            # is unchanged) while taking it out of the unconditional
            # `_RESPONSES_API_PROVIDERS` set — only an actual gpt-5.x model
            # name or an `api.openai.com` base_url still routes it through
            # the Responses API.
            azure_ai_openai_kwargs.update(
                _reasoning_effort_kwargs(
                    reasoning_effort, config, provider=provider,
                    base_url=configuration.get("endpoint"), model_name=model_name, api_mode=api_mode,
                )
            )
            return ChatOpenAI(**azure_ai_openai_kwargs)

    elif provider == LLMProvider.AZURE_OPENAI.value:
        from langchain_openai import AzureChatOpenAI

        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        azure_openai_kwargs: Dict[str, Any] = dict(
            api_key=configuration["apiKey"],
            azure_endpoint=configuration["endpoint"],
            api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            azure_deployment=configuration["deploymentName"],
            stream_usage=True,
        )
        azure_openai_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return AzureChatOpenAI(**azure_openai_kwargs)

    elif provider == LLMProvider.COHERE.value:
        from langchain_cohere import ChatCohere
        return ChatCohere(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                cohere_api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.FIREWORKS.value:
        from langchain_fireworks import ChatFireworks

        fireworks_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=0.2,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            api_key=configuration["apiKey"],
        )
        fireworks_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatFireworks(**fireworks_kwargs)

    elif provider == LLMProvider.GEMINI.value:
        from langchain_google_genai import ChatGoogleGenerativeAI

        gemini_llm_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=0.2,
            max_tokens=None,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            max_retries=2,
            google_api_key=configuration["apiKey"],
        )
        gemini_llm_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatGoogleGenerativeAI(**gemini_llm_kwargs)

    elif provider == LLMProvider.GROQ.value:
        from langchain_groq import ChatGroq

        return ChatGroq(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
            )

    elif provider == LLMProvider.MINIMAX.value:
        from langchain_openai import ChatOpenAI

        # MiniMax temperature must be in (0.0, 1.0]
        temperature = max(0.01, min(1.0, configuration.get("temperature", 0.2)))
        minimax_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,
            api_key=configuration["apiKey"],
            base_url="https://api.minimax.io/v1",
            stream_usage=True,
        )
        minimax_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatOpenAI(**minimax_kwargs)

    elif provider == LLMProvider.MISTRAL.value:
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
            )

    elif provider == LLMProvider.OLLAMA.value:
        from langchain_ollama import ChatOllama

        context_length = config.get("contextLength")
        # Non-reasoning models keep `reasoning=False` (the historical default)
        # so <think> tags never leak into their output; reasoning-capable
        # models get Ollama's native `think` level via `_reasoning_effort_kwargs`.
        reasoning_kwarg = _reasoning_effort_kwargs(
            reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
        )
        ollama_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=configuration.get("temperature", 0.2),
            timeout=DEFAULT_LLM_TIMEOUT,
            base_url=configuration.get('endpoint', os.getenv("OLLAMA_API_URL", "http://localhost:11434")),
            reasoning=reasoning_kwarg.get("reasoning", False),
            # repeat_penalty=1.0 prevents Ollama's default >1.0 from corrupting
            # tool-call JSON (legitimate calls repeat tokens intentionally).
            repeat_penalty=1.0,
            top_p=1.0,
        )
        if context_length:
            ollama_kwargs["num_ctx"] = int(context_length)
        return ChatOllama(**ollama_kwargs)

    elif provider == LLMProvider.OPENAI.value:
        from langchain_openai import ChatOpenAI

        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        openai_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
            stream_usage=True,  # Enable token usage tracking for Opik
        )
        openai_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatOpenAI(**openai_kwargs)

    elif provider == LLMProvider.XAI.value:
        from langchain_xai import ChatXAI

        xai_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=0.2,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            api_key=configuration["apiKey"],
        )
        xai_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatXAI(**xai_kwargs)

    elif provider == LLMProvider.TOGETHER.value:
        from app.utils.custom_chat_model import ChatTogether

        return ChatTogether(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
            )

    elif provider == LLMProvider.OPENAI_COMPATIBLE.value:
        from langchain_openai import ChatOpenAI
        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        openai_compat_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
            api_key=configuration["apiKey"],
            base_url=configuration["endpoint"],
            stream_usage=True,  # Enable token usage tracking for Opik
        )
        openai_compat_kwargs.update(_reasoning_effort_kwargs(
            reasoning_effort, config, provider=provider,
            base_url=configuration["endpoint"], model_name=model_name, api_mode=api_mode,
        ))
        return ChatOpenAI(**openai_compat_kwargs)

    elif provider == LLMProvider.LM_STUDIO.value:
        from langchain_openai import ChatOpenAI
        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        lm_studio_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,
            api_key=configuration.get("apiKey") or "lm-studio",
            base_url=configuration["endpoint"],
            stream_usage=True,
        )
        lm_studio_kwargs.update(_reasoning_effort_kwargs(
            reasoning_effort, config, provider=provider,
            base_url=configuration["endpoint"], model_name=model_name, api_mode=api_mode,
        ))
        return ChatOpenAI(**lm_studio_kwargs)

    elif provider == LLMProvider.LITELLM_PROXY.value:
        from langchain_openai import ChatOpenAI
        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        litellm_proxy_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,
            api_key=configuration.get("apiKey"),
            base_url=configuration["endpoint"],
            stream_usage=True,
        )
        litellm_proxy_kwargs.update(_reasoning_effort_kwargs(
            reasoning_effort, config, provider=provider,
            base_url=configuration["endpoint"], model_name=model_name, api_mode=api_mode,
        ))
        return ChatOpenAI(**litellm_proxy_kwargs)

    elif provider == LLMProvider.OPENROUTER.value:
        from langchain_openai import ChatOpenAI

        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        openrouter_kwargs: Dict[str, Any] = dict(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_LLM_TIMEOUT,
            api_key=configuration["apiKey"],
            base_url=OPENROUTER_BASE_URL,
            stream_usage=True,
        )
        openrouter_kwargs.update(_reasoning_effort_kwargs(
            reasoning_effort, config, provider=provider,
            base_url=OPENROUTER_BASE_URL, model_name=model_name, api_mode=api_mode,
        ))
        return ChatOpenAI(**openrouter_kwargs)

    elif provider == LLMProvider.VERTEX_AI.value:
        from langchain_google_genai import ChatGoogleGenerativeAI

        sa_json = configuration.get("serviceAccountJson")
        if not sa_json:
            raise ValueError(
                "Vertex AI requires a service account JSON. "
                "Please upload a valid service account JSON key file."
            )
        project = configuration.get("project")
        if not project:
            raise ValueError(
                "Vertex AI requires a GCP Project ID. "
                "Please provide the Google Cloud project that hosts Vertex AI."
            )
        creds = _create_vertex_credentials(sa_json)
        temperature = _default_temperature(configuration, config, model_name, provider=provider)
        vertex_llm_kwargs: Dict[str, Any] = dict(
            model=model_name,
            project=project,
            location=configuration.get("location") or "us-central1",
            credentials=creds,
            temperature=temperature,
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=DEFAULT_LLM_TIMEOUT,
            max_retries=2,
        )
        vertex_llm_kwargs.update(
            _reasoning_effort_kwargs(
                reasoning_effort, config, provider=provider, model_name=model_name, api_mode=api_mode,
            )
        )
        return ChatGoogleGenerativeAI(**vertex_llm_kwargs)

    raise ValueError(f"Unsupported provider type: {provider}")


async def get_generator_model_async(
    provider: str,
    config: dict[str, Any],
    model_name: str | None = None,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    """Async-safe wrapper around :func:`get_generator_model`.

    Most providers construct their LangChain model object with no I/O, so they
    run inline.  AWS Bedrock is the exception: ``_create_bedrock_client`` may
    hit the EC2 IMDS or ECS task-role metadata endpoint to resolve credentials
    when no explicit keys are supplied, which would block the event loop.
    """
    if provider == LLMProvider.AWS_BEDROCK.value:
        return await asyncio.to_thread(
            get_generator_model, provider, config, model_name, reasoning_effort
        )
    return get_generator_model(provider, config, model_name, reasoning_effort)


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------


# OpenAI Images API supports a restricted size vocabulary. Requests with any
# other value return HTTP 400. We map commonly-requested sizes to the nearest
# supported one.
_OPENAI_SUPPORTED_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}

# Map "WxH" → Imagen aspect_ratio enum values.
_IMAGEN_ASPECT_RATIOS = {
    "1024x1024": "1:1",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
}


def _normalize_openai_size(size: str) -> str:
    if size in _OPENAI_SUPPORTED_SIZES:
        return size
    # Map the common portrait/landscape aliases used by our tool schema onto
    # the supported OpenAI sizes.
    aliases = {
        "1024x1792": "1024x1536",
        "1792x1024": "1536x1024",
    }
    return aliases.get(size, "1024x1024")


def _size_to_aspect_ratio(size: str) -> str:
    return _IMAGEN_ASPECT_RATIOS.get(size, "1:1")


class ImageGenerationAdapter:
    """Thin wrapper around a provider SDK that returns raw PNG bytes.

    Concrete subclasses implement :meth:`generate` and, where the provider
    supports it, :meth:`edit`. Callers should treat the adapter as an opaque
    handle obtained from :func:`get_image_generation_model`.
    """

    provider: str
    model: str

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        raise NotImplementedError

    async def edit(
        self,
        prompt: str,
        *,
        input_image: bytes,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        """Edit/update ``input_image`` per ``prompt``. Providers without a
        native image-edit API raise ``NotImplementedError`` so callers can
        surface a clear, provider-specific error instead of silently
        falling back to text-to-image generation."""
        raise NotImplementedError(
            f"The '{self.provider}' image provider does not support editing "
            "existing images."
        )


class _OpenAIImageAdapter(ImageGenerationAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        organization: str | None = None,
        base_url: str | None = None,
        provider_override: str | None = None,
    ) -> None:
        self.provider = provider_override or ImageGenerationProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization
        self._base_url = base_url or None

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        import base64

        from openai import AsyncOpenAI

        client_kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "organization": self._organization,
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        client = AsyncOpenAI(**client_kwargs)

        # The ``response_format`` parameter is **only** accepted by DALL-E
        # models; ``gpt-image-*`` rejects it with HTTP 400 ("Unknown parameter:
        # 'response_format'"). DALL-E defaults to returning URLs, so we
        # explicitly ask for base64 there. ``gpt-image-*`` always returns
        # base64 so no hint is needed.
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": _normalize_openai_size(size),
            "n": n,
        }
        if self.model.startswith("dall-e"):
            request_kwargs["response_format"] = "b64_json"

        try:
            response = await client.images.generate(**request_kwargs)
        finally:
            await client.close()

        images: list[bytes] = []
        for item in response.data or []:
            b64 = getattr(item, "b64_json", None)
            if b64:
                images.append(base64.b64decode(b64))
                continue
            # Fallback: if the provider returned a URL instead of base64
            # (e.g. DALL-E without response_format, or a future model default),
            # download it so the caller still gets raw bytes.
            url = getattr(item, "url", None)
            if url:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=60.0) as http_client:
                        resp = await http_client.get(url)
                        resp.raise_for_status()
                        images.append(resp.content)
                except Exception:
                    logger.exception(
                        "Failed to download OpenAI image URL fallback"
                    )
        return images

    async def edit(
        self,
        prompt: str,
        *,
        input_image: bytes,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        """Edit ``input_image`` via ``POST /v1/images/edits``.

        DALL-E 2 is the only legacy model that supports this endpoint;
        ``gpt-image-*`` (and any LiteLLM-proxied equivalent) supports it
        natively too. ``response_format`` is DALL-E-only, same restriction
        as :meth:`generate`.
        """
        import base64
        import io

        from openai import AsyncOpenAI

        client_kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "organization": self._organization,
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        client = AsyncOpenAI(**client_kwargs)

        image_file = io.BytesIO(input_image)
        image_file.name = "input.png"

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "image": image_file,
            "prompt": prompt,
            "size": _normalize_openai_size(size),
            "n": n,
        }
        if self.model.startswith("dall-e"):
            request_kwargs["response_format"] = "b64_json"

        try:
            response = await client.images.edit(**request_kwargs)
        finally:
            await client.close()

        images: list[bytes] = []
        for item in response.data or []:
            b64 = getattr(item, "b64_json", None)
            if b64:
                images.append(base64.b64decode(b64))
                continue
            url = getattr(item, "url", None)
            if url:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=60.0) as http_client:
                        resp = await http_client.get(url)
                        resp.raise_for_status()
                        images.append(resp.content)
                except Exception:
                    logger.exception(
                        "Failed to download OpenAI image edit URL fallback"
                    )
        return images


class _GeminiImageAdapter(ImageGenerationAdapter):
    def __init__(self, *, model: str, api_key: str) -> None:
        self.provider = ImageGenerationProvider.GEMINI.value
        self.model = model
        self._api_key = api_key

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self._api_key)

        if self.model.startswith("imagen-"):
            response = await client.aio.models.generate_images(
                model=self.model,
                prompt=prompt,
                config=genai_types.GenerateImagesConfig(
                    number_of_images=n,
                    aspect_ratio=_size_to_aspect_ratio(size),
                ),
            )
            images: list[bytes] = []
            for gi in getattr(response, "generated_images", None) or []:
                img = getattr(gi, "image", None)
                data = getattr(img, "image_bytes", None) if img is not None else None
                if data:
                    images.append(data)
            return images

        # gemini-*-image models: multimodal generate_content with IMAGE modality.
        # The API returns one candidate per call, so we run n requests in
        # parallel for multi-image generation.
        import asyncio

        async def _one_call() -> list[bytes]:
            resp = await client.aio.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            out: list[bytes] = []
            for candidate in getattr(resp, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    data = getattr(inline, "data", None) if inline is not None else None
                    if data:
                        out.append(data)
            return out

        results = await asyncio.gather(*[_one_call() for _ in range(max(1, n))])
        return [img for batch in results for img in batch]

    async def edit(
        self,
        prompt: str,
        *,
        input_image: bytes,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        """Edit ``input_image`` by sending it as multimodal input alongside
        the text prompt. Imagen (``imagen-*``) has no native edit endpoint
        in this client, so editing is only supported on the
        ``gemini-*-image`` multimodal models.
        """
        if self.model.startswith("imagen-"):
            raise NotImplementedError(
                f"Imagen model '{self.model}' does not support image "
                "editing via this adapter; configure a 'gemini-*-image' "
                "model for editing."
            )

        import asyncio

        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self._api_key)

        async def _one_call() -> list[bytes]:
            resp = await client.aio.models.generate_content(
                model=self.model,
                contents=[
                    genai_types.Part.from_bytes(
                        data=input_image, mime_type="image/png",
                    ),
                    prompt,
                ],
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            out: list[bytes] = []
            for candidate in getattr(resp, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    data = getattr(inline, "data", None) if inline is not None else None
                    if data:
                        out.append(data)
            return out

        results = await asyncio.gather(*[_one_call() for _ in range(max(1, n))])
        return [img for batch in results for img in batch]


# OpenRouter uses POST /images (not /images/generations) and returns
# data[].b64_json. Aspect ratio is specified as "W:H" rather than "WxH".
_OPENROUTER_ASPECT_RATIO_MAP = {
    "1024x1024": "1:1",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
}


def _size_to_openrouter_aspect_ratio(size: str) -> str:
    return _OPENROUTER_ASPECT_RATIO_MAP.get(size, "1:1")


class _OpenRouterImageAdapter(ImageGenerationAdapter):
    def __init__(self, *, model: str, api_key: str) -> None:
        self.provider = ImageGenerationProvider.OPENROUTER.value
        self.model = model
        self._api_key = api_key

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        import base64

        import httpx

        body: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": n,
            "aspect_ratio": _size_to_openrouter_aspect_ratio(size),
        }
        url = f"{OPENROUTER_BASE_URL}/images"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            snippet = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    err = payload.get("error")
                    if isinstance(err, dict):
                        snippet = str(err.get("message") or "")
                    else:
                        snippet = str(err or payload.get("message") or "")
            except Exception:
                pass
            logger.error(
                "OpenRouter image generation failed: status=%d body=%r",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"OpenRouter image generation failed (upstream {response.status_code})"
                + (f": {snippet}" if snippet else "")
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("OpenRouter image generation returned a non-JSON response.") from exc

        images: list[bytes] = []
        for item in (payload.get("data") or []):
            b64 = item.get("b64_json") if isinstance(item, dict) else None
            if b64:
                images.append(base64.b64decode(b64))
        return images


def get_image_generation_model(
    provider: str,
    config: Dict[str, Any],
    model_name: str | None = None,
) -> ImageGenerationAdapter:
    """Return an adapter capable of producing image bytes from a prompt.

    ``config`` follows the same shape used by :func:`get_generator_model` and
    :func:`get_embedding_model`: a dict with a ``configuration`` sub-dict plus
    optional ``isDefault`` flag. The ``configuration.model`` field may carry a
    comma-separated list; we pick the first entry (or validate ``model_name``
    against the list).
    """

    configuration = config["configuration"]
    is_default = config.get("isDefault")
    model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
    if model_name is None:
        if not model_names:
            raise ValueError("No image-generation model configured")
        model_name = model_names[0]
    elif not is_default and model_name not in model_names:
        raise ValueError(f"Model name {model_name} not found in {configuration['model']}")

    logger.info(
        f"Getting image generation model: provider={provider}, model_name={model_name}"
    )

    if provider == ImageGenerationProvider.OPENAI.value:
        return _OpenAIImageAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
        )

    if provider == ImageGenerationProvider.GEMINI.value:
        return _GeminiImageAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
        )

    if provider == ImageGenerationProvider.OPENROUTER.value:
        return _OpenRouterImageAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
        )

    if provider == ImageGenerationProvider.LITELLM_PROXY.value:
        return _OpenAIImageAdapter(
            model=model_name,
            api_key=configuration.get("apiKey"),
            base_url=configuration["endpoint"],
            provider_override=ImageGenerationProvider.LITELLM_PROXY.value,
        )

    raise ValueError(f"Unsupported image generation provider: {provider}")


# ---------------------------------------------------------------------------
# Text-to-Speech (TTS)
# ---------------------------------------------------------------------------


_OPENAI_TTS_DEFAULT_VOICE = "alloy"
_OPENAI_TTS_VALID_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
_TTS_FORMAT_TO_MIME = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}


def tts_format_mime(response_format: str) -> str:
    """Return an HTTP ``Content-Type`` string for a given TTS format."""
    return _TTS_FORMAT_TO_MIME.get(response_format, "application/octet-stream")


class TTSAdapter:
    """Thin wrapper around a TTS provider SDK that returns raw audio bytes."""

    provider: str
    model: str
    default_voice: str = _OPENAI_TTS_DEFAULT_VOICE
    default_format: str = "mp3"

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        response_format: str | None = None,
        speed: float = 1.0,
    ) -> bytes:
        raise NotImplementedError


class _OpenAITTSAdapter(TTSAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        organization: str | None = None,
        default_voice: str | None = None,
        default_format: str | None = None,
        base_url: str | None = None,
        provider_override: str | None = None,
    ) -> None:
        self.provider = provider_override or TTSProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization
        self.default_voice = default_voice or _OPENAI_TTS_DEFAULT_VOICE
        self.default_format = default_format or "mp3"
        self._base_url = base_url or None

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        response_format: str | None = None,
        speed: float = 1.0,
    ) -> bytes:
        from openai import AsyncOpenAI

        fmt = (response_format or self.default_format or "mp3").lower()
        if fmt not in _OPENAI_TTS_VALID_FORMATS:
            fmt = "mp3"

        client_kwargs: Dict[str, Any] = {
            "api_key": self._api_key,
            "organization": self._organization,
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        client = AsyncOpenAI(**client_kwargs)
        try:
            response = await client.audio.speech.create(
                model=self.model,
                voice=voice or self.default_voice,
                input=text,
                response_format=fmt,
                speed=speed,
            )
            return await response.aread()
        finally:
            await client.close()


# Gemini's generateContent TTS endpoint returns raw PCM: signed 16-bit, mono,
# 24 kHz. We always receive those bytes and then (a) wrap in a WAV container
# for the "wav" format, (b) return them untouched for "pcm", or (c) shell out
# to ffmpeg for the other compressed formats.
_GEMINI_TTS_DEFAULT_VOICE = "Kore"
_GEMINI_TTS_VALID_FORMATS = {"wav", "pcm", "mp3", "opus", "aac", "flac"}
_GEMINI_TTS_PCM_SAMPLE_RATE = 24000
_GEMINI_TTS_PCM_SAMPLE_WIDTH = 2  # 16-bit
_GEMINI_TTS_PCM_CHANNELS = 1


def _wrap_pcm_as_wav(
    pcm: bytes,
    *,
    sample_rate: int = _GEMINI_TTS_PCM_SAMPLE_RATE,
    sample_width: int = _GEMINI_TTS_PCM_SAMPLE_WIDTH,
    channels: int = _GEMINI_TTS_PCM_CHANNELS,
) -> bytes:
    """Wrap raw signed 16-bit mono PCM in a minimal WAV container."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


async def _reencode_pcm_via_ffmpeg(
    pcm: bytes,
    *,
    target_format: str,
    sample_rate: int = _GEMINI_TTS_PCM_SAMPLE_RATE,
    channels: int = _GEMINI_TTS_PCM_CHANNELS,
) -> bytes:
    """Use ffmpeg to transcode raw 16-bit PCM to mp3/opus/aac/flac/etc."""
    import asyncio

    # Map our public format name to the ffmpeg output muxer. Opus is wrapped
    # in ogg so browsers / <audio> elements can play it back.
    muxer = {
        "mp3": "mp3",
        "opus": "ogg",
        "aac": "adts",
        "flac": "flac",
        "wav": "wav",
    }.get(target_format, target_format)

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            "-f",
            muxer,
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Encoding Gemini TTS audio to "
            f"{target_format!r} requires ffmpeg on the backend host. Install "
            "ffmpeg or pick the 'wav' / 'pcm' output format instead."
        ) from exc

    stdout, stderr = await process.communicate(input=pcm)
    if process.returncode != 0:
        err_msg = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ffmpeg failed to encode Gemini TTS output as {target_format} "
            f"(exit {process.returncode}): {err_msg or 'no stderr output'}"
        )
    if not stdout:
        raise RuntimeError(
            f"ffmpeg produced no output while encoding Gemini TTS audio as {target_format}."
        )
    return stdout


class _GeminiTTSAdapter(TTSAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        default_voice: str | None = None,
        default_format: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.provider = TTSProvider.GEMINI.value
        self.model = model
        self._api_key = api_key
        self.default_voice = default_voice or _GEMINI_TTS_DEFAULT_VOICE
        self.default_format = (default_format or "wav").lower()
        self._endpoint = (
            endpoint or "https://generativelanguage.googleapis.com"
        ).rstrip("/")

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        response_format: str | None = None,
        speed: float = 1.0,
    ) -> bytes:
        import base64

        import httpx

        fmt = (response_format or self.default_format or "wav").lower()
        if fmt not in _GEMINI_TTS_VALID_FORMATS:
            fmt = "wav"

        chosen_voice = voice or self.default_voice or _GEMINI_TTS_DEFAULT_VOICE
        url = f"{self._endpoint}/v1beta/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": chosen_voice},
                    }
                },
            },
        }
        # Gemini TTS ignores `speed` today; we accept it for parity with the
        # OpenAI adapter but don't forward it.
        _ = speed

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            snippet = ""
            try:
                data = response.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        snippet = str(err.get("message") or "")
            except Exception:
                pass
            logger.error(
                "Gemini TTS synthesis failed: status=%d body=%r",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"Gemini TTS synthesis failed (upstream {response.status_code})"
                + (f": {snippet}" if snippet else "")
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(
                "Gemini TTS returned a non-JSON response."
            ) from exc

        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        inline_b64: str | None = None
        if isinstance(candidates, list) and candidates:
            parts = (
                candidates[0].get("content", {}).get("parts", [])
                if isinstance(candidates[0], dict)
                else []
            )
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    inline_b64 = str(inline["data"])
                    break

        if not inline_b64:
            raise RuntimeError(
                "Gemini TTS response did not contain inline audio data."
            )

        pcm = base64.b64decode(inline_b64)
        if fmt == "pcm":
            return pcm
        if fmt == "wav":
            return _wrap_pcm_as_wav(pcm)
        return await _reencode_pcm_via_ffmpeg(pcm, target_format=fmt)


def get_tts_model(
    provider: str,
    config: Dict[str, Any],
    model_name: str | None = None,
) -> TTSAdapter:
    """Return a TTS adapter for the given provider/config.

    Mirrors the ``get_image_generation_model`` signature.
    """
    configuration = config["configuration"]
    is_default = config.get("isDefault")
    model_names = [
        name.strip()
        for name in str(configuration.get("model", "")).split(",")
        if name.strip()
    ]
    if model_name is None:
        if not model_names:
            raise ValueError("No TTS model configured")
        model_name = model_names[0]
    elif not is_default and model_name not in model_names:
        raise ValueError(
            f"Model name {model_name} not found in {configuration.get('model')}"
        )

    logger.info(f"Getting TTS model: provider={provider}, model_name={model_name}")

    if provider == TTSProvider.OPENAI.value:
        return _OpenAITTSAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
            default_voice=configuration.get("voice"),
            default_format=configuration.get("responseFormat"),
        )

    if provider == TTSProvider.GEMINI.value:
        return _GeminiTTSAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            default_voice=configuration.get("voice"),
            default_format=configuration.get("responseFormat"),
            endpoint=configuration.get("endpoint") or None,
        )

    if provider == TTSProvider.OPENROUTER.value:
        return _OpenAITTSAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            default_voice=configuration.get("voice"),
            default_format=configuration.get("responseFormat"),
            base_url=OPENROUTER_BASE_URL,
            provider_override=TTSProvider.OPENROUTER.value,
        )

    if provider == TTSProvider.LITELLM_PROXY.value:
        return _OpenAITTSAdapter(
            model=model_name,
            api_key=configuration.get("apiKey"),
            default_voice=configuration.get("voice"),
            default_format=configuration.get("responseFormat"),
            base_url=configuration["endpoint"],
            provider_override=TTSProvider.LITELLM_PROXY.value,
        )

    raise ValueError(f"Unsupported TTS provider: {provider}")


# ---------------------------------------------------------------------------
# Speech-to-Text (STT)
# ---------------------------------------------------------------------------


class STTAdapter:
    """Thin wrapper around an STT provider SDK that returns a transcript."""

    provider: str
    model: str

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        raise NotImplementedError


def _stt_filename_for_mime(mime: str, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    ext_map = {
        "audio/webm": "audio.webm",
        "audio/ogg": "audio.ogg",
        "audio/mp4": "audio.mp4",
        "audio/m4a": "audio.m4a",
        "audio/mpeg": "audio.mp3",
        "audio/wav": "audio.wav",
        "audio/x-wav": "audio.wav",
        "audio/flac": "audio.flac",
    }
    return ext_map.get(mime.lower(), "audio.webm")


class _OpenAISTTAdapter(STTAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        organization: str | None = None,
        base_url: str | None = None,
        provider_override: str | None = None,
    ) -> None:
        self.provider = provider_override or STTProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization
        self._base_url = base_url or None

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        from openai import AsyncOpenAI

        client_kwargs: Dict[str, Any] = {
            "api_key": self._api_key,
            "organization": self._organization,
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        client = AsyncOpenAI(**client_kwargs)
        file_tuple = (
            _stt_filename_for_mime(mime, filename),
            audio,
            mime or "application/octet-stream",
        )
        try:
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "file": file_tuple,
            }
            if language:
                kwargs["language"] = language
            response = await client.audio.transcriptions.create(**kwargs)
        finally:
            await client.close()

        text = getattr(response, "text", None)
        if text is None and isinstance(response, dict):
            text = response.get("text")
        return text or ""


# Process-level cache so we don't reload the Whisper weights on every request.
# Keyed by (model_name, device, compute_type, download_root).
_WHISPER_MODEL_CACHE: Dict[tuple, Any] = {}


class _WhisperLocalSTTAdapter(STTAdapter):
    def __init__(
        self,
        *,
        model: str,
        device: str = "auto",
        compute_type: str = "int8",
        download_root: str | None = None,
    ) -> None:
        self.provider = STTProvider.WHISPER.value
        self.model = model
        self._device = device or "auto"
        self._compute_type = compute_type or "int8"
        self._download_root = download_root or None

    def _get_whisper_model(self) -> Any:
        key = (
            self.model,
            self._device,
            self._compute_type,
            self._download_root,
        )
        cached = _WHISPER_MODEL_CACHE.get(key)
        if cached is not None:
            return cached

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "The 'whisper' STT provider requires the 'faster-whisper' "
                "package. Install dependencies (e.g. pip install faster-whisper) "
                "or reinstall this service."
            ) from exc

        logger.info(
            f"Loading faster-whisper model='{self.model}' device='{self._device}' "
            f"compute_type='{self._compute_type}'"
        )
        whisper_kwargs: Dict[str, Any] = {
            "device": self._device,
            "compute_type": self._compute_type,
        }
        if self._download_root:
            whisper_kwargs["download_root"] = self._download_root

        model_instance = WhisperModel(self.model, **whisper_kwargs)
        _WHISPER_MODEL_CACHE[key] = model_instance
        return model_instance

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        import asyncio
        import io

        def _run() -> str:
            whisper_model = self._get_whisper_model()
            segments, _info = whisper_model.transcribe(
                io.BytesIO(audio),
                language=language,
            )
            parts: list[str] = []
            for seg in segments:
                text = getattr(seg, "text", "") or ""
                if text:
                    parts.append(text)
            return "".join(parts).strip()

        return await asyncio.to_thread(_run)


# Wispr Flow expects 16 kHz mono WAV, base64-encoded, <= 25 MB / 6 min.
# We transcode server-side using ffmpeg so any format the browser produces
# (webm/opus, mp4/aac, wav, mp3, etc.) is accepted.
_WISPR_DEFAULT_ENDPOINT = "https://platform-api.wisprflow.ai"
_WISPR_MAX_WAV_BYTES = 25 * 1024 * 1024


async def _transcode_to_wispr_wav(audio: bytes) -> bytes:
    """Run ffmpeg to convert arbitrary input audio to 16 kHz mono WAV.

    Raises ``RuntimeError`` with an operator-friendly message if ffmpeg
    isn't installed on PATH or the transcode fails.
    """
    import asyncio

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "The 'wispr' STT provider requires ffmpeg to transcode audio to "
            "16 kHz WAV. Install ffmpeg on the backend host (e.g. "
            "'apt-get install ffmpeg' or 'brew install ffmpeg') and retry."
        ) from exc

    stdout, stderr = await process.communicate(input=audio)
    if process.returncode != 0:
        err_msg = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ffmpeg failed to transcode audio for Wispr Flow (exit {process.returncode}): "
            f"{err_msg or 'no stderr output'}"
        )
    if not stdout:
        raise RuntimeError(
            "ffmpeg produced no output while transcoding audio for Wispr Flow."
        )
    return stdout


class _WisprFlowSTTAdapter(STTAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        endpoint: str | None = None,
        default_language: str | None = None,
        default_app_type: str | None = None,
    ) -> None:
        self.provider = STTProvider.WISPR.value
        self.model = model
        self._api_key = api_key
        self._endpoint = (endpoint or _WISPR_DEFAULT_ENDPOINT).rstrip("/")
        self._default_language = (default_language or "").strip() or None
        self._default_app_type = (default_app_type or "ai").strip() or "ai"

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        import base64

        import httpx

        wav_bytes = await _transcode_to_wispr_wav(audio)
        if len(wav_bytes) > _WISPR_MAX_WAV_BYTES:
            raise RuntimeError(
                f"Transcoded audio is {len(wav_bytes)} bytes which exceeds "
                f"Wispr Flow's 25 MB ceiling. Shorten the recording."
            )

        payload_audio = base64.b64encode(wav_bytes).decode("ascii")
        properties: Dict[str, Any] = {
            "app_type": self._default_app_type,
        }
        lang = (language or self._default_language or "").strip()
        if lang:
            properties["language"] = lang

        url = f"{self._endpoint}/api/v1/dash/api"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "audio": payload_audio,
            "properties": properties,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            # Avoid leaking the raw upstream body (may include request ids).
            # Log details server-side via the module logger and surface a
            # compact error to the caller.
            snippet = ""
            try:
                data = response.json()
                if isinstance(data, dict):
                    snippet = str(data.get("detail") or data.get("message") or "")
            except Exception:
                pass
            logger.error(
                "Wispr Flow transcription failed: status=%d body=%r",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"Wispr Flow transcription failed (upstream {response.status_code})"
                + (f": {snippet}" if snippet else "")
            )

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(
                "Wispr Flow returned a non-JSON response."
            ) from exc

        if not isinstance(data, dict):
            return ""
        text = data.get("text")
        return text if isinstance(text, str) else ""


# Gemini STT piggybacks on the generic multimodal generateContent endpoint:
# we pass inline audio bytes plus a short prompt asking for a verbatim
# transcript. Request size (incl. audio) must stay under 20 MB.
_GEMINI_STT_TRANSCRIBE_PROMPT = (
    "Transcribe the following audio verbatim. Return only the raw transcript "
    "text with no additional commentary, formatting, or quotation marks."
)
_GEMINI_STT_MIME_MAP = {
    "audio/webm": "audio/webm",
    "audio/ogg": "audio/ogg",
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/mpeg": "audio/mp3",
    "audio/mp3": "audio/mp3",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/flac": "audio/flac",
    "audio/aac": "audio/aac",
}

# When the browser sends ``application/octet-stream`` (no real audio/* type),
# infer from the upload filename so Gemini receives a supported audio MIME.
_GEMINI_STT_OCTET_EXT_MAP = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "mp3": "audio/mp3",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "aac": "audio/aac",
}


def _gemini_stt_mime(mime: str, filename: str | None = None) -> str:
    """Normalize a browser-supplied mime to a Gemini-accepted value."""
    if not mime:
        return "audio/mp3"
    normalized = mime.split(";")[0].strip().lower()
    if normalized in ("application/octet-stream", "binary/octet-stream"):
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[-1].strip().lower()
            mapped = _GEMINI_STT_OCTET_EXT_MAP.get(ext)
            if mapped:
                return mapped
        # Typical MediaRecorder default when the pipeline drops audio/*
        return "audio/webm"
    return _GEMINI_STT_MIME_MAP.get(normalized, normalized or "audio/mp3")


class _GeminiSTTAdapter(STTAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        endpoint: str | None = None,
    ) -> None:
        self.provider = STTProvider.GEMINI.value
        self.model = model
        self._api_key = api_key
        self._endpoint = (
            endpoint or "https://generativelanguage.googleapis.com"
        ).rstrip("/")

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        import base64

        import httpx

        effective_mime = _gemini_stt_mime(mime, filename)
        prompt = _GEMINI_STT_TRANSCRIBE_PROMPT
        if language:
            prompt = f"{prompt} The speaker is using language code '{language}'."

        url = f"{self._endpoint}/v1beta/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": effective_mime,
                                "data": base64.b64encode(audio).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"responseModalities": ["TEXT"]},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            snippet = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    err = payload.get("error")
                    if isinstance(err, dict):
                        snippet = str(err.get("message") or "")
            except Exception:
                pass
            logger.error(
                "Gemini STT transcription failed: status=%d body=%r",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"Gemini STT transcription failed (upstream {response.status_code})"
                + (f": {snippet}" if snippet else "")
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(
                "Gemini STT returned a non-JSON response."
            ) from exc

        if not isinstance(payload, dict):
            return ""

        candidates = payload.get("candidates") or []
        if not isinstance(candidates, list) or not candidates:
            return ""

        parts = (
            candidates[0].get("content", {}).get("parts", [])
            if isinstance(candidates[0], dict)
            else []
        )
        pieces: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    pieces.append(text)
        return "".join(pieces).strip()


# ---------------------------------------------------------------------------
# OpenRouter STT — JSON body with base64 audio (not multipart upload)
# ---------------------------------------------------------------------------

_OPENROUTER_STT_FORMAT_MAP = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/aac": "aac",
}

_OPENROUTER_STT_EXT_MAP = {
    "webm": "webm",
    "ogg": "ogg",
    "opus": "ogg",
    "mp4": "mp4",
    "m4a": "m4a",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "aac": "aac",
}


def _openrouter_stt_format(mime: str, filename: str | None = None) -> str:
    """Map a browser-supplied MIME type to an OpenRouter audio format string."""
    if not mime:
        return "webm"
    normalized = mime.split(";")[0].strip().lower()
    if normalized in ("application/octet-stream", "binary/octet-stream"):
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[-1].strip().lower()
            mapped = _OPENROUTER_STT_EXT_MAP.get(ext)
            if mapped:
                return mapped
        return "webm"
    return _OPENROUTER_STT_FORMAT_MAP.get(normalized, "webm")


class _OpenRouterSTTAdapter(STTAdapter):
    def __init__(self, *, model: str, api_key: str) -> None:
        self.provider = STTProvider.OPENROUTER.value
        self.model = model
        self._api_key = api_key

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        import base64

        import httpx

        fmt = _openrouter_stt_format(mime, filename)
        body: Dict[str, Any] = {
            "model": self.model,
            "input_audio": {
                "data": base64.b64encode(audio).decode("ascii"),
                "format": fmt,
            },
        }
        if language:
            body["language"] = language

        url = f"{OPENROUTER_BASE_URL}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            snippet = ""
            try:
                data = response.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        snippet = str(err.get("message") or "")
                    else:
                        snippet = str(err or data.get("message") or "")
            except Exception:
                pass
            logger.error(
                "OpenRouter STT transcription failed: status=%d body=%r",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"OpenRouter STT transcription failed (upstream {response.status_code})"
                + (f": {snippet}" if snippet else "")
            )

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError("OpenRouter STT returned a non-JSON response.") from exc

        if not isinstance(data, dict):
            return ""
        text = data.get("text")
        return text if isinstance(text, str) else ""


def get_stt_model(
    provider: str,
    config: Dict[str, Any],
    model_name: str | None = None,
) -> STTAdapter:
    """Return an STT adapter for the given provider/config."""
    configuration = config["configuration"]
    is_default = config.get("isDefault")
    model_names = [
        name.strip()
        for name in str(configuration.get("model", "")).split(",")
        if name.strip()
    ]
    if model_name is None:
        if not model_names:
            raise ValueError("No STT model configured")
        model_name = model_names[0]
    elif not is_default and model_name not in model_names:
        raise ValueError(
            f"Model name {model_name} not found in {configuration.get('model')}"
        )

    logger.info(f"Getting STT model: provider={provider}, model_name={model_name}")

    if provider == STTProvider.OPENAI.value:
        return _OpenAISTTAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
        )

    if provider == STTProvider.WHISPER.value:
        return _WhisperLocalSTTAdapter(
            model=model_name,
            device=configuration.get("device", "auto"),
            compute_type=configuration.get("computeType", "int8"),
            download_root=configuration.get("modelDir") or None,
        )

    if provider == STTProvider.WISPR.value:
        return _WisprFlowSTTAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            endpoint=configuration.get("endpoint") or None,
            default_language=configuration.get("language") or None,
            default_app_type=configuration.get("appType") or "ai",
        )

    if provider == STTProvider.GEMINI.value:
        return _GeminiSTTAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
            endpoint=configuration.get("endpoint") or None,
        )

    if provider == STTProvider.OPENROUTER.value:
        return _OpenRouterSTTAdapter(
            model=model_name,
            api_key=configuration["apiKey"],
        )

    if provider == STTProvider.LITELLM_PROXY.value:
        return _OpenAISTTAdapter(
            model=model_name,
            api_key=configuration.get("apiKey"),
            base_url=configuration["endpoint"],
            provider_override=STTProvider.LITELLM_PROXY.value,
        )

    raise ValueError(f"Unsupported STT provider: {provider}")
