from app.connectors.core.base.event_service.event_service import BaseEventService
from app.modules.retrieval.retrieval_service import RetrievalService


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
        try:
            self.logger.info(f"Processing AI config event: {event_type}")

            if event_type == "llmConfigured":
                return await self.__handle_llm_configured(payload)
            elif event_type == "embeddingModelConfigured":
                return await self.__handle_embedding_configured(payload)
            elif event_type == "imageGenerationModelConfigured":
                return await self.__handle_image_generation_configured(payload)
            else:
                self.logger.error(f"Unknown AI config event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error processing AI config event: {str(e)}")
            return False

    async def __handle_llm_configured(self, payload: dict) -> bool:
        """Handle LLM configuration update

        Args:
            payload (dict): Event payload containing configuration details

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("📥 Processing LLM configured event")
            self.logger.debug(f"LLM config payload: {payload}")

            # Refresh the LLM instance with new configuration
            await self.retrieval_service.get_llm_instance(use_cache=False)

            self.logger.info("✅ Successfully updated LLM configuration in all services")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to update LLM configuration: {str(e)}")
            return False

    async def __handle_embedding_configured(self, payload: dict) -> bool:
        """Handle embedding model configuration update

        Args:
            payload (dict): Event payload containing configuration details

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("📥 Processing embedding model configured event")
            self.logger.debug(f"Embedding config payload: {payload}")

            # Refresh the embedding model instance with new configuration
            await self.retrieval_service.get_embedding_model_instance(use_cache=False)

            self.logger.info("✅ Successfully updated embedding model in all services")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to update embedding model configuration: {str(e)}")
            return False

    async def __handle_image_generation_configured(self, payload: dict) -> bool:
        """Handle image generation model configuration update

        Args:
            payload (dict): Event payload containing configuration details

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("📥 Processing image generation model configured event")
            self.logger.debug(f"Image generation config payload: {payload}")

            self.logger.info("✅ Successfully processed image generation model configuration event")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to process image generation model configuration: {str(e)}")
            return False
