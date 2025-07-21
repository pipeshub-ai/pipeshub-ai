
import os
from typing import Any, Dict

from langchain.chat_models.base import BaseChatModel
from langchain_core.embeddings.embeddings import Embeddings

from app.config.utils.named_constants.ai_models_named_constants import (
    AZURE_EMBEDDING_API_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    AzureOpenAILLM,
    EmbeddingProvider,
    LLMProvider,
)


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

def get_embedding_model(provider: str, config: Dict[str, Any]) -> Embeddings:
    configuration = config['configuration']
    if provider == EmbeddingProvider.AZURE_OPENAI.value:
        from langchain_openai.embeddings import AzureOpenAIEmbeddings

        return AzureOpenAIEmbeddings(
            model=configuration['model'],
            api_key=configuration['apiKey'],
            api_version=AZURE_EMBEDDING_API_VERSION,
            azure_endpoint=configuration['endpoint'],
        )

    elif provider == EmbeddingProvider.OPENAI.value:
        from langchain_openai.embeddings import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=configuration["model"],
            api_key=configuration["apiKey"],
            organization=configuration.get("organizationId"),
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
            model_name=configuration['model'],
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs
        )
    elif provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value:
        from langchain_community.embeddings import SentenceTransformerEmbeddings

        encode_kwargs = configuration.get('encode_kwargs', {}).copy()

        return SentenceTransformerEmbeddings(
            model_name=configuration['model'],
            cache_folder=configuration.get('cache_folder', None),
            encode_kwargs=encode_kwargs
        )
    elif provider == EmbeddingProvider.GEMINI.value:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        # Add "models/" prefix if it's missing
        model_name = configuration['model']
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        return GoogleGenerativeAIEmbeddings(
            model=model_name,  # Now properly formatted as models/text-embedding-004
            google_api_key=configuration['apiKey'],
        )
    elif provider == EmbeddingProvider.COHERE.value:
        from langchain_cohere import CohereEmbeddings

        return CohereEmbeddings(
            model=configuration['model'],
            cohere_api_key=configuration['apiKey'],
        )
    elif provider == EmbeddingProvider.OPENAI_COMPATIBLE.value:
        return OpenAIEmbeddings(
            model=configuration['model'],
            api_key=configuration['apiKey'],
            base_url=configuration['endpoint'],
        )
    elif provider == EmbeddingProvider.OLLAMA.value:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=configuration['model'],
            base_url=configuration['endpoint']
        )
    elif provider == EmbeddingProvider.DEFAULT.value:
        return get_default_embedding_model()

    raise ValueError(f"Unsupported embedding config type: {provider}")


def get_generator_model(provider: str, config: Dict[str, Any]) -> BaseChatModel:
    configuration = config['configuration']
    print(provider, "provider")
    print(configuration, "configuration")
    if provider == LLMProvider.AZURE_OPENAI.value:

        from langchain_community.chat_models import AzureChatOpenAI
        return AzureChatOpenAI(
                api_key=configuration["apiKey"],
                model=configuration["model"],
                azure_endpoint=configuration["endpoint"],
                api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                temperature=0.2,
                azure_deployment=configuration["deploymentName"],
            )
    elif provider == LLMProvider.OPENAI.value:
        from langchain_community.chat_models import ChatOpenAI
        return ChatOpenAI(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
                organization=configuration.get("organizationId"),
            )
    elif provider == LLMProvider.GEMINI.value:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
                model=configuration["model"],
                temperature=0.2,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                google_api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.ANTHROPIC.value:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
                model=configuration["model"],
                temperature=0.2,
                timeout=None,
                max_retries=2,
                api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.AWS_BEDROCK.value:
        from langchain_aws import ChatBedrock

        return ChatBedrock(
                model=configuration["model"],
                temperature=0.2,
                aws_access_key_id=configuration["awsAccessKeyId"],
                aws_secret_access_key=configuration["awsAccessSecretKey"],
                region_name=configuration["region"],
            )
    elif provider == LLMProvider.OLLAMA.value:
        from langchain_ollama import ChatOllama

        return ChatOllama(
                model=configuration["model"],
                temperature=0.2,
                base_url=configuration.get('endpoint', os.getenv("OLLAMA_API_URL", "http://localhost:11434"))
            )
    elif provider == LLMProvider.MISTRAL.value:
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.XAI.value:
        from langchain_xai import ChatXAI
        return ChatXAI(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
            )

    elif provider == LLMProvider.TOGETHER.value:
        from langchain_together import ChatTogether

        return ChatTogether(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
            )

    elif provider == LLMProvider.GROQ.value:
        from langchain_groq import ChatGroq
        return ChatGroq(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
            )


    elif provider == LLMProvider.COHERE.value:
        from langchain_cohere import ChatCohere
        return ChatCohere(
                model=configuration["model"],
                temperature=0.2,
                cohere_api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.FIREWORKS.value:
        from langchain_fireworks import ChatFireworks
        return ChatFireworks(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
            )
    elif provider == LLMProvider.OPENAI_COMPATIBLE.value:
        from langchain_community.chat_models import ChatOpenAI

        return ChatOpenAI(
                model=configuration["model"],
                temperature=0.2,
                api_key=configuration["apiKey"],
                base_url=configuration["endpoint"],
            )
    raise ValueError(f"Unsupported provider type: {provider}")
