"""
Knowledge Base to Connector Migration Service

This module provides functionality to migrate Knowledge Base record groups to use
the new connector-based architecture with belongs_to edges.

Migration Steps:
1. Check ETCD flag - skip if migration already completed
2. For each organization:
   a. Find or create KB app (with key: knowledgeBase_{orgId})
   b. Create belongs_to edge between org and KB app
   c. For each KB record group:
      - Create belongs_to edge between KB and KB app
      - Delete record_relations edges between KB and records
      - Update all records' connectorId to KB app ID
3. Set ETCD flag on completion

The migration is idempotent and can be safely re-run.
All operations within a single organization are wrapped in transactions for atomicity.
"""

import asyncio
import traceback
from typing import Dict, List, Optional

from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    Connectors,
    ConnectorScopes,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KnowledgeBaseToConnectorMigrationError(Exception):
    """Base exception for knowledge base to connector migration errors."""
    pass


class KnowledgeBaseToConnectorMigrationService:
    """
    Service for migrating Knowledge Base record groups to connector-based architecture.

    This service handles:
    - Checking ETCD migration flag
    - Creating KB connector app instances for organizations that don't have them
    - Creating belongs_to edges between orgs and KB apps
    - Creating belongs_to edges between KB record groups and KB apps
    - Deleting old record_relations edges between KB record groups and records
    - Updating records' connectorId to point to KB app
    - Setting ETCD migration flag on completion

    All database operations for a single organization are wrapped in transactions
    for atomicity and safety.

    Attributes:
        arango (BaseArangoService): ArangoDB service instance
        config_service: Configuration service for ETCD operations
        logger: Logger instance for migration tracking
    """

    MIGRATION_FLAG_KEY = "kb_to_connector_migration_completed"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service,
        logger,
    ) -> None:
        """
        Initialize the knowledge base to connector migration service.

        Args:
            arango_service: Service for ArangoDB operations
            config_service: Service for ETCD configuration management
            logger: Logger for tracking migration progress
        """
        self.arango = arango_service
        self.config_service = config_service
        self.logger = logger

    async def run_migration(
        self,
        dry_run: bool = False,
        batch_size: int = 100
    ) -> Dict:
        """
        Run the complete migration process.

        Args:
            dry_run: If True, only reports what would be done without making changes
            batch_size: Number of organizations to process in each batch

        Returns:
            Dict with statistics about the migration
        """
        try:
            self.logger.info("🚀 Starting Knowledge Base to Connector Migration (dry_run=%s)", dry_run)

            # Step 1: Check ETCD flag - skip if migration already completed
            if await self._is_migration_completed():
                self.logger.info("⏭️ Migration already completed (ETCD flag is set), skipping")
                return {
                    "success": True,
                    "message": "Migration already completed",
                    "skipped": True,
                    "orgs_processed": 0,
                    "apps_created": 0,
                    "org_app_edges_created": 0,
                    "kb_app_edges_created": 0,
                    "record_relations_deleted": 0,
                    "records_updated": 0,
                    "errors": []
                }

            # Step 2: Get all organizations
            orgs = await self._get_all_orgs()

            if not orgs:
                self.logger.info("✅ No organizations found")
                return {
                    "success": True,
                    "message": "No organizations found",
                    "orgs_processed": 0,
                    "apps_created": 0,
                    "org_app_edges_created": 0,
                    "kb_app_edges_created": 0,
                    "record_relations_deleted": 0,
                    "records_updated": 0,
                    "errors": []
                }

            self.logger.info(f"Found {len(orgs)} organization(s)")

            if dry_run:
                return await self._dry_run_analysis(orgs)

            # Step 3: Process each organization
            total_apps_created = 0
            total_org_app_edges = 0
            total_kb_app_edges = 0
            total_record_relations_deleted = 0
            total_records_updated = 0
            errors = []

            for org in orgs:
                org_id = org.get('_key')
                try:
                    result = await self._migrate_org_knowledge_bases(org_id)
                    if result["success"]:
                        total_apps_created += result.get("apps_created", 0)
                        total_org_app_edges += result.get("org_app_edges_created", 0)
                        total_kb_app_edges += result.get("kb_app_edges_created", 0)
                        total_record_relations_deleted += result.get("record_relations_deleted", 0)
                        total_records_updated += result.get("records_updated", 0)
                        self.logger.info(
                            f"✅ Successfully migrated KBs for org {org_id}: "
                            f"{result.get('apps_created', 0)} apps created, "
                            f"{result.get('org_app_edges_created', 0)} org-app edges, "
                            f"{result.get('kb_app_edges_created', 0)} kb-app edges, "
                            f"{result.get('record_relations_deleted', 0)} record_relations deleted, "
                            f"{result.get('records_updated', 0)} records updated"
                        )
                    else:
                        errors.append({
                            "org_id": org_id,
                            "error": result.get("error", "Unknown error")
                        })
                except Exception as e:
                    error_msg = f"Failed to migrate KBs for org {org_id}: {str(e)}"
                    self.logger.error(f"❌ {error_msg}")
                    self.logger.error(traceback.format_exc())
                    errors.append({
                        "org_id": org_id,
                        "error": error_msg
                    })

            summary = {
                "success": len(errors) == 0,
                "orgs_processed": len(orgs),
                "apps_created": total_apps_created,
                "org_app_edges_created": total_org_app_edges,
                "kb_app_edges_created": total_kb_app_edges,
                "record_relations_deleted": total_record_relations_deleted,
                "records_updated": total_records_updated,
                "errors": errors
            }

            # Step 4: Set ETCD flag on successful completion
            if summary["success"]:
                await self._mark_migration_completed()
                self.logger.info("✅ Migration flag set in ETCD")

            self.logger.info(
                f"✅ Migration completed: {summary['orgs_processed']} orgs processed, "
                f"{summary['apps_created']} apps created, "
                f"{summary['org_app_edges_created']} org-app edges, "
                f"{summary['kb_app_edges_created']} kb-app edges, "
                f"{summary['record_relations_deleted']} record_relations deleted, "
                f"{summary['records_updated']} records updated, "
                f"{len(errors)} errors"
            )

            return summary

        except Exception as e:
            self.logger.error(f"❌ Migration failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e),
                "orgs_processed": 0,
                "apps_created": 0,
                "org_app_edges_created": 0,
                "kb_app_edges_created": 0,
                "record_relations_deleted": 0,
                "records_updated": 0,
                "errors": [{"error": str(e)}]
            }

    async def _is_migration_completed(self) -> bool:
        """
        Check if migration has already been completed by checking ETCD flag.

        Returns:
            True if migration is completed, False otherwise
        """
        try:
            from app.config.constants.service import config_node_constants
            migrations_key = config_node_constants.MIGRATIONS.value
            state = await self.config_service.get_config(migrations_key, default={})
            return bool(state.get(self.MIGRATION_FLAG_KEY, False))
        except Exception as e:
            self.logger.warning(f"Failed to check migration flag in ETCD: {str(e)}")
            # If we can't check, assume not completed to be safe
            return False

    async def _mark_migration_completed(self) -> None:
        """
        Mark migration as completed in ETCD.
        """
        try:
            from app.config.constants.service import config_node_constants
            migrations_key = config_node_constants.MIGRATIONS.value
            state = await self.config_service.get_config(migrations_key, default={})
            state = state or {}
            state[self.MIGRATION_FLAG_KEY] = True
            await self.config_service.set_config(migrations_key, state)
        except Exception as e:
            self.logger.error(f"Failed to set migration flag in ETCD: {str(e)}")
            raise

    async def _get_all_orgs(self) -> List[Dict]:
        """
        Get all organizations from the database.

        Returns:
            List of organization documents
        """
        try:
            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
                FILTER org.isDeleted != true
                RETURN org
            """
            cursor = self.arango.db.aql.execute(query)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get organizations: {str(e)}")
            raise

    async def _dry_run_analysis(self, orgs: List[Dict]) -> Dict:
        """
        Perform a dry run analysis without making changes.

        Args:
            orgs: List of organization documents to analyze

        Returns:
            Dict with analysis results
        """
        total_kbs = 0
        orgs_needing_apps = []
        total_record_relations_to_delete = 0
        total_kb_app_edges_to_create = 0
        total_records_to_update = 0

        for org in orgs:
            org_id = org.get('_key')

            # Check if KB app exists
            org_apps = await self.arango.get_org_apps(org_id)
            kb_app = next(
                (app for app in org_apps if app.get('type') == Connectors.KNOWLEDGE_BASE.value),
                None
            )

            if not kb_app:
                orgs_needing_apps.append(org_id)

            # Count KBs and edges for this org
            kbs = await self._get_kb_record_groups_for_org(org_id)
            total_kbs += len(kbs)

            for kb in kbs:
                kb_id = kb.get('_key')
                # Each KB will get one belongs_to edge to KB app
                total_kb_app_edges_to_create += 1

                # Count record_relations edges to delete
                record_relations_count = await self._count_record_relations_edges(kb_id)
                total_record_relations_to_delete += record_relations_count

                # Count records that will be updated
                records_count = await self._count_kb_records(kb_id)
                total_records_to_update += records_count

        return {
            "success": True,
            "dry_run": True,
            "orgs_analyzed": len(orgs),
            "total_kbs": total_kbs,
            "orgs_needing_apps": len(orgs_needing_apps),
            "record_relations_to_delete": total_record_relations_to_delete,
            "kb_app_edges_to_create": total_kb_app_edges_to_create,
            "records_to_update": total_records_to_update,
            "orgs_needing_apps_list": orgs_needing_apps
        }

    async def _migrate_org_knowledge_bases(self, org_id: str) -> Dict:
        """
        Migrate all Knowledge Bases for a single organization.

        Migration flow:
        1. Find or create KB app (with key: knowledgeBase_{orgId})
        2. Create belongs_to edge between org and KB app (if needed)
        3. For each KB record group:
           a. Create belongs_to edge between KB and KB app
           b. Delete record_relations edges between KB and records
           c. Update records' connectorId to KB app ID

        Args:
            org_id: Organization ID

        Returns:
            Dict with migration results for this org
        """
        try:
            # Start transaction (not async)
            transaction = self.arango.db.begin_transaction(
                write=[
                    CollectionNames.APPS.value,
                    CollectionNames.ORG_APP_RELATION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.RECORDS.value,
                ]
            )

            try:
                # Step 1: Find or create KB app for this org
                kb_app, app_created, org_app_edge_created = await self._get_or_create_kb_app(org_id, transaction)
                if not kb_app:
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return {
                        "success": False,
                        "error": f"Failed to get or create KB app for org {org_id}",
                        "apps_created": 0,
                        "org_app_edges_created": 0,
                        "kb_app_edges_created": 0,
                        "record_relations_deleted": 0,
                        "records_updated": 0
                    }

                kb_app_id = kb_app.get('_key')

                # Step 2: Get all KB record groups for this org
                kbs = await self._get_kb_record_groups_for_org(org_id)

                if not kbs:
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
                    return {
                        "success": True,
                        "apps_created": 1 if app_created else 0,
                        "org_app_edges_created": 1 if org_app_edge_created else 0,
                        "kb_app_edges_created": 0,
                        "record_relations_deleted": 0,
                        "records_updated": 0
                    }

                # Step 3: For each KB, process migration
                kb_app_edges_created = 0
                record_relations_deleted = 0
                records_updated = 0

                for kb in kbs:
                    kb_id = kb.get('_key')

                    # 3a. Create belongs_to edge from KB record group to KB app
                    if await self._create_kb_to_app_edge(kb_id, kb_app_id, transaction):
                        kb_app_edges_created += 1
                    # 3b. Update records' connectorId to KB app ID
                    updated_count = await self._update_records_connector_id(kb_id, kb_app_id, transaction)
                    records_updated += updated_count
                    # 3c. Delete record_relations edges from KB to records
                    deleted_count = await self._delete_record_relations_edges(kb_id, transaction)
                    record_relations_deleted += deleted_count


                # Commit transaction
                await asyncio.to_thread(lambda: transaction.commit_transaction())

                return {
                    "success": True,
                    "apps_created": 1 if app_created else 0,
                    "org_app_edges_created": 1 if org_app_edge_created else 0,
                    "kb_app_edges_created": kb_app_edges_created,
                    "record_relations_deleted": record_relations_deleted,
                    "records_updated": records_updated
                }

            except Exception:
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                raise

        except Exception as e:
            self.logger.error(f"Failed to migrate KBs for org {org_id}: {str(e)}")
            raise

    async def _get_or_create_kb_app(self, org_id: str, transaction) -> tuple[Optional[Dict], bool, bool]:
        """
        Get existing KB app or create a new one for the organization.

        The KB app key follows the format: knowledgeBase_{orgId}

        Args:
            org_id: Organization ID
            transaction: Database transaction

        Returns:
            Tuple of (KB app document, app_created flag, org_app_edge_created flag)
        """
        try:
            # Check if KB app already exists with the expected key
            instance_key = f"knowledgeBase_{org_id}"

            # Try to get the app by key
            try:
                existing_app = await self.arango.get_document(instance_key, CollectionNames.APPS.value)
                if existing_app and existing_app.get('type') == Connectors.KNOWLEDGE_BASE.value:
                    self.logger.debug(f"KB app already exists for org {org_id}: {instance_key}")

                    # Check if org-app edge exists
                    await self._check_org_app_edge_exists(org_id, instance_key, transaction)

                    return existing_app, False, False
            except Exception:
                pass

            # Also check by searching org apps (fallback for any existing KB apps)
            org_apps = await self.arango.get_org_apps(org_id)
            existing_kb_app = next(
                (app for app in org_apps if app.get('type') == Connectors.KNOWLEDGE_BASE.value),
                None
            )

            if existing_kb_app:
                self.logger.debug(f"Found existing KB app via org_apps for org {org_id}: {existing_kb_app.get('_key')}")
                return existing_kb_app, False, False

            # Create new KB app with the standard key format
            from app.connectors.sources.localKB.connector import KnowledgeBaseConnector

            # Get KB connector metadata
            if not hasattr(KnowledgeBaseConnector, '_connector_metadata'):
                self.logger.warning("Knowledge Base connector metadata not found")
                return None, False, False

            metadata = KnowledgeBaseConnector._connector_metadata
            connector_type = metadata.get('name', Connectors.KNOWLEDGE_BASE.value)
            app_group = metadata.get('appGroup', AppGroups.LOCAL_STORAGE.value)

            current_timestamp = get_epoch_timestamp_in_ms()

            instance_document = {
                '_key': instance_key,
                'name': connector_type,
                'type': connector_type,
                'appGroup': app_group,
                'authType': 'NONE',
                'scope': ConnectorScopes.TEAM.value,
                'isActive': True,
                'isAgentActive': True,
                'isConfigured': True,
                'isAuthenticated': True,
                'createdBy': 'system',
                'updatedBy': 'system',
                'createdAtTimestamp': current_timestamp,
                'updatedAtTimestamp': current_timestamp
            }

            # Create app in transaction
            await self.arango.batch_upsert_nodes(
                [instance_document],
                CollectionNames.APPS.value,
                transaction=transaction
            )

            self.logger.info(f"Created KB app for org {org_id}: {instance_key}")

            # Create org-app relation edge
            edge_document = {
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{instance_key}",
                "createdAtTimestamp": current_timestamp,
            }

            await self.arango.batch_create_edges(
                [edge_document],
                CollectionNames.ORG_APP_RELATION.value,
                transaction=transaction
            )

            self.logger.info(f"Created org-app edge for org {org_id} -> {instance_key}")

            return instance_document, True, True

        except Exception as e:
            self.logger.error(f"Failed to get or create KB app: {str(e)}")
            raise

    async def _get_kb_record_groups_for_org(self, org_id: str) -> List[Dict]:
        """
        Get all KB record groups for an organization.

        Args:
            org_id: Organization ID

        Returns:
            List of KB record group documents
        """
        try:
            query = f"""
            FOR kb IN {CollectionNames.RECORD_GROUPS.value}
                FILTER kb.orgId == @org_id
                    AND (kb.connectorName == @kb_connector OR kb.groupType == @kb_type)
                    AND (kb.isDeleted == false OR kb.isDeleted == null)
                RETURN kb
            """
            bind_vars = {
                "org_id": org_id,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
            }
            cursor = self.arango.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get KB record groups: {str(e)}")
            raise

    async def _check_org_app_edge_exists(self, org_id: str, app_id: str, transaction) -> bool:
        """
        Check if org-app edge exists.

        Args:
            org_id: Organization ID
            app_id: App ID
            transaction: Database transaction

        Returns:
            True if edge exists, False otherwise
        """
        try:
            query = f"""
            FOR edge IN {CollectionNames.ORG_APP_RELATION.value}
                FILTER edge._from == CONCAT(@org_collection, '/', @org_id)
                    AND edge._to == CONCAT(@app_collection, '/', @app_id)
                LIMIT 1
                RETURN 1
            """
            bind_vars = {
                "org_id": org_id,
                "app_id": app_id,
                "org_collection": CollectionNames.ORGS.value,
                "app_collection": CollectionNames.APPS.value,
            }
            cursor = transaction.aql.execute(query, bind_vars=bind_vars)
            return len(list(cursor)) > 0
        except Exception as e:
            self.logger.error(f"Failed to check org-app edge: {str(e)}")
            return False

    async def _count_record_relations_edges(self, kb_id: str) -> int:
        """
        Count record_relations edges from KB to records.

        Args:
            kb_id: Knowledge Base record group ID

        Returns:
            Number of edges
        """
        try:
            query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._from == CONCAT(@kb_collection, '/', @kb_id)
                    AND edge.relationshipType == "PARENT_CHILD"
                RETURN 1
            """
            bind_vars = {
                "kb_id": kb_id,
                "kb_collection": CollectionNames.RECORD_GROUPS.value,
            }
            cursor = self.arango.db.aql.execute(query, bind_vars=bind_vars)
            return len(list(cursor))
        except Exception as e:
            self.logger.error(f"Failed to count record_relations edges: {str(e)}")
            raise

    async def _count_kb_records(self, kb_id: str) -> int:
        """
        Count records that belong to a KB.

        Args:
            kb_id: Knowledge Base record group ID

        Returns:
            Number of records
        """
        try:
            query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._from == CONCAT(@kb_collection, '/', @kb_id)
                    AND edge.relationshipType == "PARENT_CHILD"
                LET record = DOCUMENT(edge._to)
                FILTER record != null
                RETURN 1
            """
            bind_vars = {
                "kb_id": kb_id,
                "kb_collection": CollectionNames.RECORD_GROUPS.value,
            }
            cursor = self.arango.db.aql.execute(query, bind_vars=bind_vars)
            return len(list(cursor))
        except Exception as e:
            self.logger.error(f"Failed to count KB records: {str(e)}")
            raise

    async def _delete_record_relations_edges(self, kb_id: str, transaction) -> int:
        """
        Delete all record_relations edges from KB to records.

        Args:
            kb_id: Knowledge Base record group ID
            transaction: Database transaction

        Returns:
            Number of edges deleted
        """
        try:
            query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._from == CONCAT(@kb_collection, '/', @kb_id)
                    AND edge.relationshipType == "PARENT_CHILD"
                REMOVE edge IN {CollectionNames.RECORD_RELATIONS.value}
                RETURN 1
            """
            bind_vars = {
                "kb_id": kb_id,
                "kb_collection": CollectionNames.RECORD_GROUPS.value,
            }
            cursor = transaction.aql.execute(query, bind_vars=bind_vars)
            deleted_count = len(list(cursor))
            return deleted_count
        except Exception as e:
            self.logger.error(f"Failed to delete record_relations edges: {str(e)}")
            raise

    async def _create_kb_to_app_edge(self, kb_id: str, kb_app_id: str, transaction) -> bool:
        """
        Create belongs_to edge from KB record group to KB app.

        Args:
            kb_id: Knowledge Base record group ID
            kb_app_id: Knowledge Base app ID
            transaction: Database transaction

        Returns:
            True if edge was created, False if already exists
        """
        try:
            # Check if edge already exists
            check_query = f"""
            FOR edge IN {CollectionNames.BELONGS_TO.value}
                FILTER edge._from == CONCAT(@kb_collection, '/', @kb_id)
                    AND edge._to == CONCAT(@app_collection, '/', @kb_app_id)
                    AND edge.entityType == @entity_type
                LIMIT 1
                RETURN 1
            """
            bind_vars = {
                "kb_id": kb_id,
                "kb_app_id": kb_app_id,
                "kb_collection": CollectionNames.RECORD_GROUPS.value,
                "app_collection": CollectionNames.APPS.value,
                "entity_type": Connectors.KNOWLEDGE_BASE.value,
            }
            cursor = transaction.aql.execute(check_query, bind_vars=bind_vars)
            if list(cursor):
                # Edge already exists
                self.logger.debug(f"KB-to-app edge already exists: {kb_id} -> {kb_app_id}")
                return False

            # Create edge
            current_timestamp = get_epoch_timestamp_in_ms()
            belongs_to_edge = {
                "_from": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                "_to": f"{CollectionNames.APPS.value}/{kb_app_id}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }

            await self.arango.batch_create_edges(
                [belongs_to_edge],
                CollectionNames.BELONGS_TO.value,
                transaction=transaction
            )

            self.logger.debug(f"Created KB-to-app edge: {kb_id} -> {kb_app_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to create KB-to-app edge: {str(e)}")
            raise

    async def _update_records_connector_id(self, kb_id: str, kb_app_id: str, transaction) -> int:
        """
        Update connectorId on all records that belong to a KB to point to KB app.

        Uses belongs_to edges between records and KB record group.

        Args:
            kb_id: Knowledge Base record group ID
            kb_app_id: Knowledge Base app ID
            transaction: Database transaction

        Returns:
            Number of records updated
        """
        try:
            current_timestamp = get_epoch_timestamp_in_ms()

            # Update all records that belong to this KB via belongs_to edge
            query = f"""
            FOR edge IN {CollectionNames.BELONGS_TO.value}
                FILTER edge._to == CONCAT(@kb_collection, '/', @kb_id)
                    AND edge.entityType == @entity_type
                LET record = DOCUMENT(edge._from)
                FILTER record != null
                FILTER IS_SAME_COLLECTION(@records_collection, record._id)
                FILTER record.connectorId != @kb_app_id
                UPDATE record WITH {{
                    connectorId: @kb_app_id,
                    updatedAtTimestamp: @timestamp
                }} IN {CollectionNames.RECORDS.value}
                RETURN 1
            """
            bind_vars = {
                "kb_id": kb_id,
                "kb_app_id": kb_app_id,
                "kb_collection": CollectionNames.RECORD_GROUPS.value,
                "records_collection": CollectionNames.RECORDS.value,
                "entity_type": Connectors.KNOWLEDGE_BASE.value,
                "timestamp": current_timestamp,
            }
            cursor = transaction.aql.execute(query, bind_vars=bind_vars)
            updated_count = len(list(cursor))

            if updated_count > 0:
                self.logger.debug(f"Updated connectorId for {updated_count} records in KB {kb_id}")

            return updated_count

        except Exception as e:
            self.logger.error(f"Failed to update records connectorId: {str(e)}")
            raise


# Integration function for container initialization
async def run_kb_to_connector_migration(container) -> Dict:
    """
    Wrapper function to be called from container initialization.

    Args:
        container: Dependency injection container

    Returns:
        Dict with migration results
    """
    try:
        logger = container.logger()
        arango_service = await container.arango_service()
        config_service = container.config_service()

        migration_service = KnowledgeBaseToConnectorMigrationService(
            arango_service=arango_service,
            config_service=config_service,
            logger=logger
        )

        result = await migration_service.run_migration(dry_run=False)

        return result

    except Exception as e:
        logger = container.logger()
        logger.error(f"❌ KB to Connector Migration error: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "orgs_processed": 0,
            "apps_created": 0,
            "org_app_edges_created": 0,
            "kb_app_edges_created": 0,
            "record_relations_deleted": 0,
            "records_updated": 0,
            "errors": [{"error": str(e)}]
        }
