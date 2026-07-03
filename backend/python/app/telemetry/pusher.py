"""Background task that pushes this service's metrics to the collector gateway..
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.event_buffer import event_buffer
from app.telemetry.modules.collection_metrics import set_metric_collection_enabled
from app.utils.request_context import (
    HEADER_REQUEST_ID,
    get_context,
    new_system_root,
    reset_context,
    set_context,
)

SCHEMA_VERSION = 1

# Shared config key — mirrors Node's `configPaths.metricsCollection`.
METRICS_CONFIG_KEY = "/services/metricsCollection"

PUSH_TIMEOUT_S = 10
METRICS_VERSION = "2"

REQUIRED_CONFIG_FIELDS = (
    "serverUrl",
    "installId",
    "apiKey",
    "appVersion",
    "pushIntervalMs",
    "enableMetricCollection",
)

# Retry cadence while waiting for Node to publish the config.
CONFIG_POLL_INTERVAL_S = 5

def _as_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


class MetricsPusher:
    """Periodically serializes the registry and POSTs it to the gateway."""

    def __init__(self, config_service, service_name: str, logger) -> None:
        self._config_service = config_service
        self._service_name = service_name
        self._logger = logger
        self._task: Optional[asyncio.Task] = None
        self._waiting_for_config_logged = False

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            self._log_with_context(
                f"📈 Telemetry pusher started for '{self._service_name}'"
            )

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._log_with_context("📉 Telemetry pusher stopped")

    def _log_with_context(self, message: str) -> None:
        if get_context() is not None:
            self._logger.info(message)
            return
        token = set_context(new_system_root())
        try:
            self._logger.info(message)
        finally:
            reset_context(token)

    async def _run(self) -> None:
        while True:
            interval_s = float(CONFIG_POLL_INTERVAL_S)
            token = set_context(new_system_root())
            try:
                cfg = await self._load_config()
                if cfg is None:
                    if not self._waiting_for_config_logged:
                        self._waiting_for_config_logged = True
                        self._logger.info(
                            "Telemetry deferred: waiting for the Node service "
                            "to publish the metrics config"
                        )
                else:
                    self._waiting_for_config_logged = False
                    interval_s = cfg["interval_s"]
                    # Reflect current consent locally each tick (not pushed while off).
                    set_metric_collection_enabled(self._service_name, cfg["enabled"])
                    if cfg["enabled"]:
                        await self._push(cfg)
                        await self._ship_events(cfg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"Failed to push telemetry: {e}")
            finally:
                reset_context(token)
            await asyncio.sleep(interval_s)

    async def _load_config(self) -> Optional[dict]:
        raw = _as_dict(await self._config_service.get_config(METRICS_CONFIG_KEY, default={}))
        if any(raw.get(field) in (None, "") for field in REQUIRED_CONFIG_FIELDS):
            return None
        return {
            "url": raw["serverUrl"],
            "token": raw["apiKey"],
            "interval_s": int(raw["pushIntervalMs"]) / 1000,
            "enabled": _as_bool(raw["enableMetricCollection"]),
            "install_id": raw["installId"],
            # Node owns appVersion; every service reports the same value.
            "version": raw["appVersion"],
        }

    def _has_samples(self, metrics_text: str) -> bool:
        return any(
            line.strip() and not line.strip().startswith("#")
            for line in metrics_text.splitlines()
        )

    async def _push(self, cfg: dict) -> None:
        metrics_text = METRICS_BACKEND.serialize()
        if not self._has_samples(metrics_text):
            return
        payload = {
            "metrics": metrics_text,
            "instanceId": cfg["install_id"],
            "version": cfg["version"],
            "metricsVersion": METRICS_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        headers = self._headers(cfg)
        timeout = aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(cfg["url"], json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    self._logger.warning(
                        f"Metrics push rejected: status={resp.status} body={body[:200]}"
                    )
                else:
                    self._logger.debug(
                        f"Successfully pushed metrics"
                    )

    @staticmethod
    def _headers(cfg: dict) -> dict:
        ctx = get_context()
        headers = {
            HEADER_REQUEST_ID: ctx.root_id if ctx else new_system_root(),
            "X-Metrics-Version": METRICS_VERSION,
        }
        if cfg["token"]:
            headers["Authorization"] = f"Bearer {cfg['token']}"
        return headers

    @staticmethod
    def _collector_url(metrics_url: str, suffix: str) -> str:
        # The events endpoint sits alongside metrics on the collector host.
        if "collect-metrics" in metrics_url:
            return metrics_url.replace("collect-metrics", suffix)
        parts = urlsplit(metrics_url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{suffix}", "", ""))

    async def _ship_events(self, cfg: dict) -> None:
        events = event_buffer.drain()
        if not events:
            return
        payload = {
            "instanceId": cfg["install_id"],
            "service": self._service_name,
            "version": cfg["version"],
            "metricsVersion": METRICS_VERSION,
            "schemaVersion": SCHEMA_VERSION,
            "events": events,
        }
        headers = self._headers(cfg)
        url = self._collector_url(cfg["url"], "collect-events")
        timeout = aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    self._logger.warning(
                        f"Event shipment rejected: status={resp.status} body={body[:200]}"
                    )
