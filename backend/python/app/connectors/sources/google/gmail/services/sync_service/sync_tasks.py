"""Google sync tasks class with dynamic connector registration"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from app.connectors.core.base.sync_service.sync_tasks import BaseSyncTasks
from app.core.celery_app import CeleryApp


class GmailSyncTasks(BaseSyncTasks):
    """Gmail-specific sync tasks"""

    def __init__(
        self, logger, celery_app: CeleryApp, arango_service
    ) -> None:
        super().__init__(logger, celery_app, arango_service)
        
        # Initialize sync services as None - they will be registered later
        self.gmail_sync_service = None
        self.logger.info("🔄 Initializing GmailSyncTasks")

    def register_gmail_sync_service(self, gmail_sync_service) -> None:
        """Register the Gmail sync service"""
        self.gmail_sync_service = gmail_sync_service
        self.register_connector_sync_control("gmail", self.__gmail_manual_sync_control)
        self.logger.info("✅ Gmail sync service registered")

    async def __gmail_manual_sync_control(self, action: str, org_id: str) -> Dict[str, Any]:
        """
        Manual task to control Gmail sync operations
        Args:
            action: 'start', 'pause', or 'resume'
            org_id: Organization ID
        """
        if not self.gmail_sync_service:
            return {"status": "error", "message": "Gmail sync service not registered"}

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

    async def _renew_user_watches(self, email: str) -> None:
        """Handle watch renewal for a single user"""
        self.logger.info(f"🔄 Renewing watch for user: {email}")
        # Renew Gmail watches
        if self.gmail_sync_service:
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
