"""Jira webhook verifier.

Jira uses a shared secret in the URL query parameter or as a custom header.
We check the X-Jira-Webhook-Secret header against cfg.webhook_secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from app.services.events.models import AppCredentials, AppEvent, RawWebhookRequest
from app.services.events.verifiers.base import (
    VerificationError,
    assert_within_replay_window,
    get_verifier_registry,
)

logger = logging.getLogger(__name__)


def _occurred_at_epoch(data: dict) -> float | None:
    """Jira's `timestamp`, in seconds. `None` when absent or unparseable.

    Jira sends milliseconds, but not on every event shape, so the magnitude
    decides the unit rather than trusting the field to be present and uniform.
    """
    raw = data.get("timestamp")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return ts / 1000 if ts > 1e10 else ts


class JiraEventVerifier:
    async def verify(self, req: RawWebhookRequest, cfg: AppCredentials) -> AppEvent:
        # Jira supports a shared secret or JWT; we use the shared secret approach.
        # Fail closed in every branch: previously an endpoint with no secret,
        # or a signed-secret endpoint called without the header, was accepted.
        secret_header = req.headers.get("x-hub-signature-256", "")
        if cfg.signing_secret:
            expected = "sha256=" + hmac.new(
                cfg.signing_secret.encode(),
                req.body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(secret_header, expected):
                raise VerificationError("Invalid Jira webhook signature")
        elif cfg.webhook_secret:
            # URL query param approach
            provided = req.query_params.get("secret", "")
            if not hmac.compare_digest(provided, cfg.webhook_secret):
                raise VerificationError("Invalid Jira webhook secret")
        else:
            raise VerificationError("No Jira webhook secret configured for this endpoint")

        data = json.loads(req.body.decode("utf-8", errors="replace"))
        assert_within_replay_window(_occurred_at_epoch(data), provider="Jira")
        webhook_event = data.get("webhookEvent", "")
        issue = data.get("issue", {})
        issue_fields = issue.get("fields", {})
        project_key = issue_fields.get("project", {}).get("key", "")

        # Normalize
        if "jira:issue_created" in webhook_event:
            event_type = "jira.issue.created"
        elif "jira:issue_updated" in webhook_event:
            event_type = "jira.issue.updated"
        elif "jira:issue_deleted" in webhook_event:
            event_type = "jira.issue.deleted"
        elif "comment_created" in webhook_event:
            event_type = "jira.issue.commented"
        else:
            event_type = f"jira.{webhook_event.replace(':', '.')}"

        normalized_payload = {
            "issue": {
                "key": issue.get("key", ""),
                "id": issue.get("id", ""),
                "summary": issue_fields.get("summary", ""),
                "status": (issue_fields.get("status") or {}).get("name", ""),
                "priority": (issue_fields.get("priority") or {}).get("name"),
                "assignee": (issue_fields.get("assignee") or {}).get("displayName"),
                "type": (issue_fields.get("issuetype") or {}).get("name", ""),
            },
            "project_key": project_key,
        }

        if event_type == "jira.issue.updated":
            normalized_payload["changelog"] = data.get("changelog", {})
        if event_type == "jira.issue.commented":
            comment = data.get("comment", {})
            normalized_payload["comment"] = comment.get("body", "")
            normalized_payload["commenter"] = (comment.get("author") or {}).get("displayName", "")

        occurred_epoch = _occurred_at_epoch(data)
        dedupe_key = f"jira-{webhook_event}-{issue.get('id', '')}-{data.get('timestamp', 0)}"

        return AppEvent(
            org_id=cfg.org_id,
            source_app="jira",
            event_type=event_type,
            payload=normalized_payload,
            occurred_at=(
                datetime.fromtimestamp(occurred_epoch, tz=timezone.utc)
                if occurred_epoch is not None
                else datetime.now(timezone.utc)
            ),
            dedupe_key=dedupe_key,
        )


get_verifier_registry().register("jira", JiraEventVerifier())
