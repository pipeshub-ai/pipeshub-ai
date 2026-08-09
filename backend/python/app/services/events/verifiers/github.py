"""GitHub webhook verifier — X-Hub-Signature-256 HMAC."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from app.services.events.models import AppCredentials, AppEvent, RawWebhookRequest
from app.services.events.verifiers.base import VerificationError, get_verifier_registry

logger = logging.getLogger(__name__)


class GitHubEventVerifier:
    async def verify(self, req: RawWebhookRequest, cfg: AppCredentials) -> AppEvent:
        sig_header = req.headers.get("x-hub-signature-256", "")
        # Fail closed: a missing secret used to mean "skip the HMAC check",
        # which turned a misconfigured endpoint into an open event injector.
        if not cfg.signing_secret:
            raise VerificationError("No GitHub signing secret configured for this endpoint")
        expected = "sha256=" + hmac.new(
            cfg.signing_secret.encode(),
            req.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            raise VerificationError("Invalid GitHub webhook signature")

        github_event = req.headers.get("x-github-event", "unknown")
        # GitHub sends no timestamp, so a captured request cannot be aged out
        # the way Slack's and Jira's can -- the delivery id is the only replay
        # defence, and it only works if it is present and unique. A signed body
        # without one is either not from GitHub or is a stripped replay.
        delivery_id = req.headers.get("x-github-delivery", "").strip()
        if not delivery_id:
            raise VerificationError("Missing X-GitHub-Delivery; cannot deduplicate this delivery")
        data = json.loads(req.body.decode("utf-8", errors="replace"))

        action = data.get("action", "")
        event_type = f"github.{github_event}" + (f".{action}" if action else "")

        repo = data.get("repository", {})

        normalized_payload: dict = {
            "repository": {"full_name": repo.get("full_name", ""), "name": repo.get("name", "")},
            "action": action,
        }
        if github_event == "push":
            normalized_payload["ref"] = data.get("ref", "")
            normalized_payload["pusher"] = data.get("pusher", {})
            normalized_payload["commits"] = data.get("commits", [])
        elif github_event == "pull_request":
            pr = data.get("pull_request", {})
            normalized_payload["pull_request"] = {
                "title": pr.get("title", ""),
                "number": pr.get("number"),
                "base": pr.get("base", {}),
                "head": pr.get("head", {}),
            }

        return AppEvent(
            org_id=cfg.org_id,
            source_app="github",
            event_type=event_type,
            payload=normalized_payload,
            occurred_at=datetime.now(timezone.utc),
            dedupe_key=delivery_id,
        )


get_verifier_registry().register("github", GitHubEventVerifier())
