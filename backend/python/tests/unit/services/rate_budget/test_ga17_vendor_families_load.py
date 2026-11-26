"""GA-17 (G-31, C20.32), per vendor family WP-38 added: agents hammer the vendor while a full sync runs.

Each family's fake vendor (no network) admits ``VENDOR_PER_S`` calls in any one-second window and refuses the rest
the way that vendor does: Jira and Confluence Cloud a 429 with Retry-After, Google a 403 whose reason is
``rateLimitExceeded``, Microsoft Graph a 503 with Retry-After. Calls go through the family's real client layer, the
one its sync and its agent toolsets use: ``HTTPClient`` (paced by the connector's policy through
``ResilientHTTPTransport``) for Atlassian, ``GoogleDriveDataSource.execute`` over ``SendSafeHttp`` for Google, and
the Graph middleware (``ResilienceHandler``, ``SendReportHandler``) for Microsoft. The connectors and query services
hold separate ``RedisRateBudget`` instances over one Redis, as two processes would.

Expected, as for Slack: the sync completes, interactive calls come first, the background agent is not starved, no
throttle storm; and a throttle either side meets pauses the other.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httplib2  # type: ignore
import httpx
import pytest

from app.services.rate_budget import (
    AccountBudget,
    BudgetLimits,
    RateAccount,
    RatePriority,
    RedisRateBudget,
    budget_scope,
    budgeted_call,
)
from app.sources.client.google.send_safe_http import SendSafeHttp
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.microsoft.send_safe_middleware import (
    ResilienceHandler,
    SendReportHandler,
)
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from kiota_http.middleware import BaseMiddleware  # type: ignore

VENDOR_PER_S = 60
LIMITS = BudgetLimits(rate=40, burst=8)
PAGES = 30
ORG = "org-1"

# A call through a family's client layer, answered with the vendor's status.
Call = Callable[[str], Awaitable[int]]


@dataclass
class FakeVendor:
    """A vendor's per-second limit, refusing past it with ``throttle``; called from the loop and worker threads."""

    per_s: int
    throttle: tuple[int, dict[str, str], bytes]
    window: deque[float] = field(default_factory=deque)
    answered: Counter[tuple[str, int]] = field(default_factory=Counter)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def answer(self, who: str) -> tuple[int, dict[str, str], bytes]:
        with self.lock:
            now = time.monotonic()
            while self.window and now - self.window[0] >= 1.0:
                _ = self.window.popleft()
            if len(self.window) >= self.per_s:
                self.answered[(who, self.throttle[0])] += 1
                return self.throttle
            self.window.append(now)
            self.answered[(who, 200)] += 1
            return 200, {}, b"{}"

    def count(self, who: str, status: int = 200) -> int:
        return self.answered[(who, status)]

    @property
    def throttled(self) -> int:
        return sum(n for (_, status), n in self.answered.items() if status != 200)


@dataclass
class Family:
    """How one family's sync and agent toolsets reach the vendor."""

    name: str
    throttle: tuple[int, dict[str, str], bytes]
    # (vendor, the sync's policy) -> one sync call; vendor -> one agent call.
    sync_call: Callable[[FakeVendor, ResiliencePolicy], Call]
    agent_call: Callable[[FakeVendor], Call]


# ── Atlassian: HTTPClient, paced by the connector's policy through ResilientHTTPTransport ─────────────────────────

_WHO_HEADER = "x-test-caller"


def _atlassian_request(who: str) -> HTTPRequest:
    return HTTPRequest(url="https://acme.atlassian.net/rest/api/3/search", headers={_WHO_HEADER: who})


def _atlassian_sync(vendor: FakeVendor, policy: ResiliencePolicy) -> Call:
    client = HTTPClient("token")
    client.use_resilience(policy)

    async def call(who: str) -> int:
        return (await client.execute(_atlassian_request(who))).status

    return call


def _atlassian_agent(vendor: FakeVendor) -> Call:
    client = HTTPClient("token")

    async def call(who: str) -> int:
        return (await client.execute(_atlassian_request(who))).status

    return call


# ── Google: the datasource's execute (paced by the connector's policy) over SendSafeHttp on a worker thread ──────

_GOOGLE_RATE_LIMITED = json.dumps(
    {"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded", "domain": "usageLimits"}]}}
).encode()


def _google_request(http: SendSafeHttp, who: str) -> int:
    response, _ = http.request(f"https://www.googleapis.com/drive/v3/files?caller={who}")
    return int(response.status)


def _google_sync(vendor: FakeVendor, policy: ResiliencePolicy) -> Call:
    source = GoogleDriveDataSource(object(), resilience=policy)  # type: ignore[arg-type]
    http = SendSafeHttp()

    async def call(who: str) -> int:
        return await source.execute(lambda: _google_request(http, who))

    return call


def _google_agent(vendor: FakeVendor) -> Call:
    http = SendSafeHttp()

    async def call(who: str) -> int:
        # As the Drive toolset runs a request: off the loop, carrying the tool call's context.
        return await asyncio.to_thread(_google_request, http, who)

    return call


# ── Microsoft Graph: the send-safe middleware, with the connector's ResilienceHandler on the sync side ───────────


class _GraphTransport(httpx.AsyncBaseTransport):
    def __init__(self, vendor: FakeVendor) -> None:
        self._vendor = vendor

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        status, headers, body = self._vendor.answer(request.headers[_WHO_HEADER])
        await asyncio.sleep(0.002)
        return httpx.Response(status, headers=headers, content=body)


async def _graph_send(first: BaseMiddleware, vendor: FakeVendor, who: str) -> int:
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/sites/root/drive/root/delta", headers={_WHO_HEADER: who})
    response = await first.send(request, _GraphTransport(vendor))
    return int(response.status_code)


def _graph_sync(vendor: FakeVendor, policy: ResiliencePolicy) -> Call:
    paced = ResilienceHandler(policy)
    paced.next = SendReportHandler()

    async def call(who: str) -> int:
        return await _graph_send(paced, vendor, who)

    return call


def _graph_agent(vendor: FakeVendor) -> Call:
    report = SendReportHandler()

    async def call(who: str) -> int:
        return await _graph_send(report, vendor, who)

    return call


_ATLASSIAN_THROTTLE = (429, {"Retry-After": "1"}, b"{}")
_GRAPH_THROTTLE = (503, {"Retry-After": "1"}, b"{}")
FAMILIES = [
    Family("jira", _ATLASSIAN_THROTTLE, _atlassian_sync, _atlassian_agent),
    Family("confluence", _ATLASSIAN_THROTTLE, _atlassian_sync, _atlassian_agent),
    Family("google_drive", (403, {}, _GOOGLE_RATE_LIMITED), _google_sync, _google_agent),
    Family("gmail", (403, {}, _GOOGLE_RATE_LIMITED), _google_sync, _google_agent),
    Family("microsoft_sharepoint", _GRAPH_THROTTLE, _graph_sync, _graph_agent),
    Family("microsoft_outlook", _GRAPH_THROTTLE, _graph_sync, _graph_agent),
]


@pytest.fixture(autouse=True)
def fake_vendor_wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeVendor], None]:
    """Route the Atlassian and Google client layers' wire to the test's vendor."""
    current: dict[str, FakeVendor] = {}

    async def httpx_send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        status, headers, body = current["vendor"].answer(request.headers[_WHO_HEADER])
        await asyncio.sleep(0.002)
        return httpx.Response(status, headers=headers, content=body, request=request)

    def httplib2_send(self: httplib2.Http, uri: str, *args: object, **kwargs: object) -> tuple[httplib2.Response, bytes]:
        status, headers, body = current["vendor"].answer(uri.rsplit("caller=", 1)[-1])
        time.sleep(0.002)
        return httplib2.Response({"status": str(status), **{k.lower(): v for k, v in headers.items()}}), body

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", httpx_send)
    monkeypatch.setattr(httplib2.Http, "request", httplib2_send)

    def use(vendor: FakeVendor) -> None:
        current["vendor"] = vendor

    return use


def _policy(budget: AccountBudget) -> ResiliencePolicy:
    # No local limit: what is under test is the shared budget, as every process of the org sees it.
    policy = ResiliencePolicy(rate_limit=None, max_retries=0, base_delay=0.05, max_delay=5)
    policy.shared = budget
    return policy


async def _sync(call: Call, throttle_status: int, fetchers: int = 2) -> None:
    pages: asyncio.Queue[int] = asyncio.Queue()
    for page in range(PAGES):
        pages.put_nowait(page)

    async def fetcher() -> None:
        while not pages.empty():
            page = pages.get_nowait()
            if await call("sync") == throttle_status:
                pages.put_nowait(page)

    await asyncio.gather(*(fetcher() for _ in range(fetchers)))


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.name)
async def test_ga17_agents_hammer_each_vendor_while_its_full_sync_runs_and_nothing_starves_or_storms(
    family: Family, fake_vendor_wire: Callable[[FakeVendor], None]
) -> None:
    redis = FakeRedisConnectionProvider()
    account = RateAccount(ORG, family.name)
    connectors = AccountBudget(RedisRateBudget(redis), account, LIMITS)
    query = AccountBudget(RedisRateBudget(redis), account, LIMITS)
    vendor = FakeVendor(VENDOR_PER_S, family.throttle)
    fake_vendor_wire(vendor)
    done = asyncio.Event()

    async def agent(who: str, priority: RatePriority) -> None:
        call = family.agent_call(vendor)
        while not done.is_set():
            async with budgeted_call(query, priority, max_wait_s=20):
                _ = await call(who)
            await asyncio.sleep(0.005)

    agents = [asyncio.create_task(agent("interactive", RatePriority.INTERACTIVE)) for _ in range(4)]
    agents.append(asyncio.create_task(agent("background", RatePriority.BACKGROUND)))
    started = time.monotonic()
    try:
        # As run_sync_task spends it.
        with budget_scope(connectors, RatePriority.SYNC):
            await asyncio.wait_for(_sync(family.sync_call(vendor, _policy(connectors)), family.throttle[0]), timeout=20)
    finally:
        done.set()
        await asyncio.gather(*agents)
    elapsed = time.monotonic() - started

    assert vendor.count("sync") == PAGES, "the sync completes"
    assert vendor.throttled == 0, f"no throttle storm: {dict(vendor.answered)}"
    interactive, background = vendor.count("interactive"), vendor.count("background")
    assert interactive >= 1.5 * PAGES, f"interactive calls come first: {dict(vendor.answered)}"
    assert background >= 2, "the background agent is not starved"
    assert interactive + background + PAGES <= LIMITS.burst + LIMITS.rate * elapsed + 2


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.name)
@pytest.mark.parametrize("throttled_side", ["agent", "sync"])
async def test_ga17_a_throttle_either_side_meets_pauses_the_other(
    family: Family, throttled_side: str, fake_vendor_wire: Callable[[FakeVendor], None]
) -> None:
    redis = FakeRedisConnectionProvider()
    account = RateAccount(ORG, family.name)
    connectors = AccountBudget(RedisRateBudget(redis), account, LIMITS)
    query = AccountBudget(RedisRateBudget(redis), account, LIMITS)
    vendor = FakeVendor(0, family.throttle)
    fake_vendor_wire(vendor)

    if throttled_side == "agent":
        async with budgeted_call(query, RatePriority.INTERACTIVE, max_wait_s=20):
            status = await family.agent_call(vendor)("interactive")
        other, priority = connectors, RatePriority.SYNC
    else:
        with budget_scope(connectors, RatePriority.SYNC):
            status = await family.sync_call(vendor, _policy(connectors))("sync")
        other, priority = query, RatePriority.INTERACTIVE
    assert status == family.throttle[0]
    # A pause armed from a worker thread or a middleware lands on the loop a moment later.
    await asyncio.sleep(0.05)

    wait = await other.budget.take(account, LIMITS, priority)
    assert wait > 0, f"the {family.name} throttle the {throttled_side} met paused the whole account"
