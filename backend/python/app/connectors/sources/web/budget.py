"""How long one crawl may run, and the report of what it did."""

import asyncio
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum

MANUAL_RUN_HOURS = 24.0
# A scheduled run must end before the next one is due, or ticks pile up behind it.
SCHEDULE_SHARE = 0.9
STALL_MINUTES = 60.0
# Pages in a row the site refused with "too many requests" (no page answered in between) before the
# crawl stops asking and leaves the rest to the next sync.
RATE_LIMITED_PAGES_TO_STOP = 10
MAX_RUN_HOURS_ENV = "WEB_CRAWL_MAX_RUN_HOURS"
STALL_MINUTES_ENV = "WEB_CRAWL_STALL_MINUTES"


def _positive(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw else None
    except ValueError:
        return None
    return value if value and value > 0 else None


def run_limits(sync_config: Mapping[str, object], env: Mapping[str, str]) -> tuple[float, float]:
    """(deadline, stall) in seconds for a crawl with this sync config.

    The operator's env settings win; otherwise a scheduled crawl gets most of its interval and a
    manual one a day.
    """
    deadline_hours = _positive(env.get(MAX_RUN_HOURS_ENV))
    if deadline_hours is None:
        deadline_hours = MANUAL_RUN_HOURS
        if str(sync_config.get("selectedStrategy", "")).upper() == "SCHEDULED":
            scheduled = sync_config.get("scheduledConfig")
            interval = _positive(str((scheduled or {}).get("intervalMinutes", ""))) if isinstance(scheduled, Mapping) else None
            if interval is not None:
                deadline_hours = interval * SCHEDULE_SHARE / 60
    stall_minutes = _positive(env.get(STALL_MINUTES_ENV)) or STALL_MINUTES
    return deadline_hours * 3600, stall_minutes * 60


class StopReason(str, Enum):
    """Why a crawl ended before it ran out of pages to visit."""

    MAX_PAGES = "max_pages"
    TIME_LIMIT = "time_limit"
    STALLED = "stalled"
    RATE_LIMITED = "rate_limited"


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    STOPPED = "stopped"
    FAILED = "failed"


class CrawlBudget:
    """A crawl's wall-clock deadline and its no-progress (stall) limit.

    ``now`` is a monotonic clock in seconds. The crawl calls ``progress`` whenever a fetch
    attempt finishes and ``check`` between fetches; ``watch`` is the backstop for a fetch that
    never returns, and waits on real time so a test's virtual clock can't trip it.
    """

    def __init__(self, *, now: Callable[[], float], deadline_seconds: float, stall_seconds: float) -> None:
        if deadline_seconds <= 0 or stall_seconds <= 0:
            raise ValueError("crawl budget limits must be positive")
        self._now = now
        self._deadline = now() + deadline_seconds
        self._stall_seconds = stall_seconds
        self._last_progress = now()
        self._rate_limited_in_a_row = 0
        self.stop_reason: StopReason | None = None

    def progress(self) -> None:
        self._last_progress = self._now()

    def page_answered(self, *, rate_limited: bool) -> None:
        """Record a page's final answer; a run of rate-limited ones stops the crawl."""
        self._rate_limited_in_a_row = self._rate_limited_in_a_row + 1 if rate_limited else 0
        if self.stop_reason is None and self._rate_limited_in_a_row >= RATE_LIMITED_PAGES_TO_STOP:
            self.stop_reason = StopReason.RATE_LIMITED

    def check(self) -> StopReason | None:
        """The reason to stop now, if any; once set it stays set."""
        if self.stop_reason is None:
            now = self._now()
            if now >= self._deadline:
                self.stop_reason = StopReason.TIME_LIMIT
            elif now - self._last_progress >= self._stall_seconds:
                self.stop_reason = StopReason.STALLED
        return self.stop_reason

    async def watch(self, task: "asyncio.Future[object]", *, interval: float) -> None:
        """Cancel ``task`` once the budget runs out."""
        while not task.done():
            if self.check() is not None:
                task.cancel()
                return
            await asyncio.sleep(interval)


@dataclass
class RunReport:
    """What one crawl did, kept with the connector's sync point and shown to the user."""

    started_at: int
    finished_at: int | None = None
    outcome: RunOutcome | None = None
    stop_reason: StopReason | None = None
    pages_visited: int = 0
    pages_saved: int = 0
    # Pages the connector stores at the end of the run, which Max Pages caps (None before it is known).
    pages_stored: int | None = None
    robots_skipped: int = 0
    deferred: int = 0
    # New pages left out because the connector already stores Max Pages pages.
    skipped_max_pages: int = 0
    failed_by_status: Counter[str] = field(default_factory=Counter)
    pruned: int = 0
    sitemap_urls: int = 0
    sitemap_files_read: int = 0
    sitemap_files_failed: int = 0
    sitemap_limits_hit: list[str] = field(default_factory=list)
    # Pages sitemap-only discovery didn't fetch because their lastmod predates the stored copy.
    unchanged_by_lastmod: int = 0
    noindex_skipped: int = 0
    # Carried on from where an unfinished crawl stopped, rather than from the start page.
    resumed: bool = False
    prune_skipped: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["outcome"] = self.outcome.value if self.outcome else None
        data["stop_reason"] = self.stop_reason.value if self.stop_reason else None
        data["failed_by_status"] = dict(self.failed_by_status)
        return data

    def summary(self) -> dict[str, object]:
        """The figures the connector page shows for the last run."""
        return {
            "pagesSaved": self.pages_saved,
            "pagesStored": self.pages_stored,
            "pagesVisited": self.pages_visited,
            "failed": sum(self.failed_by_status.values()),
            "failedByStatus": dict(self.failed_by_status),
            "robotsSkipped": self.robots_skipped,
            "noindexSkipped": self.noindex_skipped,
            "skippedMaxPages": self.skipped_max_pages,
            "deferred": self.deferred,
            "pruned": self.pruned,
            "sitemapUrls": self.sitemap_urls,
            "stopReason": self.stop_reason.value if self.stop_reason else None,
            "resumed": self.resumed,
        }

    def to_json(self) -> str:
        """Stored as text: a graph store such as Neo4j can't hold a nested map as a property."""
        return json.dumps(self.to_dict(), sort_keys=True)
