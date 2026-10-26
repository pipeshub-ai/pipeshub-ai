"""Web search and fetch for one caller: access checks, fallback across sources, deadlines."""

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.web.budget import WebBudget
from app.web.chain import ChainOutcome, Cooldowns, run_chain
from app.web.deadline import Deadline
from app.web.errors import AccessDenied, InvalidUrl
from app.web.models import Attempt, CallerContext, WebPage, WebResult
from app.web.ports import AccessRequest, ContentFetcher, SearchProvider, WebAccessGate
from app.web.url import canonical_url

SEARCH_DEADLINE_S = 12.0
SEARCH_ATTEMPT_S = 5.0
FETCH_DEADLINE_S = 20.0
FETCH_ATTEMPT_S = 15.0


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    results: tuple[WebResult, ...]
    served_by: str
    attempts: tuple[Attempt, ...]


def _canonical_or_none(url: str) -> str | None:
    try:
        return canonical_url(url)
    except ValueError:
        return None


class _Guarded:
    def __init__(self, gate: WebAccessGate, budget: WebBudget | None) -> None:
        self._gate = gate
        self._budget = budget

    async def _require(self, request: AccessRequest) -> None:
        decision = await self._gate.check(request)
        if not decision.allowed:
            raise AccessDenied(decision.reason or "Web access to this is not allowed", source="gate")

    async def _allowed(self, url: str, caller: CallerContext) -> bool:
        target = _canonical_or_none(url)
        return target is not None and (await self._gate.check(AccessRequest("fetch", target, caller))).allowed


class WebSearchService(_Guarded):
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        gate: WebAccessGate,
        *,
        cooldowns: Cooldowns,
        budget: WebBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(gate, budget)
        self._providers = tuple(providers)
        self._cooldowns = cooldowns
        self._clock = clock

    async def search(self, query: str, caller: CallerContext) -> SearchOutcome:
        """Results from the first provider that answers, deduplicated by page, without any the
        caller may not open. Raises a ``WebError``."""
        await self._require(AccessRequest("search", query, caller))
        if self._budget is not None:
            self._budget.take_search()
        outcome = await run_chain(
            self._providers,
            lambda provider, deadline: provider.search(query, deadline),
            Deadline.after(SEARCH_DEADLINE_S, self._clock),
            attempt_s=SEARCH_ATTEMPT_S,
            cooldowns=self._cooldowns,
        )
        return SearchOutcome(query, await self._visible(outcome.value, caller), outcome.served_by, outcome.attempts)

    async def _visible(self, results: Sequence[WebResult], caller: CallerContext) -> tuple[WebResult, ...]:
        unique: dict[str, WebResult] = {}
        for result in results:
            # One malformed link (a bad port, a broken IPv6 literal) costs that result, not the search.
            key = _canonical_or_none(result.url) if result.url.lower().startswith(("http://", "https://")) else None
            if key is not None:
                unique.setdefault(key, result)
        allowed = await asyncio.gather(*(self._allowed(url, caller) for url in unique))
        return tuple(result for result, ok in zip(unique.values(), allowed, strict=True) if ok)


class WebFetchService(_Guarded):
    def __init__(
        self,
        fetchers: Sequence[ContentFetcher],
        gate: WebAccessGate,
        *,
        cooldowns: Cooldowns,
        budget: WebBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(gate, budget)
        self._fetchers = tuple(fetchers)
        self._cooldowns = cooldowns
        self._clock = clock

    async def fetch(self, url: str, caller: CallerContext) -> ChainOutcome[WebPage]:
        """The page's text, from the first fetcher that reads it. The gate is asked about the URL
        and about every redirect. Raises a ``WebError``."""
        target = _canonical_or_none(url)
        if target is None:
            raise InvalidUrl(f"{url!r} is not a valid web address", source="gate")
        await self._require(AccessRequest("fetch", target, caller))
        if self._budget is not None:
            self._budget.take_page()

        async def allow_hop(target: str) -> bool:
            return await self._allowed(target, caller)

        return await run_chain(
            self._fetchers,
            lambda fetcher, deadline: fetcher.fetch(url, deadline, allow_hop=allow_hop),
            Deadline.after(FETCH_DEADLINE_S, self._clock),
            attempt_s=FETCH_ATTEMPT_S,
            cooldowns=self._cooldowns,
        )
