from app.connectors.core.base.event_service.event_service import BaseEventService
from app.exceptions.non_retryable import NonRetryableError
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils import error_class
from app.utils.llm_api_mode_store import get_llm_api_mode_store


class AiConfigEventService(BaseEventService):
    def __init__(
        self,
        logger,
        retrieval_service: RetrievalService,
    ) -> None:
        super().__init__(logger)
        self.logger = logger
        self.retrieval_service = retrieval_service

    async def process_event(self, event_type: str, payload: dict) -> bool:
        """Handle AI configuration events by calling appropriate handlers"""
        if event_type == "llmConfigured":
            return await self.__handle_llm_configured(payload)
        if event_type == "embeddingModelConfigured":
            return await self.__handle_embedding_configured(payload)
        raise NonRetryableError(f"unknown AI config event type {event_type}")

    async def __handle_llm_configured(self, payload: dict) -> bool:
        """Handle LLM configuration update

        Args:
            payload (dict): Event payload containing configuration details

        Returns:
            bool: True if successful; a failure raises
        """
        try:
            self.logger.debug("Processing LLM configured event")

            # Refresh the LLM instance with new configuration
            await self.retrieval_service.get_llm_instance(use_cache=False)

            # This pod's own learned facts are already in its in-process
            # snapshot the instant `LangChainTransport._record_api_mode`
            # writes them — reloading here is to pick up facts a *different*
            # query-service pod learned and persisted to the shared KV store
            # since this pod's last load (see `app/utils/llm_api_mode_store
            # .py`). It does NOT invalidate/re-check a fact already learned
            # under this model's previous provider/endpoint: entries are
            # keyed only by (modelKey, model_name), which an admin edit
            # doesn't change, so a stale fact still rides out its
            # `_LEARNED_FACT_TTL_SECONDS` window regardless of this call.
            # No `config_service` passed: the store is already initialized
            # by service startup (`query_main.initialize_container`, which
            # runs before Kafka consumers start), so this only ever reads
            # the existing container-bound singleton.
            store = get_llm_api_mode_store()
            if store is not None:
                await store.load()

            self.logger.info("Successfully updated LLM configuration in all services")
            return True

        except Exception as e:
            error_class.log_failure(self.logger, "ai_config.refresh_failed", e)
            raise

    async def __handle_embedding_configured(self, payload: dict) -> bool:
        """Handle embedding model configuration update

        Args:
            payload (dict): Event payload containing configuration details

        Returns:
            bool: True if successful; a failure raises
        """
        try:
            self.logger.debug("Processing embedding model configured event")

            # Refresh the embedding model instance with new configuration
            await self.retrieval_service.get_embedding_model_instance(use_cache=False)

            self.logger.info("Successfully updated embedding model in all services")
            return True

        except Exception as e:
            error_class.log_failure(self.logger, "ai_config.refresh_failed", e)
            raise
