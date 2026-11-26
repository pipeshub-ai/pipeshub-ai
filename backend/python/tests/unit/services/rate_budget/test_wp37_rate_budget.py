"""WP-37 RATEBUDGET (G-31, C20.32): the shared per-(org, vendor) budget itself, through the Redis provider seam."""

from __future__ import annotations

import asyncio

import pytest

from app.services.rate_budget import (
    AccountBudget,
    BudgetLimits,
    RateAccount,
    RateBudgetWait,
    RatePriority,
    RedisRateBudget,
    account_budget,
    account_scope,
    budgeted_call,
    family_for_connector,
    family_for_toolset,
    limits_for,
    note_throttled,
    priority_for_run_kind,
    rate_priority,
    set_shared_budget,
)
from harness_core.effects.failures import retry_after_s, tool_error_code
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

ORG = "org-1"
SLOW = BudgetLimits(rate=0.01, burst=4)


def _budget(provider: FakeRedisConnectionProvider | None = None) -> RedisRateBudget:
    return RedisRateBudget(provider or FakeRedisConnectionProvider())


async def _takes(budget: RedisRateBudget, account: RateAccount, priority: RatePriority, limits=SLOW) -> int:
    taken = 0
    while await budget.take(account, limits, priority) == 0:
        taken += 1
        if taken > 100:
            raise AssertionError("the bucket never ran dry")
    return taken


async def test_wp37_a_bucket_grants_its_burst_then_names_the_wait() -> None:
    budget = _budget()
    account = RateAccount(ORG, "slack")
    assert await _takes(budget, account, RatePriority.INTERACTIVE) == 4
    wait = await budget.take(account, BudgetLimits(rate=2, burst=4), RatePriority.INTERACTIVE)
    assert 0 < wait <= 0.5 + 0.01


async def test_wp37_ga17_sync_leaves_a_quarter_and_background_half_for_interactive_calls() -> None:
    budget = _budget()
    sync, background = RateAccount(ORG, "slack"), RateAccount(ORG, "notion")
    assert await _takes(budget, sync, RatePriority.SYNC) == 3
    assert await budget.take(sync, SLOW, RatePriority.INTERACTIVE) == 0
    assert await _takes(budget, background, RatePriority.BACKGROUND) == 2
    assert await budget.take(background, SLOW, RatePriority.SYNC) == 0
    assert await budget.take(background, SLOW, RatePriority.INTERACTIVE) == 0


async def test_wp37_ga17_a_waiting_sync_is_owed_its_floor_however_busy_the_agents_are() -> None:
    budget = _budget()
    account = RateAccount(ORG, "slack")
    limits = BudgetLimits(rate=10, burst=20)
    assert await _takes(budget, account, RatePriority.INTERACTIVE, limits) == 20
    refused = await budget.take(account, limits, RatePriority.SYNC)
    # Credit at the sync floor (3/s) comes before the bucket refills above the sync reserve (5 tokens).
    assert 0 < refused <= 1 / limits.floor(RatePriority.SYNC) + 0.01
    await asyncio.sleep(refused + 0.05)
    assert await _takes(budget, account, RatePriority.INTERACTIVE, limits) >= 1
    assert await budget.take(account, limits, RatePriority.SYNC) == 0, "the agents left the waiting sync its token"


def test_wp37_floors_and_reserves_by_class() -> None:
    assert (SLOW.reserve(RatePriority.INTERACTIVE), SLOW.reserve(RatePriority.SYNC), SLOW.reserve(RatePriority.BACKGROUND)) == (0, 1, 2)
    limits = BudgetLimits(rate=10, burst=4)
    assert (limits.floor(RatePriority.INTERACTIVE), limits.floor(RatePriority.SYNC), limits.floor(RatePriority.BACKGROUND)) == (0, 3, 1)


async def test_wp37_ga17_one_pause_holds_every_process_of_the_account() -> None:
    provider = FakeRedisConnectionProvider()
    connectors, query = _budget(provider), RedisRateBudget(provider)
    account = RateAccount(ORG, "slack")
    await query.pause(account, 30)
    wait = await connectors.take(account, SLOW, RatePriority.INTERACTIVE)
    assert 29 < wait <= 30
    await connectors.pause(account, 1)
    assert await query.take(account, SLOW, RatePriority.INTERACTIVE) > 29, "a shorter pause must not cut a longer one"


async def test_wp37_accounts_are_per_org_and_family_under_the_key_namespace() -> None:
    provider = FakeRedisConnectionProvider(key_namespace="tenant")
    budget = _budget(provider)
    assert await _takes(budget, RateAccount(ORG, "slack"), RatePriority.INTERACTIVE) == 4
    assert await budget.take(RateAccount("org-2", "slack"), SLOW, RatePriority.INTERACTIVE) == 0
    assert await budget.take(RateAccount(ORG, "notion"), SLOW, RatePriority.INTERACTIVE) == 0
    keys = sorted([key async for key in provider.scan_keys("*")])
    assert keys == [
        "tenant:pipeshub:ratebudget:org-1:notion",
        "tenant:pipeshub:ratebudget:org-1:slack",
        "tenant:pipeshub:ratebudget:org-2:slack",
    ]


async def test_wp37_redis_down_fails_open_and_is_not_asked_on_every_call() -> None:
    provider = FakeRedisConnectionProvider()
    calls = 0

    async def broken(body: str) -> str:
        nonlocal calls
        calls += 1
        raise ConnectionError("redis is down")

    provider.load_script = broken  # type: ignore[method-assign]
    budget = _budget(provider)
    account = RateAccount(ORG, "slack")
    assert await budget.take(account, SLOW, RatePriority.SYNC) == 0
    assert await budget.take(account, SLOW, RatePriority.SYNC) == 0
    await budget.pause(account, 5)
    assert calls == 1


def test_wp37_sync_and_toolset_names_meet_in_one_family() -> None:
    assert family_for_connector("Slack") == family_for_connector("Slack Workspace") == family_for_toolset("slack")
    assert family_for_connector("Notion Personal") == family_for_toolset("notion") == "notion"
    assert family_for_connector("Jira Data Center") is None
    assert family_for_toolset("github") is None
    assert family_for_toolset(None) is None


def test_wp37_run_kinds_map_to_priority() -> None:
    for kind in ("interactive", "authoring", "command", "child", None):
        assert priority_for_run_kind(kind) is RatePriority.INTERACTIVE
    for kind in ("scheduled", "workflow_code"):
        assert priority_for_run_kind(kind) is RatePriority.BACKGROUND


def test_wp37_limits_can_be_overridden_and_a_bad_override_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    default = limits_for("slack")
    monkeypatch.setenv("CONNECTOR_RATE_BUDGETS", '{"slack": {"rate": 1, "burst": 3}}')
    assert limits_for("slack") == BudgetLimits(rate=1, burst=3)
    monkeypatch.setenv("CONNECTOR_RATE_BUDGETS", "not json")
    assert limits_for("slack") == default


def test_wp37_budgets_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    set_shared_budget(_budget())
    try:
        assert account_budget(ORG, "slack") is not None
        assert account_budget(ORG, None) is None
        assert account_budget("", "slack") is None
        monkeypatch.setenv("CONNECTOR_RATE_BUDGET_ENABLED", "false")
        assert account_budget(ORG, "slack") is None
    finally:
        set_shared_budget(None)


async def test_wp37_an_agent_call_that_would_wait_too_long_answers_rate_limited() -> None:
    handle = AccountBudget(_budget(), RateAccount(ORG, "slack"), BudgetLimits(rate=0.5, burst=1))
    assert await handle.acquire(RatePriority.INTERACTIVE, max_wait_s=1) == 0
    with pytest.raises(RateBudgetWait) as raised:
        _ = await handle.acquire(RatePriority.INTERACTIVE, max_wait_s=1)
    assert tool_error_code(raised.value) == "rate_limited"
    assert 1 < (retry_after_s(raised.value) or 0) <= 2


class _Throttled(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__("slow down")
        self.response = type("R", (), {"headers": {"Retry-After": "20"}})()


async def test_wp37_ga17_a_throttled_agent_call_pauses_the_account_for_sync_too() -> None:
    budget = _budget()
    handle = AccountBudget(budget, RateAccount(ORG, "slack"), SLOW)
    with pytest.raises(_Throttled):
        async with budgeted_call(handle, RatePriority.INTERACTIVE, max_wait_s=5):
            raise _Throttled
    assert 19 < await budget.take(handle.account, SLOW, RatePriority.SYNC) <= 20


async def test_wp37_code_below_a_call_reports_a_throttle_to_the_account_in_scope() -> None:
    budget = _budget()
    handle = AccountBudget(budget, RateAccount(ORG, "slack"), SLOW)
    await note_throttled(9)
    assert await budget.take(handle.account, SLOW, RatePriority.INTERACTIVE) == 0
    with account_scope(handle), rate_priority(RatePriority.SYNC):
        await note_throttled(9)
    assert await budget.take(handle.account, SLOW, RatePriority.INTERACTIVE) > 8
