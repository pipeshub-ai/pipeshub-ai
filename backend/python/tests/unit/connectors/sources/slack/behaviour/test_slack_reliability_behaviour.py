"""Tokens, rate limits and outages in the Slack Workspace connector, over a fake Slack workspace.

Waits are recorded rather than slept (see conftest ``waits``), so a test can
check how long the connector would have paused without actually pausing.
"""

import inspect
import logging
from unittest.mock import MagicMock

import pytest
import slack_sdk
from slack_behaviour_fakes import (
    BOT_TOKEN,
    FakeConfigService,
    RateLimited,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    GENERAL,
    SECRET,
    WORKSPACE_CONNECTOR_ID,
    standard_workspace,
    workspace_config,
    workspace_connector,
)

from app.connectors.sources.slack.team.connector import SlackConnector
from app.sources.client.slack.slack import (
    SlackClient,
    SlackRESTClientViaToken,
    SlackTokenConfig,
)
from app.sources.external.slack.slack import SlackDataSource


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


class TestRealSlackClient:
    async def test_the_real_slack_sdk_is_under_test(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        web_client = connector.external_client.get_web_client()
        assert not isinstance(slack_sdk, MagicMock)
        assert inspect.isclass(slack_sdk.WebClient)
        assert "site-packages" in inspect.getfile(slack_sdk.WebClient)
        assert type(web_client) is slack_sdk.WebClient
        assert type(connector.data_source) is SlackDataSource

    async def test_init_learns_the_workspace_so_links_point_at_it(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(10)
        workspace.post(GENERAL, ts, ALICE, "link me")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.records[ts].weburl == f"https://acme.slack.com/archives/{GENERAL}/p{ts.replace('.', '')}"


class TestTokenFormats:
    @pytest.mark.parametrize("token", ["xoxb-classic", "xoxp-classic", "xoxe.xoxb-1-rotating", "xoxe.xoxp-1-rotating"])
    def test_bot_and_user_access_tokens_are_accepted(self, token: str) -> None:
        client = SlackRESTClientViaToken(token)

        assert client.get_token() == token
        assert type(client.get_web_client()) is slack_sdk.WebClient

    @pytest.mark.parametrize("token", ["xoxe-1-refresh-token", "not-a-slack-token"])
    def test_a_refresh_token_or_other_text_is_refused_with_a_next_step(self, token: str) -> None:
        with pytest.raises(ValueError, match="Paste a bot token") as err:
            SlackRESTClientViaToken(token)
        assert "xoxe.xoxb-" in str(err.value)
        assert "xoxe.xoxp-" in str(err.value)

    def test_a_refresh_token_cannot_replace_a_working_token(self) -> None:
        client = SlackRESTClientViaToken(BOT_TOKEN)

        with pytest.raises(ValueError, match="Invalid Slack token format"):
            client.set_token("xoxe-1-refresh-token")
        assert client.get_token() == BOT_TOKEN

    async def test_a_refresh_token_written_into_config_is_never_sent_to_slack(self, workspace, store, checkpoints) -> None:
        connector, config_service = await workspace_connector(store, checkpoints)
        config_service.config["auth"]["apiToken"] = "xoxe-1-refresh-token"

        with pytest.raises(ValueError, match="Invalid Slack token format"):
            await connector.run_sync()
        assert "xoxe-1-refresh-token" not in {c.token for c in workspace.calls}


class TestTokens:
    async def test_a_rotated_token_is_used_from_the_next_call_on(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0ROT", "rot.txt", b"rotated", mimetype="text/plain", filetype="text")
        workspace.post(GENERAL, ts_minutes_ago(10), ALICE, "file", files=[fd])
        connector, config_service = await workspace_connector(store, checkpoints)
        calls_before = len(workspace.calls)

        workspace.valid_tokens = {"xoxb-bot-token-2"}
        config_service.config["auth"]["apiToken"] = "xoxb-bot-token-2"
        await connector.run_sync()

        assert {c.token for c in workspace.calls[calls_before:]} == {"xoxb-bot-token-2"}
        assert workspace.downloads_seen[-1].headers["authorization"] == "Bearer xoxb-bot-token-2"
        assert "F0ROT" in store.records

    async def test_a_revoked_token_leaves_everything_stored_as_it_was(self, workspace, store, checkpoints) -> None:
        workspace.post(SECRET, ts_minutes_ago(10), ALICE, "kept")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        access = {k: list(v) for k, v in store.group_access.items()}
        role_members = list(store.roles["workspace_member"][1])
        records = dict(store.records)

        workspace.valid_tokens = set()
        await connector.run_sync()

        assert store.group_access == access
        assert store.roles["workspace_member"][1] == role_members
        assert store.records == records
        assert await connector.test_connection_and_access() is False

    async def test_a_token_that_is_not_a_slack_token_is_refused_at_setup(self, workspace, store, checkpoints) -> None:
        config_service = FakeConfigService(WORKSPACE_CONNECTOR_ID, workspace_config(token="not-a-slack-token"))
        connector = SlackConnector(
            logging.getLogger("test.slack_workspace"), store, checkpoints, config_service,
            WORKSPACE_CONNECTOR_ID, "team", "creator-1",
        )

        assert await connector.init() is False
        assert workspace.calls == []


class TestRateLimits:
    async def test_a_429_reaches_the_connector_with_its_retry_after(self, workspace) -> None:
        workspace.fail("conversations.history", RateLimited(42))
        data_source = SlackDataSource(SlackClient.build_with_config(SlackTokenConfig(token=BOT_TOKEN)))

        response = await data_source.conversations_history(channel=GENERAL)

        assert (response.success, response.error, response.status_code, response.retry_after) == (
            False, "ratelimited", 429, "42",
        )

    @pytest.mark.xfail(strict=True, reason=(
        "A 429 from Slack is not retried: the connector ignores Retry-After, gives up on the "
        "channel for this run and, on a later page, also moves the checkpoint past unread messages."
    ))
    async def test_a_rate_limited_read_is_retried_after_the_wait_slack_asks_for(self, workspace, store, checkpoints, waits) -> None:
        ts = ts_minutes_ago(10)
        workspace.post(GENERAL, ts, ALICE, "eventually")
        workspace.fail("conversations.history", RateLimited(30), when=lambda p: p.get("channel") == GENERAL)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert ts in store.records
        assert 30 in waits

    async def test_tier_two_methods_pause_once_the_per_minute_budget_is_spent(self, workspace, store, checkpoints, waits) -> None:
        for n in range(15):
            workspace.add_user(f"U0EXTRA{n:02d}", f"extra{n}@acme.com", f"Extra {n}")
        workspace.page_size["users.list"] = 1
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert len(workspace.calls_to("users.list")) == 20
        assert waits, "20 users.list pages exceed Slack's tier-2 budget and must pause"
        assert 50 < waits[0] <= 60
        assert len(store.roles["workspace_member"][1]) == 17

    async def test_tier_four_calls_have_their_own_budget(self, workspace, store, checkpoints, waits) -> None:
        for n in range(60):
            uid = f"U0MEMBER{n:02d}"
            workspace.add_user(uid, f"member{n}@acme.com", f"Member {n}")
            workspace.members[SECRET].append(uid)
        workspace.page_size["conversations.members"] = 1
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert len([c for c in workspace.calls_to("conversations.members") if c.params["channel"] == SECRET]) == 62
        assert waits == []
        assert len(store.access_emails(SECRET)) == 62
