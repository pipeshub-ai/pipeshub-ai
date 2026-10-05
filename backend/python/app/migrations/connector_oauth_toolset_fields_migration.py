"""One-time repair of connector OAuth apps saved with a toolset's OAuth settings.

Until the OAuth config registry kept connector and toolset entries apart (#3883),
a connector OAuth app created for a name that a toolset also registers was filled
from the toolset's entry: its callback URL, scopes, sign-in settings and display
metadata. Saved apps kept those values, because defaults only fill missing
fields; Salesforce apps, for one, forced a consent screen on every sign-in.

Writes are idempotent (each repaired value depends only on the stored app and the
registry), so two replicas running this at once save the same result.
"""

import copy
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.connectors.core.constants import AuthFieldKeys, ConfigPaths, OAuthConfigKeys
from app.connectors.core.registry.auth_builder import OAuthConfig
from app.connectors.core.registry.oauth_config_registry import (
    CONNECTOR_SOURCE,
    OAuthConfigRegistry,
)

MIGRATION_FLAG_KEY = "/migrations/connector_oauth_toolset_fields_v1"

# The names both a connector and a toolset register OAuth configs under.
AFFECTED_CONNECTOR_TYPES = (
    "Calendar",
    "Confluence",
    "Drive",
    "Gmail",
    "Jira",
    "Salesforce",
    "Slack",
    "Zoom",
)

TOOLSET_CALLBACK_SEGMENT = "/toolsets/oauth/callback/"
_TOOLSET_REDIRECT_PREFIX = TOOLSET_CALLBACK_SEGMENT.lstrip("/")


def _connector_redirect_uri(saved_redirect_uri: str, registry_path: str) -> str:
    # Keep the host the admin's app was saved with; only the callback path was wrong.
    if not registry_path:
        return ""
    base_url = saved_redirect_uri.split(TOOLSET_CALLBACK_SEGMENT, 1)[0]
    return f"{base_url.rstrip('/')}/{registry_path.lstrip('/')}"


def _repair_app(app: dict[str, Any], registry_config: OAuthConfig) -> bool:
    """Reset the infrastructure fields of one app in place; True when it changed."""
    redirect_uri = app.get(AuthFieldKeys.REDIRECT_URI)
    if not isinstance(redirect_uri, str) or TOOLSET_CALLBACK_SEGMENT not in redirect_uri:
        return False

    app[AuthFieldKeys.REDIRECT_URI] = _connector_redirect_uri(redirect_uri, registry_config.redirect_uri)
    app[OAuthConfigKeys.SCOPES] = copy.deepcopy(registry_config.scopes.to_dict())

    # A new connector app only gets these when the registry has a value, so drop the toolset's otherwise.
    for key, value in (
        (OAuthConfigKeys.TOKEN_ACCESS_TYPE, registry_config.token_access_type),
        (OAuthConfigKeys.ADDITIONAL_PARAMS, dict(registry_config.additional_params or {})),
    ):
        if value:
            app[key] = value
        else:
            app.pop(key, None)

    app["appGroup"] = registry_config.app_group
    app["appDescription"] = registry_config.app_description
    app["appCategories"] = list(registry_config.app_categories or [])
    return True


class ConnectorOAuthToolsetFieldsRepair:
    def __init__(
        self,
        config_service: ConfigurationService,
        oauth_registry: OAuthConfigRegistry,
        logger: Any,
    ) -> None:
        self.config_service = config_service
        self.oauth_registry = oauth_registry
        self.logger = logger

    async def _is_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(MIGRATION_FLAG_KEY, use_cache=False)
            return isinstance(flag, dict) and flag.get("done") is True
        except Exception as e:
            self.logger.debug(f"Unable to read the connector OAuth repair flag (assuming not done): {e}")
            return False

    async def _mark_done(self, apps_repaired: int) -> None:
        try:
            saved = await self.config_service.set_config(
                MIGRATION_FLAG_KEY, {"done": True, "apps_repaired": apps_repaired}
            )
        except Exception as e:
            self.logger.debug(f"Unable to save the connector OAuth repair flag: {e}")
            saved = False
        if not saved:
            self.logger.warning(
                "Connector OAuth app repair finished but its completion flag was not saved; "
                "it will check the apps again on the next start."
            )

    def _connector_registry_config(self, connector_type: str) -> OAuthConfig | None:
        # get_config falls back to the toolset's entry when no connector registered the name.
        config = self.oauth_registry.get_config(connector_type, source=CONNECTOR_SOURCE)
        if config is None or (config.redirect_uri or "").startswith(_TOOLSET_REDIRECT_PREFIX):
            return None
        return config

    async def _repair_type(self, connector_type: str) -> tuple[int, int]:
        """Repair every saved app of one type; returns (repaired, failed)."""
        registry_config = self._connector_registry_config(connector_type)
        if registry_config is None:
            self.logger.warning(
                f"Connector OAuth app repair: no connector OAuth registration for {connector_type}; "
                "its apps will be checked again on the next start."
            )
            return 0, 1

        path = ConfigPaths.OAUTH_CONFIG.format(connector_type=connector_type.lower().replace(" ", ""))
        apps = await self.config_service.get_config(path, default=[], use_cache=False)
        if not isinstance(apps, list):
            return 0, 0

        repaired_ids: list[str] = []
        failed = 0
        for app in apps:
            app_id = app.get("_id") if isinstance(app, dict) else None
            try:
                if _repair_app(app, registry_config):
                    repaired_ids.append(str(app_id))
            except Exception as e:
                failed += 1
                self.logger.error(
                    f"Connector OAuth app repair: could not repair {connector_type} app {app_id}: {e}"
                )

        if not repaired_ids:
            return 0, failed

        if not await self.config_service.set_config(path, apps):
            self.logger.error(
                f"Connector OAuth app repair: could not save the repaired {connector_type} apps; "
                "they will be repaired on the next start."
            )
            return 0, failed + len(repaired_ids)

        for app_id in repaired_ids:
            self.logger.info(
                f"Connector OAuth app repair: reset {connector_type} app {app_id} to the connector's "
                "callback URL, scopes, sign-in settings and display details"
            )
        return len(repaired_ids), failed

    async def run(self) -> dict[str, Any]:
        if await self._is_already_done():
            return {"success": True, "skipped": True, "apps_repaired": 0}

        apps_repaired = 0
        failures = 0
        for connector_type in AFFECTED_CONNECTOR_TYPES:
            try:
                repaired, failed = await self._repair_type(connector_type)
            except Exception as e:
                self.logger.error(f"Connector OAuth app repair: could not check the {connector_type} apps: {e}")
                repaired, failed = 0, 1
            apps_repaired += repaired
            failures += failed

        # Leave the flag unset after a failure so the next start retries; repaired apps are skipped then.
        if failures == 0:
            await self._mark_done(apps_repaired)
        return {"success": failures == 0, "skipped": False, "apps_repaired": apps_repaired, "failures": failures}


async def run_connector_oauth_toolset_fields_repair(
    config_service: ConfigurationService,
    oauth_registry: OAuthConfigRegistry,
    logger: Any,
) -> dict[str, Any]:
    return await ConnectorOAuthToolsetFieldsRepair(config_service, oauth_registry, logger).run()
