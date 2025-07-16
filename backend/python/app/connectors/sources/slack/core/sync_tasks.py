import asyncio
from datetime import datetime

from app.config.utils.named_constants.timestamp_constants import SYNC_STUCK_THRESHOLD_MS
from app.core.celery_app import CeleryApp


class SlackSyncTasks:
    """Class to manage sync-related Celery tasks - Slack only version"""

    def __init__(
        self, logger, celery_app: CeleryApp, arango_service, slack_sync_service
    ) -> None:
        self.logger = logger
        self.celery = celery_app
        self.arango_service = arango_service
        self.slack_sync_service = slack_sync_service
        self.logger.info("🔄 Initializing Slack SyncTasks ")
        self.setup_tasks()

    def setup_tasks(self) -> None:
        """Setup Celery task decorators"""
        self.logger.info("🔄 Starting task registration")

        @self.celery.task(
            name="app.connectors.sources.slack.core.sync_tasks.schedule_next_changes_watch",
            autoretry_for=(Exception,),
            retry_backoff=True,
            retry_backoff_max=600,
            retry_jitter=True,
            max_retries=5,
        )
        def schedule_next_changes_watch() -> None:
            """Renew watches for Slack services"""
            try:
                self.logger.info("🔄 Starting scheduled Slack watch renewal cycle")
                self.logger.info("📅 Current execution time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                # Create event loop for async operations
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    loop.run_until_complete(self._async_schedule_next_changes_watch())
                finally:
                    loop.close()

                self.logger.info("✅ Slack watch renewal cycle completed")

            except Exception as e:
                self.logger.error(f"❌ Critical error in Slack watch renewal cycle: {str(e)}")
                self.logger.exception("Detailed error information:")
                # Only retry for specific exceptions that warrant retries
                if isinstance(e, (ConnectionError, TimeoutError)):
                    raise
                return  # Don't retry for other exceptions

        async def _async_schedule_next_changes_watch() -> None:
            """Async implementation of Slack watch renewal"""
            try:
                orgs = await self.arango_service.get_orgs()
            except Exception as e:
                self.logger.error(f"Failed to fetch organizations: {str(e)}")
                raise

            for org in orgs:
                org_id = org["_key"]
                try:
                    # Check if Slack is enabled for this org
                    sync_state = await self.arango_service.get_workspace_sync_state(org_id)
                    if not sync_state:
                        self.logger.info(f"No Slack workspace configured for org {org_id}, skipping")
                        continue

                    await self._renew_slack_watches(org_id)
                except Exception as e:
                    self.logger.error(f"Failed to renew Slack watches for org {org_id}: {str(e)}")
                    continue

        async def _renew_slack_watches(self, org_id: str) -> None:
            """Handle Slack watch renewal for organization"""
            self.logger.info(f"🔄 Renewing Slack watches for org: {org_id}")

            if not self.slack_sync_service:
                self.logger.warning("Slack sync service not available")
                return

            try:
                self.logger.info("🔄 Attempting to renew Slack watches")
                # Note: Slack typically doesn't have watch-based webhooks like Google services
                # This is more for consistency and potential future Slack webhook features
                # For now, this could be used for periodic sync health checks

                # Check if workspace sync is healthy
                sync_state = await self.arango_service.get_workspace_sync_state(org_id)
                if sync_state:
                    current_state = sync_state.get("syncState")
                    if current_state == "FAILED":
                        self.logger.warning(f"Slack sync is in FAILED state for org {org_id}")
                        # Could potentially trigger auto-recovery here
                    elif current_state == "IN_PROGRESS":
                        # Check if sync has been running too long (potential stuck sync)
                        last_update = sync_state.get("lastSyncUpdate", 0)
                        current_time = datetime.now().timestamp() * 1000
                        if current_time - last_update > SYNC_STUCK_THRESHOLD_MS:  # 1 hour in milliseconds
                            self.logger.warning(f"Slack sync appears stuck for org {org_id}, last update: {last_update}")
                            # Could potentially reset sync state here

                self.logger.info("✅ Slack watch renewal completed for org: %s", org_id)

            except Exception as e:
                self.logger.error(f"Failed to renew Slack watches for org {org_id}: {str(e)}")

        self.schedule_next_changes_watch = schedule_next_changes_watch
        self.logger.info("✅ Slack watch renewal task registered successfully")

    async def slack_manual_sync_control(self, action: str, org_id: str) -> dict:
        """
        Manual task to control Slack sync operations
        Args:
            action: 'start', 'pause', or 'resume'
            org_id: Organization ID
        """
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Manual Slack sync control - Action: {action} at {current_time}"
            )

            if not self.slack_sync_service:
                return {"status": "error", "message": "Slack sync service not available"}

            if action == "start":
                self.logger.info("Starting Slack sync")
                success = await self.slack_sync_service.start(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Slack sync start operation queued",
                    }
                return {"status": "error", "message": "Failed to queue Slack sync start"}

            elif action == "pause":
                self.logger.info("Pausing Slack sync")
                self.slack_sync_service._stop_requested = True
                await asyncio.sleep(2)
                success = await self.slack_sync_service.pause(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Slack sync pause operation queued",
                    }
                return {"status": "error", "message": "Failed to queue Slack sync pause"}

            elif action == "resume":
                self.logger.info("Resuming Slack sync")
                success = await self.slack_sync_service.resume(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Slack sync resume operation queued",
                    }
                return {"status": "error", "message": "Failed to queue Slack sync resume"}

            return {"status": "error", "message": f"Invalid action: {action}"}

        except Exception as e:
            self.logger.error(f"Error in manual Slack sync control: {str(e)}")
            return {"status": "error", "message": str(e)}
