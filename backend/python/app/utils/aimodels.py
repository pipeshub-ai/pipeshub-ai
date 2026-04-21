
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
