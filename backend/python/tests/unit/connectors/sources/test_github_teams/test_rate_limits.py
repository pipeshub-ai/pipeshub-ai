"""GitHub rate limits are rate limits, not permission denials.

GitHub answers an exhausted primary limit ("API rate limit exceeded for user
ID ...") and a secondary limit with 403. Read as a 403, every such item was
streamed to indexing as "access denied" and marked FAILED for good. These
tests pin the classification, the bounded wait in ``ds_call``, the headroom
reserve that keeps PipesHub from draining the user's hourly budget, and the
status a rate-limited stream finally reports. No request reaches GitHub: the
HTTP layer is an ``httpx.MockTransport``.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.github_teams.issues import IssuesSync
from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.models.entities import TicketRecord
from app.sources.client.github.github import (
    GitHubClient,
    GitHubClientViaToken,
    GitHubResponse,
    github_retry_after_seconds,
    is_github_rate_limit,
)
from app.sources.external.github.github_async import GitHubAsyncDataSource
from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio

PRIMARY_MESSAGE = "API rate limit exceeded for user ID 1234."
SECONDARY_MESSAGE = (
    "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."
)
DENIED_MESSAGE = "Resource not accessible by personal access token"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def no_sleep() -> Iterator[AsyncMock]:
    with patch(
        "app.connectors.sources.github_teams.runtime.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        yield sleep


def _data_source(handler) -> GitHubAsyncDataSource:
    inner = GitHubClientViaToken(token="dummy-token", per_page=90)
    inner.create_client()
    return GitHubAsyncDataSource(GitHubClient(inner), transport=httpx.MockTransport(handler))


def _budget_headers(*, remaining: int, reset_in: int, limit: int = 5000, resource: str = "core") -> dict:
    return {
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(int(time.time()) + reset_in),
        "x-ratelimit-resource": resource,
    }


def _primary_limit(reset_in: int) -> httpx.Response:
    return httpx.Response(
        403,
        json={"message": PRIMARY_MESSAGE},
        headers=_budget_headers(remaining=0, reset_in=reset_in),
    )


def _runtime_over(ds: GitHubAsyncDataSource) -> tuple[MagicMock, RuntimeHelper]:
    c = make_mock_connector()
    c.data_source = ds
    runtime = RuntimeHelper(c)
    c.runtime = runtime
    return c, runtime


class TestClassification:
    @pytest.mark.parametrize(
        ("status", "headers", "message"),
        [
            (429, {}, None),
            (403, {"X-RateLimit-Remaining": "0"}, None),
            (403, {"Retry-After": "60"}, None),
            (403, {}, PRIMARY_MESSAGE),
            (403, {}, SECONDARY_MESSAGE),
        ],
    )
    def test_rate_limits(self, status: int, headers: dict, message: str | None) -> None:
        assert is_github_rate_limit(status, headers, message) is True

    @pytest.mark.parametrize(
        ("status", "headers", "message"),
        [
            (403, {"x-ratelimit-remaining": "4999"}, DENIED_MESSAGE),
            (403, None, "Must have admin rights to Repository."),
            (404, {"x-ratelimit-remaining": "0"}, "Not Found"),
            (401, {}, "Bad credentials"),
            (None, {}, None),
        ],
    )
    def test_not_rate_limits(self, status: int | None, headers: dict | None, message: str | None) -> None:
        assert is_github_rate_limit(status, headers, message) is False

    def test_retry_after_wins_over_reset(self) -> None:
        headers = {"Retry-After": "30", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"}
        assert github_retry_after_seconds(headers) == 30

    def test_reset_counts_only_when_exhausted(self) -> None:
        now = 1_000_000.0
        assert github_retry_after_seconds(
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000120"}, now=now
        ) == 120
        assert github_retry_after_seconds(
            {"x-ratelimit-remaining": "17", "x-ratelimit-reset": "1000120"}, now=now
        ) is None

    def test_accepts_httpx_headers(self) -> None:
        assert github_retry_after_seconds(httpx.Headers({"Retry-After": "5"})) == 5

    def test_response_property(self) -> None:
        assert GitHubResponse(success=False, status_code=403, retry_after_seconds=10).is_rate_limited
        assert GitHubResponse(success=False, status_code=403, api_message=SECONDARY_MESSAGE).is_rate_limited
        assert not GitHubResponse(success=False, status_code=403, api_message=DENIED_MESSAGE).is_rate_limited
        assert not GitHubResponse(success=True, status_code=429).is_rate_limited


class TestDataSourceReportsRateLimitsAs429:
    async def test_primary_limit(self) -> None:
        ds = _data_source(lambda request: _primary_limit(reset_in=1800))
        res = await ds.get_issue("acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert res.is_rate_limited is True
        assert res.api_message == PRIMARY_MESSAGE
        assert 1790 <= (res.retry_after_seconds or 0) <= 1800

    async def test_secondary_limit_with_retry_after(self) -> None:
        ds = _data_source(lambda request: httpx.Response(
            403, json={"message": SECONDARY_MESSAGE}, headers={"retry-after": "60"},
        ))
        res = await ds.get_issue("acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert res.retry_after_seconds == 60

    async def test_secondary_limit_body_only(self) -> None:
        ds = _data_source(lambda request: httpx.Response(403, json={"message": SECONDARY_MESSAGE}))
        res = await ds.get_issue("acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert res.retry_after_seconds is None

    async def test_permission_denial_stays_403(self) -> None:
        ds = _data_source(lambda request: httpx.Response(
            403, json={"message": DENIED_MESSAGE},
            headers=_budget_headers(remaining=4200, reset_in=1800),
        ))
        res = await ds.get_issue("acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.FORBIDDEN.value
        assert res.is_rate_limited is False
        assert res.retry_after_seconds is None


class TestReserve:
    async def test_stops_before_the_reserve_and_sends_nothing(self) -> None:
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.url.path)
            return httpx.Response(200, json={"number": 1}, headers=_budget_headers(remaining=500, reset_in=1800))

        ds = _data_source(handler)
        first = await ds.get_issue("acme", "widgets", 1)
        second = await ds.get_issue("acme", "widgets", 2)

        assert first.success is True
        assert len(sent) == 1
        assert second.success is False
        assert second.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert second.exception_type == "GitHubRateLimitReserveError"
        assert 1790 <= (second.retry_after_seconds or 0) <= 1800

    async def test_above_the_reserve_keeps_sending(self) -> None:
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.url.path)
            return httpx.Response(200, json={"number": 1}, headers=_budget_headers(remaining=501, reset_in=1800))

        ds = _data_source(handler)
        await ds.get_issue("acme", "widgets", 1)
        res = await ds.get_issue("acme", "widgets", 2)

        assert res.success is True
        assert len(sent) == 2

    async def test_a_reset_window_reopens_the_budget(self) -> None:
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.url.path)
            return httpx.Response(200, json={"number": 1}, headers=_budget_headers(remaining=0, reset_in=-5))

        ds = _data_source(handler)
        await ds.get_issue("acme", "widgets", 1)
        res = await ds.get_issue("acme", "widgets", 2)

        assert res.success is True
        assert len(sent) == 2

    async def test_pools_are_tracked_separately(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                return httpx.Response(200, json={"data": {"ok": True}}, headers=_budget_headers(
                    remaining=4000, reset_in=1800, resource="graphql",
                ))
            return httpx.Response(200, json={"number": 1}, headers=_budget_headers(remaining=10, reset_in=1800))

        ds = _data_source(handler)
        await ds.get_issue("acme", "widgets", 1)

        assert (await ds.graphql_query("{ viewer { login } }")).success is True
        assert (await ds.get_issue("acme", "widgets", 2)).is_rate_limited is True

    async def test_reserve_can_be_switched_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_RATE_LIMIT_RESERVE_PERCENT", "0")
        ds = _data_source(lambda request: httpx.Response(
            200, json={"number": 1}, headers=_budget_headers(remaining=3, reset_in=1800),
        ))
        await ds.get_issue("acme", "widgets", 1)

        assert (await ds.get_issue("acme", "widgets", 2)).success is True

    async def test_an_invalid_setting_keeps_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_RATE_LIMIT_RESERVE_PERCENT", "lots")
        ds = _data_source(lambda request: httpx.Response(
            200, json={"number": 1}, headers=_budget_headers(remaining=3, reset_in=1800),
        ))
        await ds.get_issue("acme", "widgets", 1)

        assert (await ds.get_issue("acme", "widgets", 2)).is_rate_limited is True


class TestDsCallWaitsOutShortLimits:
    async def test_waits_for_retry_after_then_succeeds(self, no_sleep: AsyncMock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(403, json={"message": SECONDARY_MESSAGE}, headers={"retry-after": "30"})
            return httpx.Response(200, json={"number": 7})

        ds = _data_source(handler)
        c, runtime = _runtime_over(ds)
        res = await runtime.ds_call(ds.get_issue, "acme", "widgets", 7)

        assert res.success is True
        assert calls["n"] == 2
        [wait] = [call.args[0] for call in no_sleep.await_args_list]
        assert 31 <= wait <= 34
        logged = " ".join(str(call.args[0]) for call in c.logger.warning.call_args_list)
        assert "GitHub rate limit" in logged
        assert "permission" not in logged.lower()

    async def test_secondary_limit_without_retry_after_waits_a_minute(self, no_sleep: AsyncMock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(403, json={"message": SECONDARY_MESSAGE})
            return httpx.Response(200, json={"number": 7})

        ds = _data_source(handler)
        _c, runtime = _runtime_over(ds)
        res = await runtime.ds_call(ds.get_issue, "acme", "widgets", 7)

        assert res.success is True
        [wait] = [call.args[0] for call in no_sleep.await_args_list]
        assert 61 <= wait <= 67

    async def test_a_distant_reset_returns_at_once_with_the_reset_time(self, no_sleep: AsyncMock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _primary_limit(reset_in=2400)

        ds = _data_source(handler)
        c, runtime = _runtime_over(ds)
        res = await runtime.ds_call(ds.get_issue, "acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert calls["n"] == 1
        no_sleep.assert_not_awaited()
        [warning] = c.logger.warning.call_args_list
        assert "GitHub rate limit" in warning.args[0]
        assert PRIMARY_MESSAGE in warning.args
        assert any(isinstance(a, str) and a.endswith(" UTC") for a in warning.args)

    async def test_gives_up_after_the_retry_budget(self, no_sleep: AsyncMock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"message": "slow down"}, headers={"retry-after": "1"})

        ds = _data_source(handler)
        _c, runtime = _runtime_over(ds)
        res = await runtime.ds_call(ds.get_issue, "acme", "widgets", 7)

        assert res.is_rate_limited is True
        assert calls["n"] == 3
        assert no_sleep.await_count == 2

    async def test_a_permission_denial_is_not_retried(self, no_sleep: AsyncMock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(403, json={"message": DENIED_MESSAGE})

        ds = _data_source(handler)
        _c, runtime = _runtime_over(ds)
        res = await runtime.ds_call(ds.get_issue, "acme", "widgets", 7)

        assert res.status_code == HttpStatusCode.FORBIDDEN.value
        assert calls["n"] == 1
        no_sleep.assert_not_awaited()


class TestStreamReportsRateLimitNotAccessDenied:
    @staticmethod
    def _ticket() -> TicketRecord:
        return TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )

    async def test_rate_limited_issue_streams_as_429(self, no_sleep: AsyncMock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repositories/42":
                return httpx.Response(200, json={"id": 42, "name": "widgets", "owner": {"login": "acme"}})
            return _primary_limit(reset_in=1800)

        c, _runtime = _runtime_over(_data_source(handler))
        with pytest.raises(HTTPException) as exc:
            await IssuesSync(c).build_ticket_blocks(self._ticket())

        assert exc.value.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        assert "permission" not in str(exc.value.detail).lower()

    async def test_denied_issue_still_streams_as_403(self, no_sleep: AsyncMock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repositories/42":
                return httpx.Response(200, json={"id": 42, "name": "widgets", "owner": {"login": "acme"}})
            return httpx.Response(403, json={"message": DENIED_MESSAGE})

        c, _runtime = _runtime_over(_data_source(handler))
        with pytest.raises(HTTPException) as exc:
            await IssuesSync(c).build_ticket_blocks(self._ticket())

        assert exc.value.status_code == HttpStatusCode.FORBIDDEN.value
