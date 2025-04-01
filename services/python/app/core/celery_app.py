from celery import Celery
from celery.schedules import crontab
from typing import Any
from app.utils.logger import logger
from app.config.configuration_service import ConfigurationService, config_node_constants, CeleryConfig, RedisConfig


class CeleryApp:
    """Celery application manager"""

    def __init__(self, config_service: ConfigurationService):
        self.config_service = config_service
        self.app = Celery('drive_sync')

    async def setup_app(self) -> None:
        """Setup Celery application"""
        await self.configure_app()
        await self.setup_schedules()

    async def configure_app(self) -> None:
        """Configure Celery application"""
        try:
            redis_config = await self.config_service.get_config(config_node_constants.REDIS.value)
            redis_url = f"redis://{redis_config['host']}:{redis_config['port']}/{RedisConfig.REDIS_DB.value}"

            celery_config = {
                'broker_url': redis_url,
                'result_backend': redis_url,
                'task_serializer': CeleryConfig.TASK_SERIALIZER.value,
                'result_serializer': CeleryConfig.RESULT_SERIALIZER.value,
                'accept_content': CeleryConfig.ACCEPT_CONTENT.value,
                'timezone': CeleryConfig.TIMEZONE.value,
                'enable_utc': CeleryConfig.ENABLE_UTC.value
            }

            self.app.conf.update(celery_config)
            logger.info("Celery app configured successfully")
        except Exception as e:
            logger.error(f"Failed to configure Celery app: {str(e)}")
            raise

    async def setup_schedules(self) -> None:
        """Setup periodic task schedules"""
        try:
            sync_config = CeleryConfig.SCHEDULE.value
            start_time = sync_config['syncStartTime']
            pause_time = sync_config['syncPauseTime']
            self.app.conf.update(sync_config)

            self.app.conf.beat_schedule = {
                'start-sync-schedule': {
                    'task': 'src.tasks.sync_tasks.SyncTasks.scheduled_sync_control',
                    'schedule': crontab(
                        hour=start_time.split(':')[0],
                        minute=start_time.split(':')[1]
                    ),
                    'kwargs': {'action': 'start'}
                },
                'pause-sync-schedule': {
                    'task': 'src.tasks.sync_tasks.SyncTasks.scheduled_sync_control',
                    'schedule': crontab(
                        hour=pause_time.split(':')[0],
                        minute=pause_time.split(':')[1]
                    ),
                    'kwargs': {'action': 'pause'}
                },
                'resume-sync-schedule': {
                    'task': 'tasks.sync_tasks.SyncTasks.scheduled_sync_control',
                    'schedule': crontab(
                        hour=start_time.split(':')[0],
                        minute=start_time.split(':')[1]
                    ),
                    'kwargs': {'action': 'resume'}
                },
            }
            logger.info("Celery beat schedules configured successfully")
        except Exception as e:
            logger.error(f"Failed to setup Celery schedules: {str(e)}")
            raise

    def get_app(self) -> Celery:
        """Get the Celery application instance"""
        return self.app

    def task(self, *args: Any, **kwargs: Any) -> Any:
        """Decorator for registering tasks"""
        return self.app.task(*args, **kwargs)
