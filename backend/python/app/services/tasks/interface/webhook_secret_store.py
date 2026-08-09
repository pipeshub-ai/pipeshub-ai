"""`IWebhookSecretStore` -- persistence port for the reveal-once HMAC
secret minted for each `webhook`-kind `TaskTrigger` at creation time.

Kept as its own narrow port (rather than folded into `ITriggerStore`)
because the secret is sensitive at-rest data with a different lifecycle and
different storage requirements (must be encrypted; `TaskTrigger` rows
themselves are not) -- same reasoning as `ITaskNotifier` being split out
from `ITaskRunStore`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IWebhookSecretStore(ABC):
    @abstractmethod
    async def store(self, webhook_id: str, secret: str) -> None:
        """Persists `secret` for `webhook_id`, encrypted at rest.
        Overwrites any existing secret for the same `webhook_id` (used by a
        future "rotate webhook secret" action)."""
        ...

    @abstractmethod
    async def get(self, webhook_id: str) -> str | None:
        ...

    @abstractmethod
    async def delete(self, webhook_id: str) -> None:
        """Best-effort. Called when a webhook trigger is deleted/cancelled
        so a leaked secret can no longer verify against a live endpoint --
        must not raise if the secret was already absent."""
        ...
