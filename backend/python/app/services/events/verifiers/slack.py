"""Slack webhook verifier.

Verifies Slack's v0= HMAC-SHA256 signature scheme:
    v0=HMAC-SHA256(signing_secret, "v0:{timestamp}:{body}")

Signature must be present in X-Slack-Signature header.
Timestamp must be within 300 seconds (replay protection).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs

from app.services.events.models import AppCredentials, AppEvent, RawWebhookRequest
from app.services.events.verifiers.base import (
    VerificationError,
    assert_within_replay_window,
    get_verifier_registry,
)

logger = logging.getLogger(__name__)


class SlackEventVerifier:
    async def verify(self, req: RawWebhookRequest, cfg: AppCredentials) -> AppEvent:
        ts_header = req.headers.get("x-slack-request-timestamp", "")
        sig_header = req.headers.get("x-slack-signature", "")

        if not cfg.signing_secret:
            raise VerificationError("No Slack signing secret configured for this endpoint")

        if not ts_header or not sig_header:
            raise VerificationError("Missing Slack signature headers")

        try:
            ts = int(ts_header)
        except ValueError as exc:
            raise VerificationError("Invalid timestamp header") from exc
        assert_within_replay_window(ts, provider="Slack")

        # HMAC check
        sig_base = f"v0:{ts_header}:{req.body.decode('utf-8', errors='replace')}"
        expected = "v0=" + hmac.new(
            cfg.signing_secret.encode(),
            sig_base.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(sig_header, expected):
            raise VerificationError("Invalid Slack signature")

        # Parse and normalize
        body = req.body.decode("utf-8", errors="replace")
        if req.headers.get("content-type", "").startswith("application/json"):
            data = json.loads(body)
        else:
            parsed = parse_qs(body)
            data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        # Handle Slack URL verification challenge
        if data.get("type") == "url_verification":
            raise VerificationError(f"URL_VERIFICATION:{data.get('challenge', '')}")

        event = data.get("event", {})
        event_type = event.get("type", "unknown")
        slack_event_type = f"slack.{event_type.replace('.', '_')}"

        # Normalize common event shapes
        normalized_payload: dict = {}
        if event_type in {"message", "message.channels", "message.groups"}:
            slack_event_type = "slack.message.posted"
            normalized_payload = {
                "channel": {"id": event.get("channel", ""), "name": event.get("channel_name", "")},
                "user": {"id": event.get("user", ""), "name": ""},
                "text": event.get("text", ""),
                "ts": event.get("ts", ""),
                "thread_ts": event.get("thread_ts"),
            }
        elif event_type == "reaction_added":
            slack_event_type = "slack.reaction.added"
            item = event.get("item", {})
            normalized_payload = {
                "reaction": event.get("reaction", ""),
                "user": {"id": event.get("user", ""), "name": ""},
                "item_channel": item.get("channel", ""),
                "item_ts": item.get("ts", ""),
            }
        elif event_type == "channel_created":
            slack_event_type = "slack.channel.created"
            channel = event.get("channel", {})
            normalized_payload = {
                "channel": {"id": channel.get("id", ""), "name": channel.get("name", "")},
                "creator": {"id": channel.get("creator", ""), "name": ""},
                "created": channel.get("created", 0),
            }
        else:
            normalized_payload = dict(event)

        event_id = data.get("event_id", f"slack-{ts_header}-{event_type}")

        return AppEvent(
            org_id=cfg.org_id,
            source_app="slack",
            event_type=slack_event_type,
            payload=normalized_payload,
            occurred_at=datetime.fromtimestamp(ts, tz=timezone.utc),
            dedupe_key=event_id,
        )


# Auto-register
get_verifier_registry().register("slack", SlackEventVerifier())
