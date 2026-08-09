"""AppEvent ingress — receives raw provider webhooks and emits to Topic.APP_EVENTS.

Lives in the connectors service (port 8088) where app credentials already live.
Security order: verify signature → rate limit → dedupe → normalize → publish.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.services.events.models import RawWebhookRequest
from app.services.events.verifiers.base import VerificationError, get_verifier_registry

if TYPE_CHECKING:
    from app.services.events.models import AppCredentials
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.tasks.interface.rate_limiter import IRateLimiter

__all__ = ["AppEventIngress"]

logger = logging.getLogger(__name__)

# Generous enough for a busy Slack workspace, low enough that a compromised
# signing secret cannot be used to flood the workflow engine.
_DEFAULT_RATE_LIMIT = 600
_DEFAULT_RATE_WINDOW_S = 60


class AppEventIngress:
    """Receives raw webhooks, verifies, normalizes, deduplicates, and publishes."""

    def __init__(
        self,
        *,
        producer: "IMessagingProducer",
        redis_client: Any = None,          # for dedupe
        dedupe_ttl_seconds: int = 3600,
        rate_limiter: "IRateLimiter | None" = None,
        rate_limit: int = _DEFAULT_RATE_LIMIT,
        rate_limit_window_seconds: int = _DEFAULT_RATE_WINDOW_S,
    ) -> None:
        self._producer = producer
        self._redis = redis_client
        self._dedupe_ttl = dedupe_ttl_seconds
        self._verifier_registry = get_verifier_registry()
        # The sibling task-webhook path is limited; this one was not, and it is
        # equally unauthenticated. Reuses `IRateLimiter` rather than adding a
        # second limiter implementation.
        self._rate_limiter = rate_limiter
        self._rate_limit = rate_limit
        self._rate_limit_window = rate_limit_window_seconds

    async def _within_rate_limit(self, org_id: str, source_app: str) -> bool:
        """Per org and app, so one noisy install cannot starve another's events.

        Applied after verification: an unverified request must not be able to
        consume a tenant's budget.
        """
        try:
            return await self._rate_limiter.allow(  # type: ignore[union-attr]
                f"app_events:{org_id}:{source_app}",
                limit=self._rate_limit,
                window_seconds=self._rate_limit_window,
            )
        except Exception:
            # Redis down is not a reason to drop a provider webhook that
            # already proved its signature; providers rarely redeliver.
            logger.exception("app-events: rate limiter failed, allowing event through")
            return True

    async def handle(
        self,
        *,
        source_app: str,
        req: RawWebhookRequest,
        credentials: "AppCredentials",
    ) -> dict[str, Any]:
        """Handle one inbound webhook. Returns {ok, event_type, dedupe_key, action}.

        'action' is one of: published, dedupe_skipped, rate_limited,
        verification_failed, url_verification, unsupported_app.
        """
        verifier = self._verifier_registry.get(source_app)
        if verifier is None:
            logger.warning("No verifier for source_app=%s", source_app)
            return {"ok": False, "action": "unsupported_app", "source_app": source_app}

        try:
            event = await verifier.verify(req, credentials)
        except VerificationError as exc:
            msg = str(exc)
            if msg.startswith("URL_VERIFICATION:"):
                # Slack challenge — respond with the challenge token
                challenge = msg.removeprefix("URL_VERIFICATION:")
                return {"ok": True, "action": "url_verification", "challenge": challenge}
            # Logged at warning with the org: an unauthenticated endpoint with
            # no server-side record of failed verification leaves a
            # signature-guessing attempt invisible.
            logger.warning(
                "app-events: verification failed source_app=%s org=%s reason=%s",
                source_app, credentials.org_id, exc,
            )
            return {"ok": False, "action": "verification_failed", "error": str(exc)}

        if self._rate_limiter is not None and not await self._within_rate_limit(event.org_id, source_app):
            logger.warning(
                "app-events: rate limit exceeded org=%s source_app=%s", event.org_id, source_app,
            )
            return {"ok": False, "action": "rate_limited", "org_id": event.org_id}

        # Deduplication, scoped by org: provider event ids are unique per
        # provider install, not globally, so a global key drops the second org's
        # copy of the same Slack `event_id` as a duplicate -- silent
        # cross-tenant event loss.
        if self._redis and event.dedupe_key:
            dedupe_redis_key = f"wf:event:dedupe:{event.org_id}:{source_app}:{event.dedupe_key}"
            was_new = await self._redis.set(
                dedupe_redis_key, "1", nx=True, ex=self._dedupe_ttl
            )
            if not was_new:
                logger.debug("Duplicate event dedupe_key=%s, skipping", event.dedupe_key)
                return {"ok": True, "action": "dedupe_skipped", "dedupe_key": event.dedupe_key}

        # Publish
        from app.services.messaging.config import Topic
        await self._producer.send_event(
            topic=Topic.APP_EVENTS.value,
            event_type=event.event_type,
            payload={
                "org_id": event.org_id,
                "source_app": event.source_app,
                "event_type": event.event_type,
                "payload": event.payload,
                "occurred_at": event.occurred_at.isoformat(),
                "dedupe_key": event.dedupe_key,
                "chain_depth": event.chain_depth,
            },
        )
        logger.info(
            "Published app event: %s/%s org=%s dedupe_key=%s",
            source_app, event.event_type, event.org_id, event.dedupe_key,
        )
        return {"ok": True, "action": "published", "event_type": event.event_type, "dedupe_key": event.dedupe_key}
