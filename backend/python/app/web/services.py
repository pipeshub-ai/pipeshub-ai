"""Web search and fetch for one caller: access checks, fallback across sources, deadlines."""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.web.budget import WebBudget
from app.web.chain import ChainOutcome, Cooldowns, InFlight, run_chain
from app.web.deadline import Deadline
from app.web.errors import AccessDenied, InvalidUrl, WebError
from app.web.fetch.images import fetch_image_data_uri
from app.web.models import Attempt, CallerContext, WebPage, WebResult
from app.web.ports import AccessRequest, ContentFetcher, SearchProvider, WebAccessGate
from app.web.url import canonical_url

SEARCH_DEADLINE_S = 12.0
SEARCH_ATTEMPT_S = 5.0
FETCH_DEADLINE_S = 20.0
FETCH_ATTEMPT_S = 15.0
# Shared by every image of one page: past it the page is answered without the rest.
IMAGES_DEADLINE_S = 10.0
# Images of one page read at once: each may be MAX_IMAGE_MB, held in memory until it is sent.
IMAGES_IN_FLIGHT = 4

logger = logging.getLogger(__name__)


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
        in_flight: InFlight | None = None,
    ) -> None:
        super().__init__(gate, budget)
        self._providers = tuple(providers)
        self._cooldowns = cooldowns
        self._clock = clock
        self._in_flight = in_flight

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
            in_flight=self._in_flight,
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

    async def fetch_images(self, urls: Sequence[str], caller: CallerContext) -> list[str | None]:
        """Each of ``urls`` as a ``data:`` URI, in order, or None where the gate refuses it or
        it cannot be read (not an image, too large, unreachable, out of time). The gate is asked
        about every image and every redirect, as for a page. Never raises: an image is extra."""
        deadline = Deadline.after(IMAGES_DEADLINE_S, self._clock)
        gate = asyncio.Semaphore(IMAGES_IN_FLIGHT)

        async def allow_hop(target: str) -> bool:
            return await self._allowed(target, caller)

        async def one(url: str) -> str | None:
            try:
                if not await self._allowed(url, caller):
                    return None
                async with gate:
                    return await fetch_image_data_uri(url, deadline, allow_hop=allow_hop)
            except WebError as e:
                logger.info("Page image not read (%s): %s", e.error_type, e.message)
            except Exception:
                logger.warning("Page image not read", exc_info=True)
            return None

        return list(await asyncio.gather(*(one(url) for url in urls)))
