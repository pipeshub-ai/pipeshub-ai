"""Pure schedule math. Zero I/O, zero storage dependency.

Computes "when does this trigger fire next" for every `TriggerKind` that has
a computable schedule (`CRON`, `INTERVAL`, `ONE_TIME`). `EVENT` and `WEBHOOK`
triggers have no calculable `next_run_at` -- they fire when an external
signal arrives, handled by `application/engine.py`, not here.

DST correctness: all cron/interval math is done in the trigger's own
IANA timezone via `zoneinfo`, then converted to UTC for storage/comparison.
`croniter` (MIT, pallets-eco) walks wall-clock time in that zone, so a
"9am daily" cron fires at 9am local time on both sides of a DST transition,
never at 8am or 10am UTC-equivalent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

from app.services.tasks.domain.errors import InvalidScheduleError
from app.services.tasks.domain.models import TaskTrigger, TriggerKind
from app.services.tasks.domain.policies import MisfirePolicy


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_cron_expression(expression: str) -> None:
    """Raises `InvalidScheduleError` if `expression` is not a valid 5-field
    cron string. Cheap to call at task-creation time so a bad expression
    from schedule inference (Part A2) is caught before it's persisted."""
    try:
        croniter(expression)
    except (CroniterBadCronError, ValueError) as exc:
        raise InvalidScheduleError(f"Invalid cron expression {expression!r}: {exc}") from exc


def validate_timezone(tz_name: str) -> None:
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidScheduleError(f"Unknown IANA timezone {tz_name!r}") from exc


class ScheduleCalculator:
    """Stateless -- every method takes the trigger and a reference time and
    returns a new value. Never mutates its argument."""

    def next_run_at(self, trigger: TaskTrigger, *, after: datetime | None = None) -> str | None:
        """Returns the next fire time (ISO-8601 UTC) strictly after `after`
        (defaults to now), or `None` if the trigger has no more scheduled
        fires (exhausted ONE_TIME, `max_runs` reached, or disabled)."""
        if not trigger.enabled or trigger.is_exhausted():
            return None

        reference = after or datetime.now(timezone.utc)

        if trigger.kind == TriggerKind.ONE_TIME:
            return self._next_one_time(trigger, reference)
        if trigger.kind == TriggerKind.CRON:
            return self._next_cron(trigger, reference)
        if trigger.kind == TriggerKind.INTERVAL:
            return self._next_interval(trigger, reference)

        # EVENT / WEBHOOK triggers have no calculable next_run_at.
        return None

    def _next_one_time(self, trigger: TaskTrigger, reference: datetime) -> str | None:
        if not trigger.fire_at:
            raise InvalidScheduleError("ONE_TIME trigger requires fire_at")
        if trigger.run_count > 0:
            return None
        fire_at = _parse_iso(trigger.fire_at)
        if fire_at <= reference and trigger.misfire_policy == MisfirePolicy.SKIP:
            return None
        return fire_at.isoformat()

    def _next_cron(self, trigger: TaskTrigger, reference: datetime) -> str:
        if not trigger.cron_expression:
            raise InvalidScheduleError("CRON trigger requires cron_expression")
        validate_cron_expression(trigger.cron_expression)
        try:
            tz = ZoneInfo(trigger.timezone)
        except ZoneInfoNotFoundError as exc:
            raise InvalidScheduleError(f"Unknown timezone {trigger.timezone!r}") from exc

        local_reference = reference.astimezone(tz)
        it = croniter(trigger.cron_expression, local_reference)
        next_local: datetime = it.get_next(datetime)
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=tz)
        return next_local.astimezone(timezone.utc).isoformat()

    def _next_interval(self, trigger: TaskTrigger, reference: datetime) -> str:
        if not trigger.interval_seconds or trigger.interval_seconds <= 0:
            raise InvalidScheduleError("INTERVAL trigger requires a positive interval_seconds")
        base = _parse_iso(trigger.last_fire_at) if trigger.last_fire_at else reference
        candidate = base + timedelta(seconds=trigger.interval_seconds)
        # Catch up: if the interval fell far behind (e.g. scheduler was
        # down), jump straight to the next boundary at/after `reference`
        # rather than replaying every missed tick -- misfire_policy
        # (RUN_ALL) is what governs replaying missed *fires*, this only
        # protects next_run_at from drifting into the past forever.
        if candidate <= reference:
            elapsed = (reference - base).total_seconds()
            ticks_missed = int(elapsed // trigger.interval_seconds) + 1
            candidate = base + timedelta(seconds=trigger.interval_seconds * ticks_missed)
        return candidate.isoformat()

    def apply_misfire_policy(
        self, trigger: TaskTrigger, *, claimed_at: datetime
    ) -> list[str]:
        """When a trigger is claimed later than its `next_run_at` (the
        scheduler was down, or fell behind), decide how many fire events to
        emit for the gap. Returns a list of ISO-8601 UTC "fire times" (one
        `TaskRun` per entry). Never returns an empty list for a due
        trigger: SKIP still fires exactly once, "now", to represent the
        single run this claim covers.
        """
        if trigger.next_run_at is None:
            return []
        due_at = _parse_iso(trigger.next_run_at)

        if trigger.misfire_policy == MisfirePolicy.SKIP:
            return [claimed_at.astimezone(timezone.utc).isoformat()]

        if trigger.misfire_policy == MisfirePolicy.RUN_ONCE:
            return [due_at.isoformat()]

        # RUN_ALL: replay every missed cron/interval boundary between
        # due_at and claimed_at, capped to avoid a pathological backlog
        # (e.g. a per-minute cron down for a week) from generating tens of
        # thousands of runs in one claim.
        fire_times = [due_at]
        cursor = due_at
        max_replays = 500
        while len(fire_times) < max_replays:
            probe = trigger.model_copy(update={"next_run_at": cursor.isoformat(), "run_count": 0})
            nxt = self.next_run_at(probe, after=cursor)
            if nxt is None:
                break
            next_dt = _parse_iso(nxt)
            if next_dt > claimed_at:
                break
            fire_times.append(next_dt)
            cursor = next_dt
        return [ft.isoformat() for ft in fire_times]
