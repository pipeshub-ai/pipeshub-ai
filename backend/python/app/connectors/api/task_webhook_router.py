"""Inbound webhook ingress for `webhook`-kind `TaskTrigger`s (Phase 8 of
the task engine plan). Lives in the connectors service (port 8088),
following the plan's Part F1 precedent ("Webhook ingress on Python (8088),
following existing Google Drive webhook pattern") rather than the query
service where the rest of the task engine's chat-facing tools live --
inbound webhooks are unauthenticated-by-JWT ingress, exactly like the
existing (stub) Drive/Gmail webhook paths this router is excluded from auth
alongside in `connectors_main.py`.

Own HMAC + timestamp + nonce verification (`WebhookDispatchService`)
substitutes for the request-level JWT auth every other route on this
service requires -- see `EXCLUDE_PATH_PREFIXES` in `connectors_main.py`.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import Routes
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.tasks.adapters.config.webhook_secret_store import (
    ConfigServiceWebhookSecretStore,
)
from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.adapters.redis.nonce_store import RedisNonceStore
from app.services.tasks.adapters.redis.rate_limiter import RedisRateLimiter
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.application.webhook_dispatch import WebhookDispatchService
from app.services.tasks.domain.errors import (
    RateLimitExceededError,
    TaskEngineError,
    TaskNotFoundError,
    TriggerNotFoundError,
    WebhookVerificationError,
)
from app.services.tasks.task_store_provider_factory import TaskScheduleStoreFactory

__all__ = ["router", "WEBHOOK_PATH_PREFIX"]

logger = logging.getLogger(__name__)

WEBHOOK_PATH_PREFIX = Routes.TASK_WEBHOOK_PREFIX.value

_SIGNATURE_HEADER = "X-PipesHub-Signature"
_TIMESTAMP_HEADER = "X-PipesHub-Timestamp"
_NONCE_HEADER = "X-PipesHub-Nonce"

router = APIRouter()

_init_lock = asyncio.Lock()
_cached_service: WebhookDispatchService | None = None

# Generic messages only -- never echo back the `WebhookVerificationError.reason`
# (see that class's own docstring on why).
_REJECTION_STATUS: dict[str, int] = {
    "unknown_webhook": HttpStatusCode.NOT_FOUND.value,
    "no_secret": HttpStatusCode.NOT_FOUND.value,
    "missing_headers": HttpStatusCode.BAD_REQUEST.value,
    "invalid_timestamp": HttpStatusCode.BAD_REQUEST.value,
    "expired_timestamp": HttpStatusCode.UNAUTHORIZED.value,
    "replayed_nonce": HttpStatusCode.CONFLICT.value,
    "bad_signature": HttpStatusCode.UNAUTHORIZED.value,
}


async def _get_dispatch_service(request: Request) -> WebhookDispatchService | None:
    """Process-level cached singleton, same rationale as
    `tasks_wiring.shared_task_redis_client`/`shared_task_producer`: this handler
    fires on every inbound webhook request and must not open a fresh Redis
    connection + producer per call."""
    global _cached_service
    if _cached_service is not None:
        return _cached_service
    async with _init_lock:
        if _cached_service is not None:
            return _cached_service
        graph_provider = getattr(request.app.state, "graph_provider", None)
        if graph_provider is None:
            logger.error("task webhook: no graph_provider on app.state -- cannot build dispatch service")
            return None
        try:
            config_service = request.app.container.config_service()  # type: ignore[attr-defined]
            redis_config = await config_service.get_redis_config()
            redis_client = await TaskScheduleStoreFactory.create_redis_client(redis_config)
            trigger_store = await TaskScheduleStoreFactory.create_trigger_store(
                logger, redis_config, redis_client=redis_client,
            )
            run_store = await TaskScheduleStoreFactory.create_run_store(
                logger, redis_config, redis_client=redis_client, graph_provider=graph_provider,
            )
            producer_config = await MessagingUtils.create_producer_config_from_service(
                config_service, client_id="task_webhook_producer",
            )
            producer = MessagingFactory.create_producer(
                logging.getLogger("app.services.tasks.webhook_producer"), producer_config,
            )
            engine = TaskEngine(
                task_store=GraphTaskStore(graph_provider),
                trigger_store=trigger_store,
                run_store=run_store,
                producer=producer,
                logger=logger,
            )
            _cached_service = WebhookDispatchService(
                engine=engine,
                trigger_store=trigger_store,
                secret_store=ConfigServiceWebhookSecretStore(config_service),
                nonce_store=RedisNonceStore(redis_client),
                rate_limiter=RedisRateLimiter(redis_client),
            )
        except Exception:
            logger.exception("task webhook: failed to build dispatch service dependencies")
            return None
    return _cached_service


@router.post(WEBHOOK_PATH_PREFIX + "{webhook_id}")
async def receive_task_webhook(webhook_id: str, request: Request) -> JSONResponse:
    service = await _get_dispatch_service(request)
    if service is None:
        return JSONResponse(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            content={"detail": "Task webhook processing is temporarily unavailable"},
        )

    raw_body = await request.body()
    try:
        run = await service.handle(
            webhook_id=webhook_id,
            signature=request.headers.get(_SIGNATURE_HEADER, ""),
            timestamp=request.headers.get(_TIMESTAMP_HEADER, ""),
            nonce=request.headers.get(_NONCE_HEADER, ""),
            raw_body=raw_body,
        )
    except RateLimitExceededError:
        logger.warning("task webhook: rate limited webhook_id=%s", webhook_id)
        return JSONResponse(
            status_code=HttpStatusCode.TOO_MANY_REQUESTS.value, content={"detail": "Rate limit exceeded"},
        )
    except WebhookVerificationError as exc:
        # The response deliberately says nothing about which check failed, so
        # the log is the only place a signature-guessing run against this
        # unauthenticated endpoint is visible at all.
        logger.warning(
            "task webhook: verification failed webhook_id=%s reason=%s",
            webhook_id, exc.reason,
        )
        status_code = _REJECTION_STATUS.get(exc.reason, HttpStatusCode.UNAUTHORIZED.value)
        return JSONResponse(status_code=status_code, content={"detail": "Webhook verification failed"})
    except (TriggerNotFoundError, TaskNotFoundError):
        logger.warning("task webhook: unknown webhook_id=%s", webhook_id)
        return JSONResponse(
            status_code=HttpStatusCode.NOT_FOUND.value, content={"detail": "Webhook verification failed"},
        )
    except TaskEngineError as exc:
        logger.warning("task webhook: rejected webhook_id=%s: %s", webhook_id, exc)
        return JSONResponse(status_code=HttpStatusCode.BAD_REQUEST.value, content={"detail": str(exc)})
    except Exception:
        logger.exception("task webhook: unhandled error dispatching webhook %s", webhook_id)
        return JSONResponse(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, content={"detail": "Internal server error"},
        )

    return JSONResponse(
        status_code=HttpStatusCode.ACCEPTED.value,
        content={"run_id": run.run_id, "task_id": run.task_id, "status": run.status.value},
    )
