from typing import Any, Dict

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KBAppsMigrationError(Exception):
    """Base exception for KB apps migration errors."""
    pass


class KBAppsMigrationService:
    """
    Migrates legacy Knowledge Base data from the old model (each KB stored as
    a `recordGroups` document under one shared per-org hub app
    `apps/knowledgeBase_{orgId}`) to the new model (each KB is its own `apps`
    document, reusing the same key).

    For every org: fetches its legacy KB recordGroups, migrates each one via
    the DB-agnostic `graph_provider.migrate_legacy_kb_to_app` (which handles
    the Arango-vs-Neo4j implementation differences internally), then — once
    zero legacy KBs remain for that org — deletes the now-unused shared hub
    app.

    Resolvable-but-degenerate legacy data (e.g. an unresolvable `createdBy`,
    or a KB with no OWNER permission edge) is logged and skipped; it does not
    block the completion flag, since it would never self-resolve on retry.
    Genuine failures (DB/transaction errors) do block the flag for that KB's
    org, so the whole thing retries on next startup — same philosophy as
    AllTeamMigrationService.
    """

    MIGRATION_FLAG_KEY = "/migrations/kb_apps_v1"
    # Tracked separately from MIGRATION_FLAG_KEY: an org can finish the KB
    # recordGroup->app migration (and have MIGRATION_FLAG_KEY already set)
    AGENT_KNOWLEDGE_MIGRATION_FLAG_KEY = "/migrations/agent_hub_knowledge_v1"

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _is_migration_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(f"Unable to read migration flag (assuming not done): {e}")
            return False

    async def _is_agent_knowledge_migration_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(self.AGENT_KNOWLEDGE_MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(f"Unable to read agent-knowledge migration flag (assuming not done): {e}")
            return False

    async def _mark_agent_knowledge_migration_done(self, agents_migrated: int) -> None:
        try:
            await self.config_service.set_config(
                self.AGENT_KNOWLEDGE_MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "agents_migrated": agents_migrated,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
            self.logger.info("✅ Agent hub-knowledge migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set agent-knowledge migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _mark_migration_done(self, result: Dict) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "orgs_processed": result.get("orgs_processed", 0),
                    "kbs_migrated": result.get("kbs_migrated", 0),
                    "kbs_skipped_degenerate": result.get("kbs_skipped_degenerate", 0),
                    "hub_apps_deleted": result.get("hub_apps_deleted", 0),
                    "agents_migrated": result.get("agents_migrated", 0),
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
            self.logger.info("✅ KB apps migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _resolve_creator_user_key(self, old_created_by: str | None) -> str | None:
        """Resolve the legacy `createdBy` value to a graph user key.

        The old model has two inconsistent creation paths: one set
        `createdBy` to the graph user key directly, the other set it to the
        external Mongo `userId`. Try both, in the order most likely to hit:
        external userId first (matches the more common `entity.py` path),
        then fall back to treating it as already a graph key.
        """
        if not old_created_by:
            return None
        try:
            user = await self.graph_provider.get_user_by_user_id(old_created_by)
            if user:
                return user.get("_key") or user.get("id")
        except Exception as e:
            self.logger.debug(f"get_user_by_user_id lookup failed for {old_created_by}: {e}")

        try:
            user = await self.graph_provider.get_document(old_created_by, CollectionNames.USERS.value)
            if user:
                return old_created_by
        except Exception as e:
            self.logger.debug(f"get_document(users) lookup failed for {old_created_by}: {e}")

        return None

    async def _migrate_org(self, org_id: str) -> Dict[str, Any]:
        """Migrate every legacy KB for one org, then clean up its hub app if
        fully done. Raises on genuine (non-data-quality) failures so the
        caller's per-org try/except can count this org as failed.
        """
        kbs_migrated = 0
        kbs_skipped_degenerate = 0
        hub_apps_deleted = 0

        legacy_kbs = await self.graph_provider.get_legacy_kb_record_groups(org_id)

        for kb in legacy_kbs:
            kb_key = kb.get("_key") or kb.get("id")
            try:
                resolved_creator_key = await self._resolve_creator_user_key(kb.get("createdBy"))
                if resolved_creator_key is None:
                    self.logger.warning(
                        f"⚠️ Could not resolve creator for legacy KB {kb_key} "
                        f"(createdBy={kb.get('createdBy')!r}); migrating without a userAppRelation edge"
                    )

                result = await self.graph_provider.migrate_legacy_kb_to_app(
                    kb_record_group=kb,
                    org_id=org_id,
                    resolved_creator_key=resolved_creator_key,
                )
                if result.get("success"):
                    kbs_migrated += 1
                    self.logger.info(f"✅ Migrated legacy KB {kb_key} to app")
                else:
                    # migrate_legacy_kb_to_app only returns success=False for
                    # genuine failures (transaction errors) — treat as hard failure.
                    raise KBAppsMigrationError(
                        f"migrate_legacy_kb_to_app failed for {kb_key}: {result.get('reason')}"
                    )
            except KBAppsMigrationError:
                raise
            except Exception as e:
                # Data-quality issues surfaced as unexpected exceptions from
                # provider-side degenerate-data handling are still treated as
                # hard failures here, since migrate_legacy_kb_to_app itself
                # already logs-and-continues for known degenerate cases
                # (missing OWNER edge) rather than raising.
                raise KBAppsMigrationError(f"Unexpected error migrating legacy KB {kb_key}: {e}") from e

        remaining = await self.graph_provider.count_legacy_kb_record_groups(org_id)
        if remaining == 0:
            deleted = await self.graph_provider.delete_kb_hub_app(org_id)
            if deleted:
                hub_apps_deleted += 1
                self.logger.info(f"✅ Deleted legacy KB hub app for org {org_id}")
        elif remaining > 0:
            self.logger.info(
                f"ℹ️ {remaining} legacy KB(s) still remain for org {org_id}; "
                "leaving the shared hub app in place"
            )
        # remaining == -1 (count query itself failed) is already logged by the
        # provider; skip hub-app cleanup for this org this run.

        return {
            "kbs_migrated": kbs_migrated,
            "kbs_skipped_degenerate": kbs_skipped_degenerate,
            "hub_apps_deleted": hub_apps_deleted,
        }

    async def _migrate_agent_hub_knowledge_all_orgs(self, orgs: list, mark_done: bool = True) -> int:
        """Expand any agent knowledge source still pointing at an org's
        shared KB hub app into one source per current per-KB app for that
        org. Tracked by its own completion flag (AGENT_KNOWLEDGE_MIGRATION_FLAG_KEY)
        so it still runs for orgs whose KB recordGroup->app migration
        (MIGRATION_FLAG_KEY) already completed before this step existed.

        `mark_done` must be False when called from a run where the KB
        recordGroup->app migration itself had failures — an org with
        unmigrated KBs has no target per-KB apps yet, so this step is a
        harmless no-op for it *this run*, but marking the flag done here
        would permanently skip it even after that org's KBs do migrate.
        """
        if await self._is_agent_knowledge_migration_already_done():
            return 0

        agents_migrated_total = 0
        any_org_failed = False
        for org in orgs:
            org_id = org.get("_key") or org.get("id")
            if not org_id:
                continue
            try:
                result = await self.graph_provider.migrate_agent_hub_knowledge(org_id)
                migrated = result.get("agents_migrated", 0)
                agents_migrated_total += migrated
                if migrated:
                    self.logger.info(
                        f"✅ Expanded {migrated} agent(s) off the shared KB hub app for org {org_id} "
                        f"({result.get('knowledge_nodes_created', 0)} knowledge source(s) created)"
                    )
            except Exception as e:
                any_org_failed = True
                self.logger.error(f"❌ Agent hub-knowledge expansion failed for org {org_id}: {e}")

        if mark_done and not any_org_failed:
            await self._mark_agent_knowledge_migration_done(agents_migrated_total)
        elif not mark_done:
            self.logger.info(
                "ℹ️ Not marking agent hub-knowledge migration complete: KB recordGroup->app "
                "migration had failures this run; will retry both on next startup."
            )
        return agents_migrated_total

    async def migrate_all_orgs(self) -> Dict[str, Any]:
        """Execute the KB apps migration for all organizations. Idempotent —
        skips if the completion flag is already set, and naturally idempotent
        per-org even without the flag (already-migrated KBs no longer exist
        as recordGroups, so a re-run simply won't see them again).
        """
        kb_migration_already_done = await self._is_migration_already_done()

        if kb_migration_already_done:
            orgs_for_agent_pass = await self.graph_provider.get_all_orgs(active=True)
            agents_migrated_only = await self._migrate_agent_hub_knowledge_all_orgs(orgs_for_agent_pass or [])
            self.logger.info("✅ KB apps migration already completed - skipping")
            return {
                "success": True,
                "orgs_processed": 0,
                "kbs_migrated": 0,
                "agents_migrated": agents_migrated_only,
                "skipped": True,
                "message": "Migration already completed",
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting KB Apps Migration")
            self.logger.info("=" * 70)

            orgs = await self.graph_provider.get_all_orgs(active=True)

            if not orgs:
                self.logger.info("✅ No organizations found - marking migration as complete")
                result = {"success": True, "orgs_processed": 0, "kbs_migrated": 0}
                await self._mark_migration_done(result)
                return result

            self.logger.info(f"📋 Found {len(orgs)} organization(s) to process")

            orgs_processed = 0
            kbs_migrated = 0
            kbs_skipped_degenerate = 0
            hub_apps_deleted = 0
            failed_orgs = []

            for org in orgs:
                org_id = org.get("_key") or org.get("id")
                if not org_id:
                    continue

                try:
                    self.logger.info(f"🔄 Processing org {org_id}...")
                    org_result = await self._migrate_org(org_id)

                    orgs_processed += 1
                    kbs_migrated += org_result["kbs_migrated"]
                    kbs_skipped_degenerate += org_result["kbs_skipped_degenerate"]
                    hub_apps_deleted += org_result["hub_apps_deleted"]
                    self.logger.info(
                        f"✅ Org {org_id} processed: {org_result['kbs_migrated']} KB(s) migrated"
                    )

                except Exception as e:
                    self.logger.error(f"❌ Error processing org {org_id}: {e}")
                    failed_orgs.append({"org_id": org_id, "error": str(e)})
                    continue

            agents_migrated = await self._migrate_agent_hub_knowledge_all_orgs(
                orgs, mark_done=(len(failed_orgs) == 0)
            )

            self.logger.info("=" * 70)
            self.logger.info("KB Apps Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Total organizations found: {len(orgs)}")
            self.logger.info(f"✅ Organizations processed successfully: {orgs_processed}")
            self.logger.info(f"✅ KBs migrated: {kbs_migrated}")
            self.logger.info(f"✅ Legacy hub apps deleted: {hub_apps_deleted}")
            self.logger.info(f"✅ Agents expanded off shared hub app: {agents_migrated}")
            if kbs_skipped_degenerate:
                self.logger.warning(f"⚠️ KBs skipped (degenerate data): {kbs_skipped_degenerate}")

            if failed_orgs:
                self.logger.warning(f"⚠️ Failed organizations: {len(failed_orgs)}")
                for failed in failed_orgs[:10]:
                    self.logger.warning(f"  - {failed['org_id']}: {failed['error']}")
            else:
                self.logger.info("✅ No failures - all organizations processed successfully")

            self.logger.info("=" * 70)

            result: Dict[str, Any] = {
                "success": len(failed_orgs) == 0,
                "orgs_processed": orgs_processed,
                "kbs_migrated": kbs_migrated,
                "kbs_skipped_degenerate": kbs_skipped_degenerate,
                "hub_apps_deleted": hub_apps_deleted,
                "agents_migrated": agents_migrated,
                "failed_orgs": len(failed_orgs),
                "failed_orgs_details": failed_orgs if failed_orgs else None,
            }

            if failed_orgs:
                self.logger.warning(
                    "⚠️ Not marking KB apps migration complete: %s org(s) failed; will retry on next startup.",
                    len(failed_orgs),
                )
                return result

            await self._mark_migration_done(result)
            return result

        except Exception as e:
            error_msg = f"KB apps migration failed: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "orgs_processed": 0,
                "kbs_migrated": 0,
                "error": str(e),
            }


async def run_kb_apps_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> Dict[str, Any]:
    """Convenience function to execute the KB apps migration.

    Example:
        >>> result = await run_kb_apps_migration(graph_provider, config_service, logger)
    """
    service = KBAppsMigrationService(graph_provider, config_service, logger)
    return await service.migrate_all_orgs()
