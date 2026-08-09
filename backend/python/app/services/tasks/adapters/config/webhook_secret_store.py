"""`ConfigServiceWebhookSecretStore`: `IWebhookSecretStore` over
`ConfigurationService`. Encryption at rest comes for free -- every
production container wires `ConfigurationService` with an
`EncryptedKeyValueStore` backend (see `app/config/providers/encrypted_store
.py`), the same mechanism that already protects toolset OAuth/API-key
credentials at `/services/toolsets/{instanceId}/{userId}`
(`app/api/routes/toolsets.py:_get_user_auth_path`). This module follows
that exact precedent rather than inventing a new storage mechanism.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.tasks.interface.webhook_secret_store import IWebhookSecretStore

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService


def _secret_path(webhook_id: str) -> str:
    return f"/services/tasks/webhooks/{webhook_id}"


class ConfigServiceWebhookSecretStore(IWebhookSecretStore):
    def __init__(self, config_service: "ConfigurationService") -> None:
        self._config_service = config_service

    async def store(self, webhook_id: str, secret: str) -> None:
        await self._config_service.set_config(_secret_path(webhook_id), {"secret": secret})

    async def get(self, webhook_id: str) -> str | None:
        record = await self._config_service.get_config(_secret_path(webhook_id), default=None)
        if not isinstance(record, dict):
            return None
        secret = record.get("secret")
        return secret if isinstance(secret, str) else None

    async def delete(self, webhook_id: str) -> None:
        await self._config_service.delete_config(_secret_path(webhook_id))
