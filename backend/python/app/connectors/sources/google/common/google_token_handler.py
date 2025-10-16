from enum import Enum
from typing import Dict

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class CredentialKeys(Enum):
    CLIENT_ID = "clientId"
    CLIENT_SECRET = "clientSecret"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"

class GoogleTokenHandler:
    def __init__(self, logger, config_service, arango_service,key_value_store) -> None:
        self.logger = logger
        self.token_expiry = None
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service
        self.key_value_store = key_value_store

    async def _get_connector_config(self, connector_id: str) -> Dict:
        """Fetch connector config from etcd for the given app."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{connector_id}/config"
            )
            return config or {}
        except Exception as e:
            self.logger.error(f"❌ Failed to get connector config for {connector_id}: {e}")
            return {}

    # async def _resolve_instance_id_from_name(self, org_id: str, connector_name_or_id: str) -> str:
    #     """Resolve a connector instance id (app_key) from a connector name for the org.

    #     If connector_name_or_id is already an instance id with a config present, it is returned as-is.
    #     Otherwise, look up org apps and pick the first matching instance by type.
    #     """
    #     # If config exists for provided id, keep it
    #     cfg = await self._get_connector_config(connector_name_or_id)
    #     if isinstance(cfg, dict) and cfg:
    #         return connector_name_or_id

    #     try:
    #         apps = await self.arango_service.get_org_apps(org_id)
    #         target_type = (connector_name_or_id or "").upper()
    #         for app in apps or []:
    #             app_type = (app.get("type") or "").upper()
    #             if app_type == target_type:
    #                 return app.get("_key") or connector_name_or_id
    #     except Exception as e:
    #         self.logger.warning(f"⚠️ Could not resolve connector instance for {connector_name_or_id}: {e}")

    #     return connector_name_or_id

    async def get_individual_credentials_from_config(self, connector_id: str) -> Dict:
        """Get individual OAuth credentials stored in etcd for the connector."""
        config = await self._get_connector_config(connector_id)
        creds = config.get("credentials") or {}
        if not creds:
            self.logger.info(f"No individual credentials found in config for {connector_id}")
        return creds

    async def get_enterprise_credentials_from_config(self, connector_id: str) -> Dict:
        """Get enterprise/service account credentials stored in etcd for the connector."""
        config = await self._get_connector_config(connector_id)
        auth = config.get("auth") or {}
        if not auth:
            self.logger.info(f"No enterprise credentials found in config for {connector_id}")
        return auth

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def get_individual_token(self, org_id, user_id, connector_id: str) -> dict:
        """Get individual OAuth token for a specific connector (gmail/drive)."""
        # First try connector-scoped credentials from etcd
        try:
            # resolved_id = await self._resolve_instance_id_from_name(org_id, connector_id)
            config = await self._get_connector_config(connector_id)
            creds = (config or {}).get("credentials") or {}
            # Do not persist client secrets under credentials in storage; only enrich the returned view
            auth_cfg = (config or {}).get("auth", {}) or {}
            if creds:
                # Return a merged view including client info for SDK constructors
                merged = dict(creds)
                merged['clientId'] = auth_cfg.get("clientId")
                merged['clientSecret'] = auth_cfg.get("clientSecret")
                return merged
        except Exception as e:
            self.logger.error(f"❌ Failed to get individual token for {connector_id}: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def refresh_token(self, org_id, user_id, connector_id: str) -> None:
        """Refresh access token for a specific connector (gmail/drive)."""
        try:
            self.logger.info("🔄 Refreshing access token for app: %s", connector_id)

            # Load connector config and stored credentials from etcd
            # resolved_id = await self._resolve_instance_id_from_name(org_id, connector_id)
            config_key = f"/services/connectors/{connector_id}/config"
            config = await self.config_service.get_config(config_key)
            if not isinstance(config, dict):
                raise Exception(f"Connector config missing for {connector_id}")

            credentials = (config or {}).get("credentials") or {}
            refresh_token = credentials.get("refresh_token")
            if not refresh_token:
                # Nothing to refresh; rely on existing access token
                self.logger.info("No refresh_token present for %s; skipping refresh", connector_id)
                return

            auth_cfg = (config or {}).get("auth") or {}

            from app.connectors.core.base.token_service.oauth_service import (
                OAuthConfig,
                OAuthProvider,
            )

            oauth_config = OAuthConfig(
                client_id=auth_cfg.get("clientId"),
                client_secret=auth_cfg.get("clientSecret"),
                redirect_uri=auth_cfg.get("redirectUri", ""),
                authorize_url=auth_cfg.get("authorizeUrl", ""),
                token_url=auth_cfg.get("tokenUrl", ""),
                scope=' '.join(auth_cfg.get("scopes", [])) if auth_cfg.get("scopes") else ''
            )

            provider = OAuthProvider(
                config=oauth_config,
                key_value_store=self.key_value_store,  # type: ignore
                credentials_path=config_key
            )

            try:
                new_token = await provider.refresh_access_token(refresh_token)
            finally:
                await provider.close()

            # Persist updated credentials back to etcd (only token fields)
            config["credentials"] = new_token.to_dict()
            await self.config_service.set_config(config_key, config)

            self.logger.info("✅ Successfully refreshed access token for %s", connector_id)

        except Exception as e:
            self.logger.error(f"❌ Failed to refresh token for {connector_id}: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def get_enterprise_token(self, org_id, connector_id: str) -> dict:
        # Read service account JSON from etcd for the specified connector (e.g., DRIVE, GMAIL)
        # org_id currently not used because credentials are per-connector; kept for API compatibility
        # resolved_id = await self._resolve_instance_id_from_name(org_id, connector_id)
        config = await self._get_connector_config(connector_id)
        return config.get("auth", {})

    async def get_account_scopes(self, connector_id: str) -> list:
        """Get account scopes for a specific connector (gmail/drive).

        Looks under auth.scopes first (instance config shape), then config.auth.scopes,
        finally falls back to safe defaults by connector name.
        """
        config = await self._get_connector_config(connector_id)
        # Instance config shape
        scopes = (config or {}).get("auth", {}).get("scopes", [])
        if scopes:
            return scopes
        # Registry-like shape fallback
        scopes = (config or {}).get("config", {}).get("auth", {}).get("scopes", [])
        if scopes:
            return scopes
        # Name-based defaults
        name = (connector_id or "").upper()
        if name == "GMAIL":
            return ["https://www.googleapis.com/auth/gmail.readonly"]
        if name == "DRIVE":
            return ["https://www.googleapis.com/auth/drive.readonly"]
        return []
