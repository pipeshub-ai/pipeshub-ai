import asyncio
from datetime import datetime, timedelta

from app.config.configuration_service import WebhookConfig
from app.config.utils.named_constants.arangodb_constants import CollectionNames
from app.core.celery_app import CeleryApp


class SyncTasks:
    """Class to manage sync-related Celery tasks"""

    def __init__(
        self, logger, celery_app: CeleryApp, drive_sync_service, gmail_sync_service
    ):
        self.logger = logger
        self.celery = celery_app
        self.drive_sync_service = drive_sync_service
        self.gmail_sync_service = gmail_sync_service
        self.setup_tasks()

    def setup_tasks(self) -> None:
        """Setup Celery task decorators"""

        # Register scheduled sync control task
        @self.celery.app.task(name="tasks.sync_tasks.SyncTasks.scheduled_sync_control")
        def scheduled_sync_control_task(action: str) -> bool:
            return asyncio.run(self.scheduled_sync_control(action))

        # Store task references
        self.scheduled_sync_control_task = scheduled_sync_control_task

        # Manual control doesn't need to be a Celery task
        # It's called directly and is already async
        self.drive_manual_sync_control = self.drive_manual_sync_control
        self.gmail_manual_sync_control = self.gmail_manual_sync_control

        @self.celery.app.task(
            name="tasks.sync_tasks.SyncTasks.schedule_next_changes_watch",
            bind=True,
            max_retries=3,
            default_retry_delay=300,
        )
        def schedule_next_changes_watch_task(task_self, user_email: str) -> bool:
            try:
                return asyncio.run(self.schedule_next_changes_watch(user_email))
            except Exception as exc:
                self.logger.error(f"❌ Failed to schedule changes watch: {exc}")
                task_self.retry(
                    exc=exc,
                    countdown=task_self.default_retry_delay
                    * (2**task_self.request.retries),
                )

        self.schedule_next_changes_watch_task = schedule_next_changes_watch_task

    async def drive_scheduled_sync_control(self, action: str) -> bool:
        """
        Scheduled task to control sync operations
        Args:
            action: 'start', 'pause', 'resume'
        """
        try:
            current_time = datetime.now()
            current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Scheduled sync control - Action: {action} at {current_time_str}"
            )

            # Get time settings from Celery config
            start_time = self.celery.app.conf.get("syncStartTime")
            end_time = self.celery.app.conf.get("syncPauseTime")

            # Convert times to datetime.time objects
            current_time = current_time.time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()

            # Check if current time is within the allowed range
            is_within_time_range = False
            if start <= end:  # Normal time range
                is_within_time_range = start <= current_time <= end
            else:  # Overnight time range
                is_within_time_range = current_time >= start or current_time <= end

            if not is_within_time_range:
                self.logger.info(
                    "Current time %s is outside of sync window (%s-%s)",
                    current_time_str,
                    start_time,
                    end_time,
                )
                return False

            if action == "start":
                self.logger.info("Starting sync")

                success = await self.drive_sync_service.start()
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync start operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync start"}

            elif action == "pause":
                self.logger.info("Pausing sync")

                # Explicitly set stop flag
                self.drive_sync_service._stop_requested = True

                # Wait a short time to allow graceful stop
                await asyncio.sleep(2)

                # Force pause if still running
                await self.drive_sync_service.pause()

                return {"status": "accepted", "message": "Sync pause operation queued"}

            elif action == "resume":

                success = await self.drive_sync_service.resume()
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync resume operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync resume"}

            return {"status": "error", "message": f"Invalid action: {action}"}

        except Exception as e:
            self.logger.error(f"Error in scheduled sync control: {str(e)}")
            return False

    async def drive_manual_sync_control(self, action: str, org_id: str) -> bool:
        """
        Manual task to control sync operations
        Args:
            action: 'start', 'pause', or 'resume'
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
            self.logger.error(f"Error in manual sync control: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def drive_schedule_next_changes_watch(self, user_email: str) -> bool:
        """Schedule next changes watch creation"""
        try:
            self.logger.info(f"🔄 Scheduling next changes watch for user {user_email}")

            # Verify user_email is valid
            if not user_email:
                self.logger.error("❌ Invalid user email provided")
                return False

            # Verify drive service is initialized
            if not self.drive_sync_service.drive_service:
                self.logger.error("❌ Drive service not initialized")
                return False

            # Impersonate user before creating new watch
            if not await self.drive_sync_service.drive_service.impersonate_user(
                user_email
            ):
                self.logger.error(f"❌ Failed to impersonate user {user_email}")
                return False

            # Create new changes watch
            result = await self.drive_sync_service.drive_service.create_changes_watch()
            if not result:
                self.logger.error(f"❌ Failed to create changes watch for {user_email}")
                return False

            self.logger.info(
                f"✅ Successfully created new changes watch for {user_email}"
            )

            # Schedule next watch
            next_run_time = datetime.now() + timedelta(days=6, hours=12)
            self.schedule_next_changes_watch_task.apply_async(
                args=[user_email], eta=next_run_time
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Error scheduling next changes watch: {str(e)}")
            raise  # Re-raise to trigger Celery retry

    async def gmail_scheduled_sync_control(self, action: str, org_id: str) -> bool:
        """
        Scheduled task to control sync operations
        Args:
            action: 'start', 'pause', 'resume'
        """
        try:
            current_time = datetime.now()
            current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Scheduled sync control - Action: {action} at {current_time_str}"
            )

            # Get current user state from ArangoDB
            user_info = (
                await self.gmail_sync_service.gmail_user_service.list_individual_user(
                    org_id
                )
            )
            if not user_info:
                self.logger.error("No user found for gmail sync")
                return False

            user_email = user_info[0]["email"]
            user_id = (
                await self.gmail_sync_service.arango_service.get_entity_id_by_email(
                    user_email
                )
            )
            current_user = await self.gmail_sync_service.arango_service.get_document(
                user_id, CollectionNames.USERS.value
            )
            current_state = current_user.get("sync_state") if current_user else None

            # Get time settings from Celery config
            start_time = self.celery.app.conf.get("SYNC_START_TIME")
            end_time = self.celery.app.conf.get("SYNC_PAUSE_TIME")

            # Convert times to datetime.time objects
            current_time = current_time.time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()

            # Check if current time is within the allowed range
            is_within_time_range = False
            if start <= end:  # Normal time range
                is_within_time_range = start <= current_time <= end
            else:  # Overnight time range
                is_within_time_range = current_time >= start or current_time <= end

            if not is_within_time_range:
                self.logger.info(
                    "Current time %s is outside of sync window (%s-%s)",
                    current_time_str,
                    start_time,
                    end_time,
                )
                return False

            if action == "start":
                self.logger.info("Starting sync")
                if current_state == "IN_PROGRESS":
                    self.logger.info("Sync is already running")
                    return {"status": "skipped", "message": "Sync is already running"}

                success = await self.gmail_sync_service.start()
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync start operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync start"}

            elif action == "pause":
                self.logger.info("Pausing sync")
                if current_state != "IN_PROGRESS":
                    return {"status": "skipped", "message": "Sync is not running"}

                # Explicitly set stop flag
                self.gmail_sync_service._stop_requested = True

                # Wait a short time to allow graceful stop
                await asyncio.sleep(2)

                # Force pause if still running
                if current_state == "IN_PROGRESS":
                    self.logger.warning("Forcing sync pause")
                    await self.gmail_sync_service.pause()

                return {"status": "accepted", "message": "Sync pause operation queued"}

            elif action == "resume":
                if current_state == "IN_PROGRESS":
                    return {"status": "skipped", "message": "Sync is already running"}

                if current_state != "PAUSED":
                    return {
                        "status": "skipped",
                        "message": "Sync was not paused before resuming",
                    }

                success = await self.gmail_sync_service.resume()
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync resume operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync resume"}

            return {"status": "error", "message": f"Invalid action: {action}"}

        except Exception as e:
            self.logger.error(f"Error in scheduled sync control: {str(e)}")
            return False

    async def gmail_manual_sync_control(self, action: str, org_id) -> bool:
        """
        Manual task to control sync operations
        Args:
            action: 'start', 'pause', or 'resume'
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
            self.logger.error(f"Error in manual sync control: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def schedule_next_changes_watch(self, user_email: str) -> bool:
        """Schedule next changes watch for a user"""
        try:
            self.logger.info(f"🔄 Scheduling next changes watch for user {user_email}")

            # Create new watch
            channel_data = await self.drive_sync_service.setup_changes_watch()
            if not channel_data:
                self.logger.warning(f"Changes watch not created for user {user_email}")
                return False

            self.logger.info(
                f"✅ Successfully created new changes watch for {user_email}"
            )

            # Schedule next watch
            next_run_time = datetime.now() + timedelta(
                days=WebhookConfig.EXPIRATION_DAYS.value,
                hours=(WebhookConfig.EXPIRATION_HOURS.value - 12),
                minutes=(WebhookConfig.EXPIRATION_MINUTES.value),
            )
            self.schedule_next_changes_watch_task.apply_async(
                args=[user_email], eta=next_run_time
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Error scheduling next changes watch: {str(e)}")
            raise  # Re-raise to trigger Celery retry
