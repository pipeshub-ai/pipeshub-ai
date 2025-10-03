"""
Knowledge Base Migration Script
Migrates from old knowledgeBase collection to new recordGroups system
"""

import asyncio
import uuid
from logging import Logger
from typing import Dict, List, Optional

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    GraphNames,
    LegacyCollectionNames,
    LegacyGraphNames,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.schema.arango.graph import EDGE_DEFINITIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KnowledgeBaseMigrationService:
    """Service to handle migration from old KB system to new recordGroups system"""

    def __init__(self, arango_service: BaseArangoService, logger: Logger) -> None:
        self.arango_service = arango_service
        self.logger = logger
        self.db = arango_service.db

        # Old collection names (Node.js system)
        self.OLD_KB_COLLECTION = LegacyCollectionNames.KNOWLEDGE_BASE.value
        self.OLD_USER_TO_KB_EDGES = LegacyCollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value  # Direct user→kb permissions
        self.OLD_KB_TO_RECORD_EDGES = LegacyCollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value  # KB→record relationships

        # New collection names (Python system)
        self.NEW_KB_COLLECTION = CollectionNames.RECORD_GROUPS.value
        self.NEW_USER_TO_KB_EDGES = CollectionNames.PERMISSIONS_TO_KB.value
        self.NEW_RECORD_TO_KB_EDGES = CollectionNames.BELONGS_TO.value
        self.NEW_RECORD_RELATION_EDGES = CollectionNames.RECORD_RELATIONS.value

        self.OLD_GRAPH_NAME = LegacyGraphNames.FILE_ACCESS_GRAPH.value
        self.NEW_GRAPH_NAME = GraphNames.KNOWLEDGE_GRAPH.value

    async def run_migration(self) -> Dict:
        """
        Main migration method that orchestrates the entire migration process
        """
        try:
            self.logger.info("🚀 Starting Knowledge Base migration from old to new system")

            # Step 1: Validate migration preconditions
            migration_preconditions = await self._validate_migration_preconditions()
            if not migration_preconditions["success"]:
                return migration_preconditions

            # Check if migration is actually needed
            if not migration_preconditions.get("migration_needed", True):
                self.logger.info("✅ No migration needed - old KB collection not found")
                return {
                    "success": True,
                    "message": "No migration needed - old KB collection not found",
                    "migrated_count": 0,
                    "failed_count": 0,
                    "details": {
                        "successful": [],
                        "failed": []
                    }
                }

            # Step 2: Get all old knowledge bases and their relationships
            migration_data = await self._analyze_old_system()

            # Step 3: Migrate data in transaction
            migration_results = await self._migrate_data(migration_data)

            if not migration_results["success"]:
                return migration_results

            # Step 4: Verify migration
            await self._verify_migration(migration_results["details"])

            self.logger.info("🎉 Knowledge Base migration completed successfully")
            return {
                "success": True,
                "message": "Migration completed successfully",
                "migrated_count": migration_results["migrated_count"],
                "failed_count": migration_results["failed_count"],
                "details": migration_results
            }

        except Exception as e:
            self.logger.error(f"❌ Knowledge Base migration failed: {str(e)}")
            return {
                "success": False,
                "message": f"Migration failed: {str(e)}",
                "migrated_count": 0,
                "failed_count": 0,
                "details": {
                    "successful": [],
                    "failed": []
                }
            }

    async def _validate_migration_preconditions(self) -> Dict:
        """Validate that migration can proceed safely"""
        self.logger.info("🔍 Validating migration preconditions")

        # Check if old collections exist
        collections = self.db.collections()
        collection_names = [col['name'] for col in collections]

        if self.OLD_KB_COLLECTION not in collection_names:
            self.logger.info(f"✅ Old KB collection '{self.OLD_KB_COLLECTION}' not found - no migration needed")
            return {
                "success": True,
                "message": f"Old KB collection '{self.OLD_KB_COLLECTION}' not found - no migration needed",
                "old_kb_collection_exists": False,
                "migration_needed": False,
                "migrated_count": 0,
                "failed_count": 0,
                "details": {
                    "successful": [],
                    "failed": []
                }
            }

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
        return {
            "success": True,
            "message": "Migration preconditions validated",
            "old_kb_collection_exists": True,
            "migration_needed": True,
            "migrated_count": 0,
            "failed_count": 0,
            "details": {
                "successful": [],
                "failed": []
            }
        }

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

            # Get user→KB permissions
            old_user_kb_permissions_query = f"""
            FOR edge IN {self.OLD_USER_TO_KB_EDGES}
                LET user = DOCUMENT(edge._from)
                LET kb = DOCUMENT(edge._to)
                FILTER user != null AND kb != null
                RETURN {{
                    edge: edge,
                    user: user,
                    kb: kb
                }}
            """

            cursor = self.db.aql.execute(old_user_kb_permissions_query)
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

    async def _migrate_data(self, migration_data: Dict) -> Dict:
        """Migrate data from old to new system (same as original migration logic)"""
        if not migration_data["old_kbs"]:
            self.logger.info("✅ No old knowledge bases found - migration not needed")
            return {
                "success": True,
                "message": "No migration needed",
                "migrated_count": 0,
                "failed_count": 0,
                "details": {
                    "successful": [],
                    "failed": []
                }
            }

        # Step 1: Create transaction for migration
        transaction = self.db.begin_transaction(
            write=[
                self.NEW_KB_COLLECTION,
                self.NEW_USER_TO_KB_EDGES,
                self.NEW_RECORD_TO_KB_EDGES,
                self.NEW_RECORD_RELATION_EDGES,
                CollectionNames.USERS.value,
                CollectionNames.RECORDS.value,
            ]
        )

        migration_results = []

        try:
            # Step 2: Migrate each old KB to new system
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

            # Step 3: Commit transaction ONLY if all migrations succeeded
            await asyncio.to_thread(lambda: transaction.commit_transaction())
            self.logger.info("✅ Migration transaction committed successfully")
            return {
                "success": True,
                "message": "Migration completed successfully",
                "migrated_count": len(successful_migrations),
                "failed_count": len(failed_migrations),
                "details": migration_results
            }

        except Exception as e:
            self.logger.error(f"❌ Migration failed, aborting transaction: {str(e)}")
            try:
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                self.logger.info("🔄 Transaction aborted due to exception")
            except Exception as abort_error:
                self.logger.error(f"❌ Failed to abort transaction: {str(abort_error)}")
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
        parent_child_edges = []
        for record in old_records_for_kb:
            edge = {
                "_from": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                "_to": f"{self.NEW_KB_COLLECTION}/{new_kb_id}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
            parent_child_edge = {
                    "_from": f"{self.NEW_KB_COLLECTION}/{new_kb_id}",
                    "_to": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
            record_edges.append(edge)
            parent_child_edges.append(parent_child_edge)

        if record_edges:
            await self.arango_service.batch_create_edges(
                record_edges, self.NEW_RECORD_TO_KB_EDGES, transaction
            )

        if parent_child_edges:
            await self.arango_service.batch_create_edges(
                parent_child_edges,self.NEW_RECORD_RELATION_EDGES,transaction
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

    async def _create_new_edge_definition(self, graph: object, edge_def: Dict) -> None:
        """Create a new edge definition"""
        try:
            edge_collection = edge_def["edge_collection"]
            self.logger.info(f"🆕 Creating new edge definition: {edge_collection}")

            # Check if edge collection exists
            if not self.db.has_collection(edge_collection):
                self.logger.warning(f"⚠️ Edge collection {edge_collection} does not exist, skipping creation")
                return

            graph.create_edge_definition(**edge_def)
            self.logger.info(f"✅ Created new edge definition: {edge_collection}")

        except Exception as e:
            self.logger.error(f"❌ Failed to create edge definition {edge_def['edge_collection']}: {str(e)}")
            # Don't raise here - continue with other edge definitions

    async def _create_complete_new_graph(self) -> None:
        """Create a complete new graph with all required edge definitions"""
        try:
            self.logger.info(f"🆕 Creating complete new graph: {self.NEW_GRAPH_NAME}")

            graph = self.db.create_graph(self.NEW_GRAPH_NAME)
            edge_definitions = EDGE_DEFINITIONS

            created_count = 0
            for edge_def in edge_definitions:
                try:
                    edge_collection = edge_def["edge_collection"]

                    # Check if edge collection exists before creating edge definition
                    if self.db.has_collection(edge_collection):
                        graph.create_edge_definition(**edge_def)
                        created_count += 1
                        self.logger.info(f"✅ Created edge definition for {edge_collection}")
                    else:
                        self.logger.warning(f"⚠️ Skipping edge definition for non-existent collection: {edge_collection}")

                except Exception as e:
                    self.logger.error(f"❌ Failed to create edge definition for {edge_def['edge_collection']}: {str(e)}")
                    # Continue with other edge definitions

            self.logger.info(f"✅ Created new graph with {created_count} edge definitions")

        except Exception as e:
            self.logger.error(f"❌ Failed to create complete new graph: {str(e)}")
            raise

    async def _update_existing_edge_definition(self, graph: object, edge_collection: str, new_edge_def: Dict) -> None:
        """Update an existing edge definition with new vertex collections"""
        try:
            self.logger.info(f"🔄 Updating edge definition: {edge_collection}")

            # Check if the edge collection exists
            if not self.db.has_collection(edge_collection):
                self.logger.warning(f"⚠️ Edge collection {edge_collection} does not exist, skipping update")
                return

            # Get current definition
            existing_definitions = {ed['edge_collection']: ed for ed in graph.edge_definitions()}
            current_def = existing_definitions.get(edge_collection)

            if not current_def:
                self.logger.warning(f"⚠️ Current definition not found for {edge_collection}")
                return

            # Compare vertex collections
            current_from = set(current_def.get('from_vertex_collections', []))
            current_to = set(current_def.get('to_vertex_collections', []))

            new_from = set(new_edge_def['from_vertex_collections'])
            new_to = set(new_edge_def['to_vertex_collections'])

            # Check if update is needed
            if current_from == new_from and current_to == new_to:
                self.logger.debug(f"📝 No update needed for {edge_collection}")
                return

            self.logger.info(f"🔄 Updating vertices for {edge_collection}")
            self.logger.info(f"   From: {current_from} → {new_from}")
            self.logger.info(f"   To: {current_to} → {new_to}")

            # Remove old edge definition and create new one
            graph.delete_edge_definition(edge_collection, purge=False)
            graph.create_edge_definition(**new_edge_def)

            self.logger.info(f"✅ Updated edge definition: {edge_collection}")

        except Exception as e:
            self.logger.error(f"❌ Failed to update edge definition {edge_collection}: {str(e)}")
            raise

    async def _update_graph_vertices(self) -> None:
        """Update graph vertices to match the new system structure"""
        try:
            self.logger.info("🔄 Updating graph vertices to match new structure")

            if not self.db.has_graph(self.NEW_GRAPH_NAME):
                self.logger.warning(f"⚠️ Graph {self.NEW_GRAPH_NAME} not found, creating new graph")
                await self._create_complete_new_graph()
                return

            graph = self.db.graph(self.NEW_GRAPH_NAME)
            existing_definitions = {ed['edge_collection']: ed for ed in graph.edge_definitions()}

            # Define the complete new edge definitions that should exist
            new_edge_definitions = EDGE_DEFINITIONS

            # Process each new edge definition
            for edge_def in new_edge_definitions:
                edge_collection = edge_def["edge_collection"]

                try:
                    if edge_collection in existing_definitions:
                        # Update existing edge definition
                        await self._update_existing_edge_definition(graph, edge_collection, edge_def)
                    else:
                        # Create new edge definition
                        await self._create_new_edge_definition(graph, edge_def)

                except Exception as e:
                    self.logger.error(f"❌ Failed to process edge definition {edge_collection}: {str(e)}")
                    # Continue with other edge definitions

            self.logger.info("✅ Graph vertices updated successfully")

        except Exception as e:
            self.logger.error(f"❌ Failed to update graph vertices: {str(e)}")
            raise

    async def _update_graph_structure(self) -> None:
        """Handle all graph updates - rename graph and remove old edge definitions"""
        try:
            self.logger.info("🔄 Updating graph structure")

            # Step 1: Handle graph renaming
            if self.db.has_graph(self.OLD_GRAPH_NAME):
                self.db.delete_graph(self.OLD_GRAPH_NAME)
                self.logger.info(f"🗑️ Deleted old graph: {self.OLD_GRAPH_NAME}")

            # Step 2: Create the new graph with the correct definitions if it's not already there.
            if not self.db.has_graph(self.NEW_GRAPH_NAME):
                await self._create_complete_new_graph()
            else:
                self.logger.info(f"✅ Graph '{self.NEW_GRAPH_NAME}' already exists, ensuring vertices are updated.")
                await self._update_graph_vertices() # This function correctly updates/adds edge definitions.

            self.logger.info("✅ Graph structure updated successfully")

        except Exception as e:
            self.logger.error(f"❌ Failed to update graph structure: {str(e)}")
            raise

    async def _cleanup_old_collections(self) -> None:
        """Clean up old collections after successful migration"""
        self.logger.info("🧹 Starting cleanup of old collections")

        try:
            # Determine which old collections still exist
            target_collections = [
                self.OLD_KB_TO_RECORD_EDGES,
                self.OLD_USER_TO_KB_EDGES,
                self.OLD_KB_COLLECTION,
            ]

            existing_collections = [
                name for name in target_collections if self.db.has_collection(name)
            ]

            if not existing_collections:
                self.logger.info("⏭️ No old collections found - skipping cleanup")
                return

            # Create cleanup transaction only for existing collections
            cleanup_transaction = self.db.begin_transaction(write=existing_collections)

            try:
                # Delete old data

                for collection_name in existing_collections:
                    delete_query = f"FOR doc IN {collection_name} REMOVE doc IN {collection_name}"
                    cleanup_transaction.aql.execute(delete_query)
                    self.logger.info(f"🗑️ Deleted data from {collection_name}")

                await asyncio.to_thread(lambda: cleanup_transaction.commit_transaction())
                self.logger.info("✅ Old data deleted successfully")

            except Exception as e:
                self.logger.error(f"❌ Failed to cleanup old collections: {str(e)}")
                await asyncio.to_thread(lambda: cleanup_transaction.abort_transaction())
                raise

            # Step 2: Drop the now-empty collections (non-transactional)
            self.logger.info("🗑️ Dropping empty old collections")
            collections_to_drop = existing_collections

            for collection_name in collections_to_drop:
                try:
                    if self.db.has_collection(collection_name):
                        self.db.delete_collection(collection_name)
                        self.logger.info(f"🗑️ Successfully dropped collection: '{collection_name}'")
                    else:
                        self.logger.info(f"⏭️ Collection '{collection_name}' does not exist - skipping")
                except Exception as drop_error:
                    self.logger.warning(f"⚠️ Failed to drop collection '{collection_name}': {str(drop_error)}")
                    # Continue with other collections even if one fails

            self.logger.info("✅ Old collections cleanup and drop completed")

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

        migration_service = KnowledgeBaseMigrationService(kb_arango_service,logger)
        result = await migration_service.run_migration()

        # Step 2: Update graph structure (remove old edges, rename graph)
        await migration_service._update_graph_structure()

        # Step 3: Clean up old collections
        await migration_service._cleanup_old_collections()


        if result['success']:
            if result['migrated_count'] == 0 and "no migration needed" in result['message'].lower():
                logger.info(f"✅ KB Migration: {result['message']}")
            else:
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
