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


def owner_of(item: dict[str, Any]) -> object:
    """The org a stored app or intent belongs to: compared with the caller's, never acted as."""
    return item.get("orgId")


def held_by_another_org(app: dict[str, Any], org_id: str) -> bool:
    """Whether ``app`` is another org's: a store that does not scope reads by org answers it for any caller."""
    return owner_of(app) not in (None, "", org_id)


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
        """Raises ``error_type`` when the KV store refuses the write, or the connector already has an
        intent of another org: its cleanup is never replaced."""
        try:
            earlier = await config_service.get_config(self.key(connector_id), use_cache=False, raise_on_error=True)
        except Exception as e:
            raise self.error_type(f"could not read the earlier {self.what} of connector {connector_id}") from e
        if isinstance(earlier, dict) and owner_of(earlier) not in (None, org_id):
            raise self.error_type(f"the {self.what} of connector {connector_id} is recorded for another org")
        intent = {
            "orgId": org_id,
            "connectorId": connector_id,
            **fields,
            "requestedAt": get_epoch_timestamp_in_ms() if now_ms is None else now_ms,
        }
        if earlier is None:
            try:
                if await config_service.create_config_if_absent(self.key(connector_id), intent):
                    return
                # Created since the read: whose it is decides.
                earlier = await config_service.get_config(self.key(connector_id), use_cache=False, raise_on_error=True)
            except Exception as e:
                raise self.error_type(f"could not record {self.what} for connector {connector_id}") from e
            if isinstance(earlier, dict) and owner_of(earlier) not in (None, org_id):
                raise self.error_type(f"the {self.what} of connector {connector_id} is recorded for another org")
        if not await config_service.set_config(self.key(connector_id), intent):
            raise self.error_type(f"could not record {self.what} for connector {connector_id}")

    async def clear(
        self, config_service: ConfigurationService, connector_id: str, *, org_id: str | None = None,
    ) -> bool:
        """A failed delete is returned, not raised: the intent then runs again,
        and every cleanup behind one is idempotent. With ``org_id``, an intent of another org is left."""
        # Absent for deletes made before intents existed and for ones already
        # settled; Redis reports deleting a missing key as a failure.
        try:
            current = await config_service.get_config(self.key(connector_id), use_cache=False, raise_on_error=True)
        except Exception:
            return False
        if current is None:
            return True
        if org_id is not None and isinstance(current, dict) and owner_of(current) not in (None, org_id):
            return False
        return bool(await config_service.delete_config(self.key(connector_id)))

    async def reschedule(
        self, config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
    ) -> bool:
        """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``. Another org's intent is left."""
        key = self.key(str(intent["connectorId"]))
        # Raised, not None: a failed read must never pass for an absent intent and be written over.
        current = await config_service.get_config(key, use_cache=False, raise_on_error=True)
        if isinstance(current, dict):
            if owner_of(current) not in (None, owner_of(intent)):
                return False
            # Added to the stored intent since the caller read it.
            groups = [g for g in current.get("recordGroupIds") or [] if g not in (intent.get("recordGroupIds") or [])]
            if groups:
                intent = {**intent, "recordGroupIds": [*(intent.get("recordGroupIds") or []), *groups]}
        updated = {**intent, "attempts": int(intent.get("attempts") or 0) + 1, "nextAttemptAt": next_attempt_at}
        return bool(await config_service.set_config(key, updated))

    async def list(self, config_service: ConfigurationService) -> list[dict[str, Any]]:
        """Every recorded intent, oldest first; malformed entries are skipped."""
        intents = []
        for key in await config_service.list_keys_in_directory(self.directory):
            value = await config_service.get_config(key, use_cache=False)
            if (
                isinstance(value, dict)
                and owner_of(value)
                and value.get("connectorId")
                and key == self.key(str(value["connectorId"]))
            ):
                intents.append(value)
        return sorted(intents, key=lambda i: (int(i.get("requestedAt") or 0), str(i["connectorId"])))
