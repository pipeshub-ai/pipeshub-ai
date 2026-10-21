"""A more varied first page for the search API (``SEARCH_SOURCE_DIVERSITY``).

One source can fill the top of a search: a run of near-identical records from
one connector (pull requests written from one template, say) takes most of
the first ten places and pushes the one message or page that answers the
question to place 20 or lower, past what a caller reads. With the switch on,
the search API reads a deeper pool of matches, lets no one source hold more
than ``SEARCH_DIVERSITY_PER_SOURCE`` of the first ``SEARCH_DIVERSITY_WINDOW``
records, and fills the places that frees with the next best records from other
sources. The records it demoted are not dropped: they follow the first page,
best first, so the response holds every record the plain search would have
returned, plus those brought up.

A source is the record's connector type (``connectorName``), so two instances
of one connector count as one source. A record whose source is unknown is
never held back. Within a source, records keep their order.

It does not apply when the search is scoped to one source or one record (there
is nothing to vary), nor to a timeline's period-by-period searches, whose
matches are put in date order anyway. Only the search API (``/search``, and the
MCP tool and the CLI on it) uses it; the assistant ranks what it reads by score.

Off unless set to ``true``; ``HIGH_ACCURACY_MODE`` does not turn it on. It
changes ranking for every search, so it stays off until measured.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Hashable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

from app.utils.env_config import env_int
from app.utils.env_utils import env_bool

SOURCE_DIVERSITY_ENV_VAR = "SEARCH_SOURCE_DIVERSITY"
WINDOW_ENV_VAR = "SEARCH_DIVERSITY_WINDOW"
PER_SOURCE_ENV_VAR = "SEARCH_DIVERSITY_PER_SOURCE"
POOL_ENV_VAR = "SEARCH_DIVERSITY_POOL"

# The pool is read like a larger limit, so it stays within the search's
# over-fetch cap.
MAX_POOL = 300
MAX_WINDOW = 100

_logger = logging.getLogger(__name__)

R = TypeVar("R", bound=Hashable)


@dataclass(frozen=True)
class SourceDiversity:
    """How many records the first page holds, and how many of them one source may hold."""

    window: int = 10
    per_source: int = 4
    pool: int = 50

    def fetch_limit(self, limit: int) -> int:
        """Matches to read: at least ``pool``, never fewer than the caller asked for."""
        return max(limit, self.pool)

    def order(
        self,
        records: Sequence[R],
        *,
        shown: int,
        source_of: Callable[[R], Hashable | None],
    ) -> list[R]:
        """The records to return, first page first.

        ``records`` are best first, and the first ``shown`` of them are what
        the plain search returns. The first page has as many records as that
        (at most ``window``), at most ``per_source`` from any one source, taken
        best first from all of ``records``. The other records of the first
        ``shown`` follow, best first. Records past ``shown`` that did not make
        the first page are left out, as the plain search leaves them out.
        """
        size = min(self.window, shown)
        page: list[R] = []
        per_source: dict[Hashable, int] = {}
        for record in records:
            if len(page) >= size:
                break
            source = source_of(record)
            if source is not None:
                if per_source.get(source, 0) >= self.per_source:
                    continue
                per_source[source] = per_source.get(source, 0) + 1
            page.append(record)
        on_page = set(page)
        return page + [record for record in records[:shown] if record not in on_page]


def source_diversity_from_env() -> SourceDiversity:
    """The knobs, clamped; a malformed value keeps the default."""
    defaults = SourceDiversity()
    window = max(1, min(MAX_WINDOW, env_int(WINDOW_ENV_VAR, defaults.window)))
    return SourceDiversity(
        window=window,
        per_source=max(1, min(window, env_int(PER_SOURCE_ENV_VAR, defaults.per_source))),
        pool=max(1, min(MAX_POOL, env_int(POOL_ENV_VAR, defaults.pool))),
    )


_plain_ranking: ContextVar[bool] = ContextVar("search_plain_ranking", default=False)


@contextmanager
def plain_ranking() -> Generator[None]:
    """Searches run inside keep the plain ranking (a timeline's periods)."""
    token = _plain_ranking.set(True)
    try:
        yield
    finally:
        _plain_ranking.reset(token)


@dataclass
class _LoggedState:
    enabled: bool | None = None


_logged = _LoggedState()


def active_source_diversity(
    *,
    scope_ids: Sequence[str] | None,
    scope_virtual_ids: Sequence[str] | None,
) -> SourceDiversity | None:
    """The rule a search API search should apply, or None to keep the plain ranking.

    ``scope_ids`` are the apps or collections the search is scoped to (None
    when unscoped), ``scope_virtual_ids`` the records (None when unscoped).
    """
    if _plain_ranking.get():
        return None
    enabled = env_bool(SOURCE_DIVERSITY_ENV_VAR, False)
    if enabled is not _logged.enabled:
        _logged.enabled = enabled
        _logger.info("Search source diversity %s", "on" if enabled else "off")
    if not enabled:
        return None
    if scope_ids is not None and len(set(scope_ids)) <= 1:
        return None
    if scope_virtual_ids is not None and len(set(scope_virtual_ids)) <= 1:
        return None
    return source_diversity_from_env()


def record_source(record: dict[str, Any] | None) -> str | None:
    """The source a record counts against: its connector type."""
    if not record:
        return None
    source = record.get("connectorName") or record.get("origin")
    return str(source).upper() if source else None
