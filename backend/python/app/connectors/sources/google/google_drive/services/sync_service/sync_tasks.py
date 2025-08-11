"""Google sync tasks class with dynamic connector registration"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from app.connectors.core.base.sync_service.sync_tasks import BaseSyncTasks
from app.core.celery_app import CeleryApp


class DriveSyncTasks(BaseSyncTasks):
    """Drive-specific sync tasks"""

    def __init__(
        self, logger, celery_app: CeleryApp, arango_service
    ) -> None:
        super().__init__(logger, celery_app, arango_service)

        # Initialize sync services as None - they will be registered later
        self.drive_sync_service = None
        self.logger.info("🔄 Initializing DriveSyncTasks")

    def register_drive_sync_service(self, drive_sync_service) -> None:
        """Register the Drive sync service"""
        self.drive_sync_service = drive_sync_service
        self.register_connector_sync_control("drive", self.drive_manual_sync_control)
        self.logger.info("✅ Drive sync service registered")

    async def drive_manual_sync_control(self, action: str, org_id: Optional[str] = None, user_email: Optional[str] = None) -> Dict[str, Any]:
        """
        Manual task to control Drive sync operations
        Args:
            action: 'start', 'pause', 'resume', 'init', 'user', 'resync', 'reindex', 'stop'
            org_id: Organization ID
            user_email: User email
        """
        if not self.drive_sync_service:
            return {"status": "error", "message": "Drive sync service not registered"}

        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                f"Manual sync control - Action: {action} at {current_time}"
            )

            if action == "reindex":
                self.logger.info("Re-indexing failed records")
                success = await self.drive_sync_service.reindex_failed_records(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Re-indexing failed records operation queued",
                    }
                return {"status": "error", "message": "Failed to queue re-indexing"}

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

            elif action == "init":
                self.logger.info("Initializing sync")
                success = await self.drive_sync_service.initialize(org_id)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync initialization operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync initialization"}

            elif action == "resync":
                self.logger.info(f"Resyncing sync for user: {user_email}")

                if user_email:
                    # Resync specific user
                    self.logger.info(f"Resyncing specific user: {user_email}")
                    user = await self.arango_service.get_entity_id_by_email(user_email)
                    self.logger.info(f"User: {user}")
                    if not user:
                        self.logger.error(f"User not found: {user_email}")
                        return {"status": "error", "message": f"User not found: {user_email}"}

                    user_doc = await self.arango_service.get_document(user, "users")
                    self.logger.info(f"User document: {user_doc}")
                    if not user_doc:
                        self.logger.error(f"User document not found: {user_email}")
                        return {"status": "error", "message": f"User document not found: {user_email}"}

                    if not await self.drive_sync_service.resync_drive(org_id, user_doc):
                        self.logger.error(f"Error resyncing Google Drive user {user_email}")
                        return {"status": "error", "message": f"Failed to resync user {user_email}"}

                    self.logger.info(f"Successfully resynced user: {user_email}")
                else:
                    # Resync all users in the organization
                    self.logger.info("Resyncing all users in organization")
                    users = await self.arango_service.get_users(org_id, active=True)
                    resync_success = True
                    for user in users:
                        if not await self.drive_sync_service.resync_drive(org_id, user):
                            self.logger.error(f"Error resyncing Google Drive user {user['email']}")
                            resync_success = False
                            continue

                    if not resync_success:
                        self.logger.error("Failed to resync some users")
                        return {"status": "error", "message": "Failed to resync some users"}

                return {
                    "status": "accepted",
                    "message": "Sync resync operation queued",
                }
            elif action == "user":
                self.logger.info("Syncing user")
                success = await self.drive_sync_service.sync_specific_user(org_id, user_email)
                if success:
                    return {
                        "status": "accepted",
                        "message": "Sync user operation queued",
                    }
                return {"status": "error", "message": "Failed to queue sync user"}

            return {"status": "error", "message": f"Invalid action: {action}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _renew_user_watches(self, email: str) -> None:
        """Handle watch renewal for a single user"""
        self.logger.info(f"🔄 Renewing watch for user: {email}")

        # Renew Drive watches
        if self.drive_sync_service:
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
