import asyncio
from datetime import datetime

from app.core.celery_app import CeleryApp


class SyncTasks:
    """Class to manage sync-related Celery tasks"""

    def __init__(
        self, logger, celery_app: CeleryApp, drive_sync_service, gmail_sync_service, arango_service
    ) -> None:
        self.logger = logger
        self.celery = celery_app
        self.drive_sync_service = drive_sync_service
        self.gmail_sync_service = gmail_sync_service
        self.arango_service = arango_service
        self.logger.info("🔄 Initializing SyncTasks")

        # Check if celery_app is properly initialized
        if not self.celery:
            self.logger.error("❌ Celery app is None!")
            raise ValueError("Celery app is not initialized")

        # Check if celery has task decorator
        if not hasattr(self.celery, 'task'):
            self.logger.error("❌ Celery app does not have 'task' attribute!")
            self.logger.error(f"Celery app type: {type(self.celery)}")
            self.logger.error(f"Celery app attributes: {dir(self.celery)}")
            raise AttributeError("Celery app does not have 'task' decorator")

        self.setup_tasks()

    def setup_tasks(self) -> None:
        """Setup Celery task decorators"""
        self.logger.info("🔄 Starting task registration")

        # Get the Celery app instance - it might be wrapped
        celery_instance = self.celery

        # If CeleryApp is a wrapper, get the actual Celery instance
        if hasattr(self.celery, 'app'):
            celery_instance = self.celery.app
        elif hasattr(self.celery, 'celery'):
            celery_instance = self.celery.celery

        self.logger.info(f"📌 Using celery instance of type: {type(celery_instance)}")

        # Define the task using the actual Celery instance
        @celery_instance.task(
            name="app.connectors.sources.google.common.sync_tasks.schedule_next_changes_watch",
            autoretry_for=(Exception,),
            retry_backoff=True,
            retry_backoff_max=600,
            retry_jitter=True,
            max_retries=5,
        )
        def schedule_next_changes_watch() -> None:
            """Renew watches for all services"""
            try:
                self.logger.info("🔄 Starting scheduled watch renewal cycle")
                self.logger.info("📅 Current execution time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                # Create event loop for async operations
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    # Create and run the coroutine
                    loop.run_until_complete(self._async_schedule_next_changes_watch())
                finally:
                    loop.close()

                self.logger.info("✅ Watch renewal cycle completed")

            except Exception as e:
                self.logger.error(f"❌ Critical error in watch renewal cycle: {str(e)}")
                self.logger.exception("Detailed error information:")
                # Only retry for specific exceptions that warrant retries
                if isinstance(e, (ConnectionError, TimeoutError)):
                    raise
                return  # Don't retry for other exceptions

        # Store the task as an instance attribute
        self.schedule_next_changes_watch = schedule_next_changes_watch
        self.logger.info("✅ Watch renewal task registered successfully")

    async def _async_schedule_next_changes_watch(self) -> None:
        """Async implementation of watch renewal"""
        try:
            orgs = await self.arango_service.get_orgs()
        except Exception as e:
            self.logger.error(f"Failed to fetch organizations: {str(e)}")
            raise

        for org in orgs:
            org_id = org["_key"]
            try:
                users = await self.arango_service.get_users(org_id, active=True)
            except Exception as e:
                self.logger.error(f"Failed to fetch users for org {org_id}: {str(e)}")
                continue

            for user in users:
                email = user["email"]
                try:
                    await self._renew_user_watches(email)
                except Exception as e:
                    self.logger.error(f"Failed to renew watches for user {email}: {str(e)}")
                    continue

    async def _renew_user_watches(self, email: str) -> None:
        """Handle watch renewal for a single user"""
        self.logger.info(f"🔄 Renewing watch for user: {email}")

        # Renew Drive watches
        try:
            self.logger.info("🔄 Attempting to renew Drive watch")
            drive_channel_data = await self.drive_sync_service.setup_changes_watch()
            if drive_channel_data:
                await self.arango_service.store_page_token(
                    drive_channel_data["channelId"],
                    drive_channel_data["resourceId"],
                    email,
                    drive_channel_data["token"],
                    drive_channel_data["expiration"],
                )
                self.logger.info("✅ Drive watch set up successfully for user: %s", email)
            else:
                self.logger.warning("Changes watch not created for user: %s", email)
        except Exception as e:
            self.logger.error(f"Failed to renew Drive watch for {email}: {str(e)}")

        # Renew Gmail watches
        try:
            self.logger.info("🔄 Attempting to renew Gmail watch")
            gmail_channel_data = await self.gmail_sync_service.setup_changes_watch()
            if gmail_channel_data:
                await self.arango_service.store_channel_history_id(
                    gmail_channel_data["historyId"],
                    gmail_channel_data["expiration"],
                    email,
                )
                self.logger.info("✅ Gmail watch set up successfully for user: %s", email)
            else:
                self.logger.warning("Gmail watch not created for user: %s", email)
        except Exception as e:
            self.logger.error(f"Failed to renew Gmail watch for {email}: {str(e)}")

    async def drive_manual_sync_control(self, action: str, org_id: str) -> dict:
        """
        Manual task to control sync operations
        Args:
            action: 'start', 'pause', 'resume', 'init', 'user', 'resync', 'reindex', 'stop'
            org_id: Organization ID
        """
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Manual sync control - Action: {action} at {current_time}"
            )

            if action == "start":
                self.logger.info("Starting sync")
                success = await self.drive_sync_service.start(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync start operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync start"}

            elif action == "pause":
                self.logger.info("Pausing sync")

                self.drive_sync_service._stop_requested = True
                self.logger.info("🚀 Setting stop requested")

                # Wait a short time to allow graceful stop
                await asyncio.sleep(2)
                self.logger.info("🚀 Waited 2 seconds")
                self.logger.info("🚀 Pausing sync service")

                success = await self.drive_sync_service.pause(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync pause operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync pause"}

            elif action == "resume":
                success = await self.drive_sync_service.resume(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync resume operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync resume"}

            return {"status": "error", "message": f"Invalid action: {action}"}

        except Exception as e:
            self.logger.error(f"OLDDDDDDDDD Error in manual sync control: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def gmail_manual_sync_control(self, action: str, org_id) -> dict:
        """
        Manual task to control sync operations
        Args:
            action: 'start', 'pause', 'resume', 'init', 'user', 'resync', 'reindex', 'stop'
            org_id: Organization ID
        """
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Manual sync control - Action: {action} at {current_time}"
            )

            if action == "start":
                self.logger.info("Starting sync")
                success = await self.gmail_sync_service.start(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync start operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync start"}

            elif action == "pause":
                self.logger.info("Pausing sync")

                self.gmail_sync_service._stop_requested = True
                self.logger.info("🚀 Setting stop requested")

                # Wait a short time to allow graceful stop
                await asyncio.sleep(2)
                self.logger.info("🚀 Waited 2 seconds")
                self.logger.info("🚀 Pausing sync service")

                success = await self.gmail_sync_service.pause(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync pause operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync pause"}

            elif action == "resume":
                success = await self.gmail_sync_service.resume(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync resume operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync resume"}

            return {"status": "error", "message": f"Invalid action: {action}"}

        except Exception as e:
            self.logger.error(f"OLDDDDDDDDD Error in manual sync control: {str(e)}")
            return {"status": "error", "message": str(e)}
