"""Unit tests for `WebhookDispatchService` and the pure `compute_signature`/
`verify_signature` helpers -- Phase 8's HMAC + timestamp + nonce + rate
limit gate for inbound webhook requests.

Uses plain in-memory fakes for every port (`ITriggerStore`/
`IWebhookSecretStore`/`INonceStore`/`IRateLimiter`) and a `FakeTaskEngine`
double (only `fire_trigger` is called) so these tests exercise
`WebhookDispatchService`'s own verification-order/rejection-reason logic in
isolation -- each port's real adapter is proven separately by its own
`adapters/test_*` suite.
"""
from __future__ import annotations

import time

import pytest

from app.services.tasks.application.webhook_dispatch import (
    WebhookDispatchService,
    compute_signature,
    verify_signature,
)
from app.services.tasks.domain.errors import (
    RateLimitExceededError,
    WebhookVerificationError,
)
from app.services.tasks.domain.models import (
    RunStatus,
    TaskRun,
    TaskTrigger,
    TriggerKind,
)


def _make_trigger(**overrides: object) -> TaskTrigger:
    defaults: dict[str, object] = {
        "task_id": "task-1", "org_id": "org-1", "kind": TriggerKind.WEBHOOK, "webhook_id": "wh-1",
    }
    defaults.update(overrides)
    return TaskTrigger(**defaults)


class FakeTriggerStore:
    def __init__(self, triggers: dict[str, TaskTrigger] | None = None) -> None:
        self._by_webhook_id = triggers or {}

    async def get_by_webhook_id(self, webhook_id: str) -> TaskTrigger | None:
        return self._by_webhook_id.get(webhook_id)


class FakeSecretStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    async def get(self, webhook_id: str) -> str | None:
        return self._secrets.get(webhook_id)


class FakeNonceStore:
    def __init__(self, *, always_first_seen: bool = True) -> None:
        self._always_first_seen = always_first_seen
        self._seen: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    async def check_and_set(self, scope: str, nonce: str, *, ttl_seconds: int) -> bool:
        self.calls.append((scope, nonce))
        if not self._always_first_seen:
            return False
        key = (scope, nonce)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


class FakeRateLimiter:
    def __init__(self, *, allow: bool = True) -> None:
        self._allow = allow
        self.calls: list[str] = []

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        self.calls.append(key)
        return self._allow


class FakeTaskEngine:
    def __init__(self) -> None:
        self.fired_trigger_ids: list[str] = []
        self.fired_payloads: list[dict | None] = []
        self.fired_dedupe_tokens: list[str | None] = []

    async def fire_trigger(
        self, trigger_id: str, *, payload: dict | None = None, dedupe_token: str | None = None,
    ) -> TaskRun:
        self.fired_trigger_ids.append(trigger_id)
        self.fired_payloads.append(payload)
        self.fired_dedupe_tokens.append(dedupe_token)
        return TaskRun(
            task_id="task-1", trigger_id=trigger_id, org_id="org-1", idempotency_key="k1",
            scheduled_for="now", created_at="now", status=RunStatus.PENDING,
        )


def _make_service(
    *,
    trigger: TaskTrigger | None,
    secret: str | None = "s3cr3t",
    nonce_store: FakeNonceStore | None = None,
    rate_limiter: FakeRateLimiter | None = None,
) -> tuple[WebhookDispatchService, FakeTaskEngine]:
    triggers = {trigger.webhook_id: trigger} if trigger is not None else {}
    secrets = {trigger.webhook_id: secret} if trigger is not None and secret is not None else {}
    engine = FakeTaskEngine()
    service = WebhookDispatchService(
        engine=engine,
        trigger_store=FakeTriggerStore(triggers),
        secret_store=FakeSecretStore(secrets),
        nonce_store=nonce_store or FakeNonceStore(),
        rate_limiter=rate_limiter or FakeRateLimiter(),
    )
    return service, engine


def _signed_request(secret: str, *, body: bytes = b'{"hello":"world"}') -> dict[str, object]:
    timestamp = str(time.time())
    nonce = "nonce-1"
    signature = compute_signature(secret, timestamp=timestamp, nonce=nonce, raw_body=body)
    return {"signature": signature, "timestamp": timestamp, "nonce": nonce, "raw_body": body}


class TestSignatureHelpers:
    def test_verify_signature_accepts_a_correctly_signed_request(self) -> None:
        signature = compute_signature("secret", timestamp="123", nonce="abc", raw_body=b"body")
        assert verify_signature("secret", timestamp="123", nonce="abc", raw_body=b"body", signature=signature) is True

    def test_verify_signature_rejects_wrong_secret(self) -> None:
        signature = compute_signature("secret", timestamp="123", nonce="abc", raw_body=b"body")
        assert verify_signature("other-secret", timestamp="123", nonce="abc", raw_body=b"body", signature=signature) is False

    def test_verify_signature_rejects_tampered_body(self) -> None:
        signature = compute_signature("secret", timestamp="123", nonce="abc", raw_body=b"body")
        assert verify_signature("secret", timestamp="123", nonce="abc", raw_body=b"tampered", signature=signature) is False

    def test_verify_signature_rejects_replayed_signature_with_different_nonce(self) -> None:
        """Binding timestamp/nonce into the signed material -- a captured
        valid signature must not verify against a different nonce."""
        signature = compute_signature("secret", timestamp="123", nonce="abc", raw_body=b"body")
        assert verify_signature("secret", timestamp="123", nonce="different", raw_body=b"body", signature=signature) is False


class TestHandleHappyPath:
    async def test_valid_request_dispatches_and_returns_run(self) -> None:
        trigger = _make_trigger()
        service, engine = _make_service(trigger=trigger)
        req = _signed_request("s3cr3t")

        run = await service.handle(webhook_id="wh-1", **req)

        assert engine.fired_trigger_ids == [trigger.trigger_id]
        assert run.trigger_id == trigger.trigger_id


class TestRateLimit:
    async def test_rate_limited_request_raises_before_any_lookup(self) -> None:
        trigger = _make_trigger()
        rate_limiter = FakeRateLimiter(allow=False)
        service, engine = _make_service(trigger=trigger, rate_limiter=rate_limiter)
        req = _signed_request("s3cr3t")

        with pytest.raises(RateLimitExceededError):
            await service.handle(webhook_id="wh-1", **req)
        assert engine.fired_trigger_ids == []


class TestUnknownOrDisabledWebhook:
    async def test_unknown_webhook_id_is_rejected(self) -> None:
        service, engine = _make_service(trigger=None)
        req = _signed_request("s3cr3t")

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="no-such-webhook", **req)
        assert exc_info.value.reason == "unknown_webhook"
        assert engine.fired_trigger_ids == []

    async def test_disabled_trigger_is_rejected(self) -> None:
        trigger = _make_trigger(enabled=False)
        service, engine = _make_service(trigger=trigger)
        req = _signed_request("s3cr3t")

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **req)
        assert exc_info.value.reason == "unknown_webhook"

    async def test_missing_secret_is_rejected(self) -> None:
        trigger = _make_trigger()
        service, _engine = _make_service(trigger=trigger, secret=None)
        req = _signed_request("s3cr3t")

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **req)
        assert exc_info.value.reason == "no_secret"


class TestHeaderValidation:
    async def test_missing_signature_header_is_rejected(self) -> None:
        trigger = _make_trigger()
        service, _engine = _make_service(trigger=trigger)
        req = _signed_request("s3cr3t")
        req["signature"] = ""

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **req)
        assert exc_info.value.reason == "missing_headers"

    async def test_non_numeric_timestamp_is_rejected(self) -> None:
        trigger = _make_trigger()
        service, _engine = _make_service(trigger=trigger)
        req = _signed_request("s3cr3t")
        req["timestamp"] = "not-a-number"

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **req)
        assert exc_info.value.reason == "invalid_timestamp"

    async def test_expired_timestamp_is_rejected(self) -> None:
        trigger = _make_trigger()
        service, _engine = _make_service(trigger=trigger)
        old_timestamp = str(time.time() - 10_000)
        signature = compute_signature("s3cr3t", timestamp=old_timestamp, nonce="n1", raw_body=b"body")

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(
                webhook_id="wh-1", signature=signature, timestamp=old_timestamp, nonce="n1", raw_body=b"body",
            )
        assert exc_info.value.reason == "expired_timestamp"


class TestReplayProtection:
    async def test_replayed_nonce_is_rejected(self) -> None:
        trigger = _make_trigger()
        nonce_store = FakeNonceStore(always_first_seen=False)
        service, engine = _make_service(trigger=trigger, nonce_store=nonce_store)
        req = _signed_request("s3cr3t")

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **req)
        assert exc_info.value.reason == "replayed_nonce"
        assert engine.fired_trigger_ids == []

    async def test_bad_signature_does_not_burn_the_legit_nonce_slot(self) -> None:
        """Verification order: signature is checked BEFORE the nonce is
        consumed, so an attacker replaying an observed (not secret) nonce
        with a bogus signature cannot burn that nonce slot and cause the
        legitimate sender's genuine, correctly-signed request (same nonce)
        to be later rejected as a replay."""
        trigger = _make_trigger()
        nonce_store = FakeNonceStore()
        service, engine = _make_service(trigger=trigger, nonce_store=nonce_store)
        timestamp = str(time.time())
        bad_req = {
            "signature": "wrong-signature", "timestamp": timestamp, "nonce": "n1", "raw_body": b"body",
        }
        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(webhook_id="wh-1", **bad_req)
        assert exc_info.value.reason == "bad_signature"
        assert nonce_store.calls == []

        good_signature = compute_signature("s3cr3t", timestamp=timestamp, nonce="n1", raw_body=b"body")
        run = await service.handle(
            webhook_id="wh-1", signature=good_signature, timestamp=timestamp, nonce="n1", raw_body=b"body",
        )
        assert engine.fired_trigger_ids == [trigger.trigger_id]
        assert run is not None


class TestSignatureVerification:
    async def test_bad_signature_is_rejected(self) -> None:
        trigger = _make_trigger()
        service, engine = _make_service(trigger=trigger)

        with pytest.raises(WebhookVerificationError) as exc_info:
            await service.handle(
                webhook_id="wh-1", signature="not-the-right-signature",
                timestamp=str(time.time()), nonce="n1", raw_body=b"body",
            )
        assert exc_info.value.reason == "bad_signature"
        assert engine.fired_trigger_ids == []
