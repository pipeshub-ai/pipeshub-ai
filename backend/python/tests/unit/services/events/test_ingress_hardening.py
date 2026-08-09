"""`AppEventIngress` is the only unauthenticated entry point into the workflow
engine: whatever it publishes runs somebody's workflow with their tools. These
cover the three ways that goes wrong -- one org's event being mistaken for
another's, a captured request being replayed, and a flood.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest

from app.services.events.ingress import AppEventIngress
from app.services.events.models import AppCredentials, RawWebhookRequest

# Importing the package is what registers the provider verifiers.
from app.services.events.verifiers import VerificationError, assert_within_replay_window

_SECRET = "shhh"


class _RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_event(self, *, topic: str, event_type: str, payload: dict[str, Any]) -> None:
        self.sent.append({"topic": topic, "event_type": event_type, "payload": payload})


class _FakeRedis:
    """Just enough of `SET key val NX EX` to exercise the dedupe branch."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys[key] = value
        return True


class _FakeRateLimiter:
    def __init__(self, *, allow: bool = True) -> None:
        self._allow = allow
        self.keys: list[str] = []

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        self.keys.append(key)
        return self._allow


class _ExplodingRateLimiter:
    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        raise ConnectionError("redis is down")


def _github_request(*, delivery_id: str = "delivery-1", body: dict | None = None) -> RawWebhookRequest:
    raw = json.dumps(body or {"action": "opened", "repository": {"full_name": "acme/app"}}).encode()
    signature = "sha256=" + hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    headers = {
        "x-hub-signature-256": signature,
        "x-github-event": "pull_request",
    }
    if delivery_id:
        headers["x-github-delivery"] = delivery_id
    return RawWebhookRequest(headers=headers, body=raw)


def _jira_request(*, timestamp_ms: float) -> RawWebhookRequest:
    raw = json.dumps({
        "webhookEvent": "jira:issue_created",
        "timestamp": timestamp_ms,
        "issue": {"id": "1", "key": "OPS-1", "fields": {"summary": "s"}},
    }).encode()
    signature = "sha256=" + hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return RawWebhookRequest(headers={"x-hub-signature-256": signature}, body=raw)


def _slack_request(*, timestamp: int) -> RawWebhookRequest:
    raw = json.dumps({
        "event_id": "Ev1",
        "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1.0"},
    }).encode()
    base = f"v0:{timestamp}:{raw.decode()}"
    signature = "v0=" + hmac.new(_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    return RawWebhookRequest(
        headers={
            "x-slack-request-timestamp": str(timestamp),
            "x-slack-signature": signature,
            "content-type": "application/json",
        },
        body=raw,
    )


def _credentials(org_id: str) -> AppCredentials:
    return AppCredentials(org_id=org_id, signing_secret=_SECRET)


class TestDedupeIsOrgScoped:
    @pytest.mark.asyncio
    async def test_a_repeat_delivery_to_the_same_org_is_skipped(self) -> None:
        ingress = AppEventIngress(producer=_RecordingProducer(), redis_client=_FakeRedis())

        first = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-1"),
        )
        second = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-1"),
        )

        assert first["action"] == "published"
        assert second["action"] == "dedupe_skipped"

    @pytest.mark.asyncio
    async def test_two_orgs_sharing_a_delivery_id_both_publish(self) -> None:
        """Provider delivery ids are unique per install, not globally. A global
        dedupe key silently drops the second tenant's copy of the event.
        """
        producer = _RecordingProducer()
        ingress = AppEventIngress(producer=producer, redis_client=_FakeRedis())

        first = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-1"),
        )
        second = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-2"),
        )

        assert first["action"] == "published"
        assert second["action"] == "published"
        assert [call["payload"]["org_id"] for call in producer.sent] == ["org-1", "org-2"]


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_an_over_limit_org_is_rejected_before_publishing(self) -> None:
        producer = _RecordingProducer()
        ingress = AppEventIngress(
            producer=producer, redis_client=_FakeRedis(),
            rate_limiter=_FakeRateLimiter(allow=False),
        )

        result = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-1"),
        )

        assert result["action"] == "rate_limited"
        assert producer.sent == []

    @pytest.mark.asyncio
    async def test_the_budget_is_per_org_and_app(self) -> None:
        limiter = _FakeRateLimiter()
        ingress = AppEventIngress(
            producer=_RecordingProducer(), redis_client=_FakeRedis(), rate_limiter=limiter,
        )

        await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-7"),
        )

        assert limiter.keys == ["app_events:org-7:github"]

    @pytest.mark.asyncio
    async def test_an_unverified_request_never_reaches_the_limiter(self) -> None:
        """Otherwise anyone could exhaust a tenant's budget without knowing
        its signing secret."""
        limiter = _FakeRateLimiter()
        ingress = AppEventIngress(
            producer=_RecordingProducer(), redis_client=_FakeRedis(), rate_limiter=limiter,
        )
        bad = _github_request()
        bad.headers["x-hub-signature-256"] = "sha256=deadbeef"

        result = await ingress.handle(
            source_app="github", req=bad, credentials=_credentials("org-1"),
        )

        assert result["action"] == "verification_failed"
        assert limiter.keys == []

    @pytest.mark.asyncio
    async def test_a_broken_limiter_does_not_drop_a_verified_event(self) -> None:
        producer = _RecordingProducer()
        ingress = AppEventIngress(
            producer=producer, redis_client=_FakeRedis(),
            rate_limiter=_ExplodingRateLimiter(),
        )

        result = await ingress.handle(
            source_app="github", req=_github_request(), credentials=_credentials("org-1"),
        )

        assert result["action"] == "published"
        assert len(producer.sent) == 1


class TestReplayWindows:
    @pytest.mark.asyncio
    async def test_a_stale_jira_delivery_is_rejected(self) -> None:
        ingress = AppEventIngress(producer=_RecordingProducer(), redis_client=_FakeRedis())
        stale = _jira_request(timestamp_ms=(time.time() - 3600) * 1000)

        result = await ingress.handle(
            source_app="jira", req=stale, credentials=_credentials("org-1"),
        )

        assert result["action"] == "verification_failed"

    @pytest.mark.asyncio
    async def test_a_fresh_jira_delivery_is_accepted(self) -> None:
        ingress = AppEventIngress(producer=_RecordingProducer(), redis_client=_FakeRedis())
        fresh = _jira_request(timestamp_ms=time.time() * 1000)

        result = await ingress.handle(
            source_app="jira", req=fresh, credentials=_credentials("org-1"),
        )

        assert result["action"] == "published"

    @pytest.mark.asyncio
    async def test_a_stale_slack_delivery_is_rejected(self) -> None:
        ingress = AppEventIngress(producer=_RecordingProducer(), redis_client=_FakeRedis())
        stale = _slack_request(timestamp=int(time.time()) - 3600)

        result = await ingress.handle(
            source_app="slack", req=stale, credentials=_credentials("org-1"),
        )

        assert result["action"] == "verification_failed"

    @pytest.mark.asyncio
    async def test_github_requires_a_delivery_id_it_can_dedupe_on(self) -> None:
        """GitHub sends no timestamp, so the delivery id is the only thing
        standing between a captured request and unlimited replay."""
        ingress = AppEventIngress(producer=_RecordingProducer(), redis_client=_FakeRedis())

        result = await ingress.handle(
            source_app="github",
            req=_github_request(delivery_id=""),
            credentials=_credentials("org-1"),
        )

        assert result["action"] == "verification_failed"


class TestUnknownApp:
    @pytest.mark.asyncio
    async def test_an_unregistered_source_app_publishes_nothing(self) -> None:
        producer = _RecordingProducer()
        ingress = AppEventIngress(producer=producer, redis_client=_FakeRedis())

        result = await ingress.handle(
            source_app="not-a-real-app",
            req=RawWebhookRequest(headers={}, body=b"{}"),
            credentials=_credentials("org-1"),
        )

        assert result["action"] == "unsupported_app"
        assert producer.sent == []


class TestVerifierContract:
    def test_the_replay_helper_ignores_providers_without_a_timestamp(self) -> None:
        assert_within_replay_window(None, provider="GitHub")

    def test_the_replay_helper_rejects_an_old_timestamp(self) -> None:
        with pytest.raises(VerificationError):
            assert_within_replay_window(time.time() - 10_000, provider="Slack")
