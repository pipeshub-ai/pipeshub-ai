"""
Knowledge Base Migration Script
Migrates from old knowledgeBase collection to new recordGroups system
"""

import asyncio
import uuid
from typing import Dict, List, Optional

from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    Connectors,
)
from app.connectors.sources.localKB.core.arango_service import (
    KnowledgeBaseArangoService,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KnowledgeBaseMigrationService:
    """Service to handle migration from old KB system to new recordGroups system"""

    def __init__(self, arango_service: KnowledgeBaseArangoService) -> None:
        self.arango_service = arango_service
        self.logger = arango_service.logger
        self.db = arango_service.db

        # Old collection names (Node.js system)
        self.OLD_KB_COLLECTION = "knowledgeBase"
        self.OLD_USER_TO_RECORD_EDGES = "permissions"  # Direct user→record permissions
        self.OLD_KB_TO_RECORD_EDGES = "belongsToKnowledgeBase"  # KB→record relationships

        # New collection names (Python system)
        self.NEW_KB_COLLECTION = CollectionNames.RECORD_GROUPS.value
        self.NEW_USER_TO_KB_EDGES = CollectionNames.PERMISSIONS_TO_KB.value
        self.NEW_RECORD_TO_KB_EDGES = CollectionNames.BELONGS_TO_KB.value

    async def run_migration(self) -> Dict:
        """
        Main migration method that orchestrates the entire migration process
        """
        try:
            self.logger.info("🚀 Starting Knowledge Base migration from old to new system")

            # Step 1: Validate migration preconditions
            await self._validate_migration_preconditions()

            # Step 2: Get all old knowledge bases and their relationships
            migration_data = await self._analyze_old_system()

            if not migration_data["old_kbs"]:
                self.logger.info("✅ No old knowledge bases found - migration not needed")
                return {"success": True, "message": "No migration needed", "migrated_count": 0}

            # Step 3: Create transaction for migration
            transaction = self.db.begin_transaction(
                write=[
                    self.NEW_KB_COLLECTION,
                    self.NEW_USER_TO_KB_EDGES,
                    self.NEW_RECORD_TO_KB_EDGES,
                    CollectionNames.USERS.value,
                    CollectionNames.RECORDS.value,
                ]
            )

            migration_results = []
            migration_successful = False

            try:
                # Step 4: Migrate each old KB to new system
                migration_results = await self._migrate_knowledge_bases(
                    migration_data, transaction
                )

                # Check if ALL migrations were successful
                successful_migrations = [r for r in migration_results if r.get('success')]
                failed_migrations = [r for r in migration_results if not r.get('success')]

                if failed_migrations:
                    # If ANY migration failed, abort the entire transaction
                    self.logger.error(f"❌ {len(failed_migrations)} migrations failed - aborting transaction")
                    for failure in failed_migrations:
                        self.logger.error(f"   - KB {failure.get('old_kb_id')}: {failure.get('error')}")

                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Migration transaction aborted due to failures")

                    return {
                        "success": False,
                        "message": f"Migration failed: {len(failed_migrations)} out of {len(migration_results)} KBs failed",
                        "migrated_count": 0,
                        "failed_count": len(failed_migrations),
                        "details": {
                            "successful": successful_migrations,
                            "failed": failed_migrations
                        }
                    }

                # Step 5: Commit transaction ONLY if all migrations succeeded
                await asyncio.to_thread(lambda: transaction.commit_transaction())
                self.logger.info("✅ Migration transaction committed successfully")
                migration_successful = True

            except Exception as e:
                self.logger.error(f"❌ Migration failed, aborting transaction: {str(e)}")
                try:
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Transaction aborted due to exception")
                except Exception as abort_error:
                    self.logger.error(f"❌ Failed to abort transaction: {str(abort_error)}")
                raise

            # Step 6: Clean up old collections ONLY if migration was completely successful
            if migration_successful and len([r for r in migration_results if r.get('success')]) > 0:
                try:
                    await self._cleanup_old_collections(migration_data)
                    self.logger.info("✅ Cleanup completed successfully")
                except Exception as cleanup_error:
                    self.logger.error(f"❌ Cleanup failed but migration succeeded: {str(cleanup_error)}")
                    self.logger.warning("⚠️ Manual cleanup may be needed for old collections")
                    # Don't fail the migration for cleanup issues since data was migrated successfully
            else:
                self.logger.info("⏭️ Skipping cleanup - no successful migrations to clean up")

            # Step 7: Verify migration success
            if migration_successful:
                await self._verify_migration(migration_results)

            self.logger.info("🎉 Knowledge Base migration completed successfully")
            return {
                "success": True,
                "message": "Migration completed successfully",
                "migrated_count": len([r for r in migration_results if r.get('success')]),
                "failed_count": len([r for r in migration_results if not r.get('success')]),
                "details": migration_results
            }

        except Exception as e:
            self.logger.error(f"❌ Knowledge Base migration failed: {str(e)}")
            return {
                "success": False,
                "message": f"Migration failed: {str(e)}",
                "migrated_count": 0,
                "failed_count": 0
            }

    async def _validate_migration_preconditions(self) -> None:
        """Validate that migration can proceed safely"""
        self.logger.info("🔍 Validating migration preconditions")

        # Check if old collections exist
        collections = self.db.collections()
        collection_names = [col['name'] for col in collections]

        if self.OLD_KB_COLLECTION not in collection_names:
            self.logger.info(f"✅ Old KB collection '{self.OLD_KB_COLLECTION}' not found - no migration needed")
            return

        # Check if new collections exist
        required_new_collections = [
            self.NEW_KB_COLLECTION,
            self.NEW_USER_TO_KB_EDGES,
            self.NEW_RECORD_TO_KB_EDGES,
            CollectionNames.USERS.value,
            CollectionNames.RECORDS.value,
        ]

        missing_collections = [col for col in required_new_collections if col not in collection_names]
        if missing_collections:
            raise Exception(f"Required new collections missing: {missing_collections}")

        self.logger.info("✅ Migration preconditions validated")

    async def _analyze_old_system(self) -> Dict:
        """Analyze the old system to understand what needs to be migrated"""
        self.logger.info("📊 Analyzing old knowledge base system")

        try:
            # Get all old knowledge bases
            old_kbs_query = f"""
            FOR kb IN {self.OLD_KB_COLLECTION}
                FILTER kb.isDeleted != true
                RETURN kb
            """

            cursor = self.db.aql.execute(old_kbs_query)
            old_kbs = list(cursor)

            self.logger.info(f"📋 Found {len(old_kbs)} old knowledge bases to migrate")

            # Get old user-to-record permissions
            old_user_permissions_query = f"""
            FOR edge IN {self.OLD_USER_TO_RECORD_EDGES}
                LET user = DOCUMENT(edge._to)
                LET record = DOCUMENT(edge._from)
                FILTER user != null AND record != null
                RETURN {{
                    edge: edge,
                    user: user,
                    record: record
                }}
            """

            cursor = self.db.aql.execute(old_user_permissions_query)
            old_user_permissions = list(cursor)

            # Get old KB-to-record relationships
            old_kb_relationships_query = f"""
            FOR edge IN {self.OLD_KB_TO_RECORD_EDGES}
                LET kb = DOCUMENT(edge._to)
                LET record = DOCUMENT(edge._from)
                FILTER kb != null AND record != null
                RETURN {{
                    edge: edge,
                    kb: kb,
                    record: record
                }}
            """

            cursor = self.db.aql.execute(old_kb_relationships_query)
            old_kb_relationships = list(cursor)

            # Group data by organization for analysis
            org_data = {}
            for kb in old_kbs:
                org_id = kb.get('orgId')
                user_id = kb.get('userId')

                if org_id not in org_data:
                    org_data[org_id] = {}
                if user_id not in org_data[org_id]:
                    org_data[org_id][user_id] = {
                        'kbs': [],
                        'user_permissions': [],
                        'kb_records': []
                    }

                org_data[org_id][user_id]['kbs'].append(kb)

            # Map permissions to orgs/users
            for perm_data in old_user_permissions:
                user = perm_data['user']
                org_id = user.get('orgId')
                user_id = user.get('userId')

                if org_id in org_data and user_id in org_data[org_id]:
                    org_data[org_id][user_id]['user_permissions'].append(perm_data)

            # Map KB relationships to orgs/users
            for rel_data in old_kb_relationships:
                kb = rel_data['kb']
                org_id = kb.get('orgId')
                user_id = kb.get('userId')

                if org_id in org_data and user_id in org_data[org_id]:
                    org_data[org_id][user_id]['kb_records'].append(rel_data)

            analysis_summary = {
                'total_orgs': len(org_data),
                'total_users': sum(len(users) for users in org_data.values()),
                'total_kbs': len(old_kbs),
                'total_user_permissions': len(old_user_permissions),
                'total_kb_relationships': len(old_kb_relationships)
            }

            self.logger.info(f"📊 Analysis complete: {analysis_summary}")

            return {
                'old_kbs': old_kbs,
                'old_user_permissions': old_user_permissions,
                'old_kb_relationships': old_kb_relationships,
                'org_data': org_data,
                'analysis': analysis_summary
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to analyze old system: {str(e)}")
            raise

    async def _migrate_knowledge_bases(self, migration_data: Dict, transaction) -> List[Dict]:
        """Migrate knowledge bases from old to new system"""
        self.logger.info("🔄 Starting knowledge base migration")

        migration_results = []
        timestamp = get_epoch_timestamp_in_ms()

        for org_id, org_users in migration_data['org_data'].items():
            for user_id, user_data in org_users.items():
                try:
                    # Get or create user in new system
                    user_key = await self._ensure_user_exists(user_id, org_id, transaction)

                    for old_kb in user_data['kbs']:
                        try:
                            result = await self._migrate_single_kb(
                                old_kb, user_key, org_id, user_data, timestamp, transaction
                            )
                            migration_results.append(result)

                        except Exception as kb_error:
                            self.logger.error(f"❌ Failed to migrate KB {old_kb.get('_key')}: {str(kb_error)}")
                            migration_results.append({
                                'old_kb_id': old_kb.get('_key'),
                                'success': False,
                                'error': str(kb_error)
                            })

                except Exception as user_error:
                    self.logger.error(f"❌ Failed to process user {user_id} in org {org_id}: {str(user_error)}")

        self.logger.info(f"✅ Migration completed: {len([r for r in migration_results if r.get('success')])} successful, "
                        f"{len([r for r in migration_results if not r.get('success')])} failed")

        return migration_results

    async def _migrate_single_kb(self, old_kb: Dict, user_key: str, org_id: str,
                                user_data: Dict, timestamp: int, transaction) -> Dict:
        """Migrate a single knowledge base to the new system"""
        old_kb_id = old_kb['_key']
        old_kb_name = old_kb.get('name', 'Migrated Knowledge Base')

        self.logger.info(f"🔄 Migrating KB: {old_kb_name} (ID: {old_kb_id})")

        # Create new KB in recordGroups collection
        new_kb_id = str(uuid.uuid4())
        new_kb_data = {
            "_key": new_kb_id,
            "createdBy": user_key,
            "orgId": org_id,
            "groupName": old_kb_name,
            "groupType": Connectors.KNOWLEDGE_BASE.value,
            "connectorName": Connectors.KNOWLEDGE_BASE.value,
            "createdAtTimestamp": old_kb.get('createdAtTimestamp', timestamp),
            "updatedAtTimestamp": timestamp,
            "lastSyncTimestamp": timestamp,
            "sourceCreatedAtTimestamp": old_kb.get('createdAtTimestamp', timestamp),
            "sourceLastModifiedTimestamp": timestamp,
        }

        # Insert new KB
        await self.arango_service.batch_upsert_nodes(
            [new_kb_data], self.NEW_KB_COLLECTION, transaction
        )

        # Create user permission edge (user → recordGroup)
        permission_edge = {
            "_from": f"{CollectionNames.USERS.value}/{user_key}",
            "_to": f"{self.NEW_KB_COLLECTION}/{new_kb_id}",
            "externalPermissionId": "",
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
            "lastUpdatedTimestampAtSource": timestamp,
        }

        await self.arango_service.batch_create_edges(
            [permission_edge], self.NEW_USER_TO_KB_EDGES, transaction
        )

        # Migrate record relationships (record → recordGroup)
        migrated_records = await self._migrate_kb_records(
            old_kb_id, new_kb_id, user_data, timestamp, transaction
        )

        self.logger.info(f"✅ Successfully migrated KB {old_kb_name}: {migrated_records} records")

        return {
            'old_kb_id': old_kb_id,
            'new_kb_id': new_kb_id,
            'kb_name': old_kb_name,
            'user_key': user_key,
            'org_id': org_id,
            'migrated_records': migrated_records,
            'success': True
        }

    async def _migrate_kb_records(self, old_kb_id: str, new_kb_id: str,
                                 user_data: Dict, timestamp: int, transaction) -> int:
        """Migrate record relationships from old KB to new KB"""

        # Find all records that belonged to the old KB
        old_records_for_kb = [
            rel_data['record'] for rel_data in user_data['kb_records']
            if rel_data['kb']['_key'] == old_kb_id
        ]

        if not old_records_for_kb:
            self.logger.info(f"📝 No records found for old KB {old_kb_id}")
            return 0

        # Create new edges: record → recordGroup
        record_edges = []
        for record in old_records_for_kb:
            edge = {
                "_from": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                "_to": f"{self.NEW_KB_COLLECTION}/{new_kb_id}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
            record_edges.append(edge)

        if record_edges:
            await self.arango_service.batch_create_edges(
                record_edges, self.NEW_RECORD_TO_KB_EDGES, transaction
            )

        self.logger.info(f"📝 Migrated {len(record_edges)} record relationships for KB {old_kb_id}")
        return len(record_edges)

    async def _ensure_user_exists(self, user_id: str, org_id: str, transaction) -> str:
        """Ensure user exists in the new system and return their key"""

        # Check if user already exists in new system
        user = await self.arango_service.get_user_by_user_id(user_id)
        if user:
            return user['_key']

        # If user doesn't exist, we need to get info from old system
        # This is a fallback - in most cases users should already exist
        old_user_query = f"""
        FOR user IN {CollectionNames.USERS.value}
            FILTER user.userId == @user_id AND user.orgId == @org_id
            RETURN user
        """

        cursor = transaction.aql.execute(old_user_query, bind_vars={
            "user_id": user_id,
            "org_id": org_id
        })

        existing_users = list(cursor)
        if existing_users:
            return existing_users[0]['_key']

        # Create minimal user record if not found (should rarely happen)
        self.logger.warning(f"⚠️ Creating minimal user record for {user_id} in org {org_id}")

        user_key = str(uuid.uuid4())
        timestamp = get_epoch_timestamp_in_ms()

        minimal_user = {
            "_key": user_key,
            "userId": user_id,
            "orgId": org_id,
            "email": f"migrated-{user_id}@example.com",
            "firstName": "",
            "lastName": "",
            "fullName": f"Migrated User {user_id}",
            "isActive": True,
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
        }

        await self.arango_service.batch_upsert_nodes(
            [minimal_user], CollectionNames.USERS.value, transaction
        )

        return user_key

    async def _cleanup_old_collections(self, migration_data: Dict) -> None:
        """Clean up old collections after successful migration"""
        self.logger.info("🧹 Starting cleanup of old collections")

        try:
            # Create cleanup transaction
            cleanup_transaction = self.db.begin_transaction(
                write=[
                    self.OLD_KB_COLLECTION,
                    self.OLD_USER_TO_RECORD_EDGES,
                    self.OLD_KB_TO_RECORD_EDGES,
                ]
            )

            try:
                # Delete old KB-to-record edges
                old_kb_edges_count = len(migration_data['old_kb_relationships'])
                if old_kb_edges_count > 0:
                    delete_kb_edges_query = f"""
                    FOR edge IN {self.OLD_KB_TO_RECORD_EDGES}
                        REMOVE edge IN {self.OLD_KB_TO_RECORD_EDGES}
                    """
                    cleanup_transaction.aql.execute(delete_kb_edges_query)
                    self.logger.info(f"🗑️ Deleted {old_kb_edges_count} old KB-to-record edges")

                # Delete old user-to-record permissions that were KB-related
                old_user_perms_count = len(migration_data['old_user_permissions'])
                if old_user_perms_count > 0:
                    # Only delete user permissions that were related to KB access
                    # Be careful not to delete direct connector permissions
                    delete_user_perms_query = f"""
                    FOR edge IN {self.OLD_USER_TO_RECORD_EDGES}
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null AND record.origin == "UPLOAD"
                        REMOVE edge IN {self.OLD_USER_TO_RECORD_EDGES}
                    """
                    cleanup_transaction.aql.execute(delete_user_perms_query)
                    self.logger.info("🗑️ Deleted KB-related user permissions")

                # Delete old knowledge base documents
                old_kbs_count = len(migration_data['old_kbs'])
                if old_kbs_count > 0:
                    delete_kbs_query = f"""
                    FOR kb IN {self.OLD_KB_COLLECTION}
                        REMOVE kb IN {self.OLD_KB_COLLECTION}
                    """
                    cleanup_transaction.aql.execute(delete_kbs_query)
                    self.logger.info(f"🗑️ Deleted {old_kbs_count} old knowledge bases")

                # Commit cleanup transaction
                await asyncio.to_thread(lambda: cleanup_transaction.commit_transaction())
                self.logger.info("✅ Cleanup transaction committed successfully")

            except Exception as cleanup_error:
                self.logger.error(f"❌ Cleanup failed, aborting: {str(cleanup_error)}")
                await asyncio.to_thread(lambda: cleanup_transaction.abort_transaction())
                raise

        except Exception as e:
            self.logger.error(f"❌ Failed to cleanup old collections: {str(e)}")
            # Don't fail the entire migration for cleanup issues
            self.logger.warning("⚠️ Migration succeeded but cleanup failed - manual cleanup may be needed")

    async def _verify_migration(self, migration_results: List[Dict]) -> None:
        """Verify that migration was successful"""
        self.logger.info("🔍 Verifying migration results")

        successful_migrations = [r for r in migration_results if r.get('success')]
        failed_migrations = [r for r in migration_results if not r.get('success')]

        if failed_migrations:
            self.logger.warning(f"⚠️ {len(failed_migrations)} migrations failed:")
            for failure in failed_migrations:
                self.logger.warning(f"   - KB {failure.get('old_kb_id')}: {failure.get('error')}")

        # Verify new KBs exist
        for result in successful_migrations:
            new_kb_id = result['new_kb_id']
            try:
                kb = await self.arango_service.get_document(new_kb_id, self.NEW_KB_COLLECTION)
                if not kb:
                    raise Exception(f"New KB {new_kb_id} not found after migration")
            except Exception as e:
                self.logger.error(f"❌ Verification failed for KB {new_kb_id}: {str(e)}")
                raise

        self.logger.info(f"✅ Migration verification completed: {len(successful_migrations)} KBs verified")

    async def rollback_migration(self, backup_data: Optional[Dict] = None) -> Dict:
        """
        Rollback migration if needed (emergency use only)
        This would restore from backup data if available
        """
        self.logger.warning("🔄 Migration rollback requested")

        if not backup_data:
            return {
                "success": False,
                "message": "No backup data provided for rollback"
            }

        # Implementation would restore old collections from backup
        # This is a complex operation and should be used carefully
        self.logger.warning("⚠️ Rollback functionality not implemented - contact support")

        return {
            "success": False,
            "message": "Rollback not implemented - manual intervention required"
        }


# Integration with connector setup
async def run_kb_migration(container) -> Dict:
    """
    Function to be called from connector setup to run the migration
    """
    try:
        logger = container.logger()
        kb_arango_service = await container.kb_arango_service()

        migration_service = KnowledgeBaseMigrationService(kb_arango_service)
        result = await migration_service.run_migration()

        if result['success']:
            logger.info(f"✅ KB Migration completed successfully: {result['migrated_count']} KBs migrated")
        else:
            logger.error(f"❌ KB Migration failed: {result['message']}")

        return result

    except Exception as e:
        logger.error(f"❌ KB Migration error: {str(e)}")
        return {
            "success": False,
            "message": f"Migration error: {str(e)}",
            "migrated_count": 0
        }
