"""`WebhookDispatchService`: verifies and dispatches an inbound webhook
request for a `webhook`-kind `TaskTrigger` (Part E of the plan: "Webhook
secrets in EncryptedKeyValueStore, HMAC + timestamp + nonce").

Deliberately separate from `TaskEngine` itself -- `TaskEngine.fire_trigger`
is the generic "make a trigger fire" operation shared with `fire_event`;
everything HMAC/replay/rate-limit specific to the HTTP webhook transport
lives here so `TaskEngine` stays transport-agnostic.

Verification order matters:
  1. rate limit (cheap, protects against volume before any lookups)
  2. resolve trigger by webhook_id (one Redis read)
  3. resolve secret (one more read)
  4. timestamp skew (pure, cheap)
  5. HMAC comparison (constant-time) -- BEFORE nonce consumption. If nonce
     tracking ran first, an attacker who merely observes a nonce in transit
     (nonces are not secret) could send a bogus-signature request reusing
     that nonce, burn its slot, and cause the legitimate sender's genuine
     (correctly-signed) request to be rejected as a replay once it arrives.
     Only a request that already proved it holds the secret gets to consume
     the nonce slot.
  6. nonce replay (a Redis write -- last, and only reached once the request
     is already known-authentic)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING

from app.services.tasks.domain.errors import (
    RateLimitExceededError,
    WebhookVerificationError,
)

if TYPE_CHECKING:
    from app.services.tasks.application.engine import TaskEngine
    from app.services.tasks.domain.models import TaskRun
    from app.services.tasks.interface.nonce_store import INonceStore
    from app.services.tasks.interface.rate_limiter import IRateLimiter
    from app.services.tasks.interface.trigger_store import ITriggerStore
    from app.services.tasks.interface.webhook_secret_store import IWebhookSecretStore

__all__ = ["WebhookDispatchService", "compute_signature", "verify_signature"]

DEFAULT_MAX_CLOCK_SKEW_SECONDS = 300
DEFAULT_NONCE_TTL_SECONDS = 600
DEFAULT_RATE_LIMIT = 30
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
_MAX_RAW_BODY_CHARS = 8192


def compute_signature(secret: str, *, timestamp: str, nonce: str, raw_body: bytes) -> str:
    """`HMAC-SHA256(secret, "{timestamp}.{nonce}." + raw_body)`, hex-encoded.
    Binding `timestamp`/`nonce` into the signed material (not just checking
    them separately) prevents an attacker who intercepts one valid request
    from replaying the same body with a different timestamp/nonce pair and
    forging a "new" valid signature without knowing the secret."""
    message = f"{timestamp}.{nonce}.".encode() + raw_body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(secret: str, *, timestamp: str, nonce: str, raw_body: bytes, signature: str) -> bool:
    expected = compute_signature(secret, timestamp=timestamp, nonce=nonce, raw_body=raw_body)
    return hmac.compare_digest(expected, signature)


def _decode_webhook_body(raw_body: bytes) -> dict:
    """Best-effort JSON decode of an already-authenticated webhook body, so a
    code workflow can read what triggered it. A non-JSON or non-object body is
    surfaced under `_raw` rather than dropped."""
    if not raw_body:
        return {}
    try:
        decoded = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_raw": raw_body.decode("utf-8", errors="replace")[:_MAX_RAW_BODY_CHARS]}
    if isinstance(decoded, dict):
        return decoded
    return {"_raw": decoded}


class WebhookDispatchService:
    def __init__(
        self,
        *,
        engine: "TaskEngine",
        trigger_store: "ITriggerStore",
        secret_store: "IWebhookSecretStore",
        nonce_store: "INonceStore",
        rate_limiter: "IRateLimiter",
        max_clock_skew_seconds: int = DEFAULT_MAX_CLOCK_SKEW_SECONDS,
        nonce_ttl_seconds: int = DEFAULT_NONCE_TTL_SECONDS,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        rate_limit_window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self._engine = engine
        self._trigger_store = trigger_store
        self._secret_store = secret_store
        self._nonce_store = nonce_store
        self._rate_limiter = rate_limiter
        self._max_clock_skew_seconds = max_clock_skew_seconds
        self._nonce_ttl_seconds = nonce_ttl_seconds
        self._rate_limit = rate_limit
        self._rate_limit_window_seconds = rate_limit_window_seconds

    async def handle(
        self, *, webhook_id: str, signature: str, timestamp: str, nonce: str, raw_body: bytes,
    ) -> "TaskRun":
        """Raises `RateLimitExceededError` or `WebhookVerificationError`
        (see its `reason` attribute for the specific cause: `unknown_webhook`,
        `no_secret`, `missing_headers`, `invalid_timestamp`, `expired_timestamp`,
        `bad_signature`, `replayed_nonce`) on rejection, otherwise dispatches
        via `TaskEngine.fire_trigger` and returns the resulting `TaskRun`."""
        if not await self._rate_limiter.allow(
            webhook_id, limit=self._rate_limit, window_seconds=self._rate_limit_window_seconds,
        ):
            raise RateLimitExceededError(f"Rate limit exceeded for webhook {webhook_id}")

        trigger = await self._trigger_store.get_by_webhook_id(webhook_id)
        if trigger is None or not trigger.enabled:
            raise WebhookVerificationError("unknown_webhook", "Unknown or disabled webhook")

        secret = await self._secret_store.get(webhook_id)
        if secret is None:
            raise WebhookVerificationError("no_secret", "No secret configured for webhook")

        if not signature or not timestamp or not nonce:
            raise WebhookVerificationError("missing_headers", "Missing signature/timestamp/nonce header")

        try:
            request_time = float(timestamp)
        except ValueError as exc:
            raise WebhookVerificationError("invalid_timestamp", "Timestamp header is not numeric") from exc
        if abs(time.time() - request_time) > self._max_clock_skew_seconds:
            raise WebhookVerificationError("expired_timestamp", "Timestamp outside allowed clock skew")

        if not verify_signature(secret, timestamp=timestamp, nonce=nonce, raw_body=raw_body, signature=signature):
            raise WebhookVerificationError("bad_signature", "Signature verification failed")

        first_seen = await self._nonce_store.check_and_set(
            webhook_id, nonce, ttl_seconds=self._nonce_ttl_seconds,
        )
        if not first_seen:
            raise WebhookVerificationError("replayed_nonce", "Nonce already used")

        return await self._engine.fire_trigger(
            trigger.trigger_id,
            payload=_decode_webhook_body(raw_body),
            dedupe_token=nonce,
        )
