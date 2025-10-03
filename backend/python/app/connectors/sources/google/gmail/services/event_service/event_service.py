"""Gmail Event Service for handling Gmail-specific events"""

import logging
from typing import Any, Dict

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.connectors.sources.google.gmail.services.sync_service.sync_tasks import (
    GmailSyncTasks,
)


class GmailEventService(BaseEventService):
    """Gmail specific event service"""

    def __init__(
        self,
        logger: logging.Logger,
        sync_tasks: GmailSyncTasks,
        arango_service: ArangoService,
    ) -> None:
        super().__init__(logger)
        self.sync_tasks = sync_tasks
        self.arango_service = arango_service

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            self.logger.info(f"Handling Gmail connector event: {event_type}")

            if event_type == "gmail.init":
                return await self._handle_gmail_init(payload)
            elif event_type == "gmail.start":
                return await self._handle_gmail_start_sync(payload)
            elif event_type == "gmail.pause":
                return await self._handle_gmail_pause_sync(payload)
            elif event_type == "gmail.resume":
                return await self._handle_gmail_resume_sync(payload)
            elif event_type == "gmail.user":
                return await self._handle_gmail_sync_user(payload)
            elif event_type == "gmail.resync":
                return await self._handle_resync_gmail(payload)
            elif event_type == "gmailUpdatesEnabledEvent":
                return await self._handle_gmail_updates_enabled_event(payload)
            elif event_type == "gmailUpdatesDisabledEvent":
                return await self._handle_gmail_updates_disabled_event(payload)
            # to do  make a reindex failed event for each connector
            elif event_type == "reindexFailed":
                return await self._handle_reindex_failed(payload)
            elif event_type == "gmail.enabled":
                return await  self._handle_gmail_enabled(payload)
            elif event_type == "gmail.disabled":
                return await self._handle_gmail_disabled(payload)
            else:
                self.logger.error(f"Unknown Gmail connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling Gmail connector event {event_type}: {str(e)}")
            return False

    async def _handle_gmail_init(self, payload: Dict[str, Any]) -> bool:
        """Initialize sync service and wait for schedule"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Initializing Gmail sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control("init", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Gmail sync service initialization: %s", str(e))
            return False

    async def _handle_gmail_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting Gmail sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control("start", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Gmail sync service start: %s", str(e))
            return False

    async def _handle_gmail_pause_sync(self, payload: Dict[str, Any]) -> bool:
        """Pause the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Pausing Gmail sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control("pause", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Gmail sync service pause: %s", str(e))
            return False

    async def _handle_gmail_resume_sync(self, payload: Dict[str, Any]) -> bool:
        """Resume the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Resuming Gmail sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control("resume", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Gmail sync service resume: %s", str(e))
            return False

    async def _handle_gmail_sync_user(self, payload: Dict[str, Any]) -> bool:
        """Sync a user's Gmail"""
        try:
            user_email = payload.get("email")
            if not user_email:
                raise ValueError("email is required")

            self.logger.info(f"Syncing Gmail user: {user_email}")
            await self.sync_tasks.gmail_manual_sync_control("user", None, user_email)
            return True
        except Exception as e:
            self.logger.error("Error syncing Gmail user: %s", str(e))
            return False

    async def _handle_resync_gmail(self, payload: Dict[str, Any]) -> bool:
        """Resync a user's Gmail"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            await self.sync_tasks.gmail_manual_sync_control("init", org_id)

            user_id = payload.get("userId")
            if user_id:
                self.logger.info(f"Resyncing Gmail user: {user_id}")

                user = await self.arango_service.get_user_by_user_id(user_id)
                if not user:
                    self.logger.error(f"User not found for user_id: {user_id}")
                    return False
                result = await self.sync_tasks.gmail_manual_sync_control("resync", org_id, user_email=user["email"])
                if not result or result.get("status") != "accepted":
                    self.logger.error(f"Error resyncing Gmail user {user['email']}")
                    return False
                self.logger.info(f"Successfully re-sync Gmail user: {user['email']}")
                return True
            else:
                self.logger.info(f"Resyncing all Gmail users for org: {org_id}")

                users = await self.arango_service.get_users(org_id, active=True)
                for user in users:
                    result = await self.sync_tasks.gmail_manual_sync_control("resync", org_id, user_email=user["email"])
                    if not result or result.get("status") != "accepted":
                        self.logger.error(f"Error resyncing Gmail user {user['email']}")
                        continue
                self.logger.info(f"Successfully re-sync all Gmail users for org: {org_id}")
                return True
        except Exception as e:
            self.logger.error("Error resyncing Gmail user: %s", str(e))
            return False

    async def _handle_gmail_updates_enabled_event(self, payload: Dict[str, Any]) -> bool:
        """Handle Gmail updates enabled event"""
        try:
            self.logger.info(f"Gmail updates enabled event: {payload}")
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            org_apps = await self.arango_service.get_org_apps(org_id)
            if Connectors.GOOGLE_MAIL.value in org_apps:
                await self._handle_resync_gmail(payload)
            else:
                self.logger.info(f"Google Mail app not enabled for org {org_id}. Skipping resync_gmail for gmail_updates_enabled event.")
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail updates enabled event: %s", str(e))
            return False

    async def _handle_gmail_updates_disabled_event(self, payload: Dict[str, Any]) -> bool:
        """Handle Gmail updates disabled event"""
        try:
            self.logger.info(f"Gmail updates disabled event: {payload}")
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")
            users = await self.arango_service.get_users(org_id, active=True)
            for user in users:
                await self.sync_tasks.gmail_manual_sync_control("stop", org_id, user_email=user["email"])
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail updates disabled event: %s", str(e))
            return False

    async def _handle_gmail_enabled(self, payload: Dict[str, Any]) -> bool:
        """Handle Gmail enabled event"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")
            await self.sync_tasks.gmail_manual_sync_control("init", org_id)
            self.logger.info(f"Gmail enabled event: {payload}")
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail enabled event: %s", str(e))
            return False

    async def _handle_gmail_disabled(self, payload: Dict[str, Any]) -> bool:
        """Handle Gmail disabled event"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")
            await self.sync_tasks.gmail_manual_sync_control("stop", org_id)
            self.logger.info(f"Gmail disabled event: {payload}")
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail disabled event: %s", str(e))
            return False

    async def _handle_reindex_failed(self, payload: Dict[str, Any]) -> bool:
        """Reindex failed records for Gmail"""
        try:
            self.logger.info(f"Reindex failed payload for Gmail: {payload}")
            org_id = payload.get("orgId")
            connector = payload.get("connector")
            if not org_id or not connector:
                self.logger.info(f"Org ID: {org_id}, Connector: {connector}")
                raise ValueError("orgId and connector are required")

            if connector == Connectors.GOOGLE_MAIL.value:
                await self.sync_tasks.gmail_manual_sync_control("reindex", org_id)
            else:
                self.logger.warning(f"Connector {connector} is not Gmail, skipping reindex")
                return True

            return True
        except Exception as e:
            self.logger.error("Error re-indexing failed Gmail records: %s", str(e))
            return False
