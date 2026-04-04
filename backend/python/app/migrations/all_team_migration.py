from typing import Any, Dict

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class AllTeamMigrationError(Exception):
    """Base exception for All team migration errors."""
    pass


class AllTeamMigrationService:
    """
    Service for ensuring every organization has an "All" team with all active users.
    
    This migration backfills the All team for orgs that may have missed the orgCreated
    event or had failures in the Kafka event processing pipeline.
    """

    MIGRATION_FLAG_KEY = "/migrations/all_team_v1"

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        """
        Initialize the All team migration service.

        Args:
            graph_provider: Graph database provider (DB-agnostic)
            config_service: Service for etcd configuration management
            logger: Logger for tracking migration progress
        """
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

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
                    "orgs_processed": result.get("orgs_processed", 0),
                    "teams_created": result.get("teams_created", 0),
                    "timestamp": get_epoch_timestamp_in_ms()
                }
            )
            self.logger.info("✅ All team migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def migrate_all_orgs(self) -> Dict[str, Any]:
        """
        Execute the All team migration for all organizations.

        This method is idempotent - it will skip execution if the
        completion flag is already set.

        Returns:
            Dict: Result with success status and statistics
        """
        if await self._is_migration_already_done():
            self.logger.info(
                "✅ All team migration already completed - skipping"
            )
            return {
                "success": True,
                "orgs_processed": 0,
                "teams_created": 0,
                "skipped": True,
                "message": "Migration already completed"
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting All Team Migration")
            self.logger.info("=" * 70)

            # Get all active organizations
            orgs = await self.graph_provider.get_all_orgs(active=True)

            if not orgs:
                self.logger.info("✅ No organizations found - marking migration as complete")
                result = {
                    "success": True,
                    "orgs_processed": 0,
                    "teams_created": 0,
                }
                await self._mark_migration_done(result)
                return result

            self.logger.info(f"📋 Found {len(orgs)} organization(s) to process")

            orgs_processed = 0
            teams_created = 0
            failed_orgs = []

            for org in orgs:
                org_id = org.get("_key") or org.get("id")
                if not org_id:
                    continue

                try:
                    self.logger.info(f"🔄 Processing org {org_id}...")
                    
                    await self.graph_provider.ensure_all_team_with_users(org_id)
                    
                    orgs_processed += 1
                    teams_created += 1
                    self.logger.info(f"✅ All team ensured for org {org_id}")

                except Exception as e:
                    self.logger.error(
                        f"❌ Error processing org {org_id}: {e}"
                    )
                    failed_orgs.append({
                        "org_id": org_id,
                        "error": str(e)
                    })
                    continue

            self.logger.info("=" * 70)
            self.logger.info("All Team Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Total organizations found: {len(orgs)}")
            self.logger.info(f"✅ Organizations processed successfully: {orgs_processed}")
            self.logger.info(f"✅ All teams ensured: {teams_created}")

            if failed_orgs:
                self.logger.warning(f"⚠️ Failed organizations: {len(failed_orgs)}")
                for failed in failed_orgs[:10]:
                    self.logger.warning(
                        f"  - {failed['org_id']}: {failed['error']}"
                    )
            else:
                self.logger.info("✅ No failures - all organizations processed successfully")

            self.logger.info("=" * 70)

            result: Dict[str, Any] = {
                "success": len(failed_orgs) == 0,
                "orgs_processed": orgs_processed,
                "teams_created": teams_created,
                "failed_orgs": len(failed_orgs),
                "failed_orgs_details": failed_orgs if failed_orgs else None,
            }

            if failed_orgs:
                self.logger.warning(
                    "⚠️ Not marking All team migration complete: %s org(s) failed; "
                    "will retry on next startup.",
                    len(failed_orgs),
                )
                return result

            await self._mark_migration_done(result)

            return result

        except Exception as e:
            error_msg = f"All team migration failed: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "orgs_processed": 0,
                "teams_created": 0,
                "error": str(e)
            }


async def run_all_team_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> Dict[str, Any]:
    """
    Convenience function to execute the All team migration.

    Args:
        graph_provider: Graph database provider (DB-agnostic)
        config_service: Service for etcd configuration management
        logger: Logger for tracking migration progress

    Returns:
        Dict: Result with success status and statistics

    Example:
        >>> result = await run_all_team_migration(
        ...     graph_provider, config_service, logger
        ... )
    """
    service = AllTeamMigrationService(
        graph_provider,
        config_service,
        logger
    )
    
    return await service.migrate_all_orgs()
