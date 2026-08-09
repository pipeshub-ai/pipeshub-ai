"""Event domain models for the app-event pub/sub system (§4.4).

These models are PURE — zero infra imports. The ingress, consumer, and
verifiers all depend on this module; nothing here depends on them.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

JsonValue = Any


class RawWebhookRequest(BaseModel):
    """Raw inbound HTTP webhook request, before any provider-specific parsing."""
    headers: dict[str, str]
    body: bytes
    source_ip: str = ""
    path: str = ""
    query_params: dict[str, str] = Field(default_factory=dict)


class AppCredentials(BaseModel):
    """Provider credentials (from EncryptedKeyValueStore) needed for verification.

    `org_id` is resolved server-side from the registered webhook endpoint, NOT
    from a request header: inbound provider webhooks carry no PipesHub identity,
    so any client-supplied tenant id would let one caller publish events into
    another org's workflows.
    """
    org_id: str = ""
    signing_secret: str = ""          # Slack v0= signing secret, etc.
    webhook_secret: str = ""
    app_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class AppEvent(BaseModel):
    """Normalized app-event envelope."""
    org_id: str
    source_app: str
    event_type: str                           # namespaced: "slack.message.posted"
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime
    dedupe_key: str                           # provider delivery ID (X-Slack-Request-Timestamp + event_id etc.)
    chain_depth: int = 0                      # incremented per workflow-to-workflow hop; capped at 5


class FilterOp(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    PREFIX = "prefix"
    EXISTS = "exists"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"


CHAIN_DEPTH_CAP = 5
