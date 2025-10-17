import threading
from datetime import timedelta

from celery import Celery

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import (
    CeleryConfig,
    WebhookConfig,
    config_node_constants,
)
from app.utils.redis_util import build_redis_url

# Create the Celery instance at module level
celery = Celery("drive_sync")

class CeleryApp:
    """Celery application manager"""

    def __init__(self, logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.app = celery  # Use the module-level celery instance

    async def setup_app(self) -> None:
        """Setup Celery application"""
        await self.configure_app()
        await self.setup_schedules()

    async def configure_app(self) -> None:
        """Configure Celery application"""
        try:
            redis_config = await self.config_service.get_config(
                config_node_constants.REDIS.value
            )
            if not redis_config or not isinstance(redis_config, dict):
                raise ValueError("Redis configuration not found")
            redis_url = build_redis_url(redis_config)

            celery_config = {
                "broker_url": redis_url,
                "result_backend": redis_url,
                "task_serializer": CeleryConfig.TASK_SERIALIZER.value,
                "result_serializer": CeleryConfig.RESULT_SERIALIZER.value,
                "accept_content": CeleryConfig.ACCEPT_CONTENT.value,
                "timezone": CeleryConfig.TIMEZONE.value,
                "enable_utc": CeleryConfig.ENABLE_UTC.value,
            }

            self.app.conf.update(celery_config)
            self.start_worker()
            self.start_beat()
            self.logger.info("✅ Celery app configured successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to configure Celery app: {str(e)}")
            raise

    async def setup_schedules(self) -> None:
        """Setup periodic task schedules"""
        try:
            self.logger.info("🔄 Initializing Celery beat schedules")

            # Calculate interval to be 12 hours before webhook expiration
            watch_expiration = timedelta(days=WebhookConfig.EXPIRATION_DAYS.value, hours=WebhookConfig.EXPIRATION_HOURS.value, minutes=WebhookConfig.EXPIRATION_MINUTES.value)
            renewal_interval = watch_expiration - timedelta(hours=12)

            self.logger.info("⏰ Configuring watch renewal task")
            self.logger.info(f"   ├─ Watch expiration: {watch_expiration}")
            self.logger.info(f"   ├─ Renewal interval: {renewal_interval}")

            # Convert timedelta to seconds for Celery
            expiration_seconds = int(watch_expiration.total_seconds())
            interval_seconds = int(renewal_interval.total_seconds())

            # Add watch renewal task
            self.app.conf.beat_schedule = {
                "renew-watches": {
                    "task": "app.connectors.sources.google.common.sync_tasks.schedule_next_changes_watch",
                    "schedule": interval_seconds,
                    "options": {
                        "expires": expiration_seconds
                    }
                }
            }

            self.logger.info("📋 Celery beat configuration:")
            self.logger.info("   ├─ Task: app.connectors.sources.google.common.sync_tasks.schedule_next_changes_watch")
            self.logger.info(f"   ├─ Interval: {interval_seconds} seconds")
            self.logger.info(f"   └─ Expiration: {expiration_seconds} seconds")

            self.logger.info("✅ Watch scheduling configured successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to setup watch scheduling: {str(e)}")
            self.logger.exception("Detailed error information:")
            raise

    def get_app(self) -> Celery:
        """Get the Celery application instance"""
        return self.app

    def task(self, *args, **kwargs) -> None:
        """Decorator for registering tasks"""
        self.app.task(*args, **kwargs)

    def start_worker(self) -> None:
        """Start Celery worker in a separate thread"""
        def _worker() -> None:
            self.logger.info("🚀 Starting Celery worker...")
            argv = [
                'worker',
                '--pool=solo',
                '--traceback'
            ]
            self.app.worker_main(argv)

        threading.Thread(target=_worker, daemon=True).start()

    def start_beat(self) -> None:
        """Start Celery beat scheduler in a separate thread"""
        def _beat() -> None:
            self.logger.info("🕒 Starting Celery beat scheduler...")
            # argv = [
            #     'beat',
            #     '--traceback'
            # ]
            self.app.Beat(
                app=self.app,
                loglevel='INFO'
            ).run()

        threading.Thread(target=_beat, daemon=True).start()
