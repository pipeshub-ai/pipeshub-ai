from langchain.chat_models.base import BaseChatModel

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.utils.aimodels import get_generator_model


async def get_llm(config_service: ConfigurationService, llm_configs = None) -> BaseChatModel:
    if not llm_configs:
        ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value)
        llm_configs = ai_models["llm"]

    if not llm_configs:
        raise ValueError("No LLM configurations found")

    print(llm_configs, "llm_configs")
    for config in llm_configs:
        print(config, "config")
        llm = get_generator_model(config["provider"], config)
        print(llm, "llm")
        if llm:
            return llm

    raise ValueError("No LLM found")
