"""Reads and writes API Toolset records in the config store. No business rules here.

Secrets are encrypted at rest by the store itself, like every other credential path.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import ValidationError

from app.agents.constants.api_toolset_constants import (
    SIGN_IN_STATE_TTL_SECONDS,
    get_credentials_path,
    get_credentials_prefix,
    get_instance_path,
    get_instances_prefix,
    get_oauth_client_path,
    get_oauth_state_claim_path,
    get_oauth_state_path,
)
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    ApiToolsetInstance,
    OAuthClient,
)
from app.utils.concurrency import gather_with_concurrency

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

_READ_CONCURRENCY = 16


class ApiToolsetStore:
    def __init__(self, config_service: ConfigurationService) -> None:
        self._config = config_service

    # -- instances ---------------------------------------------------------

    async def list_instances(self, org_id: str) -> list[ApiToolsetInstance]:
        keys = await self._config.list_keys_in_directory(get_instances_prefix())
        records = await gather_with_concurrency(_READ_CONCURRENCY, *[self._read_instance(key) for key in keys])
        return sorted((r for r in records if r is not None and r.org_id == org_id), key=lambda r: r.name.lower())

    async def get_instance(self, org_id: str, instance_id: str) -> Optional[ApiToolsetInstance]:
        record = await self._read_instance(get_instance_path(instance_id))
        # Keys carry no org, so the record's own orgId decides who may see it.
        return record if record is not None and record.org_id == org_id else None

    async def save_instance(self, instance: ApiToolsetInstance) -> None:
        if not await self._config.set_config(get_instance_path(instance.id), instance.model_dump(by_alias=True, mode="json")):
            raise RuntimeError("The toolset could not be saved")

    async def delete_instance(self, instance_id: str) -> None:
        """The instance, every credential for it and its OAuth app."""
        await self.delete_instance_credentials(instance_id)
        await self._config.delete_config(get_oauth_client_path(instance_id))
        await self._config.delete_config(get_instance_path(instance_id))

    async def _read_instance(self, key: str) -> Optional[ApiToolsetInstance]:
        data = await self._config.get_config(key, default=None, use_cache=False)
        if not isinstance(data, dict):
            return None
        try:
            return ApiToolsetInstance.model_validate(data)
        except ValidationError:
            logger.warning("Skipping unreadable API toolset record at %s", key)
            return None

    # -- credentials -------------------------------------------------------

    async def get_credentials(self, instance_id: str, owner_id: str) -> Optional[ApiToolsetCredentials]:
        data = await self._config.get_config(get_credentials_path(instance_id, owner_id), default=None, use_cache=False)
        return ApiToolsetCredentials.model_validate(data) if isinstance(data, dict) else None

    async def save_credentials(self, instance_id: str, owner_id: str, credentials: ApiToolsetCredentials) -> None:
        path = get_credentials_path(instance_id, owner_id)
        if not await self._config.set_config(path, credentials.model_dump(by_alias=True, mode="json", exclude_none=True)):
            raise RuntimeError("The credentials could not be saved")

    async def delete_credentials(self, instance_id: str, owner_id: str) -> None:
        await self._config.delete_config(get_credentials_path(instance_id, owner_id))

    async def remove_org(self, org_id: str) -> int:
        """Every toolset of a deleted org, with its credentials. Returns how many toolsets went."""
        instances = await self.list_instances(org_id)
        for instance in instances:
            await self.delete_instance(instance.id)
        return len(instances)

    async def remove_owner(self, org_id: str, owner_id: str) -> int:
        """A deleted user's credentials for every toolset of their org. Returns how many went."""
        instances = await self.list_instances(org_id)
        for instance in instances:
            await self.delete_credentials(instance.id, owner_id)
        return len(instances)

    async def delete_instance_credentials(self, instance_id: str) -> None:
        """Every user's and the shared credential for one instance."""
        for key in await self._config.list_keys_in_directory(get_credentials_prefix(instance_id)):
            await self._config.delete_config(key)

    # -- OAuth app and sign-in state ----------------------------------------

    async def get_oauth_client(self, instance_id: str) -> Optional[OAuthClient]:
        data = await self._config.get_config(get_oauth_client_path(instance_id), default=None, use_cache=False)
        return OAuthClient.model_validate(data) if isinstance(data, dict) else None

    async def save_oauth_client(self, instance_id: str, client: OAuthClient) -> None:
        if not await self._config.set_config(get_oauth_client_path(instance_id), client.model_dump(by_alias=True)):
            raise RuntimeError("The OAuth app could not be saved")

    async def save_sign_in_state(self, state: str, record: dict[str, Any]) -> None:
        await self._config.set_config(get_oauth_state_path(state), record, ttl_seconds=SIGN_IN_STATE_TTL_SECONDS)

    async def get_sign_in_state(self, state: str) -> Optional[dict[str, Any]]:
        record = await self._config.get_config(get_oauth_state_path(state), default=None, use_cache=False)
        return record if isinstance(record, dict) else None

    async def claim_sign_in_state(self, state: str) -> bool:
        """True for the first caller only, so a sign-in completes once."""
        if not await self._config.create_config_if_absent(
            get_oauth_state_claim_path(state), {"claimed": True}, ttl_seconds=SIGN_IN_STATE_TTL_SECONDS,
        ):
            return False
        await self._config.delete_config(get_oauth_state_path(state))
        return True
