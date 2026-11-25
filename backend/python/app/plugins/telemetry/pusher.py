"""Background task that pushes this service's usage data to the collector.

Two contracts, chosen each tick by :class:`~app.plugins.telemetry.v3.sender.UsageV3Sender`
from ``USAGE_TELEMETRY_CONTRACT`` (``legacy`` by default; ``auto`` or ``v3`` opt in to v3):

* legacy: the whole registry as Prometheus text to ``.../collect-metrics`` and
  the event buffer to ``.../collect-events``, as always.
* v3: catalogued counters (as deltas) and gauges to ``.../v3/metrics`` and
  catalogued events to ``.../v3/events``, with the install's credential.

``app.plugins.telemetry.v3`` describes the setting and lists what the legacy
contract carries that v3 does not. Whether anything is sent at all is decided,
as before, by ``TELEMETRY_COLLECTION_FLAG`` and the install's
``enableMetricCollection``.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from app.core.org_context import spawn_detached
from app.plugins.telemetry.backend import METRICS_BACKEND
from app.plugins.telemetry.event_buffer import event_buffer
from app.plugins.telemetry.flag import TELEMETRY_COLLECTION_FLAG, telemetry_enabled
from app.plugins.telemetry.modules.collection_metrics import (
    set_metric_collection_enabled,
)
from app.plugins.telemetry.v3 import INSTALL_SECRET_FIELD
from app.plugins.telemetry.v3.http import AiohttpUsagePort
from app.plugins.telemetry.v3.sender import (
    Route,
    Secret,
    UsageHttpPort,
    UsageV3Sender,
    V3Target,
    contract_mode,
)
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

DEFAULT_PUSH_INTERVAL_MS = 300000
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


def _interval_s(raw_value: object) -> float:
    try:
        ms = float(str(raw_value).strip())
    except (TypeError, ValueError):
        ms = float(DEFAULT_PUSH_INTERVAL_MS)
    if ms <= 0:
        ms = float(DEFAULT_PUSH_INTERVAL_MS)
    return ms / 1000


# Every uvicorn worker runs its own pusher, but `instanceId` is installation-wide and
# `service` is a constant, so without this the collector receives N conflicting
# registries under one identity and cannot tell them apart.
_PROCESS_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


class MetricsPusher:
    """Periodically serializes the registry and POSTs it to the gateway."""

    def __init__(
        self, config_service, service_name: str, logger, usage_http: Optional[UsageHttpPort] = None
    ) -> None:
        self._config_service = config_service
        self._service_name = service_name
        self._logger = logger
        self._usage = UsageV3Sender(
            mode=contract_mode(),
            service=service_name,
            process_id=_PROCESS_ID,
            http=usage_http or AiohttpUsagePort(),
            # Looked up at call time: the backend is a module global that tests replace.
            snapshot=lambda: METRICS_BACKEND.snapshot(),
            events=event_buffer,
            logger=logger,
        )
        self._task: Optional[asyncio.Task] = None
        self._waiting_for_config_logged = False

    async def start(self) -> None:
        if not telemetry_enabled():
            self._log_with_context(
                f"Telemetry disabled ({TELEMETRY_COLLECTION_FLAG} is not 'true'); "
                f"pusher for '{self._service_name}' will not start"
            )
            return
        if self._task is None or self._task.done():
            self._task = spawn_detached(self._run())
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
                        # v3 sends its own batches inside tick(); LEGACY means this
                        # tick is for the legacy endpoints; HOLD means send nothing.
                        if await self._usage.tick(self._v3_target(cfg)) is Route.LEGACY:
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
        secret = raw.get(INSTALL_SECRET_FIELD)
        return {
            "url": raw["serverUrl"],
            "token": raw["apiKey"],
            "interval_s": _interval_s(raw["pushIntervalMs"]),
            "enabled": _as_bool(raw["enableMetricCollection"]),
            "install_id": raw["installId"],
            # Node owns appVersion; every service reports the same value.
            "version": raw["appVersion"],
            # Optional, written by Node once it has registered the install for v3.
            # Wrapped so that a dump of this dict cannot show it.
            "install_secret": Secret(secret) if isinstance(secret, str) and secret else None,
        }

    def _v3_target(self, cfg: dict) -> V3Target:
        return V3Target(
            metrics_url=self._collector_url(cfg["url"], "v3/metrics"),
            events_url=self._collector_url(cfg["url"], "v3/events"),
            install_id=str(cfg["install_id"]),
            secret=cfg.get("install_secret"),
            version=cfg["version"] if isinstance(cfg["version"], str) else None,
        )

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
            "processId": _PROCESS_ID,
            "version": cfg["version"],
            "metricsVersion": METRICS_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        headers = self._headers(cfg)
        timeout = aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(cfg["url"], json=payload, headers=headers) as resp:
                if resp.status == 413:
                    # The registry only grows, so a 413 would otherwise recur until restart.
                    body = await resp.text()
                    self._logger.warning(
                        "Collector rejected metrics payload as too large; "
                        f"resetting metric registry: {body[:200]}"
                    )
                    METRICS_BACKEND.reset()
                elif resp.status >= 400:
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
            "processId": _PROCESS_ID,
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
