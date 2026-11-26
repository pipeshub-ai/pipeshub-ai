"""WP62-SRCH: the keyless web search default under parallel searches, and what the model is told when it fails.

Found in a manual test: six parallel searches from one Codex child; three came back
``blocked`` ("ddgs: The search engines did not answer in time; duckduckgo: DuckDuckGo blocked the search as
automated traffic") with nothing saying what to do next.
"""

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.web.chain import ChainFailed, Cooldowns, InFlight, run_chain
from app.web.deadline import Deadline
from app.web.errors import AccessDenied, AuthFailed, Blocked, TimedOut, WebError
from app.web.models import Attempt, CallerContext, WebResult
from app.web.services import SearchOutcome
from app.web.tools.web_search import create_web_search_tool, search_hint

CALLER = CallerContext(org_id="org-1", user_id="user-1")


def _tool(outcome: SearchOutcome | Exception) -> object:
    fake = MagicMock()
    fake.search = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    return create_web_search_tool(fake, CALLER)


async def test_wp62_srch_1_a_blocked_free_engine_says_what_happened_and_what_to_do() -> None:
    attempts = (Attempt("ddgs", "timeout", 5.0), Attempt("duckduckgo", "blocked", 0.4))
    error = ChainFailed(
        [TimedOut("The search engines did not answer in time"), Blocked("DuckDuckGo blocked the search as automated traffic")],
        attempts,
    )
    payload = json.loads(await _tool(error).ainvoke({"query": "q"}))  # type: ignore[attr-defined]
    assert payload["ok"] is False and payload["error_type"] == "blocked" and payload["retryable"] is True
    hint = payload["hint"]
    assert "DuckDuckGo" in hint and "refused" in hint
    assert "one search at a time" in hint and "fetch_url" in hint, "what the model can do now"
    assert "Workspace > Web Search" in hint and "SearXNG" in hint, "what an admin can do"


async def test_wp62_srch_2_a_rejected_api_key_names_the_provider_and_where_to_fix_it() -> None:
    from app.web.search import PROVIDERS

    error = ChainFailed([AuthFailed("brave rejected the API key (HTTP 401)")], (Attempt("brave", "auth_failed", 0.2),))
    hint = search_hint(error) or ""
    assert f"{PROVIDERS['brave'].name} rejected its API key" in hint and "Workspace > Web Search" in hint
    assert "DuckDuckGo" not in hint, "no free engine was refused"


async def test_wp62_srch_3_no_hint_where_there_is_nothing_to_explain() -> None:
    assert search_hint(AccessDenied("not allowed")) is None
    outcome = SearchOutcome("q", (WebResult("https://example.com", "T", "S", "ddgs"),), "ddgs", (Attempt("ddgs", None, 0.1),))
    payload = json.loads(await _tool(outcome).ainvoke({"query": "q"}))  # type: ignore[attr-defined]
    assert "hint" not in payload


@dataclass
class _Engine:
    """A free engine that answers one search at a time, and blocks the server when two arrive together."""

    id: str = "duckduckgo"
    cooldown_key: str = "duckduckgo"
    max_in_flight: int | None = 1
    block: bool = True
    calls: int = 0
    in_flight: int = 0
    most_at_once: int = 0
    log: list[str] = field(default_factory=list)

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        self.calls += 1
        self.in_flight += 1
        self.most_at_once = max(self.most_at_once, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            if self.block:
                raise Blocked("DuckDuckGo blocked the search as automated traffic", source=self.id)
            return [WebResult(f"https://example.com/{query}", query, "", self.id)]
        finally:
            self.in_flight -= 1


async def _search(engines: list[object], query: str, cooldowns: Cooldowns, in_flight: InFlight | None, *, attempt_s: float = 5.0) -> object:
    return await run_chain(
        engines, lambda e, d: e.search(query, d), Deadline.after(10.0),  # type: ignore[attr-defined]
        attempt_s=attempt_s, cooldowns=cooldowns, in_flight=in_flight,
    )


async def test_wp62_srch_4_parallel_searches_reach_a_blocking_engine_once() -> None:
    engine, cooldowns, in_flight = _Engine(), Cooldowns(), InFlight()
    other = _Engine(id="ddgs", cooldown_key="ddgs", max_in_flight=2, block=False)
    results = await asyncio.gather(
        *(_search([engine, other], f"q{i}", cooldowns, in_flight) for i in range(6)), return_exceptions=True
    )
    assert engine.calls == 1, "the first block is remembered before the waiting searches reach the engine"
    assert engine.most_at_once == 1
    assert all(not isinstance(r, Exception) for r in results), results
    assert {r.served_by for r in results} == {"ddgs"}  # type: ignore[union-attr]
    skipped = [a for r in results for a in r.attempts if a.source == "duckduckgo"]  # type: ignore[union-attr]
    assert {a.error_type for a in skipped} == {"blocked"}
    assert other.most_at_once <= 2


async def test_wp62_srch_5_waiting_for_a_busy_engine_costs_at_most_its_attempt_time() -> None:
    slow = _Engine(block=False)

    async def hold(query: str, deadline: Deadline) -> list[WebResult]:
        await asyncio.sleep(0.3)
        return []

    slow.search = hold  # type: ignore[method-assign]
    fallback = _Engine(id="ddgs", cooldown_key="ddgs", max_in_flight=None, block=False)
    cooldowns, in_flight = Cooldowns(), InFlight()
    first = asyncio.create_task(_search([slow, fallback], "a", cooldowns, in_flight, attempt_s=0.05))
    await asyncio.sleep(0)
    second = await _search([slow, fallback], "b", cooldowns, in_flight, attempt_s=0.05)
    assert second.served_by == "ddgs"  # type: ignore[attr-defined]
    assert [(a.source, a.error_type) for a in second.attempts] == [("duckduckgo", "timeout"), ("ddgs", None)]  # type: ignore[attr-defined]
    await first


async def test_wp62_srch_6_every_source_cooling_is_still_tried() -> None:
    engine, cooldowns = _Engine(block=False), Cooldowns()
    cooldowns.failed("duckduckgo", Blocked("earlier"))
    outcome = await _search([engine], "q", cooldowns, InFlight())
    assert outcome.served_by == "duckduckgo" and engine.calls == 1  # type: ignore[attr-defined]


def test_wp62_srch_7_the_free_engines_are_limited_and_the_container_shares_one_limit() -> None:
    from app.web import container
    from app.web.search.ddgs_search import DdgsSearch
    from app.web.search.duckduckgo_html import DuckDuckGoHtml

    assert DuckDuckGoHtml.max_in_flight == 1 and DdgsSearch.max_in_flight == 2
    services = container.build_web_services({"provider": "duckduckgo"}, MagicMock())
    assert services.search._in_flight is container.IN_FLIGHT


@pytest.mark.parametrize("error", [Blocked("b"), TimedOut("t")])
def test_wp62_srch_8_a_single_failure_outside_a_chain_has_no_attempts_to_explain(error: WebError) -> None:
    assert search_hint(error) is None
