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
from app.services.sandbox_alerts.cooldown import (
    STATE_CLAIMED,
    STORE_TIMEOUT_S,
    AlertClaim,
    AlertCooldown,
    emailed_at_ms,
)
from app.services.sandbox_alerts.evaluator import AlertDecision, evaluate
from app.services.sandbox_alerts.event import build_alert_event, build_resolved_event
from app.services.sandbox_alerts.leader import AlertLeader
from app.services.sandbox_alerts.window import read_window

if TYPE_CHECKING:
    from pathlib import Path

    from app.config.configuration_service import ConfigurationService

NOTIFICATION_TOPIC = "notification"
_SUPPRESSED_LOG_EVERY_S = 900.0
# A publish (or building the producer) that takes longer is a failure.
PUBLISH_TIMEOUT_S = 10.0
_LEVELS = ("critical", "warning")

ProducerFactory = Callable[[], Awaitable[IMessagingProducer]]
SnapshotFn = Callable[[], dict[str, Any]]
OrgIds = Callable[[], Awaitable[list[str]]]


def _default_snapshot() -> dict[str, Any]:
    from app.agent_loop_lib.sandbox.governor import get_default_governor

    return get_default_governor().snapshot()


def envelope_blocks_alerts() -> str | None:
    """Why Node will reject every alert under the current envelope settings,
    or ``None``. With signing on, each org's copy of an alert is signed under
    that org (`SandboxAlertRunner._publish`); with it off the one copy is
    unsigned, which a consumer that requires a signed context drops."""
    from app.core.envelope import ctx_required, sign_enabled

    if ctx_required() and not sign_enabled():
        return "ENVELOPE_CTX_REQUIRED is on and ENVELOPE_SIGN is off"
    return None


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
        store_timeout_s: float = STORE_TIMEOUT_S,
        publish_timeout_s: float = PUBLISH_TIMEOUT_S,
        org_ids: OrgIds | None = None,
    ) -> None:
        self._settings = settings
        self._org_ids = org_ids
        self._publish_timeout_s = publish_timeout_s
        # Failures already logged at WARNING, until the next success.
        self._failing: set[str] = set()
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
            timeout_s=store_timeout_s,
        )
        self._suppressed_logged_at: dict[str, float] = {}
        self._clear_since: float | None = None
        # The clear episode has been checked for an alert to resolve.
        self._resolve_checked = False
        # The back-to-normal notice of this clear episode went out; only
        # marking the claims resolved is left.
        self._resolve_notified = False
        # Claims of unsent alerts whose release failed: (level, alert id, value it replaced).
        self._pending_releases: list[tuple[str, str, dict[str, Any] | None]] = []

    @property
    def is_leader(self) -> bool:
        return self._leader.held

    async def run_once(self) -> AlertDecision | None:
        """Evaluate once if this worker is (or becomes) the leader."""
        if not self._leader.try_acquire():
            return None
        if self._pending_releases:
            await self._release_targets(list(self._pending_releases))
        now = self._clock()
        totals = await asyncio.to_thread(read_window, self._directory, now, self._settings.window_s)
        if totals.stale:
            await asyncio.to_thread(_delete, totals.stale)
        decision = evaluate(totals, self._settings)
        if decision.any:
            self._clear_since = None
            self._resolve_checked = False
            self._resolve_notified = False
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

    def _warn_once(self, kind: str, message: str, *args: object, exc_info: bool = False) -> None:
        """WARNING the first time *kind* fails in a row, DEBUG after that."""
        level = logging.DEBUG if kind in self._failing else logging.WARNING
        self._failing.add(kind)
        self._logger.log(level, message, *args, exc_info=exc_info)

    def _recovered(self, kind: str) -> None:
        self._failing.discard(kind)

    async def _stronger_emailed_at(self, level: str) -> int | None:
        """When the critical level was last emailed, for a warning: a weaker
        email never follows a stronger one within the cooldown. A store that
        fails here fails the alert like any other store call, so it is tried
        again next interval: claiming the warning without knowing would email
        it, when the critical alert's own warning claim may have failed."""
        if level != "warning":
            return None
        return emailed_at_ms(await self._cooldown.read("critical"))

    async def _release(self, claims: list[AlertClaim], level: str, alert_id: str) -> None:
        # A claim that failed (or timed out) may still have been written.
        targets = [(c.level, alert_id, c.previous) for c in claims] or [(level, alert_id, None)]
        await self._release_targets(targets)

    async def _release_targets(self, targets: list[tuple[str, str, dict[str, Any] | None]]) -> None:
        """Release each claim; one that fails is kept and tried again at the
        next evaluation, or it would hold back an alert that was never sent
        until the claim expires."""
        for target in targets:
            claimed, alert_id, previous = target
            try:
                await self._cooldown.release_if_ours(claimed, alert_id, previous)
            except Exception as exc:
                if target not in self._pending_releases:
                    self._pending_releases.append(target)
                # One line, no traceback: a store that hangs fails every release.
                self._warn_once(
                    "release", "sandbox load alert %s: could not release the claim (%r); retrying", claimed, exc,
                )
            else:
                if target in self._pending_releases:
                    self._pending_releases.remove(target)
                self._recovered("release")

    async def _fire(self, decision: AlertDecision, now: float) -> None:
        level = decision.level
        assert level is not None
        alert_id = str(uuid.uuid4())
        claims: list[AlertClaim] = []
        try:
            claim = await self._cooldown.claim_alert(
                level, alert_id, stronger_emailed_at_ms=await self._stronger_emailed_at(level),
            )
            self._recovered("store")
            if claim is None:
                self._log_suppressed(level, now)
                return
            claims.append(claim)
            if level == "critical":
                # The critical email also counts for the warning.
                try:
                    warning = await self._cooldown.claim_alert(
                        "warning", alert_id, stronger_emailed_at_ms=claim.emailed_at_ms,
                    )
                    if warning is not None:
                        claims.append(warning)
                except Exception as exc:
                    self._logger.debug("sandbox load alert: warning claim failed", exc_info=True)
                    if isinstance(exc, TimeoutError):
                        # It may have been written: give it back too if the send fails.
                        claims.append(AlertClaim("warning", alert_id, email=False, emailed_at_ms=0))
        except Exception as exc:
            self._warn_once("store", "sandbox load alert %s not sent: config store unavailable (%r)", level, exc)
            if isinstance(exc, TimeoutError):
                await self._release(claims, level, alert_id)
            return
        send_mail = claims[0].email
        try:
            event = build_alert_event(
                decision, self._snapshot(), self._settings,
                alert_id=alert_id, host=self._host, fired_at=now, email=send_mail,
            )
        except Exception:
            self._logger.warning("sandbox load alert %s: building the alert failed", level, exc_info=True)
            event = None
        if event is not None and await self._publish(event, alert_id):
            self._recovered("send")
            totals = decision.totals
            self._logger.warning(
                "sandbox load alert fired level=%s alert_id=%s host=%s runs=%d waited=%d "
                "refused=%d busy_pct=%d reason=%s indexing_slowed=%s send_mail=%s",
                level, alert_id, self._host, totals.runs, totals.waited, totals.refused,
                round(decision.busy_percent), event["payload"]["reason"],
                event["payload"]["indexingSlowed"], bool(send_mail),
            )
            return
        await self._release(claims, level, alert_id)
        self._warn_once("send", "sandbox load alert %s not sent; retrying next interval", level)

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
        if not self._resolve_notified:
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
            self._resolve_notified = True
            self._logger.info(
                "sandbox load alert resolved level=%s alert_id=%s host=%s", level, fired.get("alertId"), self._host,
            )
        # A claim left "claimed" would hold back the next alert until it
        # expires, so a failed mark is tried again next interval (without a
        # second notice).
        marked = True
        for claimed_level, value in claimed.items():
            try:
                ok = await self._cooldown.mark_resolved(claimed_level, value)
            except Exception:
                self._logger.debug("sandbox load alert %s: mark resolved failed", claimed_level, exc_info=True)
                ok = False
            marked = marked and ok
        if marked:
            self._recovered("resolve")
        else:
            self._resolve_checked = False
            self._warn_once("resolve", "sandbox load alert: could not mark resolved; retrying next interval")

    async def _publish(self, event: dict[str, Any], alert_id: str) -> bool:
        from app.core.envelope import sign_enabled
        from app.core.org_context import for_org

        try:
            if self._producer is None:
                self._producer = await asyncio.wait_for(self._producer_factory(), self._publish_timeout_s)
            if sign_enabled() and self._org_ids is not None:
                # A signed message names one org: each org's admins get the copy signed under their org (Node delivers it to that
                # org only), so nothing is sent unsigned and nothing runs unbound. A failed copy fails the send; Node skips the orgs
                # whose copy it already delivered (by alert id and org) when the alert is sent again.
                sent = True
                for org in await asyncio.wait_for(self._org_ids(), self._publish_timeout_s):
                    with for_org(org, job="sandbox-alerts"):
                        copy = {**event, "payload": {**event["payload"], "orgId": org}}
                        ok = await asyncio.wait_for(
                            self._producer.send_message(NOTIFICATION_TOPIC, copy, key=f"sandbox-capacity-{alert_id}"),
                            self._publish_timeout_s,
                        )
                    sent = sent and ok is not False
            else:
                sent = await asyncio.wait_for(
                    self._producer.send_message(NOTIFICATION_TOPIC, event, key=f"sandbox-capacity-{alert_id}"),
                    self._publish_timeout_s,
                )
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                # A producer that hung once is rebuilt on the next try.
                await self._drop_producer()
            self._warn_once("publish", "sandbox load alert: publish failed (%r)", exc)
            return False
        if sent is False:
            self._warn_once("publish", "sandbox load alert: publish failed (not accepted)")
            return False
        self._recovered("publish")
        return True

    async def _drop_producer(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(producer.cleanup(), self._publish_timeout_s)

    def _log_suppressed(self, level: str, now: float) -> None:
        last = self._suppressed_logged_at.get(level)
        if last is not None and now - last < _SUPPRESSED_LOG_EVERY_S:
            return
        self._suppressed_logged_at[level] = now
        self._logger.info(
            "sandbox load alert %s suppressed: sent within the last %d s",
            level, round(self._settings.cooldown_s),
        )

    async def close(self) -> None:
        await self._drop_producer()
        self._leader.release()


def _delete(paths: tuple[Path, ...]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(path)


def graph_org_ids(graph_provider: Any) -> OrgIds:  # noqa: ANN401
    """The installation's active orgs, the recipients of a signed alert; a failed list fails the send, which is retried."""
    from app.core.org_context import system

    async def ids() -> list[str]:
        with system("sandbox-alerts"):
            orgs = await graph_provider.get_all_orgs(active=True, raise_on_error=True)
        return [str(o.get("_key") or o.get("id")) for o in orgs or [] if o.get("_key") or o.get("id")]

    return ids


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
    org_ids: OrgIds | None = None,
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
    blocked = envelope_blocks_alerts()
    if blocked is not None:
        log.error(
            "sandbox load alerts will not be delivered: they are sent unsigned, and unsigned "
            "notification messages are rejected (%s)", blocked,
        )
    runner = SandboxAlertRunner(
        config_service, settings, state_directory,
        producer_factory=producer_factory or default_producer_factory(config_service, log),
        snapshot=snapshot, logger=log, org_ids=org_ids,
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
