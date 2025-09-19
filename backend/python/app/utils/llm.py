from typing import Tuple

from langchain.chat_models.base import BaseChatModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.aimodels import get_generator_model


async def get_llm(config_service: ConfigurationService, llm_configs = None) -> Tuple[BaseChatModel, dict]:
    if not llm_configs:
        ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value,use_cache=False)
        llm_configs = ai_models["llm"]

    if not llm_configs:
        raise ValueError("No LLM configurations found")

    for config in llm_configs:
        if config.get("isDefault", False):
            llm = get_generator_model(config["provider"], config)
            if llm:
                return llm, config

    for config in llm_configs:
        llm = get_generator_model(config["provider"], config)
        if llm:
            return llm, config


    raise ValueError("No LLM found")
