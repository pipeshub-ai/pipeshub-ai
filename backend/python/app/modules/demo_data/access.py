"""Who sees the bundled Acme Corp demo data.

Each person decides for themselves whether the demo's records reach their
answers, search and record listings. Until they choose, the demo is on while
the organization has no data of its own, and off once real records are
indexed, so Acme Corp facts do not mix into real answers by default.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors, ProgressStatus

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

DEMO_CONNECTOR_TYPE = "Demo"

_DEMO_IDS_TTL_S = 60.0
# "No real data yet" is asked again soon; "real data" rarely changes back.
_NO_REAL_DATA_TTL_S = 60.0
_REAL_DATA_TTL_S = 600.0

_demo_ids_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_real_data_cache: dict[str, tuple[float, bool]] = {}
_real_data_locks: dict[str, asyncio.Lock] = {}
_PROBE_BATCH = 16


def preference_key(org_id: str, user_id: str) -> str:
    return f"/services/demoData/{org_id}/users/{user_id}"


def workspace_key(org_id: str) -> str:
    return f"/services/demoData/{org_id}/workspace"


@dataclass(frozen=True)
class DemoDataStatus:
    demo_connector_ids: tuple[str, ...]
    # The person's own choice; None until they make one.
    chosen: bool | None
    real_data: bool
    # An admin turned the demo off for the whole organization; overrides every choice.
    off_for_everyone: bool = False

    @property
    def has_demo(self) -> bool:
        return bool(self.demo_connector_ids)

    @property
    def include(self) -> bool:
        if not self.has_demo or self.off_for_everyone:
            return False
        if self.chosen is not None:
            return self.chosen
        return not self.real_data

    def to_dict(self) -> dict[str, Any]:
        return {
            "hasDemo": self.has_demo,
            "include": self.include,
            "chosen": self.chosen,
            "realData": self.real_data,
            "offForEveryone": self.off_for_everyone,
            "demoConnectorIds": list(self.demo_connector_ids),
        }


def _app_id(app: dict[str, Any]) -> str:
    return str(app.get("_key") or app.get("id") or "")


async def demo_connector_ids(graph_provider: IGraphDBProvider, org_id: str) -> tuple[str, ...]:
    """Ids of the org's Demo connector instances, enabled or not (their records stay searchable)."""
    cached = _demo_ids_cache.get(org_id)
    if cached and time.monotonic() - cached[0] < _DEMO_IDS_TTL_S:
        return cached[1]
    apps = await graph_provider.get_org_apps(org_id, active_only=False)
    ids = tuple(sorted(i for i in (_app_id(a) for a in apps if a.get("type") == DEMO_CONNECTOR_TYPE) if i))
    # The providers answer [] when the listing fails; an org with the demo always
    # lists at least that app, so an empty answer is not remembered.
    if apps:
        _demo_ids_cache[org_id] = (time.monotonic(), ids)
    return ids


async def org_has_real_data(graph_provider: IGraphDBProvider, org_id: str) -> bool:
    """Whether any source other than the demo has an indexed record: a connector or a Collection."""
    cached = _cached_real_data(org_id)
    if cached is not None:
        return cached
    # One look per org at a time: requests that miss together share it.
    async with _real_data_locks.setdefault(org_id, asyncio.Lock()):
        cached = _cached_real_data(org_id)
        if cached is not None:
            return cached
        apps = await graph_provider.get_org_apps(org_id, active_only=False)
        if not apps:
            # A failed listing also answers []; "no real data" would be cached.
            return False
        others = [a for a in apps if a.get("type") != DEMO_CONNECTOR_TYPE and _app_id(a)]
        # Connectors first: an org usually has few, while every user owns a Collection.
        others.sort(key=lambda a: a.get("type") == Connectors.KNOWLEDGE_BASE.value)
        found = await _any_indexed(graph_provider, org_id, [_app_id(a) for a in others])
        _real_data_cache[org_id] = (time.monotonic(), found)
        return found


def _cached_real_data(org_id: str) -> bool | None:
    cached = _real_data_cache.get(org_id)
    if cached:
        at, found = cached
        if time.monotonic() - at < (_REAL_DATA_TTL_S if found else _NO_REAL_DATA_TTL_S):
            return found
    return None


async def _any_indexed(graph_provider: IGraphDBProvider, org_id: str, app_ids: list[str]) -> bool:
    """Probe apps in parallel batches, stopping at the first batch that finds a record."""
    for i in range(0, len(app_ids), _PROBE_BATCH):
        batch = app_ids[i:i + _PROBE_BATCH]
        results = await asyncio.gather(
            *(graph_provider.get_records_by_status(org_id, a, [ProgressStatus.COMPLETED.value], limit=1) for a in batch),
            return_exceptions=True,
        )
        if any(isinstance(r, list) and r for r in results):
            return True
        # A probe that failed proves nothing; "no real data" would be cached
        # and mix the demo into real answers. Let the caller look again.
        failure = next((r for r in results if isinstance(r, BaseException)), None)
        if failure is not None:
            raise failure
    return False


async def read_preference(config_service: ConfigurationService, org_id: str, user_id: str) -> bool | None:
    # Uncached: the setting is written by another service, and a stale read
    # would show answers the person just switched off.
    value = await config_service.get_config(preference_key(org_id, user_id), use_cache=False)
    if isinstance(value, dict) and isinstance(value.get("include"), bool):
        return value["include"]
    return None


async def write_preference(
    config_service: ConfigurationService, org_id: str, user_id: str, *, include: bool | None
) -> None:
    key = preference_key(org_id, user_id)
    if include is None:
        # Nothing saved means already on the default; deleting a missing key reports failure.
        if await read_preference(config_service, org_id, user_id) is None:
            return
        saved = await config_service.delete_config(key)
    else:
        saved = await config_service.set_config(key, {"include": include})
    if not saved:
        raise RuntimeError("could not save the demo data setting")


async def read_workspace_enabled(config_service: ConfigurationService, org_id: str) -> bool:
    # Raises when the store can't be read: only a missing key means "on".
    value = await config_service.get_config(workspace_key(org_id), use_cache=False, raise_on_error=True)
    return not (isinstance(value, dict) and value.get("enabled") is False)


async def write_workspace_enabled(config_service: ConfigurationService, org_id: str, *, enabled: bool) -> None:
    if not await config_service.set_config(workspace_key(org_id), {"enabled": enabled}):
        raise RuntimeError("could not save the demo data setting for the organization")


async def demo_data_status(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
) -> DemoDataStatus:
    ids = await demo_connector_ids(graph_provider, org_id)
    # Read even without demo ids: a failed app listing also answers [], and the
    # admin's "off" must still be reported.
    off_for_everyone = not await read_workspace_enabled(config_service, org_id)
    if not ids:
        return DemoDataStatus(demo_connector_ids=(), chosen=None, real_data=False, off_for_everyone=off_for_everyone)
    return await _status_for(graph_provider, config_service, org_id, user_id, ids, off_for_everyone=off_for_everyone)


async def _status_for(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    ids: tuple[str, ...],
    *,
    off_for_everyone: bool,
) -> DemoDataStatus:
    chosen = await read_preference(config_service, org_id, user_id)
    if off_for_everyone:
        # The admin's "off" decides; a failed probe must not take that away.
        try:
            real_data = await org_has_real_data(graph_provider, org_id)
        except Exception:
            real_data = False
    else:
        real_data = await org_has_real_data(graph_provider, org_id)
    return DemoDataStatus(
        demo_connector_ids=ids, chosen=chosen, real_data=real_data, off_for_everyone=off_for_everyone
    )


async def excluded_demo_connector_ids(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
) -> frozenset[str]:
    """Demo connector ids to leave out of what this person sees; empty when the demo is on for them."""
    if not org_id or not user_id:
        return frozenset()
    ids = await demo_connector_ids(graph_provider, org_id)
    if not ids:
        return frozenset()
    try:
        enabled = await read_workspace_enabled(config_service, org_id)
    except Exception:
        # Callers treat a raise as "exclude nothing"; an unreadable admin "off" must stay off.
        return frozenset(ids)
    if not enabled:
        return frozenset(ids)
    status = await _status_for(graph_provider, config_service, org_id, user_id, ids, off_for_everyone=False)
    return frozenset() if status.include else frozenset(status.demo_connector_ids)


async def is_hidden_demo_record(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    connector_id: str | None,
) -> bool:
    """Whether a record opened by id comes from demo data this person switched off."""
    if not connector_id:
        return False
    return connector_id in await excluded_demo_connector_ids(graph_provider, config_service, org_id, user_id)


def clear_caches() -> None:
    _demo_ids_cache.clear()
    _real_data_cache.clear()
    _real_data_locks.clear()
