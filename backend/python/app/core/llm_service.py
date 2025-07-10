import os
import time
from datetime import datetime
from typing import Dict, Optional

from langchain.callbacks.base import BaseCallbackHandler
from langchain.chat_models.base import BaseChatModel
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock
from langchain_community.chat_models import AzureChatOpenAI, ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama.llms import OllamaLLM
from pydantic import BaseModel, Field

from app.config.utils.named_constants.ai_models_named_constants import AzureOpenAILLM


class BaseLLMConfig(BaseModel):
    """Base configuration for all LLM providers"""

    model: str
    temperature: float = Field(default=0.4, ge=0, le=1)
    api_key: str

class OpenAICompatibleLLMConfig(BaseLLMConfig):
    """OpenAI-compatible configuration"""
    endpoint: str = Field(default="", description="The endpoint for the OpenAI-compatible API")

class AzureLLMConfig(BaseLLMConfig):
    """Azure-specific configuration"""

    azure_endpoint: str
    azure_deployment: str
    azure_api_version: str


class GeminiLLMConfig(BaseLLMConfig):
    """Gemini-specific configuration"""

class OllamaConfig(BaseLLMConfig):
    """Gemini-specific configuration"""

class AnthropicLLMConfig(BaseLLMConfig):
    """Gemini-specific configuration"""


class OpenAILLMConfig(BaseLLMConfig):
    """OpenAI-specific configuration"""

    organization_id: Optional[str] = None


class AwsBedrockLLMConfig(BaseLLMConfig):
    """OpenAI-specific configuration"""

    region: str
    access_key: str
    access_secret: str


class CostTrackingCallback(BaseCallbackHandler):
    """Callback handler for tracking LLM usage and costs"""

    def __init__(self, logger) -> None:
        super().__init__()
        self.logger = logger
        # Azure GPT-4 pricing (per 1K tokens)
        self.cost_per_1k_tokens = {
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-35-turbo": {"input": 0.0015, "output": 0.002},
        }
        self.current_usage = {
            "tokens_in": 0,
            "tokens_out": 0,
            "start_time": None,
            "end_time": None,
            "cost": 0.0,
        }

    def on_llm_start(self, *args, **kwargs) -> None:
        self.current_usage["start_time"] = datetime.now()

    def on_llm_end(self, *args, **kwargs) -> None:
        self.current_usage["end_time"] = datetime.now()

    def on_llm_new_token(self, *args, **kwargs) -> None:
        pass

    def calculate_cost(self, model: str) -> float:
        """Calculate cost based on token usage"""
        if model not in self.cost_per_1k_tokens:
            self.logger.warning(f"Unknown model for cost calculation: {model}")
            return 0.0

        rates = self.cost_per_1k_tokens[model]
        input_cost = (self.current_usage["tokens_in"] / 1000) * rates["input"]
        output_cost = (self.current_usage["tokens_out"] / 1000) * rates["output"]
        return input_cost + output_cost


class LLMFactory:
    """Factory for creating LLM instances with cost tracking"""

    @staticmethod
    def create_llm(logger, config: BaseLLMConfig) -> BaseChatModel:
        """Create an LLM instance based on configuration"""
        # TIMING: Start timing LLM creation
        start_time = time.time()

        cost_callback = CostTrackingCallback(logger)

        if isinstance(config, AzureLLMConfig):
            # TIMING: Log the time before Azure LLM creation
            pre_azure_creation = time.time()

            llm = AzureChatOpenAI(
                api_key=config.api_key,
                model=config.model,
                azure_endpoint=config.azure_endpoint,
                api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                temperature=0.2,
                azure_deployment=config.azure_deployment,
                callbacks=[cost_callback],
                # Optimized timeout settings for Azure
                timeout=15.0,  # Reduced from 30s to 15s for faster failure detection
                max_retries=1,  # Reduced retries for faster response
                request_timeout=15.0,  # Reduced request timeout
                # Azure-specific optimizations
                max_tokens=None,  # Let the model decide
                streaming=True,  # Enable streaming for better performance
                # Additional Azure optimizations
                n=1,  # Single response for faster generation
                stop=None,  # No stop sequences for faster response
            )

            # TIMING: Log the time after Azure LLM creation
            post_azure_creation = time.time()
            azure_creation_time = post_azure_creation - pre_azure_creation
            logger.info(f"TIMING: Azure LLM client creation took {azure_creation_time:.3f}s")

            return llm

        elif isinstance(config, OpenAILLMConfig):
            # TIMING: Log the time before OpenAI LLM creation
            pre_openai_creation = time.time()

            llm = ChatOpenAI(
                model=config.model,
                temperature=0.2,
                api_key=config.api_key,
                organization=config.organization_id,
                callbacks=[cost_callback],
                # Add timeout and connection optimization settings
                timeout=30.0,
                max_retries=2,
                request_timeout=30.0,
                # Additional optimization parameters
                max_tokens=None,  # Let the model decide
                streaming=True,  # Enable streaming for better performance
            )

            # TIMING: Log the time after OpenAI LLM creation
            post_openai_creation = time.time()
            openai_creation_time = post_openai_creation - pre_openai_creation
            logger.info(f"TIMING: OpenAI LLM client creation took {openai_creation_time:.3f}s")

            return llm

        elif isinstance(config, GeminiLLMConfig):
            return ChatGoogleGenerativeAI(
                model=config.model,
                temperature=0.2,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                google_api_key=config.api_key,
                callbacks=[cost_callback],
            )

        elif isinstance(config, AnthropicLLMConfig):
            return ChatAnthropic(
                model=config.model,
                temperature=0.2,
                timeout=None,
                max_retries=2,
                api_key=config.api_key,
                callbacks=[cost_callback]
            )
        elif isinstance(config, AwsBedrockLLMConfig):
            return ChatBedrock(
                model=config.model,
                temperature=0.2,
                aws_access_key_id=config.access_key,
                aws_secret_access_key=config.access_secret,
                region_name=config.region,
                callbacks=[cost_callback]
            )
        elif isinstance(config, OllamaConfig):
            base_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434") # Set default value directly in getenv

            return OllamaLLM(
                model=config.model,
                temperature=0.2,
                callbacks=[cost_callback],
                base_url=base_url
            )
        elif isinstance(config, OpenAICompatibleLLMConfig):
            # TIMING: Log the time before OpenAI Compatible LLM creation
            pre_openai_compat_creation = time.time()

            llm = ChatOpenAI(
                model=config.model,
                temperature=0.2,
                api_key=config.api_key,
                base_url=config.endpoint,
                callbacks=[cost_callback],
                # Add timeout and connection optimization settings
                timeout=30.0,
                max_retries=2,
                request_timeout=30.0,
                # Additional optimization parameters
                max_tokens=None,  # Let the model decide
                streaming=True,  # Enable streaming for better performance
            )

            # TIMING: Log the time after OpenAI Compatible LLM creation
            post_openai_compat_creation = time.time()
            openai_compat_creation_time = post_openai_compat_creation - pre_openai_compat_creation
            logger.info(f"TIMING: OpenAI Compatible LLM client creation took {openai_compat_creation_time:.3f}s")

            return llm

        # TIMING: Log total LLM creation time
        total_time = time.time() - start_time
        logger.info(f"TIMING: Total LLM creation took {total_time:.3f}s")

        raise ValueError(f"Unsupported config type: {type(config)}")

    @staticmethod
    def get_usage_stats(llm: BaseChatModel) -> Dict:
        """Get usage statistics from the LLM's callback handler"""
        for callback in llm.callbacks:
            if isinstance(callback, CostTrackingCallback):
                return {
                    "tokens_in": callback.current_usage["tokens_in"],
                    "tokens_out": callback.current_usage["tokens_out"],
                    "processing_time": (
                        (
                            callback.current_usage["end_time"]
                            - callback.current_usage["start_time"]
                        ).total_seconds()
                        if callback.current_usage["end_time"]
                        else None
                    ),
                    "cost": callback.calculate_cost(llm.model_name),
                }
        return {}
