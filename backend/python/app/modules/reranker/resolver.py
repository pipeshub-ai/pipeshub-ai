"""Which reranker, if any, reorders search results for the current request."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.modules.reranker.factory import create_reranker
from app.services.featureflag.platform_settings import is_reranker_enabled
from app.utils.aimodels import RerankerProvider
from app.utils.llm import get_reranker_config

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.reranker.interface import IReranker

logger = logging.getLogger(__name__)

_SYSTEM_DEFAULT_CONFIG: dict[str, Any] = {
    "provider": RerankerProvider.DEFAULT.value,
    "configuration": {},
}


class RerankerResolver:
    """Returns the reranker to use now, or ``None`` when reranking is off.

    The Labs flag and the model config are read on every call, so a change in
    the admin UI applies to the next search. The client is rebuilt only when
    the config changes. Turning the flag on with no reranker configured uses
    the system default model.
    """

    def __init__(self, config_service: ConfigurationService) -> None:
        self._config_service = config_service
        self._config: dict[str, Any] | None = None
        self._reranker: IReranker | None = None

    async def active(self) -> IReranker | None:
        try:
            if not await is_reranker_enabled(self._config_service):
                return None
            config = await get_reranker_config(self._config_service) or _SYSTEM_DEFAULT_CONFIG
            if config != self._config or self._reranker is None:
                self._reranker = create_reranker(config)
                self._config = config
            return self._reranker
        except Exception as exc:  # search must still work without a reranker
            logger.warning("Reranker unavailable, keeping retrieval order: %s", exc)
            return None
