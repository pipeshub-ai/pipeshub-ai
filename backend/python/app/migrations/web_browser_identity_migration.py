"""One-time backfill that stores, for every Web connector, the settings it actually crawls with.

The settings form shows a field's default when nothing is stored, and the server now checks
numeric ranges on save. Three older states would otherwise surprise an existing connector:

- Browser-like Identity missing: it crawled as a browser, but the form showed off, so the first
  save of any other setting turned the identity off.
- Maximum pages missing: the crawler used 1,000, but the form showed 100, so the first save cut
  the crawl to a tenth.
- Depth, Maximum pages or size out of range: the crawler clamped them, but the form shows the
  stored value and the save is refused, so no other setting could be saved.

Writes are idempotent (computed from the stored settings alone), so two replicas running this
at once save the same result.
"""

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.connector_resolvers import resolve_config_service
from app.connectors.core.constants import ConfigPaths
from app.connectors.sources.web.connector import (
    MAX_DEPTH_LIMIT,
    MAX_PAGES_LIMIT,
    MAX_SIZE_MB_LIMIT,
)

# v2: also fills Maximum pages and clamps the numeric settings; v1 did the identity alone.
MIGRATION_FLAG_KEY = "/migrations/web_sync_settings_backfill_v2"
SETTING = "browser_like_identity"
# What connectors without the setting crawled with.
LEGACY_VALUE = True
# What the crawler reads for each number when it is unset, empty or zero: ``int(value or default)``,
# then clamps to the range. Only Maximum pages has a form default (100) that differs.
CRAWLED_AS = {
    "depth": (3, 1, MAX_DEPTH_LIMIT),
    "max_pages": (1000, 1, MAX_PAGES_LIMIT),
    "max_size_mb": (10, 1, MAX_SIZE_MB_LIMIT),
}


def _as_crawled(values: dict[str, Any]) -> bool:
    """Store each number as the crawler reads it from ``values``; True when changed."""
    changed = False
    for name, (default, low, high) in CRAWLED_AS.items():
        if name not in values and name != "max_pages":
            continue  # unset reads as the form's default, which is what the crawler uses too
        raw = values.get(name)
        try:
            number = int(raw or default)  # exactly as the crawler reads it
        except (TypeError, ValueError):
            continue  # a value the crawler can't read either; left for the user
        crawled = min(max(number, low), high)
        if raw != crawled or isinstance(raw, bool):
            # "12" stays: the form and the crawler both read it as 12.
            if isinstance(raw, str) and raw.strip().isdigit() and int(raw) == crawled:
                continue
            values[name] = crawled
            changed = True
    return changed


def backfill_sync_settings(sync: dict[str, Any]) -> bool:
    """Store what the crawler uses in ``sync`` and in the form's copy, ``sync.customValues``; True when changed.

    The form reads ``customValues`` only when it holds something, and the flat values otherwise.
    An empty one is left empty: a single value written into it would hide every other setting.
    """
    custom = sync.get("customValues")
    form = custom if isinstance(custom, dict) and custom else None
    value = sync.get(SETTING, form.get(SETTING, LEGACY_VALUE) if form else LEGACY_VALUE)
    changed = False
    if SETTING not in sync:
        sync[SETTING] = value
        changed = True
    if form is not None and SETTING not in form:
        form[SETTING] = value
        changed = True
    if form is not None and form.get("max_pages") in (None, "") and sync.get("max_pages") not in (None, ""):
        form["max_pages"] = sync["max_pages"]  # the form keeps what the crawler already has
        changed = True
    changed |= _as_crawled(sync)
    if form is not None:
        changed |= _as_crawled(form)
    return changed


class WebBrowserIdentityBackfill:
    def __init__(self, container: Any, graph_provider: Any, logger: Any) -> None:
        self.container = container
        self.graph_provider = graph_provider
        self.config_service = container.config_service()
        self.logger = logger

    async def _is_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(MIGRATION_FLAG_KEY, use_cache=False)
            return isinstance(flag, dict) and flag.get("done") is True
        except Exception as e:
            self.logger.debug(f"Unable to read the Web settings backfill flag (assuming not done): {e}")
            return False

    async def _backfill(self, connector_id: str, org_id: str | None) -> bool:
        """Backfill one connector; True when its stored settings changed."""
        config_service = resolve_config_service(self.container, org_id)
        path = ConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id)
        config = await config_service.get_config(path, use_cache=False, raise_on_error=True)
        sync = config.get("sync") if isinstance(config, dict) else None
        if not isinstance(sync, dict) or not backfill_sync_settings(sync):
            return False
        if not await config_service.set_config(path, config):
            raise RuntimeError("the settings could not be saved")
        return True

    async def run(self) -> dict[str, Any]:
        if await self._is_already_done():
            return {"success": True, "skipped": True, "connectors_updated": 0}

        apps = await self.graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value, "type", ["Web"], ["id", "orgId"], raise_on_error=True
        )
        updated = failures = 0
        for app in apps or []:
            connector_id = app.get("id")
            if not connector_id:
                continue
            try:
                updated += await self._backfill(connector_id, app.get("orgId"))
            except Exception as e:
                failures += 1
                self.logger.error(f"Web settings backfill: could not update connector {connector_id}: {e}")

        # Leave the flag unset after a failure so the next start retries; updated connectors are skipped then.
        if failures == 0:
            try:
                await self.config_service.set_config(MIGRATION_FLAG_KEY, {"done": True, "connectors_updated": updated})
            except Exception as e:
                self.logger.warning(f"Web settings backfill finished but its flag was not saved; it runs again: {e}")
        return {"success": failures == 0, "skipped": False, "connectors_updated": updated, "failures": failures}


async def run_web_browser_identity_backfill(container: Any, graph_provider: Any, logger: Any) -> dict[str, Any]:
    return await WebBrowserIdentityBackfill(container, graph_provider, logger).run()
