
import os
from enum import Enum
from typing import Any, Dict

from langchain.chat_models.base import BaseChatModel
from langchain_core.embeddings.embeddings import Embeddings

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
    AZURE_OPENAI = "azureOpenAI"
    COHERE = "cohere"
    FIREWORKS = "fireworks"
    GEMINI = "gemini"
    GROQ = "groq"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    TOGETHER = "together"
    VERTEX_AI = "vertexAI"
    XAI = "xai"


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

    if provider == EmbeddingProvider.AZURE_OPENAI.value:
        from langchain_openai.embeddings import AzureOpenAIEmbeddings

        return AzureOpenAIEmbeddings(
            model=model_name,
            api_key=configuration['apiKey'],
            api_version=AZURE_EMBEDDING_API_VERSION,
            azure_endpoint=configuration['endpoint'],
        )

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

        # Add "models/" prefix if it's missing
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        return GoogleGenerativeAIEmbeddings(
            model=model_name,  # Now properly formatted as models/text-embedding-004
            google_api_key=configuration['apiKey'],
        )

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

        return MistralAIEmbeddings(
            model=model_name,
            api_key=configuration['apiKey'],
        )


    elif provider == EmbeddingProvider.OLLAMA.value:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model_name,
            base_url=configuration['endpoint']
        )

    elif provider == EmbeddingProvider.OPENAI.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=model_name,
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
        )

    elif provider == EmbeddingProvider.AWS_BEDROCK.value:
        from langchain_aws import BedrockEmbeddings

        return BedrockEmbeddings(
            model_id=configuration["model"],
            aws_access_key_id=configuration["awsAccessKeyId"],
            aws_secret_access_key=configuration["awsAccessSecretKey"],
            region_name=configuration["region"],
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

        return OpenAIEmbeddings(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
        )

    elif provider == EmbeddingProvider.TOGETHER.value:
        from langchain_together import TogetherEmbeddings

        return TogetherEmbeddings(
            model=model_name,
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
        )

    elif provider == EmbeddingProvider.VOYAGE.value:
        from app.utils.custom_embeddings import VoyageEmbeddings

        return VoyageEmbeddings(
            model=model_name,
            voyage_api_key=configuration['apiKey'],
        )

    raise ValueError(f"Unsupported embedding config type: {provider}")

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

        return ChatAnthropic(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                max_retries=2,
                api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.AWS_BEDROCK.value:
        from langchain_aws import ChatBedrock

        return ChatBedrock(
                model_id=model_name,
                temperature=0.2,
                aws_access_key_id=configuration["awsAccessKeyId"],
                aws_secret_access_key=configuration["awsAccessSecretKey"],
                region_name=configuration["region"],
                provider=configuration.get("provider", "anthropic"),
            )
    elif provider == LLMProvider.AZURE_OPENAI.value:
        from langchain_openai import AzureChatOpenAI

        is_reasoning_model = "gpt-5" in model_name or configuration.get("isReasoning")
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)
        return AzureChatOpenAI(
                api_key=configuration["apiKey"],
                azure_endpoint=configuration["endpoint"],
                api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                azure_deployment=configuration["deploymentName"],
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

        is_reasoning_model = "gpt-5" in model_name or configuration.get("isReasoning")
        temperature = 1 if is_reasoning_model else configuration.get("temperature", 0.2)
        return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
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
        from langchain_together import ChatTogether

        return ChatTogether(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
            )

    elif provider == LLMProvider.OPENAI_COMPATIBLE.value:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
                model=model_name,
                temperature=0.2,
                timeout=DEFAULT_LLM_TIMEOUT,  # 6 minute timeout
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
            )

    raise ValueError(f"Unsupported provider type: {provider}")
