from enum import Enum
from typing import Dict

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.constants.arangodb import CollectionNames


class CredentialKeys(Enum):
    CLIENT_ID = "clientId"
    CLIENT_SECRET = "clientSecret"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"

class GoogleTokenHandler:
    def __init__(self, logger, config_service, arango_service) -> None:
        self.logger = logger
        self.token_expiry = None
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service

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

    async def _get_connector_type(self, connector_id: str, config: Dict = None) -> str:
        """Get connector type from config or ArangoDB as fallback."""
        try:
            # First try to get from config if provided
            if config:
                auth_cfg = config.get("auth", {})
                connector_type = auth_cfg.get("connectorType", "")
                if connector_type:
                    return connector_type
                # Try root level
                connector_type = config.get("connectorType", "")
                if connector_type:
                    return connector_type

            # Fallback to ArangoDB
            connector_doc = await self.arango_service.get_document(connector_id, CollectionNames.APPS.value)
            return connector_doc.get("type", "") if connector_doc else ""
        except Exception as e:
            self.logger.error(f"❌ Failed to get connector type for {connector_id}: {e}")
            return ""

    async def get_individual_credentials_from_config(self, connector_id: str) -> Dict:
        """Get individual OAuth credentials stored in etcd for the connector."""
        config = await self._get_connector_config(connector_id)
        creds = config.get("credentials") or {}
        auth_cfg = config.get("auth", {}) or {}

        if not creds:
            self.logger.info(f"No individual credentials found in config for {connector_id}")
            return {}

        # Get OAuth credentials - priority: OAuth config > auth config (fallback)
        client_id = None
        client_secret = None
        oauth_config_id = auth_cfg.get("oauthConfigId")

        # If using shared OAuth config, fetch credentials from there (primary source)
        if oauth_config_id:
            try:
                # Get connector type from config or ArangoDB as fallback
                connector_type = await self._get_connector_type(connector_id, config)
                connector_type = connector_type.lower().replace(" ", "") if connector_type else ""

                if connector_type:
                    oauth_config_path = f"/services/oauth/{connector_type}"
                    oauth_configs = await self.config_service.get_config(oauth_config_path, default=[])

                    if isinstance(oauth_configs, list):
                        # Find the OAuth config by ID
                        for oauth_cfg in oauth_configs:
                            if oauth_cfg.get("_id") == oauth_config_id:
                                oauth_config_data = oauth_cfg.get("config", {})
                                if oauth_config_data:
                                    client_id = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
                                    client_secret = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")
                                    self.logger.info(f"Using shared OAuth config {oauth_config_id} for credentials")
                                break
            except Exception as e:
                self.logger.warning(f"Failed to fetch shared OAuth config: {e}, will try auth config fallback")

        # Fallback to auth config if OAuth config didn't provide credentials
        if not (client_id and client_secret):
            client_id = auth_cfg.get("clientId")
            client_secret = auth_cfg.get("clientSecret")
            if client_id and client_secret:
                self.logger.info(f"Using credentials from auth config as fallback for {connector_id}")

        # Enrich credentials with client info
        merged = dict(creds)
        if client_id:
            merged['clientId'] = client_id
        if client_secret:
            merged['clientSecret'] = client_secret

        return merged

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
    async def get_individual_token(self, connector_id: str) -> dict:
        """Get individual OAuth token for a specific connector (gmail/drive)."""
        # Use the enhanced method that handles OAuth configs
        try:
            return await self.get_individual_credentials_from_config(connector_id)
        except Exception as e:
            self.logger.error(f"❌ Failed to get individual token for {connector_id}: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def refresh_token(self, connector_id: str) -> None:
        """Refresh access token for a specific connector (gmail/drive)."""
        try:
            self.logger.info("🔄 Refreshing access token for app: %s", connector_id)

            # Load connector config and stored credentials from etcd
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

            # Get connector type from config or ArangoDB as fallback
            connector_type = await self._get_connector_type(connector_id, config)

            # Build OAuth flow config (handles shared OAuth configs)
            oauth_flow_config = {}
            oauth_config_id = auth_cfg.get("oauthConfigId")

            if oauth_config_id and connector_type:
                # Fetch shared OAuth config from etcd
                try:
                    oauth_config_path = f"/services/oauth/{connector_type.lower().replace(' ', '')}"
                    oauth_configs = await self.config_service.get_config(oauth_config_path, default=[])

                    if isinstance(oauth_configs, list):
                        # Find the OAuth config by ID
                        shared_oauth_config = None
                        for oauth_cfg in oauth_configs:
                            if oauth_cfg.get("_id") == oauth_config_id:
                                shared_oauth_config = oauth_cfg
                                break

                        if shared_oauth_config:
                            # Get OAuth infrastructure fields from stored OAuth config
                            oauth_flow_config["authorizeUrl"] = shared_oauth_config.get("authorizeUrl", "")
                            oauth_flow_config["tokenUrl"] = shared_oauth_config.get("tokenUrl", "")
                            oauth_flow_config["redirectUri"] = shared_oauth_config.get("redirectUri", "")

                            # Get optional infrastructure fields (may not exist in migrated configs)
                            if "tokenAccessType" in shared_oauth_config:
                                oauth_flow_config["tokenAccessType"] = shared_oauth_config["tokenAccessType"]
                            if "additionalParams" in shared_oauth_config:
                                oauth_flow_config["additionalParams"] = shared_oauth_config["additionalParams"]

                            # If infrastructure fields are missing, try to get them from registry
                            if "tokenAccessType" not in oauth_flow_config or "additionalParams" not in oauth_flow_config:
                                try:
                                    from app.connectors.core.registry.oauth_config_registry import (
                                        get_oauth_config_registry,
                                    )
                                    oauth_registry = get_oauth_config_registry()
                                    registry_oauth_config = oauth_registry.get_config(connector_type)

                                    if registry_oauth_config:
                                        if "tokenAccessType" not in oauth_flow_config and registry_oauth_config.token_access_type:
                                            oauth_flow_config["tokenAccessType"] = registry_oauth_config.token_access_type
                                        if "additionalParams" not in oauth_flow_config and registry_oauth_config.additional_params:
                                            oauth_flow_config["additionalParams"] = registry_oauth_config.additional_params
                                        self.logger.debug(f"Enriched OAuth config with infrastructure fields from registry for {connector_type}")
                                except Exception as e:
                                    self.logger.debug(f"Could not enrich OAuth config from registry: {e}")

                            # Get connector scope to determine which scopes to use
                            connector_scope = auth_cfg.get("connectorScope", "team").lower()

                            # Convert scopes from dict to list based on connector scope
                            scopes_data = shared_oauth_config.get("scopes", {})
                            if isinstance(scopes_data, dict):
                                # Map connector scope to scope key
                                scope_key_map = {
                                    "personal": "personal_sync",
                                    "team": "team_sync",
                                    "agent": "agent"
                                }
                                scope_key = scope_key_map.get(connector_scope, "team_sync")  # Default to team_sync

                                # Get scopes for the specific connector scope
                                scope_list = scopes_data.get(scope_key, [])
                                oauth_flow_config["scopes"] = scope_list if isinstance(scope_list, list) else []
                            else:
                                oauth_flow_config["scopes"] = scopes_data if isinstance(scopes_data, list) else []

                            # Get OAuth credentials from config section
                            oauth_config_data = shared_oauth_config.get("config", {})
                            if oauth_config_data:
                                oauth_flow_config["clientId"] = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
                                oauth_flow_config["clientSecret"] = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")

                            self.logger.info(f"Using shared OAuth config {oauth_config_id} for token refresh")
                        else:
                            self.logger.warning(f"OAuth config {oauth_config_id} not found, using connector auth config")
                            oauth_flow_config = auth_cfg.copy()
                    else:
                        self.logger.warning(f"OAuth configs not found for {connector_type}, using connector auth config")
                        oauth_flow_config = auth_cfg.copy()

                except Exception as e:
                    self.logger.warning(f"Failed to fetch shared OAuth config: {e}, using connector auth config")
                    oauth_flow_config = auth_cfg.copy()
            else:
                # No shared OAuth config - use connector's auth config directly
                oauth_flow_config = auth_cfg.copy()

            from app.connectors.core.base.token_service.oauth_service import (
                OAuthConfig,
                OAuthProvider,
            )

            oauth_config = OAuthConfig(
                client_id=oauth_flow_config.get("clientId"),
                client_secret=oauth_flow_config.get("clientSecret"),
                redirect_uri=oauth_flow_config.get("redirectUri", ""),
                authorize_url=oauth_flow_config.get("authorizeUrl", ""),
                token_url=oauth_flow_config.get("tokenUrl", ""),
                scope=' '.join(oauth_flow_config.get("scopes", [])) if oauth_flow_config.get("scopes") else ''
            )

            # Set optional infrastructure fields
            if "tokenAccessType" in oauth_flow_config:
                oauth_config.token_access_type = oauth_flow_config["tokenAccessType"]
            if "additionalParams" in oauth_flow_config:
                oauth_config.additional_params = oauth_flow_config["additionalParams"]

            provider = OAuthProvider(
                config=oauth_config,
                configuration_service=self.config_service,
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
    async def get_enterprise_token(self, connector_id: str) -> dict:
        # Read service account JSON from etcd for the specified connector (e.g., DRIVE, GMAIL)
        # org_id currently not used because credentials are per-connector; kept for API compatibility
        config = await self._get_connector_config(connector_id)
        return config.get("auth", {})

    async def get_account_scopes(self, connector_id: str) -> list:
        """Get account scopes for a specific connector (gmail/drive).

        Gets scopes from OAuth config based on connector scope.
        """
        try:
            config = await self._get_connector_config(connector_id)
            auth_cfg = (config or {}).get("auth", {}) or {}
            oauth_config_id = auth_cfg.get("oauthConfigId")

            # If using shared OAuth config, fetch scopes from there
            if oauth_config_id:
                try:
                    # Get connector type from config or ArangoDB as fallback
                    connector_type = await self._get_connector_type(connector_id, config)
                    connector_type = connector_type.lower().replace(" ", "") if connector_type else ""

                    if connector_type:
                        oauth_config_path = f"/services/oauth/{connector_type}"
                        oauth_configs = await self.config_service.get_config(oauth_config_path, default=[])

                        if isinstance(oauth_configs, list):
                            # Find the OAuth config by ID
                            for oauth_cfg in oauth_configs:
                                if oauth_cfg.get("_id") == oauth_config_id:
                                    # Get connector scope to determine which scopes to use
                                    connector_scope = auth_cfg.get("connectorScope", "team").lower()

                                    # Get scopes based on connector scope
                                    scopes_data = oauth_cfg.get("scopes", {})
                                    if isinstance(scopes_data, dict):
                                        scope_key_map = {
                                            "personal": "personal_sync",
                                            "team": "team_sync",
                                            "agent": "agent"
                                        }
                                        scope_key = scope_key_map.get(connector_scope, "team_sync")
                                        scope_list = scopes_data.get(scope_key, [])
                                        if scope_list and isinstance(scope_list, list):
                                            return scope_list
                                    break
                except Exception as e:
                    self.logger.warning(f"Failed to fetch scopes from OAuth config: {e}")

            # Fallback: name-based defaults using connector type
            connector_type = await self._get_connector_type(connector_id, config)
            name = (connector_type or "").upper()
            if name == "GMAIL":
                return ["https://www.googleapis.com/auth/gmail.readonly"]
            if name == "DRIVE":
                return ["https://www.googleapis.com/auth/drive.readonly"]

            return []

        except Exception as e:
            self.logger.error(f"❌ Failed to get account scopes for {connector_id}: {e}")
            return []
