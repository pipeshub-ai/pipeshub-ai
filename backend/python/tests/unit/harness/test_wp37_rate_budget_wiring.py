"""WP-37 RATEBUDGET (G-31, C20.32): who spends the shared vendor budget, at which priority, and who arms its pause.

Agent toolset calls (classic chat and harness runs, via ``ToolInstanceCreator.call_scope``), connector sync
(``run_sync_task``), indexing fetches (``streaming_for_indexing``), ``ResiliencePolicy`` connectors and Slack's tier
limiter all take from one ``AccountBudget`` per (org, vendor family); a 429 any of them meets pauses the rest.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.instance_creator import (
    AGENT_BUDGET_MAX_WAIT_S,
    ToolInstanceCreator,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.connector.stream_purpose import streaming_for_indexing
from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.sources.slack.team.connector import RateLimiter, Tier
from app.services.rate_budget import (
    AccountBudget,
    BudgetLimits,
    IRateBudget,
    RateAccount,
    RatePriority,
    RedisRateBudget,
    account_scope,
    current_priority,
    set_shared_budget,
)
from app.services.rate_budget.budget import _account
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.slack.slack import SlackDataSource
from harness_core.effects.failures import retry_after_s, tool_error_code
from harness_core.tools.decorators import TOOL_META_ATTR, BoundMethodTool, tool
from harness_core.tools.toolset import ToolsetBuilder
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"


class RecordingBudget(IRateBudget):
    def __init__(self) -> None:
        self.takes: list[tuple[RateAccount, RatePriority]] = []
        self.pauses: list[tuple[RateAccount, float]] = []

    async def take(self, account: RateAccount, limits: BudgetLimits, priority: RatePriority) -> float:
        self.takes.append((account, priority))
        return 0.0

    async def pause(self, account: RateAccount, seconds: float) -> None:
        self.pauses.append((account, seconds))


@pytest.fixture
def recording() -> Iterator[RecordingBudget]:
    budget = RecordingBudget()
    set_shared_budget(budget)
    yield budget
    set_shared_budget(None)


def _creator(org_id: str = ORG, rate_priority: str = "interactive") -> ToolInstanceCreator:
    context = SimpleNamespace(
        logger=logging.getLogger("test"),
        config_service=None,
        tool_to_toolset_map={},
        toolset_configs={},
        agent_toolsets=[],
        user_id="u1",
        tool_state={},
        org_id=org_id,
        rate_priority=rate_priority,
    )
    return ToolInstanceCreator(context)  # type: ignore[arg-type]


class _SlackActions:
    def __init__(self) -> None:
        self.seen: list[tuple[RatePriority, AccountBudget | None]] = []

    @tool(path="/toolsets/slack/read_channel", short_description="Read a channel", description="Read a channel")
    async def read_channel(self) -> str:
        self.seen.append((current_priority(RatePriority.SYNC), _account.get()))
        return "ok"


_META = getattr(_SlackActions.read_channel, TOOL_META_ATTR)


async def test_wp37_ga17_an_agent_slack_call_takes_from_the_org_budget_at_its_runs_priority(recording) -> None:
    actions = _SlackActions()
    toolset = ToolsetBuilder(
        actions,
        name="slack",
        description="Slack",
        path_prefix="/toolsets/slack",
        call_scope=_creator(rate_priority="background").call_scope("slack"),
    )
    [read] = toolset.tools
    _ = await read.execute()
    assert recording.takes == [(RateAccount(ORG, "slack"), RatePriority.BACKGROUND)]
    [(priority, account)] = actions.seen
    assert priority is RatePriority.BACKGROUND
    assert account is not None and account.account == RateAccount(ORG, "slack")
    assert _account.get() is None


def test_wp37_a_toolset_without_a_shared_vendor_budget_has_no_scope(recording, monkeypatch) -> None:
    assert _creator().call_scope("github") is None
    assert _creator(org_id="").call_scope("slack") is None
    monkeypatch.setenv("CONNECTOR_RATE_BUDGET_ENABLED", "off")
    assert _creator().call_scope("slack") is None


async def test_wp37_the_gate_runs_before_the_budget_is_spent(recording) -> None:
    order: list[str] = []

    async def gate() -> None:
        order.append("gate")
        raise RuntimeError("credential gone")

    async def method() -> str:
        order.append("call")
        return "never"

    scope = _creator().call_scope("slack")
    bound = BoundMethodTool(method, _META, gate=gate, call_scope=scope)
    with pytest.raises(RuntimeError):
        _ = await bound.execute()
    assert order == ["gate"]
    assert recording.takes == []


async def test_wp37_an_agent_call_past_its_wait_answers_rate_limited_and_never_reaches_the_vendor() -> None:
    provider = FakeRedisConnectionProvider()
    set_shared_budget(RedisRateBudget(provider))
    try:
        await RedisRateBudget(provider).pause(RateAccount(ORG, "slack"), AGENT_BUDGET_MAX_WAIT_S + 30)
        reached: list[bool] = []

        async def method() -> str:
            reached.append(True)
            return "sent"

        bound = BoundMethodTool(
            method, _META, call_scope=_creator().call_scope("slack")
        )
        with pytest.raises(Exception) as raised:
            _ = await bound.execute()
        assert tool_error_code(raised.value) == "rate_limited"
        assert (retry_after_s(raised.value) or 0) > AGENT_BUDGET_MAX_WAIT_S
        assert reached == []
    finally:
        set_shared_budget(None)


def _connector(name: str, org_id: object) -> BaseConnector:
    cls = type("FakeConnector", (BaseConnector,), {})
    cls.__abstractmethods__ = frozenset()
    app = MagicMock()
    app.get_app_name.return_value = name
    return cls(app, logging.getLogger("t"), SimpleNamespace(org_id=org_id), MagicMock(), MagicMock(), "c1", "team", "u1")


def test_wp37_a_connector_names_its_orgs_budget_and_hands_it_to_its_resilience_policy(recording) -> None:
    slack = _connector("Slack Workspace", ORG)
    assert slack.rate_budget is not None and slack.rate_budget.account == RateAccount(ORG, "slack")
    assert slack.rate_budget is slack.rate_budget
    notion = _connector("Notion", ORG)
    notion.__class__._connector_metadata = {"resilienceConfig": {"rate_limit": 3}}
    assert notion.resilience is not None and notion.resilience.shared is notion.rate_budget
    assert _connector("Jira Data Center", ORG).rate_budget is None
    assert _connector("Slack", MagicMock()).rate_budget is None


async def test_wp37_ga17_a_sync_spends_its_connectors_budget_at_sync_priority(recording) -> None:
    connector = _connector("Slack", ORG)
    seen: list[tuple[RatePriority, AccountBudget | None]] = []
    connector.run_sync = AsyncMock(side_effect=lambda: seen.append((current_priority(), _account.get())))  # type: ignore[method-assign]
    await run_sync_task(connector, "c1", AsyncMock(), logging.getLogger("test"))
    assert seen == [(RatePriority.SYNC, connector.rate_budget)]
    assert current_priority() is RatePriority.INTERACTIVE


def test_wp37_ga17_indexing_fetches_run_at_sync_priority() -> None:
    with streaming_for_indexing():
        assert current_priority() is RatePriority.SYNC
    assert current_priority() is RatePriority.INTERACTIVE


async def test_wp37_a_resilience_policy_takes_a_shared_token_and_shares_its_pause(recording) -> None:
    policy = ResiliencePolicy(rate_limit=100, base_delay=0.01, max_delay=5)
    handle = AccountBudget(recording, RateAccount(ORG, "notion"), BudgetLimits(rate=2.5, burst=6))
    policy.shared = handle
    await policy.acquire()
    assert [account for account, _ in recording.takes] == [handle.account]
    policy.pause(30)
    await asyncio.sleep(0)
    assert recording.pauses == [(handle.account, 5)]


async def test_wp37_slacks_tier_limiter_also_takes_from_the_shared_budget(recording) -> None:
    handle = AccountBudget(recording, RateAccount(ORG, "slack"), BudgetLimits(rate=2.5, burst=10))
    limiter = RateLimiter(shared=handle)
    with account_scope(None):
        await limiter.acquire(Tier.T3)
    assert len(recording.takes) == 1


class _SlackApiError(Exception):
    def __init__(self) -> None:
        super().__init__("ratelimited")
        self.response = SimpleNamespace(status_code=429, headers={"Retry-After": "12"}, data={"error": "ratelimited"})


async def test_wp37_ga17_a_429_slack_answers_pauses_the_account_of_the_caller(recording) -> None:
    client = MagicMock()
    client.get_web_client.return_value = MagicMock()
    source = SlackDataSource(client)
    handle = AccountBudget(recording, RateAccount(ORG, "slack"), BudgetLimits(rate=2.5, burst=10))
    with account_scope(handle):
        response = await source._handle_slack_error(_SlackApiError())
    assert response.status_code == 429
    assert recording.pauses == [(handle.account, 12.0)]
    _ = await source._handle_slack_error(_SlackApiError())
    assert len(recording.pauses) == 1


@pytest.mark.parametrize(("run_kind", "priority"), [("interactive", "interactive"), ("scheduled", "background")])
async def test_wp37_ga17_a_harness_run_spends_at_its_run_kinds_priority(
    monkeypatch: pytest.MonkeyPatch, run_kind: str, priority: str
) -> None:
    from app.harness.assembly import assembler
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.assembly.test_assembler import JIRA_SEARCH, _port

    seen: list[str] = []
    original = assembler.load_run_tools

    async def spy(*args: object, **kwargs: object) -> object:
        tools = await original(*args, **kwargs)  # type: ignore[arg-type]
        seen.append(args[2].context.rate_priority)  # type: ignore[attr-defined]
        return tools

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    spec = w.pinned_spec(tool_ids=[JIRA_SEARCH]).model_copy(update={"run_kind": run_kind})
    with w.deployment():
        built = assembler.PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(_port()))
        _ = await built.build(w.claim(spec))  # type: ignore[arg-type]
    assert seen == [priority]
