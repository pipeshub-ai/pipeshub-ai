"""Off-loop refresher for current-state metrics that require scanning the
graph DB.

Graph scans are slow and their result processing is CPU work; doing either on
the request-serving event loop would add latency to every concurrent request.
This refresher runs in a dedicated thread with its own asyncio loop and its own
graph-provider connection, so the request loop is never touched. Aggregation is
pushed into the DB (see ``count_active_connectors_by_type``) so only a small
per-type result set is transferred. Best-effort: a failure only stops the gauge
from updating, it never affects the service.
"""

import asyncio
import threading
from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
from app.telemetry.modules.connector_metrics import set_connector_active


class GraphMetricsRefresher:
    def __init__(
        self,
        logger: Logger,
        config_service: ConfigurationService,
        interval_s: int = 60,
    ) -> None:
        self._logger = logger
        self._config_service = config_service
        self._interval_s = interval_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="graph-metrics-refresh", daemon=True
        )
        self._thread.start()
        self._logger.info("✅ Graph metrics refresher started (own thread)")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._loop())
        except Exception as e:
            self._logger.warning(f"Graph metrics refresher crashed: {e}")
        finally:
            loop.close()

    async def _loop(self) -> None:
        try:
            provider = await GraphDBProviderFactory.create_provider(
                logger=self._logger, config_service=self._config_service
            )
        except Exception as e:
            self._logger.warning(f"Graph metrics refresher could not connect: {e}")
            return

        try:
            while not self._stop.is_set():
                try:
                    counts = await provider.count_active_connectors_by_type(
                        CollectionNames.APPS.value
                    )
                    set_connector_active(counts)
                except Exception as e:
                    self._logger.warning(f"Failed to refresh connector_active gauge: {e}")
                await self._sleep_or_stop()
        finally:
            try:
                await provider.disconnect()
            except Exception:
                pass

    async def _sleep_or_stop(self) -> None:
        # Wake promptly on shutdown instead of sleeping the full interval.
        for _ in range(self._interval_s):
            if self._stop.is_set():
                return
            await asyncio.sleep(1)
