"""Arithmetic for the scale and stress runs, with no stack or network in sight.

A long run can pass every threshold and still be telling us something bad: a
stack that indexes 90 files a minute for the first ten minutes and 20 a minute
by the end has a problem that an average hides. ``drift`` compares the start of
a run with its end so that shows up — but only over the slices where files were
still queued, because every run empties its queue at the end and that tail is
not a slowdown. ``overload_verdicts`` answers the stress run's question, which
is not "how fast" but "did anything get lost".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    """One look at the run while it was going."""

    elapsed_seconds: float
    uploaded: int
    finished: int
    container_memory_mb: float | None = None
    indexing_memory_mb: float | None = None

    @property
    def backlog(self) -> int:
        """Uploaded but not yet finished: how far behind the indexer is."""
        return max(0, self.uploaded - self.finished)


def rounded(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def throughput_windows(samples: list[Sample], windows: int = 4) -> list[dict]:
    """Records finished per minute, in equal slices of the run.

    Slices are cut by time, not by record count, so a slice where nothing
    finished shows up as zero rather than disappearing.
    """
    if windows < 1:
        raise ValueError(f"windows must be at least 1, got {windows}")
    ordered = sorted(samples, key=lambda s: s.elapsed_seconds)
    if len(ordered) < 2:
        return []
    span = ordered[-1].elapsed_seconds - ordered[0].elapsed_seconds
    if span <= 0:
        return []
    start = ordered[0].elapsed_seconds
    width = span / windows
    out = []
    for i in range(windows):
        lo, hi = start + i * width, start + (i + 1) * width
        before = _last_at_or_before(ordered, lo)
        after = _last_at_or_before(ordered, hi)
        if before is None or after is None:
            continue
        done = max(0, after.finished - before.finished)
        minutes = (after.elapsed_seconds - before.elapsed_seconds) / 60
        out.append({
            "from_seconds": rounded(lo),
            "to_seconds": rounded(hi),
            "records_finished": done,
            "records_per_minute": rounded(done / minutes) if minutes > 0 else None,
            "backlog_at_end": after.backlog,
        })
    return out


def _last_at_or_before(ordered: list[Sample], when: float) -> Sample | None:
    chosen = None
    for s in ordered:
        if s.elapsed_seconds <= when + 1e-9:
            chosen = s
        else:
            break
    return chosen or (ordered[0] if ordered else None)


def latency_windows(finished: list[tuple[float, float]], windows: int = 4) -> list[dict]:
    """Median time-to-indexed per slice, from ``(finished_at, seconds_taken)`` pairs.

    Answers "are files taking longer the deeper into the run we get", which a
    single median over the whole run cannot.
    """
    if not finished:
        return []
    ordered = sorted(finished)
    first, last = ordered[0][0], ordered[-1][0]
    span = last - first
    if span <= 0:
        return [{"from_seconds": rounded(first), "to_seconds": rounded(last),
                 "records": len(ordered), "p50_seconds": rounded(_median([d for _, d in ordered]))}]
    width = span / windows
    out = []
    for i in range(windows):
        lo = first + i * width
        hi = last if i == windows - 1 else first + (i + 1) * width
        taken = [d for at, d in ordered if lo <= at <= hi]
        out.append({
            "from_seconds": rounded(lo),
            "to_seconds": rounded(hi),
            "records": len(taken),
            "p50_seconds": rounded(_median(taken)) if taken else None,
        })
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def drift(
    throughput: list[dict],
    latency: list[dict],
    samples: list[Sample],
    *,
    throughput_drop: float = 0.30,
    latency_rise: float = 0.50,
    memory_growth_mb: float = 500.0,
) -> dict:
    """Compare the start of the run with its end, in plain words.

    Thresholds are deliberately loose: this is meant to catch "it ground to a
    halt", not the ordinary wobble of a shared machine.
    """
    notes: list[str] = []
    # Only slices where files were still waiting say anything about speed. Every
    # run empties its queue at the end, and a tail with nothing left to index
    # would otherwise read as "it slowed down".
    busy = [w for w in throughput if (w.get("backlog_at_end") or 0) > 0]
    first_rate = _first_number(busy, "records_per_minute") if len(busy) >= 2 else None
    last_rate = _last_number(busy, "records_per_minute") if len(busy) >= 2 else None
    throughput_change = _change(first_rate, last_rate)
    if throughput_change is not None and throughput_change <= -throughput_drop:
        notes.append(
            f"Indexing slowed while files were still queued: {first_rate:.0f} records a minute early on, "
            f"{last_rate:.0f} later ({abs(throughput_change):.0%} slower). "
            "Look at the slice table below before trusting the averages."
        )

    first_p50 = _first_number(latency, "p50_seconds")
    last_p50 = _last_number(latency, "p50_seconds")
    latency_change = _change(first_p50, last_p50)
    # A file that waits behind a longer queue takes longer to index, and that is
    # queueing rather than a fault. So this is only worth saying when no queue
    # built up underneath it.
    if latency_change is not None and latency_change >= latency_rise and not _queue_built_up(samples):
        notes.append(
            f"Files took longer the further in we got, without the queue growing to explain it: "
            f"{first_p50:.0f}s to index a file at the start, {last_p50:.0f}s at the end "
            f"({latency_change:.0%} longer)."
        )

    memory = _memory_trend(samples)
    if memory["growth_mb"] is not None and memory["growth_mb"] >= memory_growth_mb:
        per_hour = memory.get("growth_mb_per_hour")
        rate = f", about {per_hour:.0f} MB an hour" if per_hour else ""
        notes.append(
            f"The indexing service's memory grew by {memory['growth_mb']:.0f} MB during the run{rate}. "
            "Check for a leak before running anything larger."
        )

    return {
        "throughput_change": rounded(throughput_change, 4),
        "latency_p50_change": rounded(latency_change, 4),
        "memory": memory,
        "steady": not notes,
        "notes": notes,
    }


def _queue_built_up(samples: list[Sample], factor: float = 1.5, floor: int = 20) -> bool:
    """Did files pile up behind the indexer at any point in the run?

    Measured against the deepest the queue got, not the depth at the end: a run
    that drains before it finishes ends at zero however far behind it fell, and
    comparing the two ends would miss every queue that cleared.
    """
    ordered = sorted(samples, key=lambda s: s.elapsed_seconds)
    if len(ordered) < 2:
        return False
    first = ordered[0].backlog
    peak = max(s.backlog for s in ordered)
    return peak > floor and peak > max(first, 1) * factor


def _memory_trend(samples: list[Sample]) -> dict:
    measured = [s for s in sorted(samples, key=lambda s: s.elapsed_seconds) if s.indexing_memory_mb is not None]
    if len(measured) < 2:
        return {"start_mb": None, "end_mb": None, "peak_mb": None, "growth_mb": None, "growth_mb_per_hour": None}
    start, end = measured[0], measured[-1]
    hours = (end.elapsed_seconds - start.elapsed_seconds) / 3600
    growth = (end.indexing_memory_mb or 0) - (start.indexing_memory_mb or 0)
    return {
        "start_mb": rounded(start.indexing_memory_mb),
        "end_mb": rounded(end.indexing_memory_mb),
        "peak_mb": rounded(max(s.indexing_memory_mb or 0 for s in measured)),
        "growth_mb": rounded(growth),
        "growth_mb_per_hour": rounded(growth / hours) if hours > 0 else None,
    }


def _first_number(rows: list[dict], key: str) -> float | None:
    for row in rows:
        if isinstance(row.get(key), (int, float)):
            return float(row[key])
    return None


def _last_number(rows: list[dict], key: str) -> float | None:
    for row in reversed(rows):
        if isinstance(row.get(key), (int, float)):
            return float(row[key])
    return None


def _change(first: float | None, last: float | None) -> float | None:
    if first is None or last is None or first == 0:
        return None
    return (last - first) / first


@dataclass(frozen=True)
class Verdict:
    check: str
    passed: bool
    detail: str


def overload_verdicts(
    *,
    attempted: int,
    uploaded: int,
    upload_failures: int,
    listed: int,
    terminal: int,
    peak_backlog: int,
    recovery_seconds: float | None,
    rejected: int = 0,
) -> list[Verdict]:
    """What the stress run actually cares about: overload must not lose work.

    Refusing an upload is fine, as long as the caller is told. Accepting one and
    then losing it is not.
    """
    verdicts = [
        Verdict(
            "Every upload was either accepted or refused with an error",
            uploaded + upload_failures >= attempted,
            f"{attempted} attempted, {uploaded} accepted, {upload_failures} refused with an error"
            + (f" ({rejected} of those were the server asking us to slow down)" if rejected else ""),
        ),
        Verdict(
            "Every accepted file appears in the knowledge base",
            listed == uploaded,
            f"{uploaded} accepted, {listed} listed"
            + ("" if listed == uploaded else f" — {abs(uploaded - listed)} "
               + ("missing" if listed < uploaded else "extra")),
        ),
        Verdict(
            "Every accepted file finished, one way or another",
            terminal == uploaded,
            f"{terminal} of {uploaded} reached a final state"
            + ("" if terminal == uploaded else f" — {uploaded - terminal} still in flight when time ran out"),
        ),
        Verdict(
            "Refusals were the stack shedding load, not breaking",
            rejected == upload_failures,
            (
                f"{upload_failures} refused, {rejected} of them by asking us to slow down"
                if upload_failures
                else "nothing was refused"
            )
            + (
                ""
                if rejected == upload_failures
                else f" — {upload_failures - rejected} failed some other way, which is worth reading "
                     "in the run's upload_failures list"
            ),
        ),
        Verdict(
            "The backlog cleared once the load stopped",
            recovery_seconds is not None,
            f"peaked at {peak_backlog} files waiting; "
            + (f"cleared {recovery_seconds:.0f}s after the last upload" if recovery_seconds is not None
               else "never cleared"),
        ),
    ]
    return verdicts


def render_verdicts(verdicts: list[Verdict]) -> str:
    lines = ["| Under overload | Result | Detail |", "| --- | --- | --- |"]
    for v in verdicts:
        lines.append(f"| {v.check} | {'✅ yes' if v.passed else '❌ no'} | {v.detail} |")
    return "\n".join(lines)
