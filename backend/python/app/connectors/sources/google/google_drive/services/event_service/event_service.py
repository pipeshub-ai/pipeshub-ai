"""Google Drive Event Service for handling Drive-specific events"""

import logging
from typing import Any, Dict

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.connectors.sources.google.google_drive.services.sync_service.sync_tasks import (
    DriveSyncTasks,
)


class GoogleDriveEventService(BaseEventService):
    """Google Drive specific event service"""

    def __init__(
        self,
        logger: logging.Logger,
        sync_tasks: DriveSyncTasks,
        arango_service: ArangoService,
    ) -> None:
        super().__init__(logger)
        self.sync_tasks = sync_tasks
        self.arango_service = arango_service

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            self.logger.info(f"Handling Google Drive connector event: {event_type}")

            if event_type == "drive.init":
                return await self._handle_drive_init(payload)
            elif event_type == "drive.start":
                return await self._handle_drive_start_sync(payload)
            elif event_type == "drive.pause":
                return await self._handle_drive_pause_sync(payload)
            elif event_type == "drive.resume":
                return await self._handle_drive_resume_sync(payload)
            elif event_type == "drive.user":
                return await self._handle_drive_sync_user(payload)
            elif event_type == "drive.resync":
                return await self._handle_resync_drive(payload)
            elif event_type == "connectorPublicUrlChanged":
                return await self._handle_connector_public_url_changed(payload)
            elif event_type == "reindexFailed":
                return await self._handle_reindex_failed(payload)
            elif event_type == "drive.enabled":
                return await  self._handle_drive_enabled(payload)
            elif event_type == "drive.disabled":
                return await self._handle_drive_disabled(payload)
            else:
                self.logger.error(f"Unknown Google Drive connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling Google Drive connector event {event_type}: {str(e)}")
            return False

    async def _handle_drive_init(self, payload: Dict[str, Any]) -> bool:
        """Initialize sync service and wait for schedule"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Initializing Google Drive init sync service for org_id: {org_id}")
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            await self.sync_tasks.drive_manual_sync_control("init",org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Google Drive sync service initialization: %s", str(e))
            return False

    async def _handle_drive_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting Google Drive sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control("start", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Google Drive sync service start: %s", str(e))
            return False

    async def _handle_drive_pause_sync(self, payload: Dict[str, Any]) -> bool:
        """Pause the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Pausing Google Drive sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control("pause", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Google Drive sync service pause: %s", str(e))
            return False

    async def _handle_drive_resume_sync(self, payload: Dict[str, Any]) -> bool:
        """Resume the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Resuming Google Drive sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control("resume", org_id)
            return True
        except Exception as e:
            self.logger.error("Failed to queue Google Drive sync service resume: %s", str(e))
            return False

    async def _handle_drive_sync_user(self, payload: Dict[str, Any]) -> bool:
        """Sync a user's Google Drive"""
        try:
            user_email = payload.get("email")
            if not user_email:
                raise ValueError("email is required")

            self.logger.info(f"Syncing Google Drive user: {user_email}")
            await self.sync_tasks.drive_manual_sync_control("user", None, user_email)
            return True
        except Exception as e:
            self.logger.error("Error syncing Google Drive user: %s", str(e))
            return False

    async def _handle_resync_drive(self, payload: Dict[str, Any]) -> bool:
        """Resync a user's Google Drive"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            await self.sync_tasks.drive_manual_sync_control("init", org_id)

            user_id = payload.get("userId")
            if user_id:
                self.logger.info(f"Resyncing Google Drive user: {user_id}")

                user = await self.arango_service.get_user_by_user_id(user_id)
                if not user:
                    self.logger.error(f"User not found for user_id: {user_id}")
                    return False

                result = await self.sync_tasks.drive_manual_sync_control("resync", org_id, user_email=user["email"])
                if not result or result.get("status") != "accepted":
                    self.logger.error(f"Error resyncing Google Drive user {user['email']}")
                    return False
                self.logger.info(f"Successfully re-sync Google Drive user: {user['email']}")
                return True
            else:
                self.logger.info(f"Resyncing all Google Drive users for org: {org_id}")

                users = await self.arango_service.get_users(org_id, active=True)
                for user in users:
                    result = await self.sync_tasks.drive_manual_sync_control("resync", org_id, user_email=user["email"])
                    if not result or result.get("status") != "accepted":
                        self.logger.error(f"Error re-syncing Google Drive user {user['email']}")
                        continue
                self.logger.info(f"Successfully re-sync all Google Drive users for org: {org_id}")
                return True
        except Exception as e:
            self.logger.error("Error resyncing Google Drive user: %s", str(e))
            return False

    async def _handle_connector_public_url_changed(self, payload: Dict[str, Any]) -> bool:
        """Handle connector public URL changed event for Google Drive"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            org_apps = await self.arango_service.get_org_apps(org_id)
            self.logger.info(f"Org apps: {org_apps}")
            if Connectors.GOOGLE_DRIVE.value in org_apps:
                await self._handle_resync_drive(payload)
            else:
                self.logger.info(f"Google Drive app not enabled for org {org_id}. Skipping resync_drive for connector_public_url_changed event.")
            return True
        except Exception as e:
            self.logger.error(
                "Error handling Google Drive connector public URL changed event: %s", str(e)
            )
            return False

    async def _handle_drive_enabled(self, payload: Dict[str, Any]) -> bool:
        """Handle drive enabled event"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")
            await self.sync_tasks.drive_manual_sync_control("init", org_id)
            return True
        except Exception as e:
            self.logger.error("Error handling Google Drive enabled event: %s", str(e))
            return False

    async def _handle_drive_disabled(self, payload: Dict[str, Any]) -> bool:
        """Handle drive disabled event"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")
            await self.sync_tasks.drive_manual_sync_control("pause", org_id)
            await self.sync_tasks.drive_manual_sync_control("stop", org_id)
            return True
        except Exception as e:
            self.logger.error("Error handling Google Drive disabled event: %s", str(e))
            return False


    async def _handle_reindex_failed(self, payload: Dict[str, Any]) -> bool:
        """Reindex failed records for Google Drive"""
        try:
            self.logger.info(f"Reindex failed payload for Google Drive: {payload}")
            org_id = payload.get("orgId")
            connector = payload.get("connector")
            if not org_id or not connector:
                self.logger.info(f"Org ID: {org_id}, Connector: {connector}")
                raise ValueError("orgId and connector are required")

            if connector == Connectors.GOOGLE_DRIVE.value:
                await self.sync_tasks.drive_manual_sync_control("reindex", org_id)
            else:
                self.logger.warning(f"Connector {connector} is not Google Drive, skipping reindex")
                return True

            return True
        except Exception as e:
            self.logger.error("Error re-indexing failed Google Drive records: %s", str(e))
            return False
