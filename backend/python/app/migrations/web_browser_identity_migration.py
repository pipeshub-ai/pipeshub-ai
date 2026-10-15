"""One-time backfill of the Web connector's Browser-like Identity setting.

Web connectors set up before the setting existed have no stored value. They kept crawling
as a browser, while the settings form showed the setting's default, off, so the first save
of any other setting turned the browser identity off without anyone asking. This writes the
value those connectors crawl with into their stored settings, so the form, the crawler and
the API agree, and a missing value can mean the default everywhere.

Writes are idempotent (only a missing value is filled, from the stored settings alone), so
two replicas running this at once save the same result.
"""

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.connector_resolvers import resolve_config_service
from app.connectors.core.constants import ConfigPaths

MIGRATION_FLAG_KEY = "/migrations/web_browser_like_identity_v1"
SETTING = "browser_like_identity"
# What connectors without the setting crawled with.
LEGACY_VALUE = True


def backfill_sync_settings(sync: dict[str, Any]) -> bool:
    """Fill the setting where the crawler (``sync``) or the form (``sync.customValues``) lacks it; True when changed."""
    custom = sync.get("customValues")
    value = sync.get(SETTING, custom.get(SETTING, LEGACY_VALUE) if isinstance(custom, dict) else LEGACY_VALUE)
    changed = False
    if SETTING not in sync:
        sync[SETTING] = value
        changed = True
    if isinstance(custom, dict) and SETTING not in custom:
        custom[SETTING] = value
        changed = True
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
            self.logger.debug(f"Unable to read the Web identity backfill flag (assuming not done): {e}")
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
                self.logger.error(f"Web identity backfill: could not update connector {connector_id}: {e}")

        # Leave the flag unset after a failure so the next start retries; updated connectors are skipped then.
        if failures == 0:
            try:
                await self.config_service.set_config(MIGRATION_FLAG_KEY, {"done": True, "connectors_updated": updated})
            except Exception as e:
                self.logger.warning(f"Web identity backfill finished but its flag was not saved; it runs again: {e}")
        return {"success": failures == 0, "skipped": False, "connectors_updated": updated, "failures": failures}


async def run_web_browser_identity_backfill(container: Any, graph_provider: Any, logger: Any) -> dict[str, Any]:
    return await WebBrowserIdentityBackfill(container, graph_provider, logger).run()
