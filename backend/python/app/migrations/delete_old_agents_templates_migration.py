"""
Delete Old Agents and Templates Migration Service

This module provides functionality to permanently delete all old agents and agent templates
from the database, including all related edges, to prevent dangling edges/nodes that could
crash the graph database.

Migration Steps:
1. Query all documents from AGENT_INSTANCES and AGENT_TEMPLATES collections (including soft-deleted)
2. Delete ALL edges connected to agents/templates from ALL edge collections:
   - Check all edge collections for edges where _to starts with agentInstances/ or agentTemplates/
   - Check all edge collections for edges where _from starts with agentInstances/ or agentTemplates/
3. Hard delete all agents and templates using REMOVE in AQL

Note: Flag checking and completion marking is handled by connector.py to avoid redundancy.
"""

import asyncio
from typing import Dict, List

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService


class DeleteOldAgentsTemplatesMigrationService:
    """
    Service for deleting all old agents and agent templates from the database.

    This service handles:
    - Querying all agents and templates (including soft-deleted)
    - Deleting all edges connected to agents/templates from all edge collections
    - Hard deleting all agents and templates

    Note: Flag checking and completion marking is handled by connector.py

    Attributes:
        arango (BaseArangoService): ArangoDB service instance
        logger: Logger instance for migration tracking
    """

    def __init__(
        self,
        arango_service: BaseArangoService,
        logger,
    ) -> None:
        """
        Initialize the delete old agents templates migration service.

        Args:
            arango_service: Service for ArangoDB operations
            logger: Logger for tracking migration progress
        """
        self.arango = arango_service
        self.logger = logger

    async def _get_all_agents(self, transaction=None) -> List[Dict]:
        """Get all agents from AGENT_INSTANCES collection (including soft-deleted)"""
        try:
            self.logger.info("📊 Querying all agents from AGENT_INSTANCES collection...")
            query = f"""
            FOR agent IN {CollectionNames.AGENT_INSTANCES.value}
                RETURN agent
            """
            db = transaction if transaction else self.arango.db
            cursor = db.aql.execute(query)
            agents = list(cursor)
            self.logger.info(f"✅ Found {len(agents)} agents")
            return agents
        except Exception as e:
            self.logger.error(f"❌ Failed to query agents: {str(e)}")
            raise

    async def _get_all_templates(self, transaction=None) -> List[Dict]:
        """Get all templates from AGENT_TEMPLATES collection (including soft-deleted)"""
        try:
            self.logger.info("📊 Querying all templates from AGENT_TEMPLATES collection...")
            query = f"""
            FOR template IN {CollectionNames.AGENT_TEMPLATES.value}
                RETURN template
            """
            db = transaction if transaction else self.arango.db
            cursor = db.aql.execute(query)
            templates = list(cursor)
            self.logger.info(f"✅ Found {len(templates)} templates")
            return templates
        except Exception as e:
            self.logger.error(f"❌ Failed to query templates: {str(e)}")
            raise

    async def _delete_edges_from_collection(self, edge_collection: str, transaction) -> int:
        """
        Delete all edges connected to agents/templates from a specific edge collection.

        Args:
            edge_collection: Name of the edge collection to check
            transaction: Database transaction

        Returns:
            Number of edges deleted
        """
        try:
            self.logger.info(f"🔍 Checking {edge_collection} for edges connected to agents/templates...")
            
            delete_query = f"""
            FOR edge IN {edge_collection}
                FILTER STARTS_WITH(edge._to, '{CollectionNames.AGENT_INSTANCES.value}/')
                   OR STARTS_WITH(edge._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                   OR STARTS_WITH(edge._from, '{CollectionNames.AGENT_INSTANCES.value}/')
                   OR STARTS_WITH(edge._from, '{CollectionNames.AGENT_TEMPLATES.value}/')
                REMOVE edge IN {edge_collection}
                RETURN OLD
            """
            
            cursor = transaction.aql.execute(delete_query)
            deleted_edges = list(cursor)
            count = len(deleted_edges)
            
            if count > 0:
                self.logger.info(f"🗑️ Deleted {count} edges from {edge_collection}")
            else:
                self.logger.debug(f"📝 No edges found in {edge_collection} connected to agents/templates")
            
            return count
        except Exception as e:
            self.logger.error(f"❌ Failed to delete edges from {edge_collection}: {str(e)}")
            raise

    async def _delete_all_edges(self, transaction) -> Dict[str, int]:
        """
        Delete all edges connected to agents/templates from all edge collections.

        Args:
            transaction: Database transaction

        Returns:
            Dictionary mapping edge collection names to deletion counts
        """
        edge_collections = [
            CollectionNames.PERMISSION.value,
            CollectionNames.BELONGS_TO.value,
            CollectionNames.INHERIT_PERMISSIONS.value,
            CollectionNames.ORG_DEPARTMENT_RELATION.value,
            CollectionNames.BELONGS_TO_DEPARTMENT.value,
            CollectionNames.BELONGS_TO_CATEGORY.value,
            CollectionNames.BELONGS_TO_TOPIC.value,
            CollectionNames.BELONGS_TO_LANGUAGE.value,
            CollectionNames.INTER_CATEGORY_RELATIONS.value,
            CollectionNames.IS_OF_TYPE.value,
            CollectionNames.RECORD_RELATIONS.value,
            CollectionNames.USER_DRIVE_RELATION.value,
            CollectionNames.USER_APP_RELATION.value,
            CollectionNames.ORG_APP_RELATION.value,
            CollectionNames.ENTITY_RELATIONS.value,
            CollectionNames.BELONGS_TO_RECORD_GROUP.value,
        ]
        
        edge_deletion_counts = {}
        total_edges_deleted = 0
        
        for edge_collection in edge_collections:
            count = await self._delete_edges_from_collection(edge_collection, transaction)
            edge_deletion_counts[edge_collection] = count
            total_edges_deleted += count
        
        self.logger.info(f"✅ Total edges deleted: {total_edges_deleted}")
        return edge_deletion_counts

    async def _hard_delete_agents(self, transaction) -> int:
        """Hard delete all agents from AGENT_INSTANCES collection"""
        try:
            self.logger.info("🗑️ Hard deleting all agents...")
            delete_query = f"""
            FOR agent IN {CollectionNames.AGENT_INSTANCES.value}
                REMOVE agent IN {CollectionNames.AGENT_INSTANCES.value}
                RETURN OLD
            """
            cursor = transaction.aql.execute(delete_query)
            deleted_agents = list(cursor)
            count = len(deleted_agents)
            self.logger.info(f"✅ Hard deleted {count} agents")
            return count
        except Exception as e:
            self.logger.error(f"❌ Failed to hard delete agents: {str(e)}")
            raise

    async def _hard_delete_templates(self, transaction) -> int:
        """Hard delete all templates from AGENT_TEMPLATES collection"""
        try:
            self.logger.info("🗑️ Hard deleting all templates...")
            delete_query = f"""
            FOR template IN {CollectionNames.AGENT_TEMPLATES.value}
                REMOVE template IN {CollectionNames.AGENT_TEMPLATES.value}
                RETURN OLD
            """
            cursor = transaction.aql.execute(delete_query)
            deleted_templates = list(cursor)
            count = len(deleted_templates)
            self.logger.info(f"✅ Hard deleted {count} templates")
            return count
        except Exception as e:
            self.logger.error(f"❌ Failed to hard delete templates: {str(e)}")
            raise

    async def run_migration(self) -> Dict:
        """
        Run the complete migration process atomically using a transaction.

        All operations are performed within a single transaction. If any operation fails,
        the entire transaction is rolled back and the migration returns success=False.

        Returns:
            Dict with statistics about the migration
        """
        transaction = None
        try:
            self.logger.info("🚀 Starting Delete Old Agents and Templates Migration")

            # Step 1: Get all agents and templates (before transaction to check if migration needed)
            agents = await self._get_all_agents()
            templates = await self._get_all_templates()

            if not agents and not templates:
                self.logger.info("✅ No agents or templates found")
                return {
                    "success": True,
                    "message": "No agents or templates found",
                    "agents_deleted": 0,
                    "templates_deleted": 0,
                    "edges_deleted": {},
                    "total_edges_deleted": 0,
                }

            self.logger.info(f"Found {len(agents)} agents and {len(templates)} templates")

            # Step 2: Start transaction with all collections we'll modify
            self.logger.info("🔄 Starting transaction for atomic deletion...")
            transaction = self.arango.db.begin_transaction(
                write=[
                    # Agent and template collections
                    CollectionNames.AGENT_INSTANCES.value,
                    CollectionNames.AGENT_TEMPLATES.value,
                    # All edge collections that might contain edges to/from agents/templates
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.ORG_DEPARTMENT_RELATION.value,
                    CollectionNames.BELONGS_TO_DEPARTMENT.value,
                    CollectionNames.BELONGS_TO_CATEGORY.value,
                    CollectionNames.BELONGS_TO_TOPIC.value,
                    CollectionNames.BELONGS_TO_LANGUAGE.value,
                    CollectionNames.INTER_CATEGORY_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.USER_DRIVE_RELATION.value,
                    CollectionNames.USER_APP_RELATION.value,
                    CollectionNames.ORG_APP_RELATION.value,
                    CollectionNames.ENTITY_RELATIONS.value,
                    CollectionNames.BELONGS_TO_RECORD_GROUP.value,
                ]
            )

            # Step 3: Delete all edges connected to agents/templates (within transaction)
            self.logger.info("🔍 Deleting all edges connected to agents/templates...")
            edge_deletion_counts = await self._delete_all_edges(transaction)
            total_edges_deleted = sum(edge_deletion_counts.values())

            # Step 4: Hard delete all agents (within transaction)
            agents_deleted = await self._hard_delete_agents(transaction)

            # Step 5: Hard delete all templates (within transaction)
            templates_deleted = await self._hard_delete_templates(transaction)

            # Step 6: Commit transaction - all operations succeeded
            await asyncio.to_thread(lambda: transaction.commit_transaction())
            self.logger.info("✅ Transaction committed successfully")

            result = {
                "success": True,
                "message": "Migration completed successfully",
                "agents_deleted": agents_deleted,
                "templates_deleted": templates_deleted,
                "edges_deleted": edge_deletion_counts,
                "total_edges_deleted": total_edges_deleted,
            }

            self.logger.info(
                f"✅ Migration completed: {agents_deleted} agents, {templates_deleted} templates, "
                f"{total_edges_deleted} edges deleted"
            )

            return result

        except Exception as e:
            # Rollback transaction on any error
            if transaction:
                try:
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.warning("🔄 Transaction rolled back due to error")
                except Exception as rollback_error:
                    self.logger.error(f"❌ Failed to rollback transaction: {str(rollback_error)}")

            self.logger.error(f"❌ Migration failed: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Migration failed: {str(e)}",
                "agents_deleted": 0,
                "templates_deleted": 0,
                "edges_deleted": {},
                "total_edges_deleted": 0,
            }


async def run_delete_old_agents_templates_migration(container) -> Dict:
    """
    Run the delete old agents and templates migration.

    Args:
        container: Application container with required services

    Returns:
        Dict with migration results
    """
    try:
        arango_service = await container.arango_service()
        logger = container.logger()

        migration_service = DeleteOldAgentsTemplatesMigrationService(
            arango_service=arango_service,
            logger=logger,
        )

        return await migration_service.run_migration()
    except Exception as e:
        logger = container.logger()
        logger.error(f"❌ Failed to run delete old agents templates migration: {str(e)}")
        return {
            "success": False,
            "message": f"Failed to run migration: {str(e)}",
            "agents_deleted": 0,
            "templates_deleted": 0,
            "edges_deleted": {},
            "total_edges_deleted": 0,
        }
