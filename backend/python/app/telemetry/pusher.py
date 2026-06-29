"""Background task that pushes this service's metrics to the collector gateway.

Reads its configuration from the SAME etcd/Redis key the Node service uses
(``/services/metricsCollection``), so every service in an install shares one
``serverUrl``, ``apiKey`` (ingest token), push interval, enable flag, and — most
importantly — one stable ``installId``. Metrics are pushed *cumulatively* and are
never reset (the TSDB computes increase()/rate()).
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.event_buffer import event_buffer
from app.telemetry.modules.collection_metrics import set_metric_collection_enabled
from app.utils.request_context import HEADER_REQUEST_ID, new_system_root

SCHEMA_VERSION = 1

# Shared config key — mirrors Node's `configPaths.metricsCollection`.
METRICS_CONFIG_KEY = "/services/metricsCollection"

# Mirrors Node's `METRIC_HOST` default so services agree when `serverUrl` is unset.
DEFAULT_SERVER_URL = "http://localhost:3031/collect-metrics/"
DEFAULT_PUSH_INTERVAL_MS = 60000
DEFAULT_VERSION = "1.0.0"
PUSH_TIMEOUT_S = 10
METRICS_VERSION = "2"


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

    def __init__(self, config_service, service_name: str, logger, version: str = DEFAULT_VERSION) -> None:
        self._config_service = config_service
        self._service_name = service_name
        self._logger = logger
        self._version = version
        self._task: Optional[asyncio.Task] = None
        self._install_id: Optional[str] = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            self._logger.info(f"📈 Telemetry pusher started for '{self._service_name}'")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._logger.info("📉 Telemetry pusher stopped")

    async def _run(self) -> None:
        while True:
            interval_s = DEFAULT_PUSH_INTERVAL_MS / 1000
            try:
                cfg = await self._load_config()
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
            await asyncio.sleep(interval_s)

    async def _load_config(self) -> dict:
        raw = _as_dict(await self._config_service.get_config(METRICS_CONFIG_KEY, default={}))
        return {
            "url": raw.get("serverUrl", DEFAULT_SERVER_URL),
            "token": raw.get("apiKey", ""),
            "interval_s": int(raw.get("pushIntervalMs", DEFAULT_PUSH_INTERVAL_MS)) / 1000,
            "enabled": _as_bool(raw.get("enableMetricCollection", True)),
            "install_id": await self._get_or_create_install_id(raw),
        }

    async def _get_or_create_install_id(self, raw: dict) -> str:
        """Stable per-install id, shared across all services via the config store."""
        if self._install_id:
            return self._install_id
        install_id = raw.get("installId")
        if not install_id:
            install_id = str(uuid.uuid4())
            try:
                merged = {**raw, "installId": install_id}
                await self._config_service.set_config(METRICS_CONFIG_KEY, merged)
            except Exception as e:
                # Non-fatal: fall back to the in-memory id for this process.
                self._logger.warning(f"Could not persist installId: {e}")
        self._install_id = install_id
        return install_id

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
            "version": self._version,
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
                        f"Successfully pushed metrics)"
                    )

    @staticmethod
    def _headers(cfg: dict) -> dict:
        # A fresh `sys-` request id per outbound POST lets the collector
        # correlate/dedupe each push.
        headers = {
            HEADER_REQUEST_ID: new_system_root(),
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
            "version": self._version,
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
