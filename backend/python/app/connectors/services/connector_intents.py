"""Durable per-connector intents in the KV store.

A connector or KB delete owes cleanup that must survive a crash or a lost
event after its graph rows are gone, when nothing else remembers the connector
existed. The deleting service records an intent first, the cleanup clears it,
and a leader-elected loop runs whatever is left behind, backing off per
attempt. Each kind of cleanup gets its own directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.configuration_service import delete_config_confirmed
from app.config.constants.arangodb import CollectionNames
from app.core.org_context import spawn_detached, system
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def owner_of(item: dict[str, Any]) -> object:
    """The org a stored app or intent belongs to: compared with the caller's, never acted as."""
    return item.get("orgId")


def held_by_another_org(app: dict[str, Any], org_id: str) -> bool:
    """Whether ``app`` is another org's: a store that does not scope reads by org answers it for any caller."""
    return owner_of(app) not in (None, "", org_id)


async def exists_in_any_org(graph_provider: IGraphDBProvider, connector_id: str) -> bool:
    """Whether any org holds the connector: a store that scopes reads by org answers None for another org's. Only that
    it exists is told. Raises when the store cannot answer."""
    async def read() -> bool:
        with system("connector_exists"):
            return await graph_provider.get_document(
                connector_id, CollectionNames.APPS.value, raise_on_error=True
            ) is not None

    # Detached: the caller may have an org bound, which a system scope may not be nested in.
    return await spawn_detached(read(), name="connector-exists")


# What a read intent carries about where it came from; never stored.
KEYS = "_keys"
UNSCOPED_AT = "_unscopedAt"
_SETTLED = "settled-"


def _merged(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The newest of one connector's entries, with the record groups all of them name."""
    merged = dict(max(entries, key=lambda e: int(e.get("requestedAt") or 0)))
    lists = [merged.get("recordGroupIds"), *(e.get("recordGroupIds") for e in entries)]
    groups = [group for groups in lists if isinstance(groups, list) for group in groups]
    if groups:
        merged["recordGroupIds"] = list(dict.fromkeys(groups))
    return merged


class ConnectorIntentStore:
    """Every delete request has an entry of its own, ``{directory}{org}/{connector}/{request}``, and settling an intent
    removes the entries that were read and no other. So nothing recorded meanwhile, by the same org or another one, can
    be removed or replaced, and no check has to be atomic with a write."""

    def __init__(self, directory: str, error_type: type[Exception], what: str) -> None:
        self.directory = directory
        self.error_type = error_type
        self.what = what

    def prefix(self, org_id: str, connector_id: str) -> str:
        return f"{self.directory}{org_id}/{connector_id}/"

    def _unscoped_key(self, connector_id: str) -> str:
        """Where earlier versions kept the intent, one for all orgs. Only ever read: a replica of such a version may put
        another org's intent there at any time. Once done with, it is named by a ``settled-`` entry of its org."""
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
        if not await config_service.set_config(self.prefix(org_id, connector_id) + uuid4().hex, intent):
            raise self.error_type(f"could not record {self.what} for connector {connector_id}")

    async def read(self, config_service: ConfigurationService, org_id: str, connector_id: str) -> dict[str, Any] | None:
        """The org's pending intent for the connector, as ``list`` gives it. Raises when the store cannot answer."""
        keys = await config_service.list_keys_in_directory(self.prefix(org_id, connector_id))
        pending = await self._pending(config_service, [*keys, self._unscoped_key(connector_id)], raise_on_error=True)
        return next((i for i in pending if (owner_of(i), i["connectorId"]) == (org_id, connector_id)), None)

    async def clear(self, config_service: ConfigurationService, intent: dict[str, Any]) -> bool:
        """Settle ``intent``, as ``read`` or ``list`` gave it. A failed write is returned, not raised: the intent then
        runs again, and every cleanup behind one is idempotent."""
        prefix = self.prefix(str(owner_of(intent)), str(intent["connectorId"]))
        try:
            if UNSCOPED_AT in intent and not await config_service.set_config(
                f"{prefix}{_SETTLED}{intent[UNSCOPED_AT]}",
                {"orgId": owner_of(intent), "connectorId": intent["connectorId"]},
            ):
                return False
            cleared = [await delete_config_confirmed(config_service, key) for key in intent.get(KEYS) or []]
        except Exception:
            return False
        return all(cleared)

    async def reschedule(
        self, config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
    ) -> bool:
        """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``."""
        updated = {k: v for k, v in intent.items() if k not in (KEYS, UNSCOPED_AT)}
        updated |= {"attempts": int(intent.get("attempts") or 0) + 1, "nextAttemptAt": next_attempt_at}
        # Its newest entry; one only an earlier version recorded gets an entry of its own, always the same.
        own = intent.get(KEYS) or []
        request = f"unscoped-{intent[UNSCOPED_AT]}" if UNSCOPED_AT in intent else uuid4().hex
        key = own[0] if own else self.prefix(str(owner_of(intent)), str(intent["connectorId"])) + request
        return bool(await config_service.set_config(key, updated))

    async def list(self, config_service: ConfigurationService) -> list[dict[str, Any]]:
        """Every pending intent, one per org and connector, oldest first; malformed entries are skipped."""
        return await self._pending(config_service, await config_service.list_keys_in_directory(self.directory))

    async def _pending(
        self, config_service: ConfigurationService, keys: Iterable[str], *, raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        own: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
        unscoped: dict[tuple[str, str], dict[str, Any]] = {}
        settled: set[str] = set()
        for key in keys:
            shared = "/" not in key.removeprefix(self.directory)
            if not shared and key.rsplit("/", 1)[-1].startswith(_SETTLED):
                settled.add(key)
                continue
            # A shared one that cannot be read is not only this org's, and must not keep its own from settling.
            value = await config_service.get_config(
                key, use_cache=False, **({"raise_on_error": True} if raise_on_error and not shared else {}),
            )
            if not isinstance(value, dict) or not owner_of(value) or not value.get("connectorId"):
                continue
            if not isinstance(value.get("requestedAt") or 0, (int, float)):
                continue
            at = (str(owner_of(value)), str(value["connectorId"]))
            if shared and key == self._unscoped_key(at[1]):
                unscoped[at] = value
            elif key.startswith(self.prefix(*at)):
                own.setdefault(at, []).append((key, value))
        intents = []
        for at in own.keys() | unscoped.keys():
            entries = sorted(own.get(at, []), key=lambda e: int(e[1].get("requestedAt") or 0), reverse=True)
            values, extra = [value for _, value in entries], {KEYS: [key for key, _ in entries]}
            earlier = unscoped.get(at)
            if earlier is not None and f"{self.prefix(*at)}{_SETTLED}{earlier.get('requestedAt')}" not in settled:
                values.append(earlier)
                extra[UNSCOPED_AT] = earlier.get("requestedAt")
            if values:
                intents.append({**_merged(values), **extra})
        return sorted(intents, key=lambda i: (int(i.get("requestedAt") or 0), str(i["connectorId"])))
