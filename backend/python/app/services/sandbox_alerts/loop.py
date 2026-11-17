"""The alert loop: one worker per container evaluates every ``SANDBOX_ALERT_EVAL_S``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.services.messaging.interface.producer import IMessagingProducer
from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.cooldown import STATE_CLAIMED, AlertCooldown
from app.services.sandbox_alerts.evaluator import AlertDecision, evaluate
from app.services.sandbox_alerts.event import build_alert_event, build_resolved_event
from app.services.sandbox_alerts.leader import AlertLeader
from app.services.sandbox_alerts.window import read_window

if TYPE_CHECKING:
    from pathlib import Path

    from app.config.configuration_service import ConfigurationService

NOTIFICATION_TOPIC = "notification"
_SUPPRESSED_LOG_EVERY_S = 900.0
_LEVELS = ("critical", "warning")

ProducerFactory = Callable[[], Awaitable[IMessagingProducer]]
SnapshotFn = Callable[[], dict[str, Any]]


def _default_snapshot() -> dict[str, Any]:
    from app.agent_loop_lib.sandbox.governor import get_default_governor

    return get_default_governor().snapshot()


def off_reason(settings: AlertSettings) -> str | None:
    """Why the loop does not run here, or ``None`` when it does."""
    if not settings.enabled:
        return "SANDBOX_ALERTS=false"
    from app.agent_loop_lib.sandbox.coding.settings import resolve_sandbox_mode

    try:
        backend = resolve_sandbox_mode()
    except Exception:
        return "no code sandbox is configured"
    if backend == "e2b":
        return "E2B sandboxes run elsewhere"
    return None


class SandboxAlertRunner:
    """One evaluation per ``run_once``. Holds the leader lock, the producer
    (built on the first send) and how long the alert condition has been clear."""

    def __init__(
        self,
        config_service: ConfigurationService,
        settings: AlertSettings,
        state_directory: Path,
        *,
        producer_factory: ProducerFactory,
        snapshot: SnapshotFn = _default_snapshot,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.time,
        host: str | None = None,
    ) -> None:
        self._settings = settings
        self._directory = state_directory / "load"
        self._leader = AlertLeader(state_directory)
        self._producer_factory = producer_factory
        self._producer: IMessagingProducer | None = None
        self._snapshot = snapshot
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._host = host or socket.gethostname()
        self._cooldown = AlertCooldown(
            config_service, settings.scope, settings.cooldown_s, clock=clock, host=self._host,
        )
        self._suppressed_logged_at: dict[str, float] = {}
        self._clear_since: float | None = None
        # The clear episode has been checked for an alert to resolve.
        self._resolve_checked = False

    @property
    def is_leader(self) -> bool:
        return self._leader.held

    async def run_once(self) -> AlertDecision | None:
        """Evaluate once if this worker is (or becomes) the leader."""
        if not self._leader.try_acquire():
            return None
        now = self._clock()
        totals = await asyncio.to_thread(read_window, self._directory, now, self._settings.window_s)
        if totals.stale:
            await asyncio.to_thread(_delete, totals.stale)
        decision = evaluate(totals, self._settings)
        if decision.any:
            self._clear_since = None
            self._resolve_checked = False
        elif self._clear_since is None:
            self._clear_since = now
        if decision.level is not None:
            await self._fire(decision, now)
        elif (
            self._clear_since is not None
            and not self._resolve_checked
            and now - self._clear_since >= self._settings.window_s
        ):
            await self._resolve(now)
        return decision

    async def _fire(self, decision: AlertDecision, now: float) -> None:
        level = decision.level
        assert level is not None
        alert_id = str(uuid.uuid4())
        claimed_levels: list[str] = []
        try:
            if not await self._cooldown.claim(level, alert_id):
                self._log_suppressed(level, now)
                return
            claimed_levels.append(level)
            if level == "critical":
                # So a weaker email never follows a stronger one.
                try:
                    if await self._cooldown.claim("warning", alert_id):
                        claimed_levels.append("warning")
                except Exception:
                    self._logger.debug("sandbox load alert: warning claim failed", exc_info=True)
        except Exception as exc:
            self._logger.warning("sandbox load alert %s not sent: config store unavailable (%s)", level, exc)
            return
        try:
            event = build_alert_event(
                decision, self._snapshot(), self._settings,
                alert_id=alert_id, host=self._host, fired_at=now,
            )
        except Exception:
            self._logger.warning("sandbox load alert %s: building the alert failed", level, exc_info=True)
            event = None
        if event is not None and await self._publish(event, alert_id):
            totals = decision.totals
            self._logger.warning(
                "sandbox load alert fired level=%s alert_id=%s host=%s runs=%d waited=%d "
                "refused=%d busy_pct=%d reason=%s indexing_slowed=%s",
                level, alert_id, self._host, totals.runs, totals.waited, totals.refused,
                round(decision.busy_percent), event["payload"]["reason"],
                event["payload"]["indexingSlowed"],
            )
            return
        for claimed in claimed_levels:
            try:
                await self._cooldown.release_if_ours(claimed, alert_id)
            except Exception:
                self._logger.warning("sandbox load alert %s: could not release the claim", claimed, exc_info=True)
        self._logger.warning("sandbox load alert %s not sent; retrying next interval", level)

    async def _resolve(self, now: float) -> None:
        try:
            current = {level: await self._cooldown.read(level) for level in _LEVELS}
        except Exception as exc:
            self._logger.debug("sandbox load alerts: resolve check skipped (%s)", exc)
            return
        claimed = {
            level: value for level, value in current.items()
            if value is not None and value.get("state") == STATE_CLAIMED
        }
        self._resolve_checked = True
        if not claimed:
            return
        level = "critical" if "critical" in claimed else "warning"
        fired = claimed[level]
        claimed_at = fired.get("claimedAt")
        fired_at = claimed_at / 1000 if isinstance(claimed_at, (int, float)) else now
        alert_id = str(uuid.uuid4())
        event = build_resolved_event(
            level=level, resolved_alert_id=str(fired.get("alertId", "")),
            alert_fired_at=fired_at, clear_since=self._clear_since or now,
            settings=self._settings, alert_id=alert_id, host=self._host, fired_at=now,
        )
        if not await self._publish(event, alert_id):
            self._resolve_checked = False
            return
        for claimed_level, value in claimed.items():
            try:
                await self._cooldown.mark_resolved(claimed_level, value)
            except Exception:
                self._logger.warning("sandbox load alert %s: could not mark resolved", claimed_level, exc_info=True)
        self._logger.info("sandbox load alert resolved level=%s alert_id=%s host=%s", level, fired.get("alertId"), self._host)

    async def _publish(self, event: dict[str, Any], alert_id: str) -> bool:
        try:
            if self._producer is None:
                self._producer = await self._producer_factory()
            sent = await self._producer.send_message(
                NOTIFICATION_TOPIC, event, key=f"sandbox-capacity-{alert_id}",
            )
        except Exception as exc:
            self._logger.warning("sandbox load alert: publish failed (%s)", exc)
            return False
        return sent is not False

    def _log_suppressed(self, level: str, now: float) -> None:
        last = self._suppressed_logged_at.get(level)
        if last is not None and now - last < _SUPPRESSED_LOG_EVERY_S:
            return
        self._suppressed_logged_at[level] = now
        self._logger.info(
            "sandbox load alert %s suppressed: sent within the last %d h",
            level, max(1, round(self._settings.cooldown_s / 3600)),
        )

    async def close(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            with contextlib.suppress(Exception):
                await producer.cleanup()
        self._leader.release()


def _delete(paths: tuple[Path, ...]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(path)


def default_producer_factory(
    config_service: ConfigurationService, logger: logging.Logger,
) -> ProducerFactory:
    async def build() -> IMessagingProducer:
        from app.services.messaging.messaging_factory import MessagingFactory
        from app.services.messaging.utils import MessagingUtils

        config = await MessagingUtils.create_producer_config_from_service(
            config_service, client_id="sandbox_alerts",
        )
        producer = MessagingFactory.create_producer(logger=logger, config=config)
        await producer.initialize()
        return producer

    return build


async def run_sandbox_alert_loop(
    config_service: ConfigurationService,
    logger: logging.Logger | None = None,
    *,
    settings: AlertSettings | None = None,
    state_directory: Path | None = None,
    producer_factory: ProducerFactory | None = None,
    snapshot: SnapshotFn = _default_snapshot,
) -> None:
    """Runs until cancelled. Exits at once, with one INFO line, where alerts
    are off: ``SANDBOX_ALERTS=false``, no code sandbox, E2B, or no state
    directory."""
    log = logger or logging.getLogger(__name__)
    settings = settings or AlertSettings.from_env()
    reason = off_reason(settings)
    if reason is None and state_directory is None:
        from app.services.resource_governor.shared_slots import sandbox_state_directory

        state_directory = sandbox_state_directory()
        if state_directory is None:
            reason = "no POSIX file locks"
    if reason is not None or state_directory is None:
        log.info("sandbox load alerts off: %s", reason)
        return
    runner = SandboxAlertRunner(
        config_service, settings, state_directory,
        producer_factory=producer_factory or default_producer_factory(config_service, log),
        snapshot=snapshot, logger=log,
    )
    unusable_logged = False
    try:
        while True:
            try:
                await runner.run_once()
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                if not unusable_logged:
                    unusable_logged = True
                    log.warning("sandbox load alerts: state directory unusable (%s)", exc)
            except Exception:
                log.exception("sandbox load alerts: evaluation failed")
            await asyncio.sleep(settings.eval_s)
    finally:
        await runner.close()
