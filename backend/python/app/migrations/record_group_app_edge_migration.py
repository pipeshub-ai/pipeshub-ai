"""
Record Group to App Edge Migration

Backfills BELONGS_TO edges from record groups to their connector app when missing.
Record groups have connectorId pointing to the app; this migration ensures the
graph has the corresponding RECORD_GROUPS -> APPS edge in the BELONGS_TO collection.

Migration is idempotent: only creates edges that do not already exist.
Skips record groups whose app document does not exist.
"""

from typing import Dict, List

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RecordGroupAppEdgeMigrationService:
    """
    Service to backfill BELONGS_TO edges from record groups to connector apps.

    Finds all record groups that have connectorId set but no BELONGS_TO edge
    to APPS/{connectorId}, and creates the missing edges.
    """

    # Batch size for creating edges
    BATCH_SIZE = 500

    MIGRATION_FLAG_KEY = "/migrations/record_group_app_edge_v1"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        self.arango = arango_service
        self.config = config_service
        self.logger = logger

    async def _is_migration_already_done(self) -> bool:
        """Check if migration has already been completed."""
        try:
            flag = await self.config.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(
                f"Unable to read migration flag (assuming not done): {e}"
            )
            return False

    async def _mark_migration_done(self, result: Dict) -> None:
        """Mark the migration as completed in the configuration store."""
        try:
            await self.config.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "edges_created": result.get("edges_created", 0),
                    "record_groups_processed": result.get(
                        "record_groups_processed", 0
                    ),
                },
            )
            self.logger.info("Record group app edge migration flag set")
        except Exception as e:
            self.logger.warning(
                f"Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _find_record_groups_missing_app_edge(self) -> List[Dict]:
        """
        Find record groups that have connectorId but no BELONGS_TO edge to their app.

        Returns list of record group docs with _key, connectorId, connectorName,
        createdAtTimestamp, updatedAtTimestamp. Only includes groups whose app exists.
        """
        query = f"""
            FOR rg IN {CollectionNames.RECORD_GROUPS.value}
                FILTER rg.connectorId != null AND rg.connectorId != ""
                LET app_doc = DOCUMENT("{CollectionNames.APPS.value}", rg.connectorId)
                FILTER app_doc != null
                LET from_id = CONCAT("{CollectionNames.RECORD_GROUPS.value}/", rg._key)
                LET to_id = CONCAT("{CollectionNames.APPS.value}/", rg.connectorId)
                LET edge_exists = (
                    FOR e IN {CollectionNames.BELONGS_TO.value}
                        FILTER e._from == from_id AND e._to == to_id
                        LIMIT 1
                        RETURN 1
                )
                FILTER LENGTH(edge_exists) == 0
                RETURN {{
                    _key: rg._key,
                    connectorId: rg.connectorId,
                    connectorName: rg.connectorName,
                    createdAtTimestamp: rg.createdAtTimestamp,
                    updatedAtTimestamp: rg.updatedAtTimestamp
                }}
        """
        try:
            cursor = self.arango.db.aql.execute(query)
            return list(cursor)
        except Exception as e:
            self.logger.error(
                f"Failed to find record groups missing app edge: {e}"
            )
            raise

    def _build_edges(self, record_groups: List[Dict]) -> List[Dict]:
        """Build BELONGS_TO edge documents for record group -> app."""
        timestamp = get_epoch_timestamp_in_ms()
        edges = []
        for rg in record_groups:
            from_id = f"{CollectionNames.RECORD_GROUPS.value}/{rg['_key']}"
            to_id = f"{CollectionNames.APPS.value}/{rg['connectorId']}"
            entity_type = rg.get("connectorName") or "CONNECTOR"
            created = rg.get("createdAtTimestamp") or timestamp
            updated = rg.get("updatedAtTimestamp") or timestamp
            edges.append({
                "_from": from_id,
                "_to": to_id,
                "entityType": entity_type,
                "createdAtTimestamp": created,
                "updatedAtTimestamp": updated,
            })
        return edges

    async def run_migration(self) -> Dict:
        """
        Run the migration: create missing BELONGS_TO edges from record groups to apps.

        Returns:
            Dict with success, edges_created, record_groups_processed, skipped (if already done).
        """
        try:
            self.logger.info(
                "🚀 Starting Record Group -> App edge migration..."
            )

            if await self._is_migration_already_done():
                self.logger.info(
                    "⏭️ Record group app edge migration already completed, skipping."
                )
                return {
                    "success": True,
                    "skipped": True,
                    "edges_created": 0,
                    "record_groups_processed": 0,
                }

            record_groups = await self._find_record_groups_missing_app_edge()
            if not record_groups:
                self.logger.info(
                    "✅ No record groups missing app edge; nothing to migrate."
                )
                await self._mark_migration_done({
                    "edges_created": 0,
                    "record_groups_processed": 0,
                })
                return {
                    "success": True,
                    "skipped": False,
                    "edges_created": 0,
                    "record_groups_processed": 0,
                }

            self.logger.info(
                f"Found {len(record_groups)} record groups missing app edge"
            )
            edges = self._build_edges(record_groups)

            total_created = 0
            for i in range(0, len(edges), self.BATCH_SIZE):
                batch = edges[i : i + self.BATCH_SIZE]
                ok = await self.arango.batch_create_edges(
                    batch,
                    CollectionNames.BELONGS_TO.value,
                )
                if ok:
                    total_created += len(batch)
                    self.logger.info(
                        f"Created {len(batch)} record group -> app edges "
                        f"({total_created}/{len(edges)} total)"
                    )
                else:
                    self.logger.error(
                        f"Batch create failed at offset {i}; stopping migration"
                    )
                    return {
                        "success": False,
                        "edges_created": total_created,
                        "record_groups_processed": len(record_groups),
                        "message": "Batch create edges failed",
                    }

            await self._mark_migration_done({
                "edges_created": total_created,
                "record_groups_processed": len(record_groups),
            })
            self.logger.info(
                f"✅ Record group app edge migration completed: "
                f"{total_created} edges created for {len(record_groups)} record groups"
            )
            return {
                "success": True,
                "skipped": False,
                "edges_created": total_created,
                "record_groups_processed": len(record_groups),
            }

        except Exception as e:
            self.logger.error(
                f"❌ Record group app edge migration failed: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "edges_created": 0,
                "record_groups_processed": 0,
                "message": str(e),
            }


async def run_record_group_app_edge_migration(
    arango_service: BaseArangoService,
    config_service: ConfigurationService,
    logger,
) -> Dict:
    """
    Run the record group -> app edge backfill migration.

    Call this from container initialization. Idempotent; safe to run multiple times.

    Returns:
        Dict with success, edges_created, record_groups_processed, and optional skipped/message.
    """
    service = RecordGroupAppEdgeMigrationService(
        arango_service=arango_service,
        config_service=config_service,
        logger=logger,
    )
    return await service.run_migration()
