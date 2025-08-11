"""Base sync tasks class for connector-level task management"""

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.core.celery_app import CeleryApp


class BaseSyncTasks:
    """Base class for managing sync-related Celery tasks with dynamic registration"""

    def __init__(self, logger, celery_app: CeleryApp, arango_service) -> None:
        self.logger = logger
        self.arango_service = arango_service
        self.registered_connectors: Dict[str, Dict[str, Callable]] = {}

        self.logger.info("🔄 Initializing BaseSyncTasks")

        #  Initalise celery app
        self.celery = celery_app()

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
            name="app.connectors.core.base.sync_service.sync_tasks.schedule_next_changes_watch",
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

    def register_connector_sync_control(
        self,
        connector_name: str,
        sync_control_method: Callable,
        task_name: Optional[str] = None
    ) -> None:
        """
        Register a connector's manual sync control method
        Args:
            connector_name: Name of the connector (e.g., 'drive', 'gmail')
            sync_control_method: The async method to handle sync control
            task_name: Optional custom task name, defaults to f"{connector_name}_manual_sync_control"
        """
        if not task_name:
            task_name = f"{connector_name}_manual_sync_control"

        self.logger.info(f"🔄 Registering sync control for connector: {connector_name}")

        # Get the Celery app instance
        celery_instance = self.celery
        if hasattr(self.celery, 'app'):
            celery_instance = self.celery.app
        elif hasattr(self.celery, 'celery'):
            celery_instance = self.celery.celery

        # Create the Celery task
        @celery_instance.task(
            name=f"app.connectors.core.base.sync_service.sync_tasks.{task_name}",
            autoretry_for=(Exception,),
            retry_backoff=True,
            retry_backoff_max=600,
            retry_jitter=True,
            max_retries=3,
        )
        def manual_sync_control_task(action: str, org_id: str) -> Dict[str, Any]:
            """Manual task to control sync operations"""
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.logger.info(
                    f"Manual sync control - Connector: {connector_name}, Action: {action} at {current_time}"
                )

                # Create event loop for async operations
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    # Call the registered sync control method
                    result = loop.run_until_complete(sync_control_method(action, org_id))
                    return result
                finally:
                    loop.close()

            except Exception as e:
                self.logger.error(f"Error in manual sync control for {connector_name}: {str(e)}")
                return {"status": "error", "message": str(e)}

        # Store the task and method
        self.registered_connectors[connector_name] = {
            "task": manual_sync_control_task,
            "method": sync_control_method
        }

        self.logger.info(f"✅ Registered sync control task for connector: {connector_name}")

    async def _async_schedule_next_changes_watch(self) -> None:
        """Async implementation of watch renewal - to be overridden by connectors"""
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
        """Handle watch renewal for a single user - to be overridden by connectors"""
        self.logger.info(f"🔄 Renewing watch for user: {email}")
        # This method should be overridden by specific connector implementations
        pass

    def get_registered_connectors(self) -> Dict[str, Dict[str, Callable]]:
        """Get all registered connectors and their sync control methods"""
        return self.registered_connectors.copy()

    def get_connector_task(self, connector_name: str) -> Optional[Callable]:
        """Get the Celery task for a specific connector"""
        connector_info = self.registered_connectors.get(connector_name)
        return connector_info["task"] if connector_info else None
