
from __future__ import annotations

import os
import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from botocore.client import BaseClient

from langchain_core.embeddings.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config.constants.ai_models import (
    AZURE_EMBEDDING_API_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    AzureOpenAILLM,
)
from app.utils.logger import create_logger


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
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    SENTENCE_TRANSFOMERS = "sentenceTransformers"
    TOGETHER = "together"
    VERTEX_AI = "vertexAI"
    VOYAGE = "voyage"

class LLMProvider(Enum):
    ANTHROPIC = "anthropic"
    AWS_BEDROCK = "bedrock"
    AZURE_AI = "azureAI"
    AZURE_OPENAI = "azureOpenAI"
    COHERE = "cohere"
    FIREWORKS = "fireworks"
    GEMINI = "gemini"
    GROQ = "groq"
    MINIMAX = "minimax"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    TOGETHER = "together"
    VERTEX_AI = "vertexAI"
    XAI = "xai"


class ImageGenerationProvider(Enum):
    OPENAI = "openAI"
    GEMINI = "gemini"


class TTSProvider(Enum):
    OPENAI = "openAI"


class STTProvider(Enum):
    OPENAI = "openAI"
    WHISPER = "whisper"

MAX_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS_CLAUDE_4_5 = 64000

def get_default_embedding_model() -> Embeddings:
    from langchain_huggingface import HuggingFaceEmbeddings

    try:
        model_name = DEFAULT_EMBEDDING_MODEL
        encode_kwargs = {'normalize_embeddings': True}
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": "cpu"},
            encode_kwargs=encode_kwargs,
        )
    except Exception  as e:
        raise e

logger = create_logger("aimodels")

def _create_bedrock_client(configuration: Dict[str, Any], service_name: str = "bedrock-runtime") -> BaseClient:
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

def is_multimodal_llm(config: Dict[str, Any]) -> bool:
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


def get_embedding_model(provider: str, config: Dict[str, Any], model_name: str | None = None) -> Embeddings:
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
        from langchain_community.embeddings import HuggingFaceEmbeddings

        model_kwargs = configuration.get('model_kwargs', {}).copy()
        # Hugging Face embedding models typically don't use API keys in the same way
        # but we include it in case it's needed for private models
        if configuration.get('apiKey'):
            model_kwargs["api_key"] = configuration['apiKey']

        # Set default encoding parameters
        encode_kwargs = configuration.get('encode_kwargs', {}).copy()
        if "normalize_embeddings" not in encode_kwargs:
            encode_kwargs["normalize_embeddings"] = True

        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs
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
        from langchain_community.embeddings import SentenceTransformerEmbeddings

        encode_kwargs = configuration.get('encode_kwargs', {}).copy()

        return SentenceTransformerEmbeddings(
            model_name=model_name,
            cache_folder=configuration.get('cache_folder', None),
            encode_kwargs=encode_kwargs
        )

    elif provider == EmbeddingProvider.OPENAI_COMPATIBLE.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        base_url = configuration['endpoint']
        providers_to_skip_check = ("google", "cohere", "voyage")
        check_embedding_ctx_length = not any(p in base_url for p in providers_to_skip_check)

        compat_kwargs: Dict[str, Any] = dict(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=base_url,
            check_embedding_ctx_length=check_embedding_ctx_length,
        )
        _set_embedding_dimensions_kwarg(compat_kwargs, dimensions)
        return OpenAIEmbeddings(**compat_kwargs)

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

    raise ValueError(f"Unsupported embedding config type: {provider}")

def _get_anthropic_max_tokens(model_name: str) -> int:
    """Gets the max output tokens for an Anthropic model based on its name."""
    if '4.5' in model_name:
        return MAX_OUTPUT_TOKENS_CLAUDE_4_5
    return MAX_OUTPUT_TOKENS


def _anthropic_supports_sampling_params(model_name: str | None) -> bool:
    """Whether the given Claude model accepts ``temperature``/``top_p``/``top_k``.

    Anthropic removed sampling parameters starting with Claude Opus 4.7:
    any non-default value returns a 400 "deprecated for this model" error.
    The same direction is expected for future Claude families, so we also
    disable sampling params for any Claude major version >= 5.

    Matches model IDs like ``claude-opus-4-7``, ``claude-opus-4.7``, and
    Bedrock/Vertex variants that embed the same version suffix.
    """
    if not model_name:
        return True

    lowered = model_name.lower()
    if "claude" not in lowered:
        return True

    match = re.search(r"claude[-_]?(opus|sonnet|haiku)[-_]?(\d+)[-_.](\d+)", lowered)
    if not match:
        return True

    tier = match.group(1)
    major = int(match.group(2))
    minor = int(match.group(3))

    if tier == "opus" and (major > 4 or (major == 4 and minor >= 7)):
        return False

    if major >= 5:
        return False

    return True

def get_generator_model(provider: str, config: Dict[str, Any], model_name: str | None = None) -> BaseChatModel:
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
        return ChatAnthropic(**anthropic_kwargs)

    elif provider == LLMProvider.AWS_BEDROCK.value:
        from langchain_aws import ChatBedrock

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

        # Auto-detect provider from model name if not explicitly set
        if not provider_in_bedrock:
            if "mistral" in model_name.lower():
                provider_in_bedrock = LLMProvider.MISTRAL.value
            elif "claude" in model_name.lower() or "anthropic" in model_name.lower():
                provider_in_bedrock = LLMProvider.ANTHROPIC.value
            elif "llama" in model_name.lower() or "meta" in model_name.lower():
                provider_in_bedrock = "meta"
            elif "titan" in model_name.lower() or "amazon" in model_name.lower():
                provider_in_bedrock = "amazon"
            elif "cohere" in model_name.lower():
                provider_in_bedrock = "cohere"
            elif "ai21" in model_name.lower() or "jamba" in model_name.lower():
                provider_in_bedrock = "ai21"
            elif "qwen" in model_name.lower():
                provider_in_bedrock = "qwen"
            else:
                # Default to anthropic for backwards compatibility
                provider_in_bedrock = LLMProvider.ANTHROPIC.value

        logger.info(f"Provider in Bedrock: {provider_in_bedrock} for model: {model_name}")

        # Set model_kwargs based on the provider
        # For Anthropic models in Bedrock, we need to pass max_tokens in model_kwargs
        # but NOT anthropic_version (which causes the validation error)
        if provider_in_bedrock == LLMProvider.ANTHROPIC.value:
            max_tokens = _get_anthropic_max_tokens(model_name)
            model_kwargs = {
                "max_tokens": max_tokens,
            }
        else:
            model_kwargs = {}

        bedrock_client = _create_bedrock_client(configuration)

        bedrock_kwargs: Dict[str, Any] = dict(
            model_id=model_name,
            client=bedrock_client,
            region_name=configuration.get("region"),
            provider=provider_in_bedrock,
            model_kwargs=model_kwargs,
            beta_use_converse_api=True,
        )
        if (
            provider_in_bedrock != LLMProvider.ANTHROPIC.value
            or _anthropic_supports_sampling_params(model_name)
        ):
            bedrock_kwargs["temperature"] = 0.2
        return ChatBedrock(**bedrock_kwargs)
    elif provider == LLMProvider.AZURE_AI.value:
        from langchain_anthropic import ChatAnthropic
        from langchain_openai import ChatOpenAI

        is_reasoning_model = "gpt-5" in model_name or config.get("isReasoning", False)
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)

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
            return ChatAnthropic(**azure_claude_kwargs)
        else:
            return ChatOpenAI(
                    model=model_name,
                    temperature=temperature,
                    timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                    api_key=configuration.get("apiKey"),
                    base_url=configuration.get("endpoint"),
                    stream_usage=True,  # Enable token usage tracking for Opik
                )

    elif provider == LLMProvider.AZURE_OPENAI.value:
        from langchain_openai import AzureChatOpenAI

        is_reasoning_model = "gpt-5" in model_name or config.get("isReasoning", False)
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)
        return AzureChatOpenAI(
                api_key=configuration["apiKey"],
                azure_endpoint=configuration["endpoint"],
                api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                azure_deployment=configuration["deploymentName"],
                stream_usage=True,  # Enable token usage tracking for Opik
            )

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

        return ChatFireworks(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
            )

    elif provider == LLMProvider.GEMINI.value:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
                model=model_name,
                temperature=0.2,
                max_tokens=None,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                max_retries=2,
                google_api_key=configuration["apiKey"],
            )

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
        return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,
                api_key=configuration["apiKey"],
                base_url="https://api.minimax.io/v1",
                stream_usage=True,
            )

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

        return ChatOllama(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                base_url=configuration.get('endpoint', os.getenv("OLLAMA_API_URL", "http://localhost:11434")),
                reasoning=False
            )

    elif provider == LLMProvider.OPENAI.value:
        from langchain_openai import ChatOpenAI

        is_reasoning_model = "gpt-5" in model_name or config.get("isReasoning", False)
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)
        return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
                stream_usage=True,  # Enable token usage tracking for Opik
            )

    elif provider == LLMProvider.XAI.value:
        from langchain_xai import ChatXAI

        return ChatXAI(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
            )

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
        is_reasoning_model = "gpt-5" in model_name or config.get("isReasoning", False)
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)
        return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
                stream_usage=True,  # Enable token usage tracking for Opik
            )

    raise ValueError(f"Unsupported provider type: {provider}")


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

    Concrete subclasses implement :meth:`generate`. Callers should treat the
    adapter as an opaque handle obtained from :func:`get_image_generation_model`.
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


class _OpenAIImageAdapter(ImageGenerationAdapter):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        organization: str | None = None,
    ) -> None:
        self.provider = ImageGenerationProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[bytes]:
        import base64

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self._api_key,
            organization=self._organization,
        )

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
    ) -> None:
        self.provider = TTSProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization
        self.default_voice = default_voice or _OPENAI_TTS_DEFAULT_VOICE
        self.default_format = default_format or "mp3"

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

        client = AsyncOpenAI(
            api_key=self._api_key,
            organization=self._organization,
        )
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
    ) -> None:
        self.provider = STTProvider.OPENAI.value
        self.model = model
        self._api_key = api_key
        self._organization = organization

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str = "audio/webm",
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self._api_key,
            organization=self._organization,
        )
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
                "package. Install the optional extra: "
                "pip install 'pipeshub-ai[whisper]' or "
                "pip install faster-whisper"
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

    raise ValueError(f"Unsupported STT provider: {provider}")
