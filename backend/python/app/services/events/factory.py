"""Composition root for the app-event ingress.

Keeps `connectors_main.py` free of wiring detail and gives tests a single seam
to build an `AppEventIngress` with fakes. Importing this module also imports
the verifier package, which is what registers each provider verifier into
`get_verifier_registry()` -- without it the ingress rejects every source_app as
unsupported.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import app.services.events.verifiers  # noqa: F401  -- registers the verifiers
from app.services.events.ingress import AppEventIngress
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.tasks.task_store_provider_factory import TaskScheduleStoreFactory

if TYPE_CHECKING:
    from app.containers.connector import ConnectorAppContainer
    from app.services.messaging.interface.producer import IMessagingProducer

__all__ = ["build_app_event_ingress"]

logger = logging.getLogger(__name__)


async def build_app_event_ingress(
    app_container: "ConnectorAppContainer",
) -> AppEventIngress:
    """Build the ingress from an initialized connectors DI container.

    Reuses the container's already-started messaging producer rather than
    opening a second one; falls back to creating a dedicated producer only when
    the container has none.
    """
    config_service = app_container.config_service()

    producer: IMessagingProducer | None = getattr(app_container, "messaging_producer", None)
    if producer is None:
        producer_config = await MessagingUtils.create_producer_config_from_service(
            config_service, client_id="app_event_ingress_producer",
        )
        producer = MessagingFactory.create_producer(
            logging.getLogger("app.services.events.producer"), producer_config,
        )
        await producer.initialize()

    redis_client = None
    try:
        redis_config = await config_service.get_redis_config()
        redis_client = await TaskScheduleStoreFactory.create_redis_client(redis_config)
    except Exception:
        # Dedupe degrades to at-least-once delivery; workflow triggers are
        # expected to tolerate that, so this is not fatal.
        logger.warning("app-events: Redis unavailable, provider redelivery will not be deduplicated")

    rate_limiter = None
    if redis_client is not None:
        from app.services.tasks.adapters.redis.rate_limiter import RedisRateLimiter

        rate_limiter = RedisRateLimiter(redis_client)

    return AppEventIngress(
        producer=producer, redis_client=redis_client, rate_limiter=rate_limiter,
    )
