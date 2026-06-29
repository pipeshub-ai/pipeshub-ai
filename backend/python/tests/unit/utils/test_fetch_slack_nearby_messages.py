"""Unit tests for app.utils.fetch_slack_nearby_messages."""

# ruff: noqa: ANN201, ANN202, ANN401

from __future__ import annotations

import importlib
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py may register a MagicMock for ``langchain_core`` when the package is
# absent, which breaks ``@tool`` on fetch_slack_nearby_messages. Provide a stub.
_LC_SHIMMED = False
if isinstance(sys.modules.get("langchain_core"), MagicMock):

    def _minimal_lc_tool(tool_name: str, args_schema: Any = None) -> object:
        def _decorator(fn: Any) -> object:
            class _AsyncToolShim:
                name = tool_name

                async def ainvoke(
                    self, tool_input: dict[str, Any], config: Any = None,
                ) -> Any:
                    return await fn(**tool_input)

            return _AsyncToolShim()

        return _decorator

    _lc_pkg = types.ModuleType("langchain_core")
    _lc_tools = types.ModuleType("langchain_core.tools")
    _lc_tools.tool = _minimal_lc_tool
    _lc_pkg.tools = _lc_tools
    sys.modules["langchain_core"] = _lc_pkg
    sys.modules["langchain_core.tools"] = _lc_tools
    _LC_SHIMMED = True

if _LC_SHIMMED and "app.utils.fetch_slack_nearby_messages" in sys.modules:
    fsn = importlib.reload(sys.modules["app.utils.fetch_slack_nearby_messages"])
else:
    from app.utils import fetch_slack_nearby_messages as fsn


def _slack_data_source_mock(**methods: Any) -> AsyncMock:
    """AsyncMock SlackDataSource with default team_info for weburl resolution."""
    mock_ds = AsyncMock()
    mock_ds.team_info = AsyncMock(
        return_value=MagicMock(
            success=True,
            data={"team": {"domain": "acme", "id": "T1"}},
        )
    )
    mock_ds.auth_test = AsyncMock(
        return_value=MagicMock(success=True, data={"team_id": "T1"})
    )
    for name, impl in methods.items():
        setattr(mock_ds, name, impl)
    return mock_ds



class TestIsoTimestampParsing:
    def test_iso_to_slack_ts_utc_z(self):
        assert fsn._iso_to_slack_ts("2023-11-14T22:13:20Z") == "1700000000.000000"

    def test_iso_to_slack_ts_with_offset(self):
        # 22:13:20 +05:30 == 16:43:20 UTC; same instant as 1699980200 epoch seconds
        assert fsn._iso_to_slack_ts("2023-11-14T22:13:20+05:30") == "1699980200.000000"

    def test_naive_timestamp_defaults_to_utc(self):
        assert fsn._iso_to_slack_ts("2023-11-14T22:13:20") == "1700000000.000000"

    def test_naive_timestamp_uses_timezone_param(self):
        # 22:13 EST (UTC-5 on 2023-11-14) == 03:13 UTC next day
        assert (
            fsn._iso_to_slack_ts("2023-11-14T22:13:20", "America/New_York")
            == "1700018000.000000"
        )

    def test_invalid_timestamp_raises(self):
        with pytest.raises(fsn.SlackTimestampError):
            fsn._iso_to_slack_ts("not-a-date")

    def test_invalid_timezone_raises(self):
        with pytest.raises(fsn.SlackTimestampError):
            fsn._iso_to_slack_ts("2023-11-14T22:13:20", "Not/A/Zone")


class TestSlackTsToIso:
    def test_converts_slack_ts(self):
        assert fsn._slack_ts_to_iso("1700000000.000000") == "2023-11-14T22:13:20Z"

    def test_invalid_ts_returns_none(self):
        assert fsn._slack_ts_to_iso("not-a-ts") is None
        assert fsn._slack_ts_to_iso("") is None

    def test_non_positive_ts_returns_none(self):
        assert fsn._slack_ts_to_iso("0") is None
        assert fsn._slack_ts_to_iso("-1.0") is None

    def test_fromtimestamp_failure_returns_none(self):
        # Platform overflow on extreme epoch seconds (no mock: datetime is immutable in 3.12+)
        assert fsn._slack_ts_to_iso("1e100") is None


class TestParseIsoTimestamp:
    def test_empty_timestamp_raises(self):
        with pytest.raises(fsn.SlackTimestampError, match="timestamp is required"):
            fsn._parse_iso_timestamp("")
        with pytest.raises(fsn.SlackTimestampError, match="timestamp is required"):
            fsn._parse_iso_timestamp("   ")

    def test_non_string_raises(self):
        with pytest.raises(fsn.SlackTimestampError):
            fsn._parse_iso_timestamp(None)  # type: ignore[arg-type]


class TestSlackMessageWeburl:
    def test_workspace_domain_archive_link(self):
        url = fsn._slack_message_weburl(
            "C123",
            "1000.000100",
            workspace_domain="acme",
            team_id="T1",
        )
        assert url == "https://acme.slack.com/archives/C123/p1000000100"

    def test_client_url_without_workspace_domain(self):
        url = fsn._slack_message_weburl("C123", "1000.000100", team_id="T99")
        assert url == "https://app.slack.com/client/T99/C123/p1000000100"

class TestCoerceMessageLimit:
    def test_default_invalid_input(self):
        assert fsn._coerce_message_limit("bad") == fsn._DEFAULT_NEARBY_MESSAGE_LIMIT

    def test_clamps_high(self):
        assert fsn._coerce_message_limit(999) == fsn._MAX_NEARBY_MESSAGE_LIMIT


class TestSlackNearbyApiParams:
    def test_before_uses_latest_and_limit(self):
        params = fsn._slack_nearby_api_params(
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
        )
        assert params.model_dump(exclude_none=True) == {
            "inclusive": True,
            "limit": 5,
            "latest": "1000.000000",
        }

    def test_after_uses_oldest_and_limit(self):
        params = fsn._slack_nearby_api_params(
            anchor_ts="1060.000000",
            direction="after",
            message_limit=10,
        )
        assert params.model_dump(exclude_none=True) == {
            "inclusive": True,
            "limit": 10,
            "oldest": "1060.000000",
        }


class TestReplaceMentionsInText:
    def test_replaces_user_mentions_from_cache(self):
        text = "<@U09MVJQF2GJ> Did you test <@U09F230NQL9>"
        out = fsn._replace_mentions_in_text(
            text,
            {"U09MVJQF2GJ": "Pat", "U09F230NQL9": "Sam"},
        )
        assert out == "@Pat Did you test @Sam"

    def test_uses_embedded_display_name_in_mention(self):
        text = "Hi <@U123|Nick>"
        out = fsn._replace_mentions_in_text(text, {})
        assert out == "Hi @Nick"

    def test_replaces_broadcast_mentions(self):
        text = "<!channel> meet at 12."
        assert fsn._replace_mentions_in_text(text, {}) == "@channel meet at 12."

    def test_empty_text_unchanged(self):
        assert fsn._replace_mentions_in_text("", {}) == ""

    def test_unknown_user_falls_back_to_id(self):
        assert fsn._replace_mentions_in_text("<@U999>", {}) == "@U999"

    def test_extract_user_mention_ids_empty(self):
        assert fsn._extract_user_mention_ids_from_text("") == set()
        assert fsn._extract_user_mention_ids_from_text("no mentions") == set()

    def test_extract_user_mention_ids(self):
        text = "<@U1> and <@W2|ext> plain"
        assert fsn._extract_user_mention_ids_from_text(text) == {"U1", "W2"}


class TestNextAnchorIsoTimestamp:
    def test_before_returns_oldest_message(self):
        messages = [
            fsn.SlackNearbyFormattedMessage(
                iso_timestamp="2023-11-14T22:10:00Z",
                channel_id="C123",
                weburl="https://example.com",
            ),
            fsn.SlackNearbyFormattedMessage(
                iso_timestamp="2023-11-14T22:12:00Z",
                channel_id="C123",
                weburl="https://example.com",
            ),
        ]
        assert (
            fsn._new_anchor_iso_timestamp(messages, "before")
            == "2023-11-14T22:10:00Z"
        )

    def test_after_returns_newest_message(self):
        messages = [
            fsn.SlackNearbyFormattedMessage(
                iso_timestamp="2023-11-14T22:10:00Z",
                channel_id="C123",
                weburl="https://example.com",
            ),
            fsn.SlackNearbyFormattedMessage(
                iso_timestamp="2023-11-14T22:12:00Z",
                channel_id="C123",
                weburl="https://example.com",
            ),
        ]
        assert (
            fsn._new_anchor_iso_timestamp(messages, "after")
            == "2023-11-14T22:12:00Z"
        )

    def test_empty_messages_returns_none(self):
        assert fsn._new_anchor_iso_timestamp([], "before") is None


@pytest.mark.asyncio
class TestResolveUserDisplayNames:
    async def test_skips_non_user_ids(self):
        mock_ds = _slack_data_source_mock()
        out = await fsn._resolve_user_display_names(mock_ds, {"W123", ""})
        assert out == {}
        mock_ds.users_info.assert_not_awaited()

    async def test_users_info_exception_continues(self):
        mock_ds = _slack_data_source_mock(
            users_info=AsyncMock(side_effect=RuntimeError("api down")),
        )
        out = await fsn._resolve_user_display_names(mock_ds, {"U1"})
        assert out == {}

    async def test_failed_response_skipped(self):
        mock_ds = _slack_data_source_mock(
            users_info=AsyncMock(return_value=MagicMock(success=False, data=None)),
        )
        assert await fsn._resolve_user_display_names(mock_ds, {"U1"}) == {}

    async def test_real_name_and_name_fallbacks(self):
        mock_ds = _slack_data_source_mock(
            users_info=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"user": {"profile": {}, "name": "slackname"}},
                )
            ),
        )
        assert await fsn._resolve_user_display_names(mock_ds, {"U1"}) == {
            "U1": "slackname",
        }

    async def test_invalid_user_payload_skipped(self):
        mock_ds = _slack_data_source_mock(
            users_info=AsyncMock(
                return_value=MagicMock(success=True, data={"user": "not-a-dict"}),
            ),
        )
        assert await fsn._resolve_user_display_names(mock_ds, {"U1"}) == {}


@pytest.mark.asyncio
class TestResolveSlackWorkspaceInfo:
    async def test_team_info_exception_falls_back_to_auth_test(self):
        mock_ds = _slack_data_source_mock(
            team_info=AsyncMock(side_effect=RuntimeError("team.info")),
            auth_test=AsyncMock(
                return_value=MagicMock(success=True, data={"team_id": "T2"}),
            ),
        )
        domain, team_id = await fsn._resolve_slack_workspace_info(mock_ds)
        assert domain is None
        assert team_id == "T2"

    async def test_auth_test_used_when_team_info_has_no_id(self):
        mock_ds = _slack_data_source_mock(
            team_info=AsyncMock(
                return_value=MagicMock(success=True, data={"team": {}}),
            ),
            auth_test=AsyncMock(
                return_value=MagicMock(success=True, data={"team_id": "T3"}),
            ),
        )
        domain, team_id = await fsn._resolve_slack_workspace_info(mock_ds)
        assert domain is None
        assert team_id == "T3"

    async def test_auth_test_exception_returns_partial_team_info(self):
        mock_ds = _slack_data_source_mock(
            team_info=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"team": {"domain": "acme"}},
                ),
            ),
            auth_test=AsyncMock(side_effect=RuntimeError("auth")),
        )
        domain, team_id = await fsn._resolve_slack_workspace_info(mock_ds)
        assert domain == "acme"
        assert team_id is None


@pytest.mark.asyncio
class TestFetchNearbyMessagesImpl:
    async def test_missing_config_service(self):
        out = await fsn._fetch_nearby_messages_impl(
            "2023-11-14T22:13:20Z",
            "before",
            "C123",
            "conn-1",
            config_service=None,
        )
        assert out.ok is False

    async def test_missing_connector_id(self):
        config = MagicMock()
        out = await fsn._fetch_nearby_messages_impl(
            "2023-11-14T22:13:20Z",
            "before",
            "C123",
            "",
            config_service=config,
        )
        assert out.ok is False
        assert "connector_id" in out.error

    async def test_before_channel_history(self):
        config = MagicMock()
        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={
                        "messages": [
                            {"ts": "900.000000", "text": "earlier", "user": "U1"},
                        ]
                    },
                )
            ),
            users_info=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"user": {"profile": {"display_name": "Alice"}}},
                )
            ),
        )

        with patch.object(fsn, "SlackClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(return_value=MagicMock())
            with patch.object(fsn, "SlackDataSource", return_value=mock_ds):
                out = await fsn._fetch_nearby_messages_impl(
                    "2023-11-14T22:13:20Z",
                    "before",
                    "C123",
                    "conn-1",
                    config_service=config,
                )

        assert out.ok is True, getattr(out, "error", None)
        assert out.anchor_iso_timestamp == "2023-11-14T22:13:20Z"
        assert out.new_anchor_iso_timestamp == "1970-01-01T00:15:00Z"
        mock_client_cls.build_from_services.assert_awaited_once_with(
            logger=fsn.logger,
            config_service=config,
            connector_instance_id="conn-1",
        )
        kwargs = mock_ds.conversations_history.await_args.kwargs
        assert kwargs["latest"] == "1700000000.000000"

    async def test_missing_channel_id(self):
        config = MagicMock()
        out = await fsn._fetch_nearby_messages_impl(
            "2023-11-14T22:13:20Z",
            "before",
            "  ",
            "conn-1",
            config_service=config,
        )
        assert out.ok is False
        assert "channel_id" in out.error

    async def test_invalid_timestamp_returns_error(self):
        config = MagicMock()
        out = await fsn._fetch_nearby_messages_impl(
            "not-a-date",
            "before",
            "C123",
            "conn-1",
            config_service=config,
        )
        assert out.ok is False
        assert "Invalid timestamp" in out.error

    async def test_slack_client_build_failure(self):
        config = MagicMock()
        with patch.object(fsn, "SlackClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(
                side_effect=ConnectionError("no token"),
            )
            out = await fsn._fetch_nearby_messages_impl(
                "2023-11-14T22:13:20Z",
                "before",
                "C123",
                "conn-1",
                config_service=config,
            )
        assert out.ok is False
        assert "Failed to connect to Slack" in out.error

    async def test_api_error_from_slack(self):
        config = MagicMock()
        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(success=False, error="rate_limited"),
            ),
        )
        with patch.object(fsn, "SlackClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(return_value=MagicMock())
            with patch.object(fsn, "SlackDataSource", return_value=mock_ds):
                out = await fsn._fetch_nearby_messages_impl(
                    "2023-11-14T22:13:20Z",
                    "after",
                    "C123",
                    "conn-1",
                    config_service=config,
                )
        assert out.ok is False
        assert out.error == "rate_limited"


@pytest.mark.asyncio
class TestFetchMessagesFromSlack:
    async def test_formats_messages_with_iso_timestamp(self):
        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"messages": [{"ts": "900.000000", "text": "hi", "user": "U1"}]},
                )
            ),
            users_info=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"user": {"profile": {"display_name": "Alice"}}},
                )
            ),
        )
        messages, err = await fsn._fetch_messages_from_slack(
            mock_ds,
            channel_id="C123",
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
            workspace_domain="acme",
            team_id="T1",
        )
        assert err is None
        assert len(messages) == 1
        assert messages[0].timestamp == "1970-01-01T00:15:00Z"
        assert messages[0].iso_timestamp == messages[0].timestamp

    async def test_resolves_tagged_users_in_message_text(self):
        async def users_info(*, user: str, **_kwargs: Any) -> MagicMock:
            names = {
                "U07303G2C2X": "Abhishek",
                "U09MVJQF2GJ": "Pat",
                "U09F230NQL9": "Sam",
                "U07QHFT78FP": "Reviewer",
            }
            return MagicMock(
                success=True,
                data={"user": {"profile": {"display_name": names[user]}}},
            )

        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={
                        "messages": [
                            {
                                "ts": "900.000000",
                                "user": "U07303G2C2X",
                                "text": (
                                    "<@U09MVJQF2GJ> <@U09F230NQL9> Did you test "
                                    "<@U07QHFT78FP>"
                                ),
                            },
                        ]
                    },
                )
            ),
            users_info=AsyncMock(side_effect=users_info),
        )
        messages, err = await fsn._fetch_messages_from_slack(
            mock_ds,
            channel_id="C123",
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
        )
        assert err is None
        assert messages[0].text == "@Pat @Sam Did you test @Reviewer"
        assert mock_ds.users_info.await_count == 4

    async def test_api_failure_returns_error_string(self):
        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(success=False, error=None),
            ),
        )
        messages, err = await fsn._fetch_messages_from_slack(
            mock_ds,
            channel_id="C123",
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
        )
        assert messages == []
        assert err == "Slack API request failed"

    async def test_non_list_messages_returns_empty(self):
        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(success=True, data={"messages": "bad"}),
            ),
        )
        messages, err = await fsn._fetch_messages_from_slack(
            mock_ds,
            channel_id="C123",
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
        )
        assert messages == []
        assert err is None

    async def test_skips_invalid_raw_entries_and_enriches_bot_message(self):
        class _TsAccessErrorDict(dict):
            def __getitem__(self, key: str) -> object:
                if key == "ts":
                    raise ValueError("bad ts access")
                return super().__getitem__(key)

        mock_ds = _slack_data_source_mock(
            conversations_history=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={
                        "messages": [
                            "not-a-dict",
                            {"text": 123, "user": "U1"},
                            {"ts": None},
                            _TsAccessErrorDict(ts="1700000001.000000", text="skip me"),
                            {
                                "ts": "1700000000.000000",
                                "bot_id": "B1",
                                "text": "bot said",
                                "subtype": "bot_message",
                                "reply_count": 2,
                                "thread_ts": "1699999999.000000",
                            },
                        ],
                    },
                )
            ),
            users_info=AsyncMock(
                return_value=MagicMock(
                    success=True,
                    data={"user": {"profile": {"real_name": "Bot User"}}},
                )
            ),
        )
        messages, err = await fsn._fetch_messages_from_slack(
            mock_ds,
            channel_id="C123",
            anchor_ts="1000.000000",
            direction="before",
            message_limit=5,
            workspace_domain="acme",
            team_id="T1",
        )
        assert err is None
        assert len(messages) == 1
        msg = messages[0]
        assert msg.user == "B1"
        assert msg.text == "bot said"
        assert msg.subtype == "bot_message"
        assert msg.reply_count == 2
        assert msg.thread_ts == "1699999999.000000"
        assert msg.weburl == "https://acme.slack.com/archives/C123/p1700000000000000"


@pytest.mark.asyncio
class TestCreateFetchSlackNearbyMessagesTool:
    async def test_tool_delegates_to_impl(self):
        config = MagicMock()
        tool_fn = fsn.create_fetch_slack_nearby_messages_tool(config_service=config)
        expected = fsn.FetchSlackNearbyMessagesSuccess(
            messages=[],
            message_count=0,
            anchor_iso_timestamp="2023-11-14T22:13:20Z",
            new_anchor_iso_timestamp=None,
            channel_id="C123",
            direction="before",
            limit=5,
            connector_id="conn-1",
        )
        with patch.object(
            fsn,
            "_fetch_nearby_messages_impl",
            AsyncMock(return_value=expected),
        ) as mock_impl:
            result = await tool_fn.ainvoke(
                {
                    "timestamp": "2023-11-14T22:13:20Z",
                    "direction": "before",
                    "channel_id": "C123",
                    "connector_id": "conn-1",
                },
            )
        assert result == expected
        mock_impl.assert_awaited_once()

    async def test_tool_catches_unexpected_exception(self):
        config = MagicMock()
        tool_fn = fsn.create_fetch_slack_nearby_messages_tool(config_service=config)
        with patch.object(
            fsn,
            "_fetch_nearby_messages_impl",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await tool_fn.ainvoke(
                {
                    "timestamp": "2023-11-14T22:13:20Z",
                    "direction": "before",
                    "channel_id": "C123",
                    "connector_id": "conn-1",
                },
            )
        assert result.ok is False
        assert "Failed to fetch nearby Slack messages" in result.error
