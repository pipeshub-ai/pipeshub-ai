import os
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

    def __init__(self, logger):
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

    def on_llm_start(self, *args, **kwargs):
        self.current_usage["start_time"] = datetime.now()

    def on_llm_end(self, *args, **kwargs):
        self.current_usage["end_time"] = datetime.now()

    def on_llm_new_token(self, *args, **kwargs):
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
        cost_callback = CostTrackingCallback(logger)

        if isinstance(config, AzureLLMConfig):
            return AzureChatOpenAI(
                api_key=config.api_key,
                model=config.model,
                azure_endpoint=config.azure_endpoint,
                api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                temperature=0.2,
                azure_deployment=config.azure_deployment,
                callbacks=[cost_callback],
            )

        elif isinstance(config, OpenAILLMConfig):
            return ChatOpenAI(
                model=config.model,
                temperature=0.2,
                api_key=config.api_key,
                organization=config.organization_id,
                callbacks=[cost_callback],
            )

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
            return ChatOpenAI(
                model=config.model,
                temperature=0.2,
                api_key=config.api_key,
                base_url=config.endpoint,
                callbacks=[cost_callback]
            )

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
