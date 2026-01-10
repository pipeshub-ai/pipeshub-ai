from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.connectors.services.base_arango_service import BaseArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class OAuthCredentialsMigrationError(Exception):
    """Base exception for OAuth credentials migration errors."""
    pass


class OAuthCredentialsMigrationService:
    """
    Service for migrating OAuth credentials from connector auth configs to OAuth config registry.

    This migration ensures that all existing connectors work with the new OAuth architecture
    where credentials are centralized and shared across connector instances.
    """

    # Migration version identifier for idempotency
    MIGRATION_FLAG_KEY = "/migrations/oauth_credentials_v1"

    def _get_oauth_field_names_from_registry(self, connector_type: str) -> List[str]:
        """
        Get OAuth field names from the OAuth config registry for a connector type.
        This makes the code generic and maintainable - no hardcoded field names.

        Args:
            connector_type: Type of connector

        Returns:
            List of OAuth field names (e.g., ["clientId", "clientSecret", "domain", ...])
        """
        try:
            oauth_config = self.oauth_registry.get_config(connector_type)
            if not oauth_config or not oauth_config.auth_fields:
                # Return default/common OAuth fields as fallback
                return ["clientId", "clientSecret"]

            # Extract field names from auth_fields
            field_names = [field.name for field in oauth_config.auth_fields]
            return field_names
        except Exception:
            # Fallback to common OAuth fields if registry lookup fails
            return ["clientId", "clientSecret"]

    def _get_oauth_infrastructure_fields(self) -> List[str]:
        """
        Get list of OAuth infrastructure field names (not credential fields).
        These are fields that come from the registry, not from user input.

        Returns:
            List of infrastructure field names
        """
        return [
            "authorizeUrl", "authorize_url", "tokenUrl", "token_url",
            "redirectUri", "redirect_uri", "scopes",
            "tokenAccessType", "token_access_type",
            "additionalParams", "additional_params"
        ]

    def __init__(
        self,
        config_service: ConfigurationService,
        arango_service: BaseArangoService,
        logger,
    ) -> None:
        """
        Initialize the OAuth credentials migration service.

        Args:
            config_service: Service for etcd configuration management
            arango_service: Service for ArangoDB operations (to get connector metadata)
            logger: Logger for tracking migration progress
        """
        self.config_service = config_service
        self.arango_service = arango_service
        self.logger = logger
        self.oauth_registry = get_oauth_config_registry()

    async def _is_migration_already_done(self) -> bool:
        """
        Check if migration has already been completed.

        Returns:
            bool: True if migration was previously completed, False otherwise
        """
        try:
            flag = await self.config_service.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(
                f"Unable to read migration flag (assuming not done): {e}"
            )
            return False

    async def _mark_migration_done(self, result: Dict) -> None:
        """
        Mark the migration as completed in the configuration store.

        Args:
            result: Migration result dictionary with statistics
        """
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "connectors_migrated": result.get("connectors_migrated", 0),
                    "oauth_configs_created": result.get("oauth_configs_created", 0),
                    "timestamp": get_epoch_timestamp_in_ms()
                }
            )
            self.logger.info("✅ Migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    def _get_oauth_config_path(self, connector_type: str) -> str:
        """Get the etcd path for OAuth configs of a connector type."""
        sanitized_type = connector_type.lower().replace(' ', '')
        return f"/services/oauth/{sanitized_type}"

    def _normalize_connector_type(self, connector_type: str) -> str:
        """Normalize connector type for registry lookup."""
        # Registry uses original case with spaces, but we need to match it
        return connector_type

    def _migrate_scopes(
        self,
        auth_config: Dict[str, Any],
        registry_fields: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """
        Migrate scopes from old format to new format.

        Old format: scopes as a flat list
        New format: scopes as dict with keys (personal_sync, team_sync, agent)

        Args:
            auth_config: Old auth configuration
            registry_fields: OAuth fields from registry

        Returns:
            Scopes in new dict format
        """
        # Get scopes from auth config (might be list or dict)
        auth_scopes = auth_config.get("scopes")

        # Get scopes from registry (always dict format)
        registry_scopes = registry_fields.get("scopes", {})

        # If auth config has scopes as a dict, use it directly (already migrated format)
        if isinstance(auth_scopes, dict):
            return auth_scopes

        # If auth config has scopes as a list, we need to map them to the new structure
        if isinstance(auth_scopes, list) and auth_scopes:
            # Get connector scope to determine which key to use
            connector_scope = auth_config.get("connectorScope", "team").lower()

            # Map connector scope to scope key
            scope_key_map = {
                "personal": "personal_sync",
                "team": "team_sync",
                "agent": "agent"
            }
            scope_key = scope_key_map.get(connector_scope, "team_sync")

            # Create dict with the list in the appropriate key
            # Also populate other keys from registry to maintain consistency
            migrated_scopes = {
                "personal_sync": registry_scopes.get("personal_sync", []),
                "team_sync": registry_scopes.get("team_sync", []),
                "agent": registry_scopes.get("agent", [])
            }

            # Override the specific scope key with the list from auth config
            migrated_scopes[scope_key] = auth_scopes

            self.logger.info(
                f"Migrated scopes from list to dict format (connector_scope={connector_scope})"
            )
            return migrated_scopes

        # If no scopes in auth config, use registry scopes
        return registry_scopes

    async def _get_oauth_fields_from_registry(self, connector_type: str) -> Dict[str, Any]:
        """
        Get OAuth infrastructure fields from the registry for a connector type.

        Returns:
            Dict with OAuth infrastructure fields (authorizeUrl, tokenUrl, etc.)
        """
        try:
            oauth_config = self.oauth_registry.get_config(connector_type)
            if not oauth_config:
                self.logger.warning(f"No OAuth config in registry for {connector_type}")
                return {}

            # Extract infrastructure fields from registry
            oauth_fields = {
                "authorizeUrl": oauth_config.authorize_url,
                "tokenUrl": oauth_config.token_url,
                "redirectUri": oauth_config.redirect_uri,
            }

            # Get scopes using to_dict() method (same as router) - returns dict with personal_sync, team_sync, agent keys
            oauth_fields["scopes"] = oauth_config.scopes.to_dict()

            # Optional fields
            if oauth_config.token_access_type:
                oauth_fields["tokenAccessType"] = oauth_config.token_access_type

            if oauth_config.additional_params:
                oauth_fields["additionalParams"] = oauth_config.additional_params

            # Get metadata fields
            oauth_fields["iconPath"] = oauth_config.icon_path or "/assets/icons/connectors/default.svg"
            oauth_fields["appGroup"] = oauth_config.app_group or ""
            oauth_fields["appDescription"] = oauth_config.app_description or ""
            oauth_fields["appCategories"] = oauth_config.app_categories or []

            return oauth_fields

        except Exception as e:
            self.logger.error(f"❌ Error getting OAuth fields from registry for {connector_type}: {e}")
            return {}

    async def _find_connectors_needing_migration(self) -> List[Dict[str, Any]]:
        """
        Find all connectors that have OAuth credentials in their auth config.

        Returns:
            List of connector info dicts with connector_id, connector_type, and auth_config
        """
        connectors_to_migrate = []

        try:
            # Get all connector instances from ArangoDB
            all_connectors = await self.arango_service.get_all_documents(CollectionNames.APPS.value)

            # Handle case where no connectors exist at all
            if not all_connectors:
                self.logger.info("No connectors found in database - migration not needed")
                return []

            self.logger.info(f"Found {len(all_connectors)} total connectors in database")

            # Check each connector
            for connector in all_connectors:
                connector_id = connector.get("_key")
                connector_type = connector.get("type")
                auth_type = connector.get("authType")
                connector_instance_name = connector.get("name")

                if not connector_id or not connector_type:
                    continue

                # Only process OAuth connectors
                if auth_type not in ["OAUTH"]:
                    continue

                # Get connector config from etcd
                config_path = f"/services/connectors/{connector_id}/config"
                try:
                    config = await self.config_service.get_config(config_path)
                    if not config:
                        self.logger.debug(f"No config found for connector {connector_id}")
                        continue

                    auth_config = config.get("auth", {})
                    if not auth_config:
                        continue

                    # Check if already migrated (has oauthConfigId and no credentials)
                    has_oauth_config_id = bool(auth_config.get("oauthConfigId"))

                    # Get OAuth field names dynamically from registry
                    oauth_field_names = self._get_oauth_field_names_from_registry(connector_type)

                    # Check if auth config has any OAuth credential fields
                    # (exclude infrastructure fields which come from registry)
                    has_credentials = any(
                        field in auth_config
                        for field in oauth_field_names
                    ) or any(
                        field in auth_config
                        for field in [f.replace("Id", "_id").replace("Secret", "_secret") for f in oauth_field_names]
                    )

                    # Need migration if: has credentials but no oauthConfigId reference
                    if has_credentials and not has_oauth_config_id:
                        # Get orgId from connector document or from relationship edge
                        org_id = connector.get("orgId")
                        if not org_id:
                            # Try to get orgId from orgAppRelation edge
                            try:
                                query = f"""
                                FOR edge IN {CollectionNames.ORG_APP_RELATION.value}
                                    FILTER edge._to == @connector_id
                                    LIMIT 1
                                    RETURN PARSE_IDENTIFIER(edge._from).key
                                """
                                cursor = self.arango_service.db.aql.execute(
                                    query,
                                    bind_vars={"connector_id": f"{CollectionNames.APPS.value}/{connector_id}"}
                                )
                                org_id = next(cursor, None)
                            except Exception as e:
                                self.logger.warning(
                                    f"Could not get orgId for connector {connector_id}: {e}"
                                )

                        connectors_to_migrate.append({
                            "connector_id": connector_id,
                            "connector_instance_name": connector_instance_name,
                            "connector_type": connector_type,
                            "auth_type": auth_type,
                            "auth_config": auth_config,
                            "org_id": org_id,
                            "created_by": connector.get("createdBy")
                        })
                        self.logger.debug(
                            f"Connector {connector_id} ({connector_type}) needs migration"
                        )
                    elif has_oauth_config_id:
                        self.logger.debug(
                            f"Connector {connector_id} already migrated (has oauthConfigId)"
                        )

                except Exception as e:
                    self.logger.warning(
                        f"Error checking connector {connector_id}: {e}"
                    )
                    continue

            self.logger.info(
                f"Found {len(connectors_to_migrate)} connector(s) needing OAuth credentials migration"
            )
            return connectors_to_migrate

        except Exception as e:
            error_msg = f"Failed to find connectors needing migration: {str(e)}"
            self.logger.error(error_msg)
            raise OAuthCredentialsMigrationError(error_msg) from e

    async def _create_oauth_config_for_connector(
        self,
        connector_info: Dict[str, Any]
    ) -> Optional[str]:
        """
        Create a new OAuth config for a connector.

        Args:
            connector_info: Dict with connector_id, connector_type, auth_config, etc.

        Returns:
            OAuth config ID if created, None if failed
        """
        connector_type = connector_info["connector_type"]
        connector_id = connector_info["connector_id"]
        auth_config = connector_info["auth_config"]
        connector_instance_name = connector_info["connector_instance_name"]

        try:
            # Get OAuth infrastructure fields from registry
            registry_fields = await self._get_oauth_fields_from_registry(connector_type)
            if not registry_fields:
                self.logger.error(
                    f"Cannot create OAuth config for {connector_type}: no registry data"
                )
                return None

            # Get OAuth field names dynamically from registry
            oauth_field_names = self._get_oauth_field_names_from_registry(connector_type)

            # Extract all OAuth credential fields from auth config (normalize field names)
            # Build config dict with all fields from auth_config
            oauth_config_dict = {}
            for field_name in oauth_field_names:
                # Try both camelCase and snake_case variants
                value = auth_config.get(field_name) or auth_config.get(
                    field_name.replace("Id", "_id").replace("Secret", "_secret")
                )
                if value is not None:
                    oauth_config_dict[field_name] = value

            # Check for required fields (clientId and clientSecret are always required)
            client_id = oauth_config_dict.get("clientId")
            client_secret = oauth_config_dict.get("clientSecret")

            if not client_id or not client_secret:
                self.logger.warning(
                    f"Connector {connector_id} has incomplete OAuth credentials (missing clientId or clientSecret)"
                )
                return None

            # Generate OAuth config ID
            oauth_config_id = str(uuid4())

            # IMPORTANT: Prioritize values from auth config over registry where they exist
            # This is crucial for fields like redirectUri that may have base URLs in old configs

            # redirectUri: Use from auth config if present (will have full URL with base),
            # otherwise fall back to registry (which only has relative path)
            redirect_uri = (
                auth_config.get("redirectUri") or
                auth_config.get("redirect_uri") or
                registry_fields.get("redirectUri", "")
            )

            # authorizeUrl: Prefer auth config, fallback to registry
            authorize_url = (
                auth_config.get("authorizeUrl") or
                auth_config.get("authorize_url") or
                registry_fields.get("authorizeUrl", "")
            )

            # tokenUrl: Prefer auth config, fallback to registry
            token_url = (
                auth_config.get("tokenUrl") or
                auth_config.get("token_url") or
                registry_fields.get("tokenUrl", "")
            )

            # Scopes: Handle both old (list) and new (dict) formats
            scopes = self._migrate_scopes(auth_config, registry_fields)

            # tokenAccessType: Prefer auth config, fallback to registry
            token_access_type = (
                auth_config.get("tokenAccessType") or
                auth_config.get("token_access_type") or
                registry_fields.get("tokenAccessType")
            )

            # additionalParams: Prefer auth config, fallback to registry
            additional_params = (
                auth_config.get("additionalParams") or
                auth_config.get("additional_params") or
                registry_fields.get("additionalParams")
            )

            # Get user_id and org_id
            user_id = connector_info.get("created_by")
            org_id = connector_info.get("org_id")

            # Validate required fields
            if not org_id:
                self.logger.error(
                    f"Cannot create OAuth config for connector {connector_id}: orgId is missing"
                )
                return None

            if not user_id:
                self.logger.warning(
                    f"Connector {connector_id} has no createdBy, using connector_id as fallback"
                )

            # Build OAuth config object - MUST match router structure exactly
            oauth_config = {
                "_id": oauth_config_id,
                "oauthInstanceName": connector_instance_name,  # Fixed: was {connector_type} which created a set
                "userId": user_id,  # Required field (matches router)
                "orgId": org_id,  # Required for filtering
                "connectorType": connector_type,
                "createdBy": user_id,  # Required field (matches router)
                "updatedBy": user_id,  # Required field (matches router)
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),

                # OAuth infrastructure fields (prioritize auth config over registry)
                "authorizeUrl": authorize_url,
                "tokenUrl": token_url,
                "redirectUri": redirect_uri,
                "scopes": scopes,  # Should be dict with personal_sync, team_sync, agent keys

                # Metadata from registry
                "iconPath": registry_fields.get("iconPath", "/assets/icons/connectors/default.svg"),
                "appGroup": registry_fields.get("appGroup", ""),
                "appDescription": registry_fields.get("appDescription", ""),
                "appCategories": registry_fields.get("appCategories", []),

                # Credentials section (sensitive data) - matches router structure
                # Include all OAuth fields from auth config (clientId, clientSecret, domain, etc.)
                "config": oauth_config_dict
            }

            # Add optional fields if present
            if token_access_type:
                oauth_config["tokenAccessType"] = token_access_type

            if additional_params:
                oauth_config["additionalParams"] = additional_params

            # Get or create the OAuth configs list for this connector type
            oauth_config_path = self._get_oauth_config_path(connector_type)
            existing_configs = await self.config_service.get_config(oauth_config_path, default=[])

            if not isinstance(existing_configs, list):
                existing_configs = []

            # Add the new config
            existing_configs.append(oauth_config)

            # Save back to etcd
            await self.config_service.set_config(oauth_config_path, existing_configs)

            # Log which fields came from auth config vs. registry
            fields_from_auth = []
            # Check infrastructure fields
            if auth_config.get("redirectUri") or auth_config.get("redirect_uri"):
                fields_from_auth.append("redirectUri")
            if auth_config.get("authorizeUrl") or auth_config.get("authorize_url"):
                fields_from_auth.append("authorizeUrl")
            if auth_config.get("tokenUrl") or auth_config.get("token_url"):
                fields_from_auth.append("tokenUrl")
            if auth_config.get("scopes") and isinstance(auth_config.get("scopes"), list):
                fields_from_auth.append("scopes (list->dict)")
            if auth_config.get("tokenAccessType") or auth_config.get("token_access_type"):
                fields_from_auth.append("tokenAccessType")
            if auth_config.get("additionalParams") or auth_config.get("additional_params"):
                fields_from_auth.append("additionalParams")

            # Log custom OAuth credential fields that were migrated (excluding clientId/clientSecret which are always present)
            custom_fields = [field for field in oauth_config_dict
                           if field not in ["clientId", "clientSecret"]]
            if custom_fields:
                fields_from_auth.append(f"custom fields: {', '.join(custom_fields)}")

            self.logger.info(
                f"✅ Created OAuth config {oauth_config_id} for connector {connector_id} ({connector_type})"
            )
            if fields_from_auth:
                self.logger.debug(
                    f"   Preserved from auth config: {', '.join(fields_from_auth)}"
                )
            return oauth_config_id

        except Exception as e:
            self.logger.error(
                f"❌ Error creating OAuth config for connector {connector_id}: {e}"
            )
            return None

    async def _update_connector_auth_config(
        self,
        connector_id: str,
        oauth_config_id: str
    ) -> bool:
        """
        Update connector's auth config to reference the OAuth config.

        Removes ALL OAuth-related fields (credentials + infrastructure) and adds oauthConfigId reference.

        Args:
            connector_id: Connector instance ID
            oauth_config_id: OAuth config ID to reference

        Returns:
            True if successful, False otherwise
        """
        try:
            config_path = f"/services/connectors/{connector_id}/config"
            config = await self.config_service.get_config(config_path)

            if not config:
                self.logger.error(f"Config not found for connector {connector_id}")
                return False

            auth_config = config.get("auth", {})

            # Add oauthConfigId reference (the only OAuth reference that should remain)
            auth_config["oauthConfigId"] = oauth_config_id

            # Update the config
            config["auth"] = auth_config
            await self.config_service.set_config(config_path, config)

            self.logger.info(
                f"✅ Updated connector {connector_id} to reference OAuth config {oauth_config_id}"
            )
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Error updating connector {connector_id} auth config: {e}"
            )
            return False

    async def migrate_all_connectors(self) -> Dict[str, Any]:
        """
        Execute the complete migration for all connectors.

        This method is idempotent - it will skip execution if the
        completion flag is already set.

        Returns:
            Dict: Result with success status and statistics
        """
        # Check if migration was already completed
        if await self._is_migration_already_done():
            self.logger.info(
                "✅ OAuth credentials migration already completed - skipping"
            )
            return {
                "success": True,
                "connectors_migrated": 0,
                "oauth_configs_created": 0,
                "skipped": True,
                "message": "Migration already completed"
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting OAuth Credentials Migration")
            self.logger.info("=" * 70)

            # Step 1: Find all connectors that need migration
            connectors_to_migrate = await self._find_connectors_needing_migration()

            if not connectors_to_migrate:
                # Check if there are any connectors at all
                try:
                    all_connectors = await self.arango_service.get_all_documents(
                        CollectionNames.APPS.value
                    )
                    if not all_connectors:
                        self.logger.info(
                            "✅ No connectors exist in system - marking migration as complete"
                        )
                    else:
                        self.logger.info(
                            "✅ All connectors already migrated or don't use OAuth - "
                            "marking migration as complete"
                        )
                except Exception:
                    self.logger.info("✅ No connectors need OAuth credentials migration")

                result = {
                    "success": True,
                    "connectors_migrated": 0,
                    "oauth_configs_created": 0,
                }
                # Mark as complete even if no connectors needed migration
                # This prevents the migration from running again unnecessarily
                await self._mark_migration_done(result)
                return result

            self.logger.info(
                f"📋 Found {len(connectors_to_migrate)} connector(s) to migrate"
            )

            # Step 2: Process each connector
            connectors_migrated = 0
            oauth_configs_created = 0
            failed_connectors = []

            for connector_info in connectors_to_migrate:
                connector_id = connector_info["connector_id"]
                connector_type = connector_info["connector_type"]

                try:
                    self.logger.info(
                        f"🔄 Processing connector {connector_id} ({connector_type})..."
                    )

                    # Create OAuth config
                    oauth_config_id = await self._create_oauth_config_for_connector(
                        connector_info
                    )

                    if not oauth_config_id:
                        failed_connectors.append({
                            "connector_id": connector_id,
                            "connector_type": connector_type,
                            "error": "Failed to create OAuth config"
                        })
                        continue

                    oauth_configs_created += 1

                    # Update connector to reference the OAuth config
                    success = await self._update_connector_auth_config(
                        connector_id,
                        oauth_config_id
                    )

                    if success:
                        connectors_migrated += 1
                        self.logger.info(
                            f"✅ Successfully migrated connector {connector_id}"
                        )
                    else:
                        failed_connectors.append({
                            "connector_id": connector_id,
                            "connector_type": connector_type,
                            "error": "Failed to update connector auth config"
                        })

                except Exception as e:
                    self.logger.error(
                        f"❌ Error processing connector {connector_id}: {e}"
                    )
                    failed_connectors.append({
                        "connector_id": connector_id,
                        "connector_type": connector_type,
                        "error": str(e)
                    })
                    continue

            # Log summary
            self.logger.info("=" * 70)
            self.logger.info("OAuth Credentials Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Total connectors found: {len(connectors_to_migrate)}")
            self.logger.info(f"✅ Connectors migrated successfully: {connectors_migrated}")
            self.logger.info(f"✅ OAuth configs created: {oauth_configs_created}")

            if failed_connectors:
                self.logger.warning(f"⚠️ Failed connectors: {len(failed_connectors)}")
                for failed in failed_connectors[:10]:  # Show first 10
                    self.logger.warning(
                        f"  - {failed['connector_id']} ({failed['connector_type']}): "
                        f"{failed['error']}"
                    )
            else:
                self.logger.info("✅ No failures - all connectors migrated successfully")

            self.logger.info("=" * 70)

            result = {
                "success": True,
                "connectors_migrated": connectors_migrated,
                "oauth_configs_created": oauth_configs_created,
                "failed_connectors": len(failed_connectors),
                "failed_connectors_details": failed_connectors if failed_connectors else None
            }

            # Mark migration as complete
            await self._mark_migration_done(result)

            return result

        except Exception as e:
            error_msg = f"OAuth credentials migration failed: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "connectors_migrated": 0,
                "oauth_configs_created": 0,
                "error": str(e)
            }


async def run_oauth_credentials_migration(
    config_service: ConfigurationService,
    arango_service: BaseArangoService,
    logger,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to execute the OAuth credentials migration.

    Args:
        config_service: Service for etcd configuration management
        arango_service: Service for ArangoDB operations
        logger: Logger for tracking migration progress
        dry_run: If True, only report what would be migrated without making changes

    Returns:
        Dict: Result with success status and statistics

    Example:
        >>> result = await run_oauth_credentials_migration(
        ...     config_service, arango_service, logger, dry_run=True
        ... )
    """
    service = OAuthCredentialsMigrationService(
        config_service,
        arango_service,
        logger
    )

    if dry_run:
        # Only find connectors that need migration, don't actually migrate
        connectors = await service._find_connectors_needing_migration()
        return {
            "success": True,
            "dry_run": True,
            "connectors_to_migrate": len(connectors),
            "message": f"Found {len(connectors)} connectors that need OAuth credentials migration (dry run)",
            "connectors": [
                {
                    "connector_id": c["connector_id"],
                    "connector_type": c["connector_type"],
                    "auth_type": c["auth_type"]
                }
                for c in connectors[:10]  # Show first 10
            ]
        }

    return await service.migrate_all_connectors()

