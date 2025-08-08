from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.services.scheduler.interface.scheduler import Scheduler
from app.services.scheduler.redis_scheduler.redis_scheduler import RedisScheduler


class SchedulerFactory:
    """Factory for creating scheduler instances"""

    @staticmethod
    def create_redis_scheduler(redis_url: str, logger: Logger, config_service: ConfigurationService, delay_hours: int = 1) -> Scheduler:
        """
        Create a Redis-based scheduler instance
        Args:
            redis_url: Redis connection URL
            logger: Logger instance
            delay_hours: Delay in hours for scheduled events
        Returns:
            Scheduler instance
        """
        return RedisScheduler(redis_url=redis_url, logger=logger, config_service=config_service, delay_hours=delay_hours)

    @staticmethod
    def scheduler(scheduler_type: str, url: str, logger: Logger, config_service: ConfigurationService, delay_hours: int = 1) -> Scheduler:
        """
        Create a scheduler instance based on the type
        """
        if scheduler_type == "redis":
            return SchedulerFactory.create_redis_scheduler(url, logger=logger, config_service=config_service, delay_hours=delay_hours)
        else:
            raise ValueError(f"Invalid scheduler type: {scheduler_type}")
