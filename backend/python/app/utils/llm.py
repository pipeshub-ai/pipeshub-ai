import os

from langchain.chat_models.base import BaseChatModel

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.ai_models_named_constants import (
    AzureOpenAILLM,
    LLMProvider,
)
from app.core.llm_service import (
    AnthropicLLMConfig,
    AwsBedrockLLMConfig,
    AzureLLMConfig,
    GeminiLLMConfig,
    LLMFactory,
    OllamaConfig,
    OpenAICompatibleLLMConfig,
    OpenAILLMConfig,
)


async def get_llm(logger, config_service: ConfigurationService, llm_configs = None) -> BaseChatModel:
    if not llm_configs:
        ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value)
        llm_configs = ai_models["llm"]
    # For now, we'll use the first available provider that matches our supported types
    # We will add logic to choose a specific provider based on our needs
    llm_config = None

    for config in llm_configs:
        provider = config["provider"]
        if provider == LLMProvider.AZURE_OPENAI.value:
            llm_config = AzureLLMConfig(
                model=config["configuration"]["model"],
                temperature=0.2,
                api_key=config["configuration"]["apiKey"],
                azure_endpoint=config["configuration"]["endpoint"],
                azure_api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
                azure_deployment=config["configuration"]["deploymentName"],
            )
            break
        elif provider == LLMProvider.OPENAI.value:
            llm_config = OpenAILLMConfig(
                model=config["configuration"]["model"],
                temperature=0.2,
                api_key=config["configuration"]["apiKey"],
            )
            break
        elif provider == LLMProvider.GEMINI.value:
            llm_config = GeminiLLMConfig(
                model=config["configuration"]["model"],
                temperature=0.2,
                api_key=config["configuration"]["apiKey"],
            )
        elif provider == LLMProvider.ANTHROPIC.value:
            llm_config = AnthropicLLMConfig(
                model=config["configuration"]["model"],
                temperature=0.2,
                api_key=config["configuration"]["apiKey"],
            )
        elif provider == LLMProvider.AWS_BEDROCK.value:
            llm_config = AwsBedrockLLMConfig(
                model=config["configuration"]["model"],
                temperature=0.2,
                region=config["configuration"]["region"],
                access_key=config["configuration"]["aws_access_key_id"],
                access_secret=config["configuration"]["aws_access_secret_key"],
                api_key=config["configuration"]["aws_access_secret_key"],
            )
        elif provider == LLMProvider.OLLAMA.value:
            llm_config = OllamaConfig(
                model=config['configuration']['model'],
                temperature=0.2,
                api_key=config['configuration'].get('apiKey', ''),
                base_url = config['configuration'].get('endpoint', os.getenv("OLLAMA_API_URL", "http://localhost:11434")) # Set default value directly in getenv
            )
        elif provider == LLMProvider.OPENAI_COMPATIBLE.value:
            llm_config = OpenAICompatibleLLMConfig(
                model=config['configuration']['model'],
                temperature=0.2,
                api_key=config['configuration'].get('apiKey', ''),
                endpoint=config['configuration']['endpoint'],
            )
    if not llm_config:
        raise ValueError("No supported LLM provider found in configuration")

    llm = LLMFactory.create_llm(logger, llm_config)

    return llm
