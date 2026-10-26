"""Durable per-connector intents in the KV store.

A connector or KB delete owes cleanup that must survive a crash or a lost
event after its graph rows are gone, when nothing else remembers the connector
existed. The deleting service records an intent first, the cleanup clears it,
and a leader-elected loop runs whatever is left behind, backing off per
attempt. Each kind of cleanup gets its own directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService


class ConnectorIntentStore:
    def __init__(self, directory: str, error_type: type[Exception], what: str) -> None:
        self.directory = directory
        self.error_type = error_type
        self.what = what

    def key(self, connector_id: str) -> str:
        return f"{self.directory}{connector_id}"

    async def record(
        self,
        config_service: ConfigurationService,
        *,
        org_id: str,
        connector_id: str,
        now_ms: int | None = None,
        **fields: Any,  # noqa: ANN401
    ) -> None:
        """Raises ``error_type`` when the KV store refuses the write."""
        intent = {
            "orgId": org_id,
            "connectorId": connector_id,
            **fields,
            "requestedAt": get_epoch_timestamp_in_ms() if now_ms is None else now_ms,
        }
        if not await config_service.set_config(self.key(connector_id), intent):
            raise self.error_type(f"could not record {self.what} for connector {connector_id}")

    async def clear(self, config_service: ConfigurationService, connector_id: str) -> bool:
        """A failed delete is returned, not raised: the intent then runs again,
        and every cleanup behind one is idempotent."""
        # Absent for deletes made before intents existed and for ones already
        # settled; Redis reports deleting a missing key as a failure.
        if await config_service.get_config(self.key(connector_id), use_cache=False) is None:
            return True
        return bool(await config_service.delete_config(self.key(connector_id)))

    async def reschedule(
        self, config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
    ) -> bool:
        """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``."""
        updated = {**intent, "attempts": int(intent.get("attempts") or 0) + 1, "nextAttemptAt": next_attempt_at}
        return bool(await config_service.set_config(self.key(str(intent["connectorId"])), updated))

    async def list(self, config_service: ConfigurationService) -> list[dict[str, Any]]:
        """Every recorded intent, oldest first; malformed entries are skipped."""
        intents = []
        for key in await config_service.list_keys_in_directory(self.directory):
            value = await config_service.get_config(key, use_cache=False)
            if (
                isinstance(value, dict)
                and value.get("orgId")
                and value.get("connectorId")
                and key == self.key(str(value["connectorId"]))
            ):
                intents.append(value)
        return sorted(intents, key=lambda i: (int(i.get("requestedAt") or 0), str(i["connectorId"])))
