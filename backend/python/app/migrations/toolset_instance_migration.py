"""
Toolset Instance Migration Service

Migrates from old per-user toolset architecture to new instance-based architecture.

Old: /services/toolsets/{userId}/{toolsetType}
New: - Instances: /services/toolset-instances
     - OAuth: /services/oauths/toolsets/{toolsetType}
     - User Auth: /services/toolsets/{instanceId}/{userId}

IMPORTANT DEPENDENCIES:
1. This migration MUST run AFTER 'deleteOldAgentsTemplates' migration
   - All old agents are deleted before this migration runs
   - This prevents agent references to old toolset paths

2. Migrated auth records are marked as isAuthenticated=False
   - Users must re-authenticate with new instance-based architecture
   - Ensures proper credential validation in new system
   - Prevents breaking changes from auth structure mismatches

3. Only admin users' configs are migrated
   - Non-admin users' configs are deleted (users_deleted count)
   - Admin users must still re-authenticate (isAuthenticated=False)
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from jose import jwt as jose_jwt

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import DefaultEndpoints
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_TOOLSET_SERVICE_PREFIX = "/services/toolsets"
_TOOLSET_INSTANCES_PATH = "/services/toolset-instances"
_TOOLSET_OAUTH_CONFIG_PREFIX = "/services/oauths/toolsets"
_SECRET_KEYS_PATH = "/services/secretKeys"
HTTP_OK_STATUS = 200
OLD_TOOLSET_KEY_PARTS = 2


class ToolsetInstanceMigrationService:
    def __init__(
        self,
        config_service: ConfigurationService,
        graph_provider: IGraphDBProvider,
        logger
    ) -> None:
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.logger = logger

    async def _get_nodejs_url(self) -> str:
        """Get Node.js URL from etcd config."""
        try:
            endpoints = await self.config_service.get_config("/services/endpoints", use_cache=False)
            if isinstance(endpoints, dict):
                url = endpoints.get("auth", {}).get("endpoint") or endpoints.get("auth", {}).get("publicEndpoint")
                if url:
                    return url.rstrip("/")
        except Exception:
            pass
        return DefaultEndpoints.NODEJS_ENDPOINT.value.rstrip("/")

    async def _get_scoped_jwt_secret(self) -> str:
        """Read scopedJwtSecret from etcd."""
        try:
            secrets = await self.config_service.get_config(_SECRET_KEYS_PATH, use_cache=False)
            if isinstance(secrets, dict):
                return secrets.get("scopedJwtSecret", "")
        except Exception as e:
            self.logger.warning(f"Could not read scopedJwtSecret: {e}")
        return ""

    def _generate_user_lookup_token(self, secret: str) -> str:
        """Generate JWT for Node.js internal API access."""
        now = int(time.time())
        return jose_jwt.encode({"scopes": ["user:lookup"], "iat": now, "exp": now + 3600}, secret, algorithm="HS256")

    async def _get_org_id(self) -> Optional[str]:
        """
        Get the organization ID using the graph provider.
        Supports both ArangoDB and Neo4j via IGraphDBProvider interface.
        """
        try:
            orgs = await self.graph_provider.get_all_orgs(active=True)
            if orgs and len(orgs) > 0:
                org_id = orgs[0].get("_key")
                if org_id:
                    self.logger.info(f"📊 Found organization: {org_id}")
                    return org_id

            self.logger.error("❌ No active organizations found in the database")
            return None
        except Exception as e:
            self.logger.error(f"❌ Failed to get organization from database: {e}")
            return None

    async def _get_admin_user_ids(self, org_id: str, nodejs_url: str, token: str) -> Set[str]:
        """Get admin user IDs for the organization."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{nodejs_url}/api/v1/users/internal/admin-users",
                    params={"orgId": org_id},
                    headers={"Authorization": f"Bearer {token}"}
                )
                if resp.status_code == HTTP_OK_STATUS:
                    admin_ids = resp.json().get("adminUserIds", [])
                    self.logger.info(f"📊 Found {len(admin_ids)} admin users for org {org_id}")
                    return set(admin_ids)
                else:
                    self.logger.warning(f"Admin API returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.logger.error(f"Failed to fetch admin users: {e}")
        return set()

    async def _delete_all_agents(self) -> Dict[str, int]:
        """
        Hard delete ALL agents from the database, including all edges and related nodes.

        This is CRITICAL before toolset migration because:
        1. Agents reference toolsets via old path structure
        2. Toolset nodes in agents have userId/toolsetType but no instanceId
        3. Tool nodes belong to specific agents and must be cleaned up
        4. Prevents dangling references after toolset path migration

        Uses the graph provider's hard_delete_all_agents method which is database-agnostic
        and works with both ArangoDB and Neo4j.

        This operation is wrapped in a transaction to ensure atomicity (all-or-nothing).

        Deletes:
        - Knowledge nodes (connected to agents)
        - Tool nodes (connected to agents' toolsets)
        - Toolset nodes (connected to agents)
        - All edges/relationships
        - Agent documents

        Returns:
            Dict with counts: {
                "agents_deleted": X,
                "toolsets_deleted": Y,
                "tools_deleted": Z,
                "knowledge_deleted": W,
                "edges_deleted": V
            }
        """
        self.logger.info("🗑️ Starting hard delete of ALL agents, toolsets, tools, and knowledge")

        # For ArangoDB, use transaction to ensure atomicity
        # For Neo4j, hard_delete_all_agents already handles this internally
        transaction_id = None

        try:
            # Check if provider supports transactions (ArangoDB)
            if hasattr(self.graph_provider, 'begin_transaction'):
                try:
                    from app.config.constants.arangodb import CollectionNames

                    # Begin transaction with all collections that will be modified
                    transaction_id = await self.graph_provider.begin_transaction(
                        read=[],
                        write=[
                            CollectionNames.AGENT_INSTANCES.value,
                            CollectionNames.AGENT_TOOLSETS.value,
                            CollectionNames.AGENT_TOOLS.value,
                            CollectionNames.AGENT_KNOWLEDGE.value,
                            CollectionNames.AGENT_HAS_TOOLSET.value,
                            CollectionNames.TOOLSET_HAS_TOOL.value,
                            CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                            CollectionNames.PERMISSION.value,
                        ]
                    )
                    self.logger.debug(f"Started transaction for agent deletion: {transaction_id}")
                except Exception as e:
                    self.logger.warning(f"Could not start transaction: {e}. Proceeding without transaction.")
                    transaction_id = None

            # Execute the hard delete
            result = await self.graph_provider.hard_delete_all_agents(transaction=transaction_id)
            agents_deleted = result.get("agents_deleted", 0)
            toolsets_deleted = result.get("toolsets_deleted", 0)
            tools_deleted = result.get("tools_deleted", 0)
            knowledge_deleted = result.get("knowledge_deleted", 0)
            edges_deleted = result.get("edges_deleted", 0)

            # Commit transaction if it was started
            if transaction_id:
                try:
                    await self.graph_provider.commit_transaction(transaction_id)
                    self.logger.debug(f"Committed transaction: {transaction_id}")
                except Exception as e:
                    self.logger.error(f"Failed to commit transaction: {e}")
                    raise

            self.logger.info(
                f"🎉 Agent deletion complete: {agents_deleted} agents, "
                f"{toolsets_deleted} toolsets, {tools_deleted} tools, "
                f"{knowledge_deleted} knowledge, {edges_deleted} edges/relationships deleted"
            )

            return result

        except Exception as e:
            # Rollback transaction on error
            if transaction_id:
                try:
                    await self.graph_provider.rollback_transaction(transaction_id)
                    self.logger.warning(f"Rolled back transaction: {transaction_id}")
                except Exception as rollback_error:
                    self.logger.error(f"Failed to rollback transaction: {rollback_error}")

            self.logger.error(f"❌ Failed to delete agents: {e}", exc_info=True)
            return {
                "agents_deleted": 0,
                "toolsets_deleted": 0,
                "tools_deleted": 0,
                "knowledge_deleted": 0,
                "edges_deleted": 0,
            }

    async def _get_all_old_keys(self) -> List[Tuple[str, str, str, Dict[str, Any]]]:
        """Scan /services/toolsets/ for old format keys: {userId}/{toolsetType}"""
        try:
            all_keys = await self.config_service.list_keys_in_directory(f"{_TOOLSET_SERVICE_PREFIX}/")
        except Exception as e:
            self.logger.error(f"Failed to list toolset keys: {e}")
            return []

        old_keys = []
        for key in all_keys:
            suffix = key[len(_TOOLSET_SERVICE_PREFIX):].lstrip("/")
            parts = suffix.split("/")

            # Old format: {userId}/{toolsetType} (2 parts)
            if len(parts) == OLD_TOOLSET_KEY_PARTS and parts[0] and parts[1]:
                user_id, toolset_type = parts
                try:
                    config_data = await self.config_service.get_config(key, default=None)
                    if config_data:
                        old_keys.append((key, user_id, toolset_type, config_data))
                except Exception as e:
                    self.logger.warning(f"Failed to read {key}: {e}")

        return old_keys

    async def _get_or_create_instance(
        self,
        org_id: str,
        toolset_type: str,
        instance_name: str,
        auth_type: str,
        oauth_config_id: Optional[str],
        creator_user_id: str
    ) -> str:
        """
        Get or create a toolset instance.

        Args:
            org_id: Organization ID
            toolset_type: Type of toolset (e.g., 'slack', 'github')
            instance_name: Display name for the instance
            auth_type: Authentication type (OAUTH, API_TOKEN, etc.)
            oauth_config_id: Optional OAuth config ID if auth_type is OAUTH
            creator_user_id: Admin user ID who created/owned this toolset config

        Returns:
            Instance ID (UUID string)
        """
        try:
            instances = await self.config_service.get_config(_TOOLSET_INSTANCES_PATH, default=[], use_cache=False)
        except Exception:
            instances = []

        if not isinstance(instances, list):
            instances = []

        # Check if instance already exists for this toolset type
        for inst in instances:
            if isinstance(inst, dict):
                if inst.get("toolsetType", "").lower() == toolset_type.lower():
                    self.logger.info(f"ℹ️ Instance already exists for {toolset_type}")
                    return inst.get("_id", str(uuid.uuid4()))

        # Create new instance with actual admin user as creator
        instance_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)

        new_instance = {
            "_id": instance_id,
            "instanceName": instance_name,
            "toolsetType": toolset_type,
            "authType": auth_type,
            "orgId": org_id,
            "createdBy": creator_user_id,
            "createdAtTimestamp": now_ms,
            "updatedAtTimestamp": now_ms,
        }

        if oauth_config_id:
            new_instance["oauthConfigId"] = oauth_config_id

        instances.append(new_instance)

        try:
            await self.config_service.set_config(_TOOLSET_INSTANCES_PATH, instances)
            self.logger.info(f"✅ Created instance {instance_id} for {toolset_type} by user {creator_user_id}")
        except Exception as e:
            self.logger.error(f"Failed to save instance: {e}")

        return instance_id

    async def _create_oauth_config(
        self,
        org_id: str,
        toolset_type: str,
        auth_object: Dict[str, Any],
        user_id: str
    ) -> Optional[str]:
        """Create OAuth config from auth object."""
        if auth_object.get("type", "").upper() != "OAUTH":
            return None

        oauth_config_path = f"{_TOOLSET_OAUTH_CONFIG_PREFIX}/{toolset_type.lower()}"

        try:
            oauth_configs = await self.config_service.get_config(oauth_config_path, default=[], use_cache=False)
        except Exception:
            oauth_configs = []

        if not isinstance(oauth_configs, list):
            oauth_configs = []

        # Check if config already exists for this org
        for cfg in oauth_configs:
            if isinstance(cfg, dict) and cfg.get("orgId") == org_id:
                self.logger.info(f"ℹ️ OAuth config already exists for {toolset_type}")
                return cfg.get("_id", str(uuid.uuid4()))

        # Create new OAuth config
        oauth_config_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)

        new_config = {
            "_id": oauth_config_id,
            "oauthInstanceName": f"{toolset_type.title()} (Migrated)",
            "toolsetType": toolset_type,
            "userId": user_id,
            "orgId": org_id,
            "config": {
                "clientId": auth_object.get("clientId", ""),
                "clientSecret": auth_object.get("clientSecret", ""),
                "authorizeUrl": auth_object.get("authorizeUrl", ""),
                "tokenUrl": auth_object.get("tokenUrl", ""),
                "redirectUri": auth_object.get("redirectUri", ""),
                "scopes": auth_object.get("scopes", []),
            },
            "createdAtTimestamp": now_ms,
            "updatedAtTimestamp": now_ms,
        }

        # Include optional OAuth fields
        for field in ["additionalParams", "tokenAccessType", "scopeParameterName", "tokenResponsePath"]:
            if field in auth_object:
                new_config["config"][field] = auth_object[field]

        oauth_configs.append(new_config)

        try:
            await self.config_service.set_config(oauth_config_path, oauth_configs)
            self.logger.info(f"✅ Created OAuth config {oauth_config_id} for {toolset_type}")
        except Exception as e:
            self.logger.error(f"Failed to save OAuth config: {e}")

        return oauth_config_id

    async def _migrate_user_auth(
        self,
        instance_id: str,
        user_id: str,
        toolset_type: str,
        org_id: str,
        old_config: Dict[str, Any]
    ) -> bool:
        """
        Migrate user's auth to new path with enriched metadata.

        IMPORTANT: Sets isAuthenticated=False so users must re-authenticate with
        the new instance-based architecture. This ensures proper credential validation
        and prevents breaking changes from mismatched auth structures.

        The new auth structure must include instanceId and toolsetType so that:
        1. Token refresh service can find the toolset type
        2. The auth record is self-documenting
        3. It matches the structure created by new authenticate endpoints
        """
        new_path = f"{_TOOLSET_SERVICE_PREFIX}/{instance_id}/{user_id}"
        now_ms = int(time.time() * 1000)

        # Enrich old config with new architecture fields
        # CRITICAL: Set isAuthenticated=False to force re-authentication
        auth_type = old_config.get("auth", {}).get("type", "NONE").upper()
        new_config = {
            **old_config,  # Preserve all old fields (auth, credentials, etc.) for reference
            "isAuthenticated": False,  # FORCE re-authentication in new architecture
            "authType": auth_type,
            "instanceId": instance_id,
            "toolsetType": toolset_type,
            "orgId": org_id,
            "updatedAt": now_ms,
            "updatedBy": "migration",
            "migrationNote": "Migrated from old architecture - please re-authenticate",
        }

        try:
            await self.config_service.set_config(new_path, new_config)
            self.logger.debug(f"✅ Migrated user {user_id} to instance {instance_id} (type: {toolset_type}) - marked as NOT authenticated")
            return True
        except Exception as e:
            self.logger.error(f"Failed to migrate user auth: {e}")
            return False

    async def run_migration(self) -> Dict[str, Any]:
        """
        Execute the migration.

        CRITICAL FIRST STEP:
        - Delete ALL agents BEFORE migrating toolsets
        - Agents reference toolsets via old path structure
        - Prevents dangling references after toolset migration
        """
        self.logger.info("🚀 Starting Toolset Instance Migration")
        self.logger.info("📋 Step 0: Delete ALL agents first")
        self.logger.info("📋 Step 1: Migrate toolset instances and OAuth configs")
        self.logger.info("📋 Step 2: Migrate admin users (marked as NOT authenticated)")
        self.logger.info("📋 Step 3: Delete non-admin user configs")

        # Step 0: Delete ALL agents FIRST (using database-agnostic method)
        agent_deletion_result = await self._delete_all_agents()
        agents_deleted = agent_deletion_result.get("agents_deleted", 0)
        toolsets_deleted = agent_deletion_result.get("toolsets_deleted", 0)
        tools_deleted = agent_deletion_result.get("tools_deleted", 0)
        edges_deleted = agent_deletion_result.get("edges_deleted", 0)

        self.logger.info(
            f"✅ Step 0 complete: {agents_deleted} agents, "
            f"{toolsets_deleted} toolsets, {tools_deleted} tools, "
            f"{edges_deleted} edges/relationships deleted"
        )

        # Step 1: Get org ID from graph database
        org_id = await self._get_org_id()
        if not org_id:
            self.logger.error("❌ Cannot determine organization ID from database")
            return {
                "success": False,
                "message": "Cannot determine organization ID",
                "instances_created": 0,
                "oauth_configs_created": 0,
                "users_migrated": 0,
                "users_deleted": 0,
                "errors": 1,
            }

        self.logger.info(f"📊 Using organization ID: {org_id}")

        # Step 2: Get admin users
        nodejs_url = await self._get_nodejs_url()
        scoped_jwt_secret = await self._get_scoped_jwt_secret()

        admin_users: Set[str] = set()
        if scoped_jwt_secret:
            token = self._generate_user_lookup_token(scoped_jwt_secret)
            admin_users = await self._get_admin_user_ids(org_id, nodejs_url, token)
        else:
            self.logger.warning("⚠️ No scopedJwtSecret - cannot determine admin users")

        if not admin_users:
            self.logger.error("❌ No admin users found - migration requires admin users")
            return {
                "success": False,
                "message": "No admin users found",
                "agents_deleted": agents_deleted,
                "toolsets_deleted": toolsets_deleted,
                "tools_deleted": tools_deleted,
                "edges_deleted": edges_deleted,
                "instances_created": 0,
                "oauth_configs_created": 0,
                "users_migrated": 0,
                "users_deleted": 0,
                "errors": 1,
            }

        # Step 3: Get all old keys
        old_keys = await self._get_all_old_keys()

        if not old_keys:
            self.logger.info("✅ No old-format keys found")
            return {
                "success": True,
                "message": "No old configs to migrate",
                "agents_deleted": agents_deleted,
                "toolsets_deleted": toolsets_deleted,
                "tools_deleted": tools_deleted,
                "edges_deleted": edges_deleted,
                "instances_created": 0,
                "oauth_configs_created": 0,
                "users_migrated": 0,
                "users_deleted": 0,
                "errors": 0,
            }

        self.logger.info(f"📊 Found {len(old_keys)} old-format keys")

        # Step 4: Group by toolset type
        keys_by_type: Dict[str, List[Tuple[str, str, Dict[str, Any]]]] = {}
        for key_path, user_id, toolset_type, config in old_keys:
            if toolset_type not in keys_by_type:
                keys_by_type[toolset_type] = []
            keys_by_type[toolset_type].append((key_path, user_id, config))

        # Step 5: Process each toolset type
        instances_created = 0
        oauth_configs_created = 0
        users_migrated = 0
        users_deleted = 0
        errors = 0

        for toolset_type, keys_for_type in keys_by_type.items():
            self.logger.info(f"🔄 Processing {toolset_type}: {len(keys_for_type)} users")

            # Find first admin user's config to extract OAuth
            oauth_config_id = None
            auth_type = "NONE"
            creator_user_id = None

            for _, user_id, config in keys_for_type:
                if user_id in admin_users:
                    auth = config.get("auth", {})
                    auth_type = auth.get("type", "NONE").upper()
                    creator_user_id = user_id

                    if auth_type == "OAUTH":
                        oauth_config_id = await self._create_oauth_config(org_id, toolset_type, auth, user_id)
                        if oauth_config_id:
                            oauth_configs_created += 1
                    break

            # Skip if no admin user configured this toolset type
            if not creator_user_id:
                self.logger.info(f"⏭️ Skipping {toolset_type}: no admin user found, only deleting non-admin users")
                # Still delete non-admin user keys
                for key_path, user_id, config in keys_for_type:
                    users_deleted += 1
                    try:
                        await self.config_service.delete_config(key_path)
                        self.logger.debug(f"🗑️ Deleted old key: {key_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {key_path}: {e}")
                continue

            # Create instance (use actual admin user as creator)
            instance_name = f"{toolset_type.title()}"
            instance_id = await self._get_or_create_instance(
                org_id, toolset_type, instance_name, auth_type, oauth_config_id, creator_user_id
            )
            instances_created += 1

            # Migrate each user
            for key_path, user_id, config in keys_for_type:
                is_admin = user_id in admin_users

                if is_admin:
                    # Migrate admin user with enriched metadata
                    if await self._migrate_user_auth(instance_id, user_id, toolset_type, org_id, config):
                        users_migrated += 1
                    else:
                        errors += 1
                else:
                    # Skip non-admin user
                    users_deleted += 1
                    self.logger.debug(f"🗑️ Skipping non-admin user {user_id}")

                # Delete old key
                try:
                    await self.config_service.delete_config(key_path)
                    self.logger.debug(f"🗑️ Deleted old key: {key_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to delete {key_path}: {e}")

        self.logger.info(
            f"✅ Migration complete: {agents_deleted} agents, {toolsets_deleted} toolsets, "
            f"{tools_deleted} tools, {edges_deleted} edges/relationships deleted, "
            f"{instances_created} instances, {oauth_configs_created} OAuth configs, "
            f"{users_migrated} admin users migrated (NOT authenticated), "
            f"{users_deleted} non-admin users deleted"
        )
        if users_migrated > 0:
            self.logger.warning(
                f"⚠️ IMPORTANT: {users_migrated} admin users have been migrated but marked as NOT authenticated. "
                f"They MUST re-authenticate to use toolsets in the new architecture."
            )

        return {
            "success": True,
            "message": "Migration completed",
            "agents_deleted": agents_deleted,
            "toolsets_deleted": toolsets_deleted,
            "tools_deleted": tools_deleted,
            "edges_deleted": edges_deleted,
            "instances_created": instances_created,
            "oauth_configs_created": oauth_configs_created,
            "users_migrated": users_migrated,
            "users_deleted": users_deleted,
            "errors": errors,
        }


async def run_toolset_instance_migration(graph_provider, config_service, logger) -> Dict[str, Any]:
    """Entry point called by ConnectorAppContainer.

    Args:
        graph_provider: Already resolved graph provider instance
        config_service: Already resolved config service instance
        logger: Logger instance
    """
    try:
        migration_service = ToolsetInstanceMigrationService(
            config_service=config_service,
            graph_provider=graph_provider,
            logger=logger
        )
        return await migration_service.run_migration()
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Migration failed: {e}",
            "agents_deleted": 0,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "edges_deleted": 0,
            "instances_created": 0,
            "oauth_configs_created": 0,
            "users_migrated": 0,
            "users_deleted": 0,
            "errors": 1,
        }
