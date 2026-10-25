"""Migration to backfill kind="service" on existing service account user nodes.

PipesHub service accounts created before Issue #4545 do not carry the `kind`
property on their ArangoDB / Neo4j user nodes. This migration identifies all
users whose email domain is `@service.pipeshub.internal` and ensures their
`kind` attribute is set to "service".

Idempotent: checked via ConfigurationService flag.
"""

from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

SERVICE_ACCOUNT_EMAIL_SUFFIX = "@service.pipeshub.internal"
MIGRATION_FLAG_KEY = "/migrations/service_account_kind_backfill"


class ServiceAccountKindMigrationService:
    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger: Any,
    ) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _is_migration_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(
                MIGRATION_FLAG_KEY, use_cache=False
            )
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(
                f"Unable to read service account kind migration flag (assuming not done): {e}"
            )
            return False

    async def migrate(self) -> dict[str, Any]:
        if await self._is_migration_already_done():
            return {"success": True, "users_updated": 0, "skipped": True}

        updated = 0
        failures = 0
        try:
            orgs = await self.graph_provider.get_all_orgs(active=False)
            for org in orgs or []:
                org_id = org.get("_key") or org.get("id")
                if not org_id:
                    continue

                users = await self.graph_provider.get_users(org_id, active=False)
                for user in users or []:
                    email = str(user.get("email") or "").strip().lower()
                    if (
                        email.endswith(SERVICE_ACCOUNT_EMAIL_SUFFIX)
                        and user.get("kind") is None
                    ):
                        user_key = user.get("_key") or user.get("id")
                        if not user_key:
                            continue
                        update_doc = {
                            "id": user_key,
                            "email": user.get("email"),
                            "kind": "service",
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        success = await self.graph_provider.batch_upsert_nodes(
                            [update_doc], CollectionNames.USERS.value
                        )
                        if success:
                            updated += 1
                        else:
                            failures += 1
                            self.logger.warning(
                                f"Failed to update kind for service account user {user_key}"
                            )
        except Exception as e:
            self.logger.error(
                f"❌ Service account kind migration failed: {e}", exc_info=True
            )
            return {"success": False, "users_updated": updated, "error": str(e)}

        if failures > 0:
            self.logger.warning(
                f"⚠️ Service account kind migration had {failures} failure(s); flag left unset for retry"
            )
            return {"success": False, "users_updated": updated, "failures": failures}

        try:
            await self.config_service.set_config(
                MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "users_updated": updated,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
            self.logger.info(
                f"✅ Service account kind migration completed: {updated} user(s) updated"
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Migration finished ({updated} users updated), but flag could not be saved: {e}"
            )

        return {"success": True, "users_updated": updated, "skipped": False}


async def run_service_account_kind_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger: Any,
) -> dict[str, Any]:
    return await ServiceAccountKindMigrationService(
        graph_provider, config_service, logger
    ).migrate()
