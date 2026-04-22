from typing import Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.aimodels import (
    STTAdapter,
    TTSAdapter,
    get_generator_model,
    get_stt_model,
    get_tts_model,
)


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

async def get_embedding_model_config(config_service: ConfigurationService) -> dict|None:
        try:
            ai_models = await config_service.get_config(
                config_node_constants.AI_MODELS.value,use_cache=False
            )
            embedding_configs = ai_models["embedding"]
            if not embedding_configs:
                return None
            else:
                config = embedding_configs[0]
                return config
        except Exception as e:
            raise e


async def get_image_generation_config(config_service: ConfigurationService) -> dict | None:
    """Return the active image-generation model config, or ``None`` if unset.

    Prefers the config marked ``isDefault``; falls back to the first entry.
    Mirrors the shape returned for other model types under the ``aiModels``
    namespace.
    """
    ai_models = await config_service.get_config(
        config_node_constants.AI_MODELS.value, use_cache=False,
    )
    configs = ai_models.get("imageGeneration") or []
    if not configs:
        return None
    return next((c for c in configs if c.get("isDefault")), configs[0])


async def _get_speech_config(
    config_service: ConfigurationService,
    bucket: str,
) -> dict | None:
    """Shared helper: return the default entry under ``ai_models[bucket]``.

    Gracefully returns ``None`` when the whole ``aiModels`` blob is missing
    (e.g. on a brand-new install) so callers — and the chat UI fallback —
    can treat TTS/STT as simply unconfigured instead of erroring.
    """
    ai_models = await config_service.get_config(
        config_node_constants.AI_MODELS.value, use_cache=False,
    )
    if not ai_models:
        return None
    configs = ai_models.get(bucket) or []
    if not configs:
        return None
    return next((c for c in configs if c.get("isDefault")), configs[0])


async def get_tts_config(config_service: ConfigurationService) -> dict | None:
    """Return the active TTS model config, or ``None`` if unset."""
    return await _get_speech_config(config_service, "tts")


async def get_stt_config(config_service: ConfigurationService) -> dict | None:
    """Return the active STT model config, or ``None`` if unset."""
    return await _get_speech_config(config_service, "stt")


async def get_tts_model_instance(
    config_service: ConfigurationService,
) -> Tuple[TTSAdapter, dict] | None:
    config = await get_tts_config(config_service)
    if not config:
        return None
    return get_tts_model(config["provider"], config), config


async def get_stt_model_instance(
    config_service: ConfigurationService,
) -> Tuple[STTAdapter, dict] | None:
    config = await get_stt_config(config_service)
    if not config:
        return None
    return get_stt_model(config["provider"], config), config

