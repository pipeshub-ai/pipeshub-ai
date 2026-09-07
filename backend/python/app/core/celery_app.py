import os
import threading
from typing import Any, Dict, Optional

from celery import Celery
from celery.signals import task_postrun, task_prerun

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import (
    CeleryConfig,
)
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.utils.request_context import (
    context_from_envelope,
    new_system_root,
    reset_context,
    set_context,
)

# Create the Celery instance at module level
celery = Celery("drive_sync")

# Per-task trace tokens, keyed by task id (solo pool → one task at a time).
_celery_trace_tokens: Dict[str, Any] = {}


@task_prerun.connect
def _celery_set_trace_context(
    task_id: Optional[str] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> None:
    """Seed a trace id for the task: honor an enqueuer-supplied id, else fresh."""
    kw = kwargs if isinstance(kwargs, dict) else {}
    if kw.get("requestId"):
        ctx = context_from_envelope(kw)
        token = set_context(ctx.root_id)
    else:
        token = set_context(new_system_root())
    if task_id:
        _celery_trace_tokens[task_id] = token


@task_postrun.connect
def _celery_reset_trace_context(task_id: Optional[str] = None, **_: Any) -> None:
    token = _celery_trace_tokens.pop(task_id, None) if task_id else None
    if token is not None:
        reset_context(token)

class CeleryApp:
    """Celery application manager"""

    def __init__(self, logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.app = celery  # Use the module-level celery instance

    async def setup_app(self) -> None:
        """Setup Celery application"""
        await self.configure_app()
        # await self.setup_schedules()

    async def configure_app(self) -> None:
        """Configure Celery application.

        kombu (Celery's Redis transport) has no Redis Cluster support (R7):
        every broker/backend operation would fail with MOVED against
        MemoryDB or a Redis Cluster. ``CELERY_BROKER_URL`` /
        ``CELERY_RESULT_BACKEND`` let an operator point Celery at a
        separate, non-cluster broker; without them, cluster mode fails
        fast here with an actionable message instead of failing on the
        first task.
        """
        try:
            broker_url = os.getenv("CELERY_BROKER_URL")
            result_backend = os.getenv("CELERY_RESULT_BACKEND")

            if not (broker_url and result_backend):
                redis_config = await self.config_service.get_redis_config()
                provider = get_redis_provider(
                    RedisConnectionConfig.from_host_port(
                        host=redis_config.host,
                        port=redis_config.port,
                        password=redis_config.password,
                        db=redis_config.db,
                        tls=redis_config.tls,
                    )
                )
                if provider.is_cluster:
                    raise ValueError(
                        "REDIS_MODE=cluster has no Celery transport (kombu does not "
                        "support Redis Cluster). Set CELERY_BROKER_URL and "
                        "CELERY_RESULT_BACKEND to a separate, non-cluster broker."
                    )
                default_url = provider.connection_url()
                broker_url = broker_url or default_url
                result_backend = result_backend or default_url
                key_namespace = provider.key_namespace
            else:
                # CELERY_BROKER_URL / CELERY_RESULT_BACKEND point at a
                # separate broker outside this provider's config, so its
                # namespace does not apply -- use REDIS_KEY_NAMESPACE
                # directly if the operator still wants one on that broker.
                key_namespace = os.getenv("REDIS_KEY_NAMESPACE", "")

            celery_config = {
                "broker_url": broker_url,
                "result_backend": result_backend,
                "task_serializer": CeleryConfig.TASK_SERIALIZER.value,
                "result_serializer": CeleryConfig.RESULT_SERIALIZER.value,
                "accept_content": CeleryConfig.ACCEPT_CONTENT.value,
                "timezone": CeleryConfig.TIMEZONE.value,
                "enable_utc": CeleryConfig.ENABLE_UTC.value,
            }
            if key_namespace:
                # REDIS_KEY_NAMESPACE (R9): kombu's own `global_keyprefix`
                # transport option -- never applied as an ioredis-style
                # client prefix, which kombu's Redis transport does not
                # support anyway.
                prefix = f"{key_namespace}:"
                celery_config["broker_transport_options"] = {
                    "global_keyprefix": prefix,
                }
                celery_config["result_backend_transport_options"] = {
                    "global_keyprefix": prefix,
                }

            self.app.conf.update(celery_config)
            self.start_worker()
            self.start_beat()
            self.logger.info("✅ Celery app configured successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to configure Celery app: {str(e)}")
            raise

    # async def setup_schedules(self) -> None:
    #     """Setup periodic task schedules"""
    #     try:
    #         self.logger.info("🔄 Initializing Celery beat schedules")

    #         # Calculate interval to be 12 hours before webhook expiration
    #         watch_expiration = timedelta(days=WebhookConfig.EXPIRATION_DAYS.value, hours=WebhookConfig.EXPIRATION_HOURS.value, minutes=WebhookConfig.EXPIRATION_MINUTES.value)
    #         renewal_interval = watch_expiration - timedelta(hours=12)

    #         self.logger.info("⏰ Configuring watch renewal task")
    #         self.logger.info(f"   ├─ Watch expiration: {watch_expiration}")
    #         self.logger.info(f"   ├─ Renewal interval: {renewal_interval}")

    #         # Convert timedelta to seconds for Celery
    #         expiration_seconds = int(watch_expiration.total_seconds())
    #         interval_seconds = int(renewal_interval.total_seconds())

    #         # Add watch renewal task
    #         self.app.conf.beat_schedule = {
    #             "renew-watches": {
    #                 "task": "app.connectors.sources.google.common.sync_tasks.schedule_next_changes_watch",
    #                 "schedule": interval_seconds,
    #                 "options": {
    #                     "expires": expiration_seconds
    #                 }
    #             }
    #         }

    #         self.logger.info("📋 Celery beat configuration:")
    #         self.logger.info("   ├─ Task: app.connectors.sources.google.common.sync_tasks.schedule_next_changes_watch")
    #         self.logger.info(f"   ├─ Interval: {interval_seconds} seconds")
    #         self.logger.info(f"   └─ Expiration: {expiration_seconds} seconds")

    #         self.logger.info("✅ Watch scheduling configured successfully")
    #     except Exception as e:
    #         self.logger.error(f"❌ Failed to setup watch scheduling: {str(e)}")
    #         self.logger.exception("Detailed error information:")
    #         raise

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
