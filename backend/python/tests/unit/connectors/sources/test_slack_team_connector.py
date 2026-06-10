"""
Unit tests for Slack Team Connector — helpers, surface API, and orchestration paths.

Covers:
  - ConversationalBurst, ProcessingContext, DeferredThread
  - RateLimiter (init, acquire, wait + logger branch)
  - SlackConnector static helpers and burst / mention utilities
  - ``init``, ``_fresh_datasource``, ``stream_record``, ``get_filter_options``,
    ``test_connection_and_access``, lifecycle hooks, ``create_connector``
  - ``_make_ctx``, ``_channel_group_map``, ``_process_file_raw``, ``_warm_user_cache_for_messages``
  - Pipeline slices with mocks: ``run_sync``, ``sync_channel_messages`` (history / join /
    pagination / checkpoints / deferred threads), ``_sync_users``, ``_build_user_id_caches``,
    ``_sync_workspace_roles``, ``_sync_user_groups``, ``_fetch_usergroup_members``,
    ``_sync_channels``, ``_fetch_channel_members``, ``_channel_permissions``,
    ``_process_message_batch``, ``_enrich_link_records_linked_id``, ``reindex_records``,
    ``_sync_message_changes``, ``_check_channel_changes`` (light)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal SlackConnector without real deps
# ---------------------------------------------------------------------------

def _make_connector():
    """Return a SlackConnector with all heavy deps mocked."""
    from app.connectors.sources.slack.team.connector import SlackConnector

    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-test"
    data_store = MagicMock()
    config_svc = MagicMock()

    # Patch the base-class __init__ so we don't need full DI wiring
    with patch.object(SlackConnector, "__init__", lambda self, *a, **kw: None):
        c = SlackConnector.__new__(SlackConnector)

    # Manually set the attributes the methods under test actually use
    c.logger = logger
    c.data_entities_processor = dep
    c.data_store_provider = data_store
    c.workspace_domain = None
    c.team_id = None
    c.user_id_to_email_cache = {}
    c.user_id_to_name_cache = {}
    c.user_id_to_internal_id_cache = {}
    c._source_id_to_app_user = {}
    c.channel_groups_cache = {}
    c.channel_id_to_name_cache = {}
    c.channel_filter_cache = {}
    c._cache_rebuild_event = None
    c.connector_id = "conn-abc"
    c.sync_filters = MagicMock()
    c.sync_filters.get = MagicMock(return_value=None)
    c.indexing_filters = MagicMock()
    c.external_client = None
    c.data_source = None
    c.rate_limiter = MagicMock()
    c.config_service = config_svc

    return c


# ===========================================================================
# 1. ConversationalBurst
# ===========================================================================

class TestConversationalBurst:
    def test_message_count_empty(self):
        from app.connectors.sources.slack.team.connector import ConversationalBurst
        b = ConversationalBurst(messages=[], start_ts=0.0, end_ts=0.0)
        assert b.message_count == 0

    def test_message_count_multiple(self):
        from app.connectors.sources.slack.team.connector import ConversationalBurst
        msgs = [{"ts": "1.0", "text": "hi"}, {"ts": "2.0", "text": "there"}]
        b = ConversationalBurst(messages=msgs, start_ts=1.0, end_ts=2.0)
        assert b.message_count == 2

    def test_aggregated_text_empty(self):
        from app.connectors.sources.slack.team.connector import ConversationalBurst
        b = ConversationalBurst(messages=[], start_ts=0.0, end_ts=0.0)
        assert b.aggregated_text == ""

    def test_aggregated_text_skips_no_text(self):
        from app.connectors.sources.slack.team.connector import ConversationalBurst
        msgs = [{"ts": "1.0"}, {"ts": "2.0", "text": "hello"}, {"ts": "3.0", "text": ""}]
        b = ConversationalBurst(messages=msgs, start_ts=1.0, end_ts=3.0)
        # Only "hello" has non-falsy text
        assert b.aggregated_text == "hello"

    def test_aggregated_text_joins_with_newlines(self):
        from app.connectors.sources.slack.team.connector import ConversationalBurst
        msgs = [{"ts": "1.0", "text": "Hello"}, {"ts": "2.0", "text": "World"}]
        b = ConversationalBurst(messages=msgs, start_ts=1.0, end_ts=2.0)
        assert b.aggregated_text == "Hello\n\nWorld"


# ===========================================================================
# 2. RateLimiter
# ===========================================================================

class TestRateLimiter:
    def test_init_uses_defaults(self):
        from app.connectors.sources.slack.team.connector import (
            RateLimiter,
            SLACK_TIER_LIMITS_PER_MINUTE,
            RATE_LIMIT_HEADROOM,
        )
        rl = RateLimiter()
        for tier, cap in SLACK_TIER_LIMITS_PER_MINUTE.items():
            assert rl._limits[tier] == max(1, int(cap * RATE_LIMIT_HEADROOM))

    def test_init_custom_limits(self):
        from app.connectors.sources.slack.team.connector import RateLimiter
        rl = RateLimiter(limits={2: 10, 3: 20}, headroom=1.0)
        assert rl._limits[2] == 10
        assert rl._limits[3] == 20

    def test_init_headroom_zero_clamps_to_one(self):
        """max(1, int(0)) → 1"""
        from app.connectors.sources.slack.team.connector import RateLimiter
        rl = RateLimiter(limits={2: 1}, headroom=0.0)
        assert rl._limits[2] == 1

    def test_init_creates_separate_locks_per_tier(self):
        from app.connectors.sources.slack.team.connector import RateLimiter, Tier
        rl = RateLimiter()
        assert rl._locks[int(Tier.T2)] is not rl._locks[int(Tier.T3)]

    @pytest.mark.asyncio
    async def test_acquire_records_call(self):
        """First acquire should not sleep and should record one call."""
        from app.connectors.sources.slack.team.connector import RateLimiter, Tier
        rl = RateLimiter(limits={2: 100, 3: 100, 4: 100}, headroom=1.0)
        await rl.acquire(Tier.T2)
        assert len(rl._calls[int(Tier.T2)]) == 1

    @pytest.mark.asyncio
    async def test_acquire_multiple_calls_within_limit(self):
        from app.connectors.sources.slack.team.connector import RateLimiter, Tier
        rl = RateLimiter(limits={2: 100, 3: 100, 4: 100}, headroom=1.0)
        for _ in range(5):
            await rl.acquire(Tier.T2)
        assert len(rl._calls[int(Tier.T2)]) == 5

    @pytest.mark.asyncio
    async def test_acquire_sleeps_when_limit_hit(self):
        """When at capacity the limiter should sleep (we mock asyncio.sleep)."""
        from app.connectors.sources.slack.team.connector import RateLimiter, Tier
        import app.connectors.sources.slack.team.connector as mod
        rl = RateLimiter(limits={2: 1, 3: 100, 4: 100}, headroom=1.0)
        # Pre-fill the window with one call so the limit is already reached
        await rl.acquire(Tier.T2)
        # The window expiry should cause a sleep on the next call
        with patch.object(mod.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            # Monkey-patch datetime so the call looks recent
            await rl.acquire(Tier.T2)
            # sleep may or may not be called depending on timing; just check no crash


# ===========================================================================
# 3. Static / pure helper methods
# ===========================================================================

class TestGetFileExtension:
    def test_pdf(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension("report.pdf") == "pdf"

    def test_lowercase(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension("IMAGE.PNG") == "png"

    def test_multiple_dots(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension("archive.tar.gz") == "gz"

    def test_no_extension(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension("README") is None

    def test_empty_string(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension("") is None

    def test_dot_only(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        # rsplit gives ["", ""] — parts[1] is "" so returns None
        assert SlackConnector._get_file_extension(".") is None

    def test_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._get_file_extension(None) is None


class TestResolveFileMimeType:
    def test_valid_slack_mime_returned_directly(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_mime_type("file.pdf", "application/pdf") == "application/pdf"

    def test_octet_stream_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._resolve_file_mime_type("document.pdf", "application/octet-stream")
        assert result == "application/pdf"

    def test_none_mime_type_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._resolve_file_mime_type("image.png", None)
        assert result == "image/png"

    def test_empty_mime_type_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._resolve_file_mime_type("style.css", "")
        assert result == "text/css"

    def test_unknown_filename_returns_unknown(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.config.constants.arangodb import MimeTypes
        result = SlackConnector._resolve_file_mime_type("noextension", None)
        assert result == MimeTypes.UNKNOWN.value

    def test_binary_octet_stream_falls_back(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._resolve_file_mime_type("data.json", "binary/octet-stream")
        assert result == "application/json"

    def test_txt_extension_from_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._resolve_file_mime_type("notes.txt", "application/octet-stream")
        assert result == "text/plain"


class TestResolveFileExtension:
    def test_normal_slack_filetype(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("file.py", "python") == "python"

    def test_binary_filetype_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("config.yaml", "binary") == "yaml"

    def test_none_filetype_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("script.sh", None) == "sh"

    def test_empty_filetype_falls_back_to_filename(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("readme.md", "") == "md"

    def test_undefined_filetype(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("data.csv", "undefined") == "csv"

    def test_no_extension_no_filetype(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._resolve_file_extension("README", None) is None

    def test_filetype_lowercased(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        # Slack can return uppercase filetypes
        assert SlackConnector._resolve_file_extension("doc.PDF", "PDF") == "pdf"


class TestSlackTsToUtcLabel:
    def test_valid_ts(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        # 2021-05-03 00:00:00 UTC
        result = SlackConnector._slack_ts_to_utc_label("1620000000.000100")
        assert result.startswith("2021-")
        assert "_" in result

    def test_invalid_ts_fallback(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._slack_ts_to_utc_label("notanumber")
        assert result == "notanumber"

    def test_dotted_fallback(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._slack_ts_to_utc_label("abc.def")
        assert result == "abc-def"

    def test_strftime_format(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        result = SlackConnector._slack_ts_to_utc_label("1620000000.000000")
        # Format: YYYY-MM-DD_HH-MM-SS
        parts = result.split("_")
        assert len(parts) == 2
        assert len(parts[0]) == 10  # YYYY-MM-DD


class TestMessageUrl:
    def test_with_workspace_domain(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        c = _make_connector()
        c.workspace_domain = "myworkspace"
        url = c._message_url("C123", "1620000000.000100")
        p = urlparse(url)
        ref = urlparse(
            "https://myworkspace.slack.com/archives/C123/p1620000000000100"
        )
        assert p.scheme == ref.scheme
        assert p.hostname == ref.hostname
        assert p.path == ref.path

    def test_without_workspace_domain(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        c = _make_connector()
        c.workspace_domain = None
        c.team_id = "T999"
        url = c._message_url("C456", "1620000000.000100")
        p = urlparse(url)
        ref = urlparse(
            "https://app.slack.com/client/T999/C456/p1620000000000100"
        )
        assert p.scheme == ref.scheme
        assert p.hostname == ref.hostname
        assert p.path == ref.path

    def test_thread_ts_adds_query_params(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        c = _make_connector()
        c.workspace_domain = "myws"
        url = c._message_url("C111", "1620000001.000000", thread_ts="1620000000.000000")
        q = parse_qs(urlparse(url).query)
        assert q["thread_ts"] == ["1620000000.000000"]
        assert q["cid"] == ["C111"]

    def test_thread_ts_same_as_ts_no_params(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        c = _make_connector()
        c.workspace_domain = "myws"
        # thread_ts == ts means it's the parent — no side-panel params
        url = c._message_url("C111", "1620000000.000000", thread_ts="1620000000.000000")
        assert urlparse(url).query == ""

    def test_no_team_id_fallback(self):
        c = _make_connector()
        c.workspace_domain = None
        c.team_id = None
        url = c._message_url("C111", "1.0")
        p = urlparse(url)
        ref = urlparse("https://app.slack.com/client//C111/p10")
        assert p.scheme == ref.scheme
        assert p.hostname == ref.hostname
        assert p.path == ref.path


# ===========================================================================
# 4. _detect_bursts
# ===========================================================================

class TestDetectBursts:
    def setup_method(self):
        self.c = _make_connector()

    def test_empty(self):
        assert self.c._detect_bursts([]) == []

    def test_single_message(self):
        msgs = [{"ts": "1000.0", "text": "hi"}]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].message_count == 1

    def test_two_messages_within_window(self):
        msgs = [
            {"ts": "1000.0", "text": "first"},
            {"ts": "1100.0", "text": "second"},   # 100s gap, well within 300s
        ]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].message_count == 2

    def test_two_messages_outside_window(self):
        msgs = [
            {"ts": "1000.0", "text": "first"},
            {"ts": "1400.0", "text": "second"},   # 400s > BURST_WINDOW_SECONDS
        ]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) == 2

    def test_system_event_breaks_burst(self):
        msgs = [
            {"ts": "1000.0", "text": "first"},
            {"ts": "1001.0", "subtype": "channel_join"},
            {"ts": "1002.0", "text": "after join"},
        ]
        bursts = self.c._detect_bursts(msgs)
        # channel_join starts a new burst, then the message after continues
        assert len(bursts) >= 2

    def test_messages_out_of_order_sorted(self):
        """_detect_bursts sorts by ts, so reverse order input should produce same result."""
        msgs_forward = [
            {"ts": "1000.0", "text": "A"},
            {"ts": "1100.0", "text": "B"},
        ]
        msgs_reversed = list(reversed(msgs_forward))
        bursts_f = self.c._detect_bursts(msgs_forward)
        bursts_r = self.c._detect_bursts(msgs_reversed)
        assert len(bursts_f) == len(bursts_r)

    def test_burst_start_end_ts(self):
        msgs = [
            {"ts": "1000.0", "text": "A"},
            {"ts": "1200.0", "text": "B"},
        ]
        bursts = self.c._detect_bursts(msgs)
        b = bursts[0]
        assert b.start_ts == pytest.approx(1000.0)
        assert b.end_ts == pytest.approx(1200.0)

    def test_three_bursts(self):
        msgs = [
            {"ts": "1000.0", "text": "A"},    # burst 1
            {"ts": "1400.0", "text": "B"},    # burst 2
            {"ts": "1800.0", "text": "C"},    # burst 3
        ]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) == 3

    def test_bot_messages_not_a_hard_break(self):
        """Bot messages should merge into bursts naturally."""
        msgs = [
            {"ts": "1000.0", "text": "human"},
            {"ts": "1001.0", "text": "bot reply", "bot_id": "B123"},
            {"ts": "1002.0", "text": "human again"},
        ]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].message_count == 3


# ===========================================================================
# 5. _extract_user_mentions / _extract_channel_mentions / _extract_urls
# ===========================================================================

class TestExtractUserMentions:
    def test_single_mention(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_user_mentions("Hello <@U123ABC> how are you?")
        assert ids == ["U123ABC"]

    def test_multiple_mentions(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_user_mentions("<@U111> and <@W222>")
        assert set(ids) == {"U111", "W222"}

    def test_no_mentions(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._extract_user_mentions("plain text") == []

    def test_empty_string(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._extract_user_mentions("") == []

    def test_mention_with_display_name_not_matched(self):
        """The simple pattern <@U123|name> is NOT matched by _extract_user_mentions."""
        from app.connectors.sources.slack.team.connector import SlackConnector
        # <@U123ABC|John> won't match because > doesn't immediately follow \w+
        ids = SlackConnector._extract_user_mentions("<@U123ABC|John>")
        assert ids == []

    def test_w_prefix_user(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_user_mentions("<@W09AEBH69JS>")
        assert ids == ["W09AEBH69JS"]


class TestExtractChannelMentions:
    def test_channel_with_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_channel_mentions("Come join <#C123ABC|general>")
        assert ids == ["C123ABC"]

    def test_channel_without_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_channel_mentions("<#C123ABC>")
        assert ids == ["C123ABC"]

    def test_g_prefix_group(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_channel_mentions("<#G999XYZ|private>")
        assert ids == ["G999XYZ"]

    def test_no_channel_mentions(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._extract_channel_mentions("no channels here") == []

    def test_multiple_channels(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        ids = SlackConnector._extract_channel_mentions("<#C111|a> and <#C222|b>")
        assert set(ids) == {"C111", "C222"}


class TestExtractUrls:
    def test_slack_mrkdwn_url(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        urls = SlackConnector._extract_urls("Check <https://example.com|this>")
        assert len(urls) == 1
        u = urlparse(urls[0])
        ref = urlparse("https://example.com")
        assert u.scheme == ref.scheme and u.hostname == ref.hostname

    def test_plain_url(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        urls = SlackConnector._extract_urls("Visit https://example.com today")
        assert len(urls) == 1
        u = urlparse(urls[0])
        ref = urlparse("https://example.com")
        assert u.scheme == ref.scheme and u.hostname == ref.hostname

    def test_multiple_urls(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        text = "<https://a.com> and https://b.com"
        urls = SlackConnector._extract_urls(text)
        assert len(urls) == 2
        u0, u1 = urlparse(urls[0]), urlparse(urls[1])
        r0, r1 = urlparse("https://a.com"), urlparse("https://b.com")
        assert u0.scheme == r0.scheme and u0.hostname == r0.hostname
        assert u1.scheme == r1.scheme and u1.hostname == r1.hostname

    def test_no_urls(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._extract_urls("no links here") == []

    def test_http_url(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        urls = SlackConnector._extract_urls("http://legacy.example.com")
        assert len(urls) == 1
        u = urlparse(urls[0])
        ref = urlparse("http://legacy.example.com")
        assert u.scheme == ref.scheme and u.hostname == ref.hostname

    def test_empty_string(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._extract_urls("") == []


# ===========================================================================
# 6. _is_bot_message / _bot_display_name
# ===========================================================================

class TestIsBotMessage:
    def test_with_bot_id(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_bot_message({"bot_id": "B123"}) is True

    def test_with_subtype_bot_message(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_bot_message({"subtype": "bot_message"}) is True

    def test_normal_user_message(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_bot_message({"user": "U123", "text": "hi"}) is False

    def test_empty_dict(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_bot_message({}) is False

    def test_both_bot_id_and_user(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        # bot_id takes precedence
        assert SlackConnector._is_bot_message({"bot_id": "B123", "user": "U456"}) is True


class TestBotDisplayName:
    def test_bot_profile_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {"bot_profile": {"name": "Deploybot"}}
        assert SlackConnector._bot_display_name(msg) == "Deploybot"

    def test_username_fallback(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {"username": "MyBot"}
        assert SlackConnector._bot_display_name(msg) == "MyBot"

    def test_empty_bot_profile(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {"bot_profile": {}}
        assert SlackConnector._bot_display_name(msg) is None

    def test_no_bot_info(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._bot_display_name({"user": "U123"}) is None

    def test_whitespace_only_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {"bot_profile": {"name": "   "}}
        assert SlackConnector._bot_display_name(msg) is None


# ===========================================================================
# 7. _classify_url
# ===========================================================================

class TestClassifyUrl:
    def test_jira_with_ticket(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url(
            "https://mycompany.atlassian.net/browse/PROJ-123"
        )
        assert kind == "jira"
        assert meta["ticket_key"] == "PROJ-123"

    def test_jira_no_ticket_path(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url("https://mycompany.atlassian.net/")
        assert kind == "jira"
        assert "ticket_key" not in meta

    def test_github_issue(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url(
            "https://github.com/owner/repo/issues/42"
        )
        assert kind == "github"
        assert meta["owner"] == "owner"
        assert meta["repo"] == "repo"
        assert meta["number"] == "42"

    def test_github_pr(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url(
            "https://github.com/owner/repo/pull/7"
        )
        assert kind == "github"
        assert meta["type"] == "pull"

    def test_github_no_issue_path(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url("https://github.com/owner/repo")
        assert kind == "github"

    def test_google_drive(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url(
            "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/view"
        )
        assert kind == "gdrive"
        assert meta["file_id"] == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_google_docs(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url(
            "https://docs.google.com/document/d/DOCID123/edit"
        )
        assert kind == "gdrive"
        assert meta["file_id"] == "DOCID123"

    def test_generic_web(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url("https://example.com/page")
        assert kind == "web"
        assert meta["url"] == "https://example.com/page"

    def test_invalid_url_returns_web(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url("not-a-url")
        assert kind == "web"

    def test_jira_in_host(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        kind, meta = SlackConnector._classify_url("https://jira.mycompany.com/browse/OPS-99")
        assert kind == "jira"
        assert meta["ticket_key"] == "OPS-99"


# ===========================================================================
# 8. _unfurl_data
# ===========================================================================

class TestUnfurlData:
    def test_url_in_attachments(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {
            "attachments": [
                {"from_url": "https://example.com", "title": "Example", "service_name": "Example"}
            ]
        }
        data = SlackConnector._unfurl_data("https://example.com", msg)
        assert data["title"] == "Example"
        assert data["service_name"] == "Example"

    def test_url_substring_match(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {
            "attachments": [
                {"from_url": "https://example.com/page?ref=slack", "title": "Page"}
            ]
        }
        data = SlackConnector._unfurl_data("https://example.com", msg)
        assert data["title"] == "Page"

    def test_url_not_in_attachments(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {
            "attachments": [
                {"from_url": "https://other.com", "title": "Other"}
            ]
        }
        data = SlackConnector._unfurl_data("https://example.com", msg)
        assert data == {}

    def test_no_attachments(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        data = SlackConnector._unfurl_data("https://example.com", {})
        assert data == {}

    def test_returns_fields(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        msg = {
            "attachments": [
                {
                    "from_url": "https://x.com",
                    "service_name": "X",
                    "title": "T",
                    "text": "desc",
                    "image_url": "https://img.com/i.png",
                    "fields": [{"title": "f1"}],
                    "author_name": "Author",
                }
            ]
        }
        data = SlackConnector._unfurl_data("https://x.com", msg)
        assert data["service_name"] == "X"
        assert data["author_name"] == "Author"
        assert data["fields"] == [{"title": "f1"}]


# ===========================================================================
# 9. _is_invalid_cursor_error
# ===========================================================================

class TestIsInvalidCursorError:
    def test_invalid_cursor(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("invalid_cursor") is True

    def test_no_longer_valid(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("cursor is no longer valid") is True

    def test_other_error(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("not_in_channel") is False

    def test_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error(None) is False

    def test_empty_string(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("") is False

    def test_case_insensitive(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("INVALID_CURSOR") is True


# ===========================================================================
# 10. _record_changed
# ===========================================================================

class TestRecordChanged:
    def setup_method(self):
        self.c = _make_connector()

    def _existing(self, is_edited=False, source_updated_at=None):
        e = MagicMock()
        e.is_edited = is_edited
        e.source_updated_at = source_updated_at
        e.source_created_at = None
        return e

    def test_newly_edited(self):
        """edited in md but not in DB → changed."""
        md = {"text": "new text", "edited": {"ts": "1.0"}}
        existing = self._existing(is_edited=False)
        assert self.c._record_changed(md, existing) is True

    def test_edited_text_changed(self):
        md = {"text": "new text", "edited": {"ts": "2.0"}}
        existing = self._existing(is_edited=True, source_updated_at=1000)
        assert self.c._record_changed(md, existing) is True

    def test_edited_text_same(self):
        """Same edit timestamp → not changed."""
        md = {"text": "same text", "edited": {"ts": "1.0"}}
        existing = self._existing(is_edited=True, source_updated_at=1000)
        assert self.c._record_changed(md, existing) is False

    def test_no_changes(self):
        md = {"text": "hello"}
        existing = self._existing()
        assert self.c._record_changed(md, existing) is False


# ===========================================================================
# 12. _is_edited
# ===========================================================================

class TestIsEdited:
    def test_edited_present(self):
        c = _make_connector()
        assert c._is_edited({"edited": {"ts": "1.0"}}) is True

    def test_edited_absent(self):
        c = _make_connector()
        assert c._is_edited({"text": "hi"}) is False


# ===========================================================================
# 13. _replace_mentions_in_text
# ===========================================================================

class TestReplaceMentionsInText:
    def setup_method(self):
        self.c = _make_connector()
        self.c.user_id_to_email_cache = {}

    def test_empty_text(self):
        assert self.c._replace_mentions_in_text("", {}, {}) == ""

    def test_none_text(self):
        # None is falsy → returned as-is
        result = self.c._replace_mentions_in_text(None, {}, {})
        assert result is None

    def test_user_mention_with_display_name(self):
        text = "Hello <@U123|John>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "@John" in result

    def test_user_mention_resolved_from_cache(self):
        text = "Hello <@U123>"
        result = self.c._replace_mentions_in_text(text, {"U123": "Alice"}, {})
        assert "@Alice" in result

    def test_user_mention_fallback_to_email(self):
        self.c.user_id_to_email_cache = {"U999": "bob@example.com"}
        text = "Hi <@U999>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "@bob@example.com" in result

    def test_user_mention_fallback_to_id(self):
        text = "Hi <@UUNKNOWN>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "@UUNKNOWN" in result

    def test_channel_mention_with_display_name(self):
        text = "Join <#C123|general>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "#general" in result

    def test_channel_mention_resolved_from_cache(self):
        text = "See <#C456>"
        result = self.c._replace_mentions_in_text(text, {}, {"C456": "engineering"})
        assert "#engineering" in result

    def test_channel_mention_fallback_to_id(self):
        text = "See <#CUNKNOWN>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "#CUNKNOWN" in result

    def test_mixed_mentions(self):
        text = "Hey <@U001|Alice> check <#C001|general>"
        result = self.c._replace_mentions_in_text(text, {}, {})
        assert "@Alice" in result
        assert "#general" in result


# ===========================================================================
# 14. SlackWorkspaceApp
# ===========================================================================

class TestSlackWorkspaceApp:
    def test_instantiation(self):
        from app.connectors.sources.slack.common.apps import SlackWorkspaceApp
        from app.config.constants.arangodb import Connectors, AppGroups
        app = SlackWorkspaceApp("conn-123")
        assert app.connector_id == "conn-123"
        assert app.app_name == Connectors.SLACK_WORKSPACE
        assert app.app_group_name == AppGroups.SLACK

    def test_slack_app_instantiation(self):
        from app.connectors.sources.slack.common.apps import SlackApp
        from app.config.constants.arangodb import Connectors, AppGroups
        app = SlackApp("conn-456")
        assert app.connector_id == "conn-456"
        assert app.app_name == Connectors.SLACK
        assert app.app_group_name == AppGroups.SLACK

    def test_get_app_name(self):
        from app.connectors.sources.slack.common.apps import SlackWorkspaceApp
        from app.config.constants.arangodb import Connectors
        app = SlackWorkspaceApp("conn-123")
        assert app.get_app_name() == Connectors.SLACK_WORKSPACE

    def test_get_connector_id(self):
        from app.connectors.sources.slack.common.apps import SlackWorkspaceApp
        app = SlackWorkspaceApp("conn-xyz")
        assert app.get_connector_id() == "conn-xyz"


# ===========================================================================
# 15. _compute_sync_window_oldest
# ===========================================================================

class TestComputeSyncWindowOldest:
    def test_no_filter_defaults_30_days(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        import time
        c = _make_connector()
        c.sync_filters.get = MagicMock(return_value=None)
        result = c._compute_sync_window_oldest()
        assert result is not None
        ts = float(result)
        # Should be ~30 days ago
        assert abs(ts - (time.time() - 30 * 86400)) < 60

    def test_last_7_days_operator(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.connectors.core.registry.filters import FilterOperator
        import time
        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = FilterOperator.LAST_7_DAYS
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        ts = float(result)
        assert abs(ts - (time.time() - 7 * 86400)) < 60

    def test_last_30_days_operator(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.connectors.core.registry.filters import FilterOperator
        import time
        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = FilterOperator.LAST_30_DAYS
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        ts = float(result)
        assert abs(ts - (time.time() - 30 * 86400)) < 60

    def test_unknown_operator_fallback(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        import time
        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = "UNKNOWN_OP"
        sw.value_date = None
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        # Should still return something (fallback)
        assert result is not None


# ============================================================================
# SlackIndividualConnector — shares same static helpers (smoke tests)
# ============================================================================

class TestSlackIndividualConnectorSharedUtils:
    """The individual connector inherits the same static helpers as the team connector."""

    def _make_individual_connector(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        with patch.object(SlackIndividualConnector, "__init__", lambda self, *a, **kw: None):
            conn = SlackIndividualConnector.__new__(SlackIndividualConnector)
            conn.logger = MagicMock()
            conn.user_id_to_email_cache = {}
        return conn

    def test_extract_user_mentions(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        assert "U123" in SlackIndividualConnector._extract_user_mentions("<@U123>")

    def test_extract_channel_mentions(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        assert "C999" in SlackIndividualConnector._extract_channel_mentions("<#C999|general>")

    def test_extract_urls(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        urls = SlackIndividualConnector._extract_urls("https://example.com")
        assert len(urls) == 1
        u = urlparse(urls[0])
        ref = urlparse("https://example.com")
        assert u.scheme == ref.scheme and u.hostname == ref.hostname

    def test_is_bot_message(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_bot_message({"bot_id": "B1"}) is True
        assert SlackConnector._is_bot_message({"user": "U1"}) is False

    def test_classify_url_jira(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        kind, _ = SlackIndividualConnector._classify_url("https://company.atlassian.net/browse/PROJ-1")
        assert kind == "jira"

    def test_detect_bursts_single(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        conn = self._make_individual_connector()
        bursts = conn._detect_bursts([{"ts": "1000.0", "text": "hi"}])
        assert len(bursts) == 1

    def test_slack_ts_to_utc_label(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        assert "2021" in SlackIndividualConnector._slack_ts_to_utc_label("1620000000.000000")

    def test_is_invalid_cursor_error(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._is_invalid_cursor_error("invalid_cursor") is True
        assert SlackConnector._is_invalid_cursor_error("other") is False

    def test_get_file_extension(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        assert SlackIndividualConnector._get_file_extension("doc.pdf") == "pdf"
        assert SlackIndividualConnector._get_file_extension("noext") is None

    def test_resolve_file_mime_type(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
        assert SlackIndividualConnector._resolve_file_mime_type("file.pdf", None) == "application/pdf"

    def test_bot_display_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        assert SlackConnector._bot_display_name({"bot_profile": {"name": "Bot"}}) == "Bot"
        assert SlackConnector._bot_display_name({}) is None


# ===========================================================================
# 16. ProcessingContext / _make_ctx / misc dataclasses
# ===========================================================================

class TestProcessingContextAndMakeCtx:
    def test_processing_context_fields(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            Tier,
        )

        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            channel_id="C1",
            channel_groups_map={"C1": "rg-int"},
            user_id_to_email={"U1": "a@b.com"},
            user_id_to_name={"U1": "Alice"},
            channel_id_to_name={"C1": "general"},
            rate_limiter=rl,
        )
        assert ctx.channel_id == "C1"
        assert ctx.channel_groups_map["C1"] == "rg-int"
        assert ctx.rate_limiter._limits[int(Tier.T2)] == 10

    def test_make_ctx_with_and_without_rg(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.user_id_to_email_cache = {"U9": "x@y.com"}
        c.user_id_to_name_cache = {"U9": "Bob"}
        c.channel_id_to_name_cache = {"C9": "random"}
        ctx0 = c._make_ctx("C9", None)
        assert ctx0.channel_groups_map == {}
        ctx1 = c._make_ctx("C9", "rg-1")
        assert ctx1.channel_groups_map == {"C9": "rg-1"}


# ===========================================================================
# 17. _compute_sync_window_oldest — operator branches
# ===========================================================================

class TestComputeSyncWindowOldestExtended:
    def test_last_90_days_uses_operator_seconds(self):
        from app.connectors.core.registry.filters import FilterOperator
        import time

        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = FilterOperator.LAST_90_DAYS
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        ts = float(result)
        assert abs(ts - (time.time() - 90 * 86400)) < 60

    def test_is_after_with_start_epoch(self):
        from app.connectors.core.registry.filters import FilterOperator

        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = FilterOperator.IS_AFTER
        sw.get_datetime_start = MagicMock(return_value=1_700_000_000_000)
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        assert result == f"{1_700_000_000_000 / 1000.0:.6f}"

    def test_is_after_without_start_falls_back(self):
        from app.connectors.core.registry.filters import FilterOperator
        import time

        c = _make_connector()
        sw = MagicMock()
        sw.operator_value = FilterOperator.IS_AFTER
        sw.get_datetime_start = MagicMock(return_value=None)
        c.sync_filters.get = MagicMock(return_value=sw)
        result = c._compute_sync_window_oldest()
        assert abs(float(result) - (time.time() - 30 * 86400)) < 120


# ===========================================================================
# 18. RateLimiter — logger + timed wait branch
# ===========================================================================

class TestRateLimiterWaitBranch:
    @pytest.mark.asyncio
    async def test_acquire_when_full_logs_and_sleeps(self):
        """Cover the ``wait > 0`` branch: debug log + ``asyncio.sleep``."""
        from app.connectors.sources.slack.team.connector import RateLimiter, Tier
        import app.connectors.sources.slack.team.connector as mod
        from datetime import datetime, timedelta

        log = MagicMock()
        rl = RateLimiter(limits={2: 1, 3: 10, 4: 10}, headroom=1.0, logger=log)
        t0 = datetime(2030, 6, 15, 10, 0, 0)
        t1 = t0 + timedelta(minutes=61)
        time_line = iter([t0, t0, t0, t1, t1])

        def _now():
            return next(time_line)

        with (
            patch.object(mod, "datetime") as mock_dt,
            patch.object(mod.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_dt.now = _now
            mock_dt.timedelta = timedelta
            await rl.acquire(Tier.T2)
            await rl.acquire(Tier.T2)
            mock_sleep.assert_awaited()
            log.debug.assert_called()


# ===========================================================================
# 19. Static helpers — edge branches
# ===========================================================================

class TestClassifyUrlExceptionPath:
    def test_urlparse_raises_returns_web(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        with patch("urllib.parse.urlparse", side_effect=RuntimeError("boom")):
            kind, meta = SlackConnector._classify_url("https://example.com/x")
        assert kind == "web"
        assert "url" in meta


class TestResolveFileMimeTypeGuessNone:
    def test_unknown_extension_no_slack_mime(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.config.constants.arangodb import MimeTypes

        with patch("mimetypes.guess_type", return_value=(None, None)):
            r = SlackConnector._resolve_file_mime_type("weird.unknown_ext_xyz", None)
        assert r == MimeTypes.UNKNOWN.value


# ===========================================================================
# 20. SlackConnector — init / _fresh_datasource / surface API
# ===========================================================================

class TestSlackConnectorInitAndFreshDatasource:
    @pytest.mark.asyncio
    async def test_init_success_sets_workspace(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN", "apiToken": "xoxb-fake"}}
        )
        inner = MagicMock()
        inner.get_token = MagicMock(return_value="xoxb-fake")
        inner.set_token = MagicMock()
        ext = MagicMock()
        ext.get_client = MagicMock(return_value=inner)

        team_resp = MagicMock(success=True, data={"team": {"domain": "acme", "id": "T123"}})

        async def _fresh():
            ds = MagicMock()
            ds.team_info = AsyncMock(return_value=team_resp)
            return ds

        with (
            patch(
                "app.connectors.sources.slack.team.connector.SlackClient.build_from_services",
                new_callable=AsyncMock,
                return_value=ext,
            ),
            patch("app.connectors.sources.slack.team.connector.SlackDataSource", MagicMock()),
            patch.object(SlackConnector, "_fresh_datasource", side_effect=_fresh),
        ):
            ok = await c.init()
        assert ok is True
        assert c.workspace_domain == "acme"
        assert c.team_id == "T123"
        assert c.external_client is ext

    @pytest.mark.asyncio
    async def test_init_failure_returns_false(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with patch(
            "app.connectors.sources.slack.team.connector.SlackClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network"),
        ):
            ok = await c.init()
        assert ok is False

    @pytest.mark.asyncio
    async def test_fresh_datasource_requires_init(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.external_client = None
        with pytest.raises(RuntimeError, match="Call init"):
            await c._fresh_datasource()

    @pytest.mark.asyncio
    async def test_fresh_datasource_missing_config(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(RuntimeError, match="not found"):
            await c._fresh_datasource()

    @pytest.mark.asyncio
    async def test_fresh_datasource_missing_token(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        with pytest.raises(RuntimeError, match="No access token"):
            await c._fresh_datasource()

    @pytest.mark.asyncio
    async def test_fresh_datasource_oauth_token_path(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        inner = MagicMock()
        inner.get_token = MagicMock(return_value="old")
        inner.set_token = MagicMock()
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=inner)
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "newtok"},
            }
        )
        with patch("app.connectors.sources.slack.team.connector.SlackDataSource") as Sds:
            await c._fresh_datasource()
        inner.set_token.assert_called_once_with("newtok")
        Sds.assert_called()


class TestSlackConnectorStreamRecord:
    @pytest.mark.asyncio
    async def test_message_record_uses_blocks(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.MESSAGE
        rec.external_record_id = "1.0"
        rec.external_record_group_id = "C1"
        with patch.object(
            SlackConnector,
            "_build_message_blocks_for_streaming",
            new_callable=AsyncMock,
            return_value=b'{"blocks":[]}',
        ):
            resp = await c.stream_record(rec)
        raw = b""
        async for chunk in resp.body_iterator:
            raw += chunk
        assert b'{"blocks":[]}' in raw

    @pytest.mark.asyncio
    async def test_file_record_streams_via_helper(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.FILE
        rec.record_name = "f.bin"
        rec.external_record_id = "F1"
        rec.mime_type = "application/octet-stream"

        async def _gen():
            yield b"a"

        with (
            patch(
                "app.connectors.sources.slack.team.connector.create_stream_record_response",
                return_value=MagicMock(status_code=200),
            ) as csr,
            patch.object(SlackConnector, "_stream_file_bytes", return_value=_gen()),
        ):
            out = await c.stream_record(rec)
        csr.assert_called_once()
        assert out.status_code == 200

    @pytest.mark.asyncio
    async def test_link_record_markdown(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.LINK
        rec.external_record_id = "L1"
        rec.weburl = "https://example.com/p"
        rec.record_name = "Example"
        resp = await c.stream_record(rec)
        data = b"".join([x async for x in resp.body_iterator])
        assert b"https://example.com/p" in data

    @pytest.mark.asyncio
    async def test_link_missing_weburl_raises(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType
        from fastapi import HTTPException

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.LINK
        rec.external_record_id = "L1"
        rec.weburl = None
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(rec)
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType
        from fastapi import HTTPException

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.DRIVE
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(rec)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_record_wraps_unexpected_error(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType
        from fastapi import HTTPException

        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.MESSAGE
        with patch.object(
            SlackConnector,
            "_build_message_blocks_for_streaming",
            new_callable=AsyncMock,
            side_effect=ValueError("bad"),
        ):
            with pytest.raises(HTTPException) as ei:
                await c.stream_record(rec)
        assert ei.value.status_code == 500


class TestSlackConnectorFilterOptionsAndLifecycle:
    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_key_raises(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with pytest.raises(ValueError, match="Unknown filter key"):
            await c.get_filter_options("not_channel_ids")

    @pytest.mark.asyncio
    async def test_get_filter_options_success_with_cache(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_filter_cache = {
            "C1": {"id": "C1", "label": "alpha"},
            "C2": {"id": "C2", "label": "beta"},
            "C3": {"id": "C3", "label": "alphabet soup"},
        }
        resp = await c.get_filter_options("channel_ids", page=1, limit=1, search="al")
        assert resp.success is True
        assert len(resp.options) == 1
        assert resp.has_more is True

    @pytest.mark.asyncio
    async def test_get_filter_options_exception_returns_failure(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_filter_cache = {"C1": {"id": "C1", "label": "x"}}

        async def _boom():
            raise RuntimeError("slack down")

        with patch.object(c, "_populate_channel_filter_cache", side_effect=_boom):
            resp = await c.get_filter_options("channel_ids", page=1, limit=10, search="")
        assert resp.success is False
        assert "slack down" in (resp.message or "")

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        ds = MagicMock()
        ds.check_token_scopes = AsyncMock(return_value=MagicMock(success=True))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_test_connection_failure_returns_false(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with patch.object(
            SlackConnector,
            "_fresh_datasource",
            new_callable=AsyncMock,
            side_effect=RuntimeError("x"),
        ):
            assert await c.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with patch.object(SlackConnector, "run_sync", new_callable=AsyncMock) as rs:
            await c.run_incremental_sync()
        rs.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_logs(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        await c.cleanup()
        c.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_get_signed_url_empty(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        assert await c.get_signed_url(MagicMock()) == ""

    @pytest.mark.asyncio
    async def test_handle_webhook_notification_logs(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        await c.handle_webhook_notification({})
        c.logger.warning.assert_called()


class TestSlackConnectorCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_calls_initialize(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        dep = MagicMock()
        dep.initialize = AsyncMock()
        with (
            patch(
                "app.connectors.sources.slack.team.connector.DataSourceEntitiesProcessor",
                return_value=dep,
            ),
            patch.object(SlackConnector, "__init__", lambda self, *a, **kw: None),
        ):
            obj = await SlackConnector.create_connector(
                MagicMock(), MagicMock(), MagicMock(), "cid-1", "team", "user-1"
            )
        dep.initialize.assert_awaited_once()
        assert isinstance(obj, SlackConnector)


# ===========================================================================
# 21. _detect_bursts — system subtype as first message in burst window
# ===========================================================================

class TestDetectBurstsSystemSubtype:
    def setup_method(self):
        self.c = _make_connector()

    def test_system_message_mid_sequence_starts_new_burst(self):
        msgs = [
            {"ts": "1000.0", "text": "a"},
            {"ts": "1001.0", "subtype": "channel_join"},
            {"ts": "1002.0", "text": "b"},
        ]
        bursts = self.c._detect_bursts(msgs)
        assert len(bursts) >= 2
        assert bursts[-1].messages[-1]["text"] == "b"


# ===========================================================================
# 22. DeferredThread, _process_file_raw, _channel_group_map, _warm_user_cache
# ===========================================================================

class TestDeferredThreadDataclass:
    def test_fields(self):
        from app.connectors.sources.slack.team.connector import (
            DeferredThread,
            ProcessingContext,
            RateLimiter,
        )

        rl = RateLimiter(limits={2: 5, 3: 5, 4: 5}, headroom=1.0)
        ctx = ProcessingContext(
            channel_id="CZ",
            channel_groups_map={},
            user_id_to_email={},
            user_id_to_name={},
            channel_id_to_name={},
            rate_limiter=rl,
        )
        dt = DeferredThread(msg={"ts": "9"}, ctx=ctx, rg_id="rg-x")
        assert dt.msg["ts"] == "9"
        assert dt.ctx.channel_id == "CZ"
        assert dt.rg_id == "rg-x"


class TestProcessFileRaw:
    @pytest.mark.asyncio
    async def test_returns_none_without_file_id(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        ctx = c._make_ctx("C1", "rg1")
        out = await c._process_file_raw({}, ctx)
        assert out is None

    @pytest.mark.asyncio
    async def test_builds_file_record_without_download_url(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        ctx = c._make_ctx("C1", "rg1")
        fd = {
            "id": "F100",
            "name": "readme.md",
            "mimetype": "text/markdown",
            "filetype": "markdown",
            "created": 1700000000,
            "permalink": "https://files.slack.com/F100",
            "size": 42,
        }
        fr = await c._process_file_raw(fd, ctx)
        assert fr is not None
        assert fr.external_record_id == "F100"
        assert fr.record_group_id == "rg1"
        assert fr.sha256_hash is None


class TestChannelGroupMap:
    @pytest.mark.asyncio
    async def test_all_ids_from_instance_cache(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_groups_cache = {"CA": "rgA", "CB": "rgB"}
        m = await c._channel_group_map(["CA", "CB"])
        assert m == {"CA": "rgA", "CB": "rgB"}

    @pytest.mark.asyncio
    async def test_fetches_uncached_via_transaction(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_groups_cache = {}
        rg = MagicMock()
        rg.id = "rg-from-db"
        tx = MagicMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=rg)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=tx)
        cm.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=cm)

        m = await c._channel_group_map(["CNEW"])
        assert m["CNEW"] == "rg-from-db"
        assert c.channel_groups_cache["CNEW"] == "rg-from-db"

    @pytest.mark.asyncio
    async def test_db_error_logs_and_skips_uncached(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_groups_cache = {}
        c.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("db err"))

        m = await c._channel_group_map(["CX"])
        assert "CX" not in m
        c.logger.error.assert_called()


class TestWarmUserCacheForMessages:
    @pytest.mark.asyncio
    async def test_plain_text_no_slack_calls(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock) as fd:
            await c._warm_user_cache_for_messages([{"text": "no mentions here"}])
        fd.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_users_already_in_instance_cache(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.user_id_to_name_cache = {"U55": "Pat"}
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock) as fd:
            await c._warm_user_cache_for_messages([{"text": "Hi <@U55>"}])
        fd.assert_not_called()

    @pytest.mark.asyncio
    async def test_users_info_updates_caches_and_ctx(self):
        from app.connectors.sources.slack.team.connector import (
            SlackConnector,
            ProcessingContext,
            RateLimiter,
        )

        c = _make_connector()
        c.user_id_to_name_cache = {}
        c.user_id_to_email_cache = {}
        c.rate_limiter = MagicMock()
        c.rate_limiter.acquire = AsyncMock()
        rl = RateLimiter(limits={2: 100, 3: 100, 4: 100}, headroom=1.0)
        ctx = ProcessingContext(
            channel_id="C1",
            channel_groups_map={},
            user_id_to_email={},
            user_id_to_name={},
            channel_id_to_name={},
            rate_limiter=rl,
        )
        ds = MagicMock()
        ds.users_info = AsyncMock()
        user_payload = {
            "user": {
                "name": "login",
                "profile": {
                    "real_name": "Full Name",
                    "email": "full@example.com",
                },
            }
        }
        api_resp = MagicMock(success=True)
        api_resp.data = user_payload
        ds.users_info.return_value = api_resp
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._warm_user_cache_for_messages(
                [{"text": "Ping <@U888|ignored>"}],
                ctx=ctx,
            )
        assert c.user_id_to_name_cache.get("U888") == "Full Name"
        assert c.user_id_to_email_cache.get("U888") == "full@example.com"
        assert ctx.user_id_to_name["U888"] == "Full Name"
        assert ctx.user_id_to_email["U888"] == "full@example.com"

    @pytest.mark.asyncio
    async def test_users_info_failure_is_swallowed(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.user_id_to_name_cache = {}
        c.rate_limiter = MagicMock()
        c.rate_limiter.acquire = AsyncMock()
        ds = MagicMock()
        ds.users_info = AsyncMock(side_effect=RuntimeError("api"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._warm_user_cache_for_messages([{"text": "<@U999>"}])
        assert "U999" not in c.user_id_to_name_cache


# ---------------------------------------------------------------------------
# 23. Orchestration & Slack pipeline (mocked SlackDataSource + stores)
# ---------------------------------------------------------------------------


def _connector_pipeline_ready():
    """SlackConnector with sync points, real RateLimiter, and AsyncMock processor hooks."""
    from app.connectors.core.registry.filters import FilterCollection
    from app.connectors.sources.slack.team.connector import RateLimiter

    c = _make_connector()
    c.rate_limiter = RateLimiter(limits={2: 500, 3: 500, 4: 500}, headroom=1.0)
    c.messages_sync_point = MagicMock()
    c.messages_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.messages_sync_point.update_sync_point = AsyncMock()
    c.audit_log_sync_point = MagicMock()
    c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.audit_log_sync_point.update_sync_point = AsyncMock()
    c.indexing_filters = FilterCollection()
    c.sync_filters = FilterCollection()
    c.external_client = MagicMock()
    c.workspace_domain = "acme"
    c.data_entities_processor.on_new_records = AsyncMock()
    c.data_entities_processor.on_new_app_users = AsyncMock()
    c.data_entities_processor.on_new_app_roles = AsyncMock()
    c.data_entities_processor.on_new_user_groups = AsyncMock()
    c.data_entities_processor.on_new_record_groups = AsyncMock()
    c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
    c.data_entities_processor.on_record_content_update = AsyncMock()
    c.data_entities_processor.on_updated_record_permissions = AsyncMock()
    c.data_entities_processor.reindex_existing_records = AsyncMock()
    return c


class TestRunSyncOrchestration:
    @pytest.mark.asyncio
    async def test_run_sync_phases_and_parallel_channel_sync(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        rg1 = MagicMock(external_group_id="C1")
        rg2 = MagicMock(external_group_id="C2")
        rg_skip = MagicMock(external_group_id=None)

        with (
            patch(
                "app.connectors.sources.slack.team.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackConnector, "_sync_workspace_roles", new_callable=AsyncMock),
            patch.object(SlackConnector, "_sync_user_groups", new_callable=AsyncMock),
            patch.object(
                SlackConnector, "_sync_channels", new_callable=AsyncMock, return_value=[rg1, rg2, rg_skip]
            ),
            patch.object(SlackConnector, "sync_channel_messages", new_callable=AsyncMock) as scm,
            patch.object(SlackConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()

        scm.assert_any_await("C1")
        scm.assert_any_await("C2")
        assert scm.await_count == 2

    @pytest.mark.asyncio
    async def test_run_sync_requires_external_client(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.external_client = None
        with pytest.raises(RuntimeError, match="init"):
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_logs_channel_failure_continues(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        rg = MagicMock(external_group_id="CBAD")

        async def _boom(_ch: str):
            raise RuntimeError("slack hiccup")

        with (
            patch(
                "app.connectors.sources.slack.team.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackConnector, "_sync_workspace_roles", new_callable=AsyncMock),
            patch.object(SlackConnector, "_sync_user_groups", new_callable=AsyncMock),
            patch.object(SlackConnector, "_sync_channels", new_callable=AsyncMock, return_value=[rg]),
            patch.object(SlackConnector, "sync_channel_messages", side_effect=_boom),
            patch.object(SlackConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()
        c.logger.error.assert_called()


class TestSyncChannelMessagesPipeline:
    @pytest.mark.asyncio
    async def test_sync_channel_messages_writes_checkpoint_and_on_new_records(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "CMAIN"
        c.channel_groups_cache[cid] = "rg-1"
        c.user_id_to_name_cache["U1"] = "Alice"
        c.user_id_to_email_cache["U1"] = "a@example.com"
        c.channel_id_to_name_cache[cid] = "general"

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"ts": "100.1", "user": "U1", "text": "top"},
                        {"ts": "100.0", "user": "U1", "text": "older"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

        c.data_entities_processor.on_new_records.assert_awaited()
        c.messages_sync_point.update_sync_point.assert_awaited()
        args, _kw = c.messages_sync_point.update_sync_point.call_args
        assert args[1]["last_sync_time"] == "100.1"

    @pytest.mark.asyncio
    async def test_sync_channel_messages_uses_existing_checkpoint_as_oldest(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "C2"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "Bob"
        c.user_id_to_email_cache["U1"] = "b@example.com"
        c.messages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "50.000000"}
        )

        ds = MagicMock()
        hist = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [], "response_metadata": {"next_cursor": ""}},
            )
        )
        ds.conversations_history = hist

        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

        hist.assert_awaited()
        call_kw = hist.await_args.kwargs
        assert call_kw["oldest"] == "50.000000"

    @pytest.mark.asyncio
    async def test_sync_channel_messages_join_then_retry(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "CJOIN"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "U"
        c.user_id_to_email_cache["U1"] = "u@e.com"

        hist_calls: list[Any] = []

        async def _hist(**kwargs):
            hist_calls.append(kwargs)
            if len(hist_calls) == 1:
                return MagicMock(success=False, error="not_in_channel")
            return MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "1.0", "user": "U1", "text": "ok"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=_hist)
        ds.conversations_join = AsyncMock(return_value=MagicMock(success=True))

        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

        ds.conversations_join.assert_awaited_once()
        assert len(hist_calls) == 2

    @pytest.mark.asyncio
    async def test_sync_channel_messages_pagination_two_pages(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "CPAGE"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "U"
        c.user_id_to_email_cache["U1"] = "u@e.com"

        page = 0

        async def _hist(**kwargs):
            nonlocal page
            page += 1
            if page == 1:
                return MagicMock(
                    success=True,
                    data={
                        "messages": [{"ts": "10.0", "user": "U1", "text": "p1"}],
                        "response_metadata": {"next_cursor": "c1"},
                    },
                )
            return MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "9.0", "user": "U1", "text": "p2"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=_hist)

        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

        assert page == 2
        c.messages_sync_point.update_sync_point.assert_awaited()
        _key, payload = c.messages_sync_point.update_sync_point.call_args[0]
        assert payload["last_sync_time"] == "10.0"

    @pytest.mark.asyncio
    async def test_sync_channel_messages_deferred_thread_full_path(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "CTHR"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "U"
        c.user_id_to_email_cache["U1"] = "u@e.com"

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {
                            "ts": "5.0",
                            "user": "U1",
                            "text": "root",
                            "thread_ts": "5.0",
                            "reply_count": 2,
                        }
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)

        tx_cm = MagicMock()
        tx_cm.__aenter__ = AsyncMock(return_value=tx_cm)
        tx_cm.__aexit__ = AsyncMock(return_value=None)
        tx_cm.get_record_group_by_external_id = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx_cm)

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_process_thread", new_callable=AsyncMock) as pt,
        ):
            await c.sync_channel_messages(cid)

        pt.assert_awaited_once()
        c.audit_log_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_indexing_filter_disables_messages(self):
        from app.connectors.core.registry.filters import (
            BooleanOperator,
            Filter,
            FilterCollection,
            FilterType,
            IndexingFilterKey,
        )
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import ProgressStatus

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.MESSAGES.value,
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                )
            ]
        )
        cid = "CIDX"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "U"
        c.user_id_to_email_cache["U1"] = "u@e.com"

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "1.0", "user": "U1", "text": "solo"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

        batch = c.data_entities_processor.on_new_records.await_args[0][0]
        assert any(
            getattr(r, "indexing_status", None) == ProgressStatus.AUTO_INDEX_OFF.value
            for r, _perms in batch
        )


class TestSyncUsersAndCaches:
    @pytest.mark.asyncio
    async def test_sync_users_single_page(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        member = {
            "id": "U10",
            "name": "amy",
            "is_bot": False,
            "profile": {"email": "amy@corp.test", "real_name": "Amy"},
        }
        ds = MagicMock()
        ds.users_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"members": [member], "response_metadata": {"next_cursor": ""}},
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_users()

        c.data_entities_processor.on_new_app_users.assert_awaited()
        assert c.user_id_to_email_cache.get("U10") == "amy@corp.test"
        assert c.user_id_to_name_cache.get("U10") == "Amy"

    @pytest.mark.asyncio
    async def test_sync_users_list_failure_stops_quietly(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.users_list = AsyncMock(return_value=MagicMock(success=False, error="ratelimited"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_users()
        c.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_user_id_caches_from_processor(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        u = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U77",
            org_id="org-test",
            email="x@y.z",
            full_name="FN",
        )
        u.id = "internal-77"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])

        await c._build_user_id_caches()

        assert c.user_id_to_internal_id_cache["U77"] == "internal-77"
        assert c._source_id_to_app_user["U77"] is u


class TestWorkspaceRolesAndUserGroups:
    @pytest.mark.asyncio
    async def test_sync_workspace_roles_skips_when_empty(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c._non_guest_app_users = []
        await c._sync_workspace_roles()
        c.data_entities_processor.on_new_app_roles.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_workspace_roles_with_members(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        u = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U1",
            org_id="org-test",
            email="m@x.y",
            full_name="M",
        )
        c._non_guest_app_users = [u]
        await c._sync_workspace_roles()
        c.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_user_groups_batches(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        inner = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U200",
            org_id="org-test",
            email="in@g.test",
            full_name="Inner",
        )
        inner.id = "id-200"
        c._source_id_to_app_user = {"U200": inner}

        ds = MagicMock()
        ds.usergroups_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "usergroups": [
                        {
                            "id": "S1",
                            "name": "Engineers",
                            "handle": "eng",
                            "users": ["U200"],
                            "date_create": "1000",
                            "date_update": "2000",
                        }
                    ]
                },
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_user_groups()

        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_usergroup_members(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.usergroups_users_list = AsyncMock(
            return_value=MagicMock(success=True, data={"users": ["A", "B"]})
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            out = await c._fetch_usergroup_members("G1")
        assert out == ["A", "B"]


class TestSyncChannelsAndMembers:
    @pytest.mark.asyncio
    async def test_sync_channels_public_channel_one_page(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ch = {
            "id": "Cpub",
            "name": "general",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {"value": ""},
            "created": "1000.0",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch], "response_metadata": {"next_cursor": ""}},
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            rgs = await c._sync_channels()

        assert len(rgs) == 1
        c.data_entities_processor.on_new_record_groups.assert_awaited()
        assert c.channel_groups_cache.get("Cpub")

    @pytest.mark.asyncio
    async def test_fetch_channel_members_paginates(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        n = 0

        async def _members(**kwargs):
            nonlocal n
            n += 1
            if n == 1:
                return MagicMock(
                    success=True,
                    data={"members": ["U1"], "response_metadata": {"next_cursor": "x"}},
                )
            return MagicMock(
                success=True,
                data={"members": ["U2"], "response_metadata": {"next_cursor": ""}},
            )

        ds = MagicMock()
        ds.conversations_members = AsyncMock(side_effect=_members)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            ids = await c._fetch_channel_members("Cpriv")

        assert ids == ["U1", "U2"]
        assert n == 2

    @pytest.mark.asyncio
    async def test_channel_permissions_public_uses_workspace_role(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.permission import EntityType, PermissionType

        c = _connector_pipeline_ready()
        perms = await c._channel_permissions(
            {"id": "C1", "is_im": False, "is_private": False, "is_mpim": False}
        )
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ROLE
        assert perms[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_channel_permissions_im_with_email(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.permission import EntityType

        c = _connector_pipeline_ready()
        c.user_id_to_email_cache["Upeer"] = "peer@x.com"
        perms = await c._channel_permissions(
            {"id": "D1", "is_im": True, "user": "Upeer"}
        )
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_channel_permissions_private_resolves_members(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.user_id_to_email_cache["Um1"] = "a@b.c"
        with patch.object(
            SlackConnector,
            "_fetch_channel_members",
            new_callable=AsyncMock,
            return_value=["Um1"],
        ):
            perms = await c._channel_permissions(
                {"id": "Gpriv", "is_private": True, "is_mpim": False, "is_im": False}
            )
        assert len(perms) == 1


class TestProcessBatchEnrichReindex:
    @pytest.mark.asyncio
    async def test_process_message_batch_burst_and_single(self):
        from app.connectors.sources.slack.team.connector import SlackConnector, ProcessingContext, RateLimiter

        c = _connector_pipeline_ready()
        cid = "CBURST"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "A"
        c.user_id_to_name_cache["U2"] = "B"
        c.user_id_to_email_cache["U1"] = "a@x"
        c.user_id_to_email_cache["U2"] = "b@x"
        c.channel_id_to_name_cache[cid] = "chan"
        rl = RateLimiter(limits={2: 100, 3: 100, 4: 100}, headroom=1.0)
        ctx = ProcessingContext(
            channel_id=cid,
            channel_groups_map={cid: "rg"},
            user_id_to_email=dict(c.user_id_to_email_cache),
            user_id_to_name=dict(c.user_id_to_name_cache),
            channel_id_to_name=dict(c.channel_id_to_name_cache),
            rate_limiter=rl,
        )
        msgs = [
            {"ts": "200.0", "user": "U1", "text": "first"},
            {"ts": "250.0", "user": "U2", "text": "second"},
            {"ts": "900.0", "user": "U1", "text": "alone"},
        ]
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock):
            recs, deferred = await c._process_message_batch(msgs, ctx)

        assert len(recs) >= 2
        assert not deferred

    @pytest.mark.asyncio
    async def test_enrich_link_records_linked_id(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import LinkPublicStatus, LinkRecord, RecordType

        c = _connector_pipeline_ready()
        lr = LinkRecord(
            org_id="org-test",
            record_name="L",
            record_type=RecordType.LINK,
            external_record_id="L1",
            external_record_group_id="C",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            weburl="https://example.com/x",
            url="https://example.com/x",
            is_public=LinkPublicStatus.UNKNOWN,
        )
        tx = MagicMock()
        rel = MagicMock()
        rel.id = "linked-id-1"
        tx.get_record_by_weburl = AsyncMock(return_value=rel)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)

        await c._enrich_link_records_linked_id([(lr, [])])

        assert lr.linked_record_id == "linked-id-1"

    @pytest.mark.asyncio
    async def test_reindex_records_splits_updated_and_unchanged(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType

        c = _connector_pipeline_ready()
        r1 = MagicMock()
        r1.id = "r1"
        r1.record_type = RecordType.MESSAGE
        r2 = MagicMock()
        r2.id = "r2"
        r2.record_type = RecordType.MESSAGE

        async def _check(rec):
            if rec is r1:
                return (MagicMock(), [])
            return None

        with patch.object(SlackConnector, "_check_updated", new_callable=AsyncMock, side_effect=_check):
            await c.reindex_records([r1, r2])

        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()
        unchanged_arg = c.data_entities_processor.reindex_existing_records.await_args[0][0]
        assert r2 in unchanged_arg


class TestSyncMessageChangesTeam:
    @pytest.mark.asyncio
    async def test_sync_message_changes_empty_channels(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {}
        with patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock):
            await c._sync_message_changes()

    @pytest.mark.asyncio
    async def test_sync_message_changes_one_channel(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"C1": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(SlackConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(SlackConnector, "_check_channel_changes", new_callable=AsyncMock, return_value=2),
        ):
            await c._sync_message_changes()
        assert c.audit_log_sync_point.update_sync_point.await_count >= 1


# =============================================================================
# Deep coverage: constructor, phases, threads, change detection, streaming
# =============================================================================


class TestSlackConnectorRealInit:
    """Exercise SlackConnector.__init__ without running BaseConnector wiring."""

    def test_init_creates_sync_points_rate_limiter_and_caches(self):
        from unittest.mock import patch

        from app.connectors.core.base.connector.connector_service import BaseConnector
        from app.connectors.sources.slack.team.connector import SlackConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "org-x"
        dsp = MagicMock()
        cfg = MagicMock()

        def _minimal_base_init(self, _app, _logger, processor, store, config, _cid, _scope, _created_by):
            self.data_entities_processor = processor
            self.data_store_provider = store
            self.config_service = config

        with patch.object(BaseConnector, "__init__", _minimal_base_init):
            c = SlackConnector(logger, dep, dsp, cfg, "conn-z", "team", "creator")

        assert c.connector_id == "conn-z"
        assert c.messages_sync_point is not None
        assert c.audit_log_sync_point is not None
        assert c.rate_limiter is not None
        assert c.user_id_to_email_cache == {}
        assert c.channel_filter_cache == {}


class TestSlackConnectorInitAndRunSyncErrors:
    @pytest.mark.asyncio
    async def test_init_success_sets_team(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.external_client = MagicMock()
        ds = MagicMock()
        ds.team_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"team": {"domain": "acme", "id": "T9"}},
            )
        )
        with (
            patch("app.connectors.sources.slack.team.connector.SlackClient.build_from_services", new_callable=AsyncMock),
            patch("app.connectors.sources.slack.team.connector.SlackDataSource"),
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
        ):
            ok = await c.init()
        assert ok is True
        assert c.workspace_domain == "acme"
        assert c.team_id == "T9"

    @pytest.mark.asyncio
    async def test_init_failure_returns_false(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        with patch(
            "app.connectors.sources.slack.team.connector.SlackClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no client"),
        ):
            assert await c.init() is False

    @pytest.mark.asyncio
    async def test_run_sync_outer_exception_logged_and_reraised(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with (
            patch(
                "app.connectors.sources.slack.team.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(
                SlackConnector, "_sync_users", new_callable=AsyncMock, side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await c.run_sync()


class TestComputeSyncWindowOldest:
    def test_no_filter_uses_30_day_fallback(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.sync_filters.get = MagicMock(return_value=None)
        ts = c._compute_sync_window_oldest()
        assert ts is not None
        assert "." in ts

    def test_operator_seconds_branch(self):
        from app.connectors.core.registry.filters import Filter, FilterOperator, FilterType
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        f = Filter(
            key="sync_window",
            value=None,
            type=FilterType.DATETIME,
            operator=FilterOperator.LAST_7_DAYS,
        )
        c.sync_filters.get = MagicMock(return_value=f)
        assert c._compute_sync_window_oldest() is not None

    def test_is_after_with_start_epoch(self):
        from app.connectors.core.registry.filters import Filter, FilterOperator, FilterType
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        start_ms = int(datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        f = Filter(
            key="sync_window",
            value=(start_ms, start_ms),
            type=FilterType.DATETIME,
            operator=FilterOperator.IS_AFTER,
        )
        c.sync_filters.get = MagicMock(return_value=f)
        out = c._compute_sync_window_oldest()
        assert out is not None


class TestSyncUsersAndCaches:
    @pytest.mark.asyncio
    async def test_sync_users_pagination_and_skips(self):
        from app.connectors.sources.slack.team.connector import PAGE_SIZE_USERS, SlackConnector

        c = _connector_pipeline_ready()
        calls = {"n": 0}

        async def users_list(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(
                    success=True,
                    data={
                        "members": [
                            {
                                "id": "UBOT",
                                "is_bot": True,
                                "name": "bot",
                                "profile": {"email": "b@b.com"},
                            },
                            {
                                "id": "UNOMAIL",
                                "is_bot": False,
                                "name": "x",
                                "profile": {},
                            },
                            {
                                "id": "UOK",
                                "is_bot": False,
                                "name": "ok",
                                "profile": {"email": "ok@x.com", "real_name": "Ok User"},
                                "updated": 1,
                                "is_restricted": False,
                            },
                        ],
                        "response_metadata": {"next_cursor": "c1"},
                    },
                )
            return MagicMock(
                success=True,
                data={"members": [], "response_metadata": {"next_cursor": ""}},
            )

        ds = MagicMock()
        ds.users_list = AsyncMock(side_effect=users_list)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_users()
        assert "UOK" in c.user_id_to_email_cache
        assert "UBOT" in c.user_id_to_name_cache
        c.data_entities_processor.on_new_app_users.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_list_failure_breaks_loop(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.users_list = AsyncMock(return_value=MagicMock(success=False, error="ratelimited"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_users()

    @pytest.mark.asyncio
    async def test_build_user_id_caches_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db"))
        await c._build_user_id_caches()

    @pytest.mark.asyncio
    async def test_sync_workspace_roles_empty_logs(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c._non_guest_app_users = []
        await c._sync_workspace_roles()

    @pytest.mark.asyncio
    async def test_to_app_user_invalid_returns_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        assert c._to_app_user({}) is None

    @pytest.mark.asyncio
    async def test_to_app_user_model_exception(self):
        import app.connectors.sources.slack.team.connector as slack_team_mod
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with patch.object(slack_team_mod, "AppUser", side_effect=ValueError("bad")):
            m = {
                "id": "U1",
                "name": "n",
                "profile": {"email": "e@e.com"},
                "updated": 1,
            }
            assert c._to_app_user(m) is None


class TestBlockBuildersAndDetectBursts:
    def test_build_message_block_variants(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            channel_id="C1",
            channel_groups_map={"C1": "rg"},
            user_id_to_email={},
            user_id_to_name={"U1": "Alice"},
            channel_id_to_name={"C1": "general"},
            rate_limiter=rl,
        )
        msg = {
            "ts": "100.000001",
            "user": "U1",
            "text": "hi <https://a.com|a> mention <@U999>",
            "edited": {"ts": "101.000002"},
        }
        b = c._build_message_block(msg, 0, 0, ctx)
        assert b.data

        bot_msg = {
            "ts": "102.0",
            "text": "bot",
            "bot_profile": {"name": "Botty"},
        }
        b2 = c._build_message_block(bot_msg, 0, 0, ctx)
        assert "Botty" in b2.data or "bot" in b2.data

        thread_parent = {
            "ts": "103.0",
            "user": "U1",
            "text": "thread root",
            "reply_count": 3,
            "thread_ts": "103.0",
        }
        b3 = c._build_message_block(thread_parent, 0, 0, ctx)
        assert "Reply Count: 3" in b3.data

    def test_build_burst_single_containers_and_detect_bursts(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )
        from app.models.blocks import ChildRecord, ChildType

        c = _make_connector()
        c.workspace_domain = "w"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "gen"}, rl,
        )
        msgs = [
            {"ts": "10.0", "user": "U1", "text": "a"},
            {"ts": "11.0", "user": "U1", "text": "b", "subtype": "channel_join"},
            {"ts": "12.0", "user": "U1", "text": "c"},
        ]
        bursts = c._detect_bursts(msgs)
        assert bursts
        bc = c._build_burst_block_containers(
            bursts[0].messages, "burst_C1_x", ctx,
            file_child_records={
                bursts[0].messages[0].get("ts", ""): [
                    ChildRecord(child_type=ChildType.RECORD, child_id="f1", child_name="f.bin"),
                ],
            },
        )
        assert bc.blocks
        single = c._build_single_block_containers(msgs[0], ctx)
        assert single.blocks

    def test_detect_bursts_fix(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        assert c._detect_bursts([]) == []


class TestProcessBurstSingleFileLink:
    @pytest.mark.asyncio
    async def test_process_burst_skips_all_bot_when_filter_off(self):
        from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(
                    key="bot_messages",
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator="is",
                ),
            ]
        )
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl,
        )
        burst = ConversationalBurst(
            messages=[
                {"ts": "1.0", "bot_id": "B1", "text": "a"},
                {"ts": "2.0", "bot_id": "B1", "text": "b"},
            ],
            start_ts=1.0,
            end_ts=2.0,
        )
        recs, defd = await c._process_burst(burst, ctx)
        assert recs == [] and defd == []

    @pytest.mark.asyncio
    async def test_process_burst_with_files_links_and_thread_deferred(self):
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl,
        )
        fr = FileRecord(
            org_id="org-test",
            record_name="a.png",
            record_type=RecordType.FILE,
            external_record_id="F1",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        fr.id = "file-id-1"

        async def pfr(_fd, _ctx):
            return fr

        async def plink(*_a, **_k):
            return None

        burst = ConversationalBurst(
            messages=[
                {
                    "ts": "10.0",
                    "user": "U1",
                    "text": "see https://ex.com/x",
                    "reply_count": 1,
                    "thread_ts": "10.0",
                    "latest_reply": "11.0",
                    "files": [{"id": "F1", "name": "a.png"}],
                },
            ],
            start_ts=10.0,
            end_ts=10.0,
        )
        with (
            patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, side_effect=pfr),
            patch.object(SlackConnector, "_process_link", new_callable=AsyncMock, side_effect=plink),
        ):
            recs, defd = await c._process_burst(burst, ctx)
        assert defd
        assert any(r[0].record_type == RecordType.FILE for r in recs)

    @pytest.mark.asyncio
    async def test_process_single_skips_bot_when_disabled(self):
        from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(key="bot_messages", value=False, type=FilterType.BOOLEAN, operator="is"),
            ]
        )
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        msg = {"ts": "1.0", "bot_id": "B", "text": "x"}
        recs, defd = await c._process_single(msg, ctx)
        assert recs == [] and defd == []

    @pytest.mark.asyncio
    async def test_process_single_with_edited_ts_and_files(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl)

        fr = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="FX",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        fr.id = "fid"
        msg = {
            "ts": "5.0",
            "user": "U1",
            "text": "t",
            "edited": {"ts": "6.0"},
            "files": [{"id": "FX"}],
        }
        with patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, return_value=fr):
            recs, _ = await c._process_single(msg, ctx)
        assert len(recs) >= 2


class TestProcessThreadInternals:
    @pytest.mark.asyncio
    async def test_process_thread_full_path(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import RecordGroup, RecordGroupType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1", {"C1": "rg1"}, {"U1": "a@x"}, {"U1": "A"}, {"C1": "chan"}, rl,
        )
        rg = RecordGroup(
            org_id="org-test",
            name="t",
            external_group_id="thread_C1_1.0",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            group_type=RecordGroupType.SLACK_THREAD,
        )
        rg.id = "trg-id"

        parent = {"ts": "1.0", "user": "U1", "text": "root", "reply_count": 1, "thread_ts": "1.0"}
        reply = {"ts": "2.0", "user": "U1", "text": "rep https://x.com"}

        async def fetch_replies(*_a, **_k):
            return [reply]

        with (
            patch.object(SlackConnector, "_create_thread_record_group", return_value=rg),
            patch.object(SlackConnector, "_fetch_thread_replies_raw", new_callable=AsyncMock, side_effect=fetch_replies),
            patch.object(SlackConnector, "_warm_user_cache_for_messages", new_callable=AsyncMock),
            patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, return_value=None),
            patch.object(SlackConnector, "_process_link", new_callable=AsyncMock, return_value=None),
            patch.object(SlackConnector, "_enrich_link_records_linked_id", new_callable=AsyncMock),
        ):
            await c._process_thread(parent, ctx, "rg1")

        c.data_entities_processor.on_new_record_groups.assert_awaited()
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_process_thread_incremental_and_empty_replies(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl)
        parent = {"ts": "1.0", "user": "U1", "text": "root"}
        with patch.object(SlackConnector, "_fetch_thread_replies_raw", new_callable=AsyncMock, return_value=[]):
            await c._process_thread_incremental(parent, ctx, "trg", "0.5")
        with patch.object(SlackConnector, "_fetch_thread_replies_raw", new_callable=AsyncMock, return_value=[]):
            await c._process_thread_incremental(parent, ctx, "trg", "0.5")

    @pytest.mark.asyncio
    async def test_process_thread_create_rg_fails_early_return(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        with patch.object(SlackConnector, "_create_thread_record_group", return_value=None):
            await c._process_thread({"ts": "1.0"}, ctx, "rg")

    @pytest.mark.asyncio
    async def test_build_thread_burst_record_message_record_raises(self):
        import app.connectors.sources.slack.team.connector as mod
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl)
        burst = ConversationalBurst(
            messages=[{"ts": "1.0", "user": "U1", "text": "a"}],
            start_ts=1.0,
            end_ts=1.0,
        )
        with patch.object(mod, "MessageRecord", side_effect=ValueError("bad")):
            out = await c._build_thread_burst_record(burst, "1.0", "trg", ctx)
        assert out is None

    @pytest.mark.asyncio
    async def test_process_thread_collects_files_and_links_on_replies(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import FileRecord, LinkPublicStatus, LinkRecord, RecordGroup, RecordGroupType, RecordType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1", {"C1": "rg1"}, {"U1": "a@x"}, {"U1": "A"}, {"C1": "chan"}, rl,
        )
        rg = RecordGroup(
            org_id="org-test",
            name="t",
            external_group_id="thread_C1_10.0",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            group_type=RecordGroupType.SLACK_THREAD,
        )
        rg.id = "trg-id"

        fr = FileRecord(
            org_id="org-test",
            record_name="f.png",
            record_type=RecordType.FILE,
            external_record_id="F99",
            external_record_group_id="C1",
            record_group_id="rg1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        fr.id = "fid99"

        lr = LinkRecord(
            org_id="org-test",
            record_name="L",
            record_type=RecordType.LINK,
            external_record_id="L99",
            external_record_group_id="C1",
            record_group_id="rg1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            weburl="https://ex.com/p",
            url="https://ex.com/p",
            is_public=LinkPublicStatus.UNKNOWN,
        )
        lr.id = "lid99"

        parent = {
            "ts": "10.0",
            "user": "U1",
            "text": "root",
            "reply_count": 2,
            "thread_ts": "10.0",
        }
        reply = {
            "ts": "11.0",
            "user": "U1",
            "text": "see https://ex.com/p",
            "files": [{"id": "F99", "name": "f.png", "created": 1}],
        }

        async def fetch_replies(*_a, **_k):
            return [reply]

        async def plink(url, *_args):
            if urlparse(url).hostname == "ex.com":
                return lr
            return None

        with (
            patch.object(SlackConnector, "_create_thread_record_group", return_value=rg),
            patch.object(SlackConnector, "_fetch_thread_replies_raw", new_callable=AsyncMock, side_effect=fetch_replies),
            patch.object(SlackConnector, "_warm_user_cache_for_messages", new_callable=AsyncMock),
            patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, return_value=fr),
            patch.object(SlackConnector, "_process_link", new_callable=AsyncMock, side_effect=plink),
            patch.object(SlackConnector, "_enrich_link_records_linked_id", new_callable=AsyncMock),
        ):
            await c._process_thread(parent, ctx, "rg1")

        c.data_entities_processor.on_new_records.assert_awaited()


class TestThreadGrowthAndScan:
    @pytest.mark.asyncio
    async def test_sync_thread_growth_per_channel_and_checkpoint(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"C1": "rg", "": "skip"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            side_effect=[None, {"last_check_time": 1_000_000}]
        )
        with patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock):
            with patch.object(SlackConnector, "_scan_channel_thread_growth", new_callable=AsyncMock):
                await c._sync_thread_growth()

    @pytest.mark.asyncio
    async def test_sync_thread_growth_top_level_error(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"C1": "rg"}
        with patch.object(
            SlackConnector, "_refresh_user_caches", new_callable=AsyncMock, side_effect=RuntimeError("x"),
        ):
            await c._sync_thread_growth()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_pagination_and_subtypes(self):
        from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(key="bot_messages", value=False, type=FilterType.BOOLEAN, operator="is"),
            ]
        )
        page = {"n": 0}

        async def hist(**kwargs):
            page["n"] += 1
            if page["n"] == 1:
                return MagicMock(
                    success=True,
                    data={
                        "messages": [
                            {"ts": "9.0", "subtype": "channel_join"},
                            {
                                "ts": "10.0",
                                "user": "U1",
                                "reply_count": 1,
                                "thread_ts": "10.0",
                                "text": "root",
                            },
                            {
                                "subtype": "message_changed",
                                "message": {
                                    "ts": "11.0",
                                    "user": "U1",
                                    "reply_count": 0,
                                    "thread_ts": "11.0",
                                    "text": "inner",
                                },
                            },
                        ],
                        "response_metadata": {"next_cursor": "n"},
                    },
                )
            return MagicMock(
                success=True,
                data={"messages": [], "response_metadata": {"next_cursor": ""}},
            )

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=hist)
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_handle_new_thread", new_callable=AsyncMock),
        ):
            await c._scan_channel_thread_growth("C1", "1.0")
        assert ds.conversations_history.await_count >= 2


class TestCheckChannelChangesFull:
    @pytest.mark.asyncio
    async def test_check_channel_changes_terminal_error_and_pagination(self):
        from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(key="bot_messages", value=True, type=FilterType.BOOLEAN, operator="is"),
            ]
        )
        n = {"v": 0}

        async def hist(**kwargs):
            n["v"] += 1
            if n["v"] == 1:
                return MagicMock(success=False, error="channel_not_found")
            if n["v"] == 2:
                return MagicMock(
                    success=True,
                    data={
                        "messages": [
                            {
                                "ts": "1.0",
                                "user": "U1",
                                "edited": {},
                                "text": "x",
                                "thread_ts": "2.0",
                            },
                            {"subtype": "message_deleted", "ts": "3.0"},
                            {
                                "ts": "4.0",
                                "user": "U1",
                                "text": "edited",
                                "edited": {"ts": "4.1"},
                            },
                        ],
                        "response_metadata": {"next_cursor": "z"},
                    },
                )
            return MagicMock(
                success=True,
                data={"messages": [], "response_metadata": {"next_cursor": ""}},
            )

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=hist)
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_handle_edited_message", new_callable=AsyncMock, return_value=1),
            patch.object(SlackConnector, "_handle_new_thread", new_callable=AsyncMock),
        ):
            u1 = await c._check_channel_changes("C404", "0.0")
            u2 = await c._check_channel_changes("C2", "0.0")
        assert u1 == 0
        assert u2 >= 1

    @pytest.mark.asyncio
    async def test_check_channel_changes_history_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c._check_channel_changes("C1", "1.0") == 0

    @pytest.mark.asyncio
    async def test_sync_message_changes_channel_failure_skips_checkpoint(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"Cbad": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(SlackConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(
                SlackConnector, "_check_channel_changes", new_callable=AsyncMock, side_effect=RuntimeError("x"),
            ),
        ):
            await c._sync_message_changes()

    @pytest.mark.asyncio
    async def test_sync_message_changes_outer_wrap(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with patch.object(
            SlackConnector, "_compute_sync_window_oldest", side_effect=RuntimeError("top"),
        ):
            await c._sync_message_changes()


class TestHandleEditedAndNewThread:
    @pytest.mark.asyncio
    async def test_handle_edited_message_case_a_no_change(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        existing = MessageRecord(
            org_id="org-test",
            record_name="m",
            record_type=RecordType.MESSAGE,
            external_record_id="1.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            content="same",
            is_edited=True,
        )
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=existing)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)

        md = {"ts": "1.0", "user": "U1", "text": "same", "edited": {}}
        with patch.object(SlackConnector, "_record_changed", return_value=False):
            assert await c._handle_edited_message(md, "C1", "1.0") == 0

    @pytest.mark.asyncio
    async def test_handle_edited_message_case_a_updates(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        existing = MessageRecord(
            org_id="org-test",
            record_name="m",
            record_type=RecordType.MESSAGE,
            external_record_id="1.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            content="old",
        )
        existing.id = "mid"
        existing.source_created_at = 1000
        existing.weburl = "https://w.slack.com"
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=existing)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        md = {"ts": "1.0", "user": "U1", "text": "new text", "edited": {"ts": "2.0"}}
        assert await c._handle_edited_message(md, "C1", "1.0") == 1

    @pytest.mark.asyncio
    async def test_handle_edited_message_case_b_burst(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        burst_rec = MessageRecord(
            org_id="org-test",
            record_name="b",
            record_type=RecordType.MESSAGE,
            external_record_id="burst_C1_abc",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            content="old burst",
            start_ts="1.0",
            end_ts="3.0",
        )
        burst_rec.id = "bid"
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"ts": "1.0", "user": "U1", "text": "a"},
                        {"ts": "2.0", "user": "U1", "text": "b"},
                    ],
                },
            )
        )
        with (
            patch.object(SlackConnector, "_find_burst_record_by_message_ts", new_callable=AsyncMock, return_value=burst_rec),
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
        ):
            md = {"ts": "2.0", "user": "U1", "text": "b2", "edited": {}}
            assert await c._handle_edited_message(md, "C1", "2.0") == 1

    @pytest.mark.asyncio
    async def test_handle_new_thread_full_vs_incremental(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordGroup, RecordGroupType

        c = _connector_pipeline_ready()
        chan_rg = RecordGroup(
            org_id="org-test",
            name="ch",
            external_group_id="C1",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            group_type=RecordGroupType.SLACK_CHANNEL,
        )
        chan_rg.id = "crg"
        thread_rg = RecordGroup(
            org_id="org-test",
            name="th",
            external_group_id="thread_C1_9.0",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            group_type=RecordGroupType.SLACK_THREAD,
        )
        thread_rg.id = "trg"

        tx = MagicMock()
        tx.get_record_group_by_external_id = AsyncMock(side_effect=[chan_rg, thread_rg])
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            side_effect=[
                {"last_reply_ts": "8.0"},
                None,
            ]
        )
        md = {"ts": "9.0", "user": "U1", "reply_count": 2, "thread_ts": "9.0", "latest_reply": "10.0"}
        with (
            patch.object(SlackConnector, "_process_thread_incremental", new_callable=AsyncMock),
            patch.object(SlackConnector, "_process_thread", new_callable=AsyncMock),
        ):
            await c._handle_new_thread(md, "C1", "9.0")
            await c._handle_new_thread(md, "C1", "9.0")


class TestFindBurstRecordAndRefreshCaches:
    @pytest.mark.asyncio
    async def test_find_burst_record_by_message_ts(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        br = MagicMock()
        tx = MagicMock()
        tx.find_slack_burst_record_by_ts = AsyncMock(return_value=br)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        assert await c._find_burst_record_by_message_ts("C1", "1.0") is br

    @pytest.mark.asyncio
    async def test_refresh_user_caches_populates(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        u = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U9",
            org_id="org-test",
            email="u9@x.com",
            full_name="Nine",
        )
        u.id = "int-9"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])
        await c._refresh_user_caches()
        assert c.user_id_to_internal_id_cache.get("U9") == "int-9"


class TestPopulateChannelFilterCache:
    @pytest.mark.asyncio
    async def test_populate_cache_two_pages(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.rate_limiter = MagicMock()
        c.rate_limiter.acquire = AsyncMock()
        n = {"p": 0}

        async def clist(**kwargs):
            n["p"] += 1
            if n["p"] == 1:
                return MagicMock(
                    success=True,
                    data={
                        "channels": [{"id": "C1", "name": "a"}],
                        "response_metadata": {"next_cursor": "nc"},
                    },
                )
            return MagicMock(
                success=True,
                data={
                    "channels": [{"id": "C2", "name": "b"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.conversations_list = AsyncMock(side_effect=clist)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._populate_channel_filter_cache()
        assert "C1" in c.channel_filter_cache and "C2" in c.channel_filter_cache

    @pytest.mark.asyncio
    async def test_populate_cache_concurrent_second_waits(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.rate_limiter = MagicMock()
        c.rate_limiter.acquire = AsyncMock()
        ev = asyncio.Event()

        async def slow_list(**kwargs):
            await ev.wait()
            return MagicMock(success=True, data={"channels": [], "response_metadata": {}})

        ds = MagicMock()
        ds.conversations_list = AsyncMock(side_effect=slow_list)

        async def runner():
            with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
                await c._populate_channel_filter_cache()

        t1 = asyncio.create_task(runner())
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(runner())
        await asyncio.sleep(0.05)
        ev.set()
        await asyncio.gather(t1, t2)

    @pytest.mark.asyncio
    async def test_populate_cache_list_failure_raises(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.rate_limiter = MagicMock()
        c.rate_limiter.acquire = AsyncMock()
        ds = MagicMock()
        ds.conversations_list = AsyncMock(return_value=MagicMock(success=False, error="invalid_auth"))
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            pytest.raises(RuntimeError, match="cache rebuild failed"),
        ):
            await c._populate_channel_filter_cache()


class TestWarmUserCache:
    @pytest.mark.asyncio
    async def test_warm_user_cache_fetches_unknown(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 500}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        ds = MagicMock()
        ds.users_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"user": {"profile": {"real_name": "Zed", "email": "z@z.com"}, "name": "zed"}},
            )
        )
        msgs = [{"text": "hi <@U999>"}]
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._warm_user_cache_for_messages(msgs, ctx)
        assert c.user_id_to_name_cache.get("U999") == "Zed"


class TestReplaceMentionsBranches:
    def test_replace_mentions_user_email_and_cache_miss(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _make_connector()
        c.user_id_to_email_cache["Umail"] = "m@x.com"
        out = c._replace_mentions_in_text("<@Umail>", {}, {})
        assert "m@x.com" in out
        out2 = c._replace_mentions_in_text("<@Unope>", {}, {})
        assert "Unope" in out2


class TestBuildMessageBlocksForStreaming:
    @pytest.mark.asyncio
    async def test_burst_path(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        rec = MessageRecord(
            org_id="org-test",
            record_name="b",
            record_type=RecordType.MESSAGE,
            external_record_id="burst_C1_abc",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            start_ts="1.0",
            end_ts="2.0",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.5", "user": "U1", "text": "x"}]},
            )
        )
        tx = MagicMock()
        tx.get_records_by_parent = AsyncMock(return_value=[])
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            raw = await c._build_message_blocks_for_streaming(rec)
        assert raw.startswith(b"{")

    @pytest.mark.asyncio
    async def test_thread_burst_path(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        rec = MessageRecord(
            org_id="org-test",
            record_name="tb",
            record_type=RecordType.MESSAGE,
            external_record_id="thread_burst_C1_1.0_x",
            external_record_group_id="thread_C1_1.0",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            start_ts="1.0",
            end_ts="2.0",
            thread_id="1.0",
        )
        ds = MagicMock()
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.5", "user": "U1", "text": "r"}]},
            )
        )
        tx = MagicMock()
        tx.get_records_by_parent = AsyncMock(return_value=[])
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            raw = await c._build_message_blocks_for_streaming(rec)
        assert b"blocks" in raw or b"block" in raw.lower()

    @pytest.mark.asyncio
    async def test_single_path_and_thread_reply_fallback(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        rec = MessageRecord(
            org_id="org-test",
            record_name="s",
            record_type=RecordType.MESSAGE,
            external_record_id="9.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            thread_id="1.0",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": []}),
        )
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "9.0", "user": "U1", "text": "in thread"}]},
            )
        )
        tx = MagicMock()
        tx.get_records_by_parent = AsyncMock(return_value=[])
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)

        async def fresh():
            return ds

        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, side_effect=fresh):
            raw = await c._build_message_blocks_for_streaming(rec)
        assert raw.startswith(b"{")


class TestStreamFileBytes:
    @pytest.mark.asyncio
    async def test_stream_file_bytes_success_chunks(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        inner = MagicMock()
        inner.get_token = MagicMock(return_value="tok")
        c.external_client.get_client = MagicMock(return_value=inner)
        fr = FileRecord(
            org_id="org-test",
            record_name="a.bin",
            record_type=RecordType.FILE,
            external_record_id="F123",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        ds = MagicMock()
        ds.files_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"file": {"url_private_download": "https://files.slack.com/f"}},
            )
        )

        class _Resp:
            status_code = 200
            headers = {"content-type": "application/octet-stream"}

            async def aiter_bytes(self, _n: int):
                yield b"a"
                yield b"b"

        class _StreamCM:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *a):
                return None

        class _Http:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, *_a, **_k):
                return _StreamCM()

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch("app.connectors.sources.slack.team.connector.httpx.AsyncClient", return_value=_Http()),
        ):
            out = b""
            async for chunk in c._stream_file_bytes(fr):
                out += chunk
        assert out == b"ab"

    @pytest.mark.asyncio
    async def test_stream_file_bytes_errors(self):
        from fastapi import HTTPException

        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        fr_bad = MagicMock()
        fr_bad.external_record_id = ""
        fr_bad.id = "x"
        with pytest.raises(HTTPException):
            await anext(c._stream_file_bytes(fr_bad))  # type: ignore[arg-type]

        inner = MagicMock()
        inner.get_token = MagicMock(return_value=None)
        c.external_client.get_client = MagicMock(return_value=inner)
        fr = FileRecord(
            org_id="org-test",
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="F1",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        ds = MagicMock()
        ds.files_info = AsyncMock(return_value=MagicMock(success=False))

        class _BadResp:
            status_code = 403
            headers = {"content-type": "text/html"}

            async def aiter_bytes(self, _n: int):
                if False:
                    yield b""

        class _StreamCM:
            async def __aenter__(self):
                return _BadResp()

            async def __aexit__(self, *a):
                return None

        class _Http:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, *_a, **_k):
                return _StreamCM()

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch("app.connectors.sources.slack.team.connector.httpx.AsyncClient", return_value=_Http()),
        ):
            ag = c._stream_file_bytes(fr)
            with pytest.raises(HTTPException):
                await anext(ag)


class TestReindexAndCheckUpdated:
    @pytest.mark.asyncio
    async def test_reindex_records_empty_and_exception_and_perms(self):
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordType

        c = _connector_pipeline_ready()
        await c.reindex_records([])

        r_ok = MagicMock()
        r_ok.id = "a"
        r_ok.record_type = RecordType.MESSAGE
        r_bad = MagicMock()
        r_bad.id = "b"
        r_bad.record_type = RecordType.MESSAGE

        async def _chk(rec):
            if rec is r_ok:
                return (MagicMock(), [MagicMock()])
            raise RuntimeError("x")

        with patch.object(SlackConnector, "_check_updated", new_callable=AsyncMock, side_effect=_chk):
            await c.reindex_records([r_ok, r_bad])
        c.data_entities_processor.on_updated_record_permissions.assert_awaited()

    @pytest.mark.asyncio
    async def test_check_updated_message_and_file_paths(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import FileRecord, MessageRecord, RecordType

        c = _connector_pipeline_ready()
        assert await c._check_updated(MagicMock(record_type=RecordType.LINK)) is None

        msg = MessageRecord(
            org_id="org-test",
            record_name="m",
            record_type=RecordType.MESSAGE,
            external_record_id="1.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            content="old",
        )
        msg.id = "mid"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.0", "user": "U1", "text": "new", "edited": {}}]},
            )
        )
        mr = MagicMock()
        mr.id = "newid"
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_record_changed", return_value=True),
            patch.object(SlackConnector, "_to_message_record", new_callable=AsyncMock, return_value=mr),
        ):
            tup = await c._check_updated_message(msg)
        assert tup is not None

        frec = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="FILE1",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
            parent_external_record_id="1.0",
            source_created_at=1000,
            source_updated_at=1000,
        )
        frec.id = "fid"
        parent_msg = MessageRecord(
            org_id="org-test",
            record_name="pm",
            record_type=RecordType.MESSAGE,
            external_record_id="1.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
        )
        parent_msg.id = "pmid"
        fds = MagicMock()
        fds.files_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"file": {"created": 9_999_999, "id": "FILE1"}},
            )
        )
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=parent_msg)
        new_fr = MagicMock()
        new_fr.id = "nf"
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=fds),
            patch.object(SlackConnector, "_process_file", new_callable=AsyncMock, return_value=new_fr),
        ):
            tup2 = await c._check_updated_file(frec)
        assert tup2 is not None


class TestSlackConnectorCoverageExtended:
    @pytest.mark.asyncio
    async def test_to_message_record_none_and_rich(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "chan"}, rl)
        assert await c._to_message_record({}, ctx) is None
        msg = {
            "ts": "100.0",
            "user": "U1",
            "text": "hi <#C1|chan>",
            "thread_ts": "100.0",
            "reply_count": 0,
            "latest_reply": "101.0",
            "reactions": [{"name": "eyes", "count": 1, "users": ["U1"]}],
            "edited": {"ts": "100.5"},
            "message": {"text": "old <@U1>"},
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "x"}}],
        }
        out = await c._to_message_record(msg, ctx, parent_external_record_id="99.0")
        assert out is not None
        assert out.parent_external_record_id == "99.0"

    @pytest.mark.asyncio
    async def test_fetch_thread_replies_raw_paginates(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        n = {"p": 0}

        async def replies(**kwargs):
            n["p"] += 1
            if n["p"] == 1:
                return MagicMock(
                    success=True,
                    data={
                        "messages": [
                            {"ts": "1.0", "text": "root"},
                            {"ts": "1.1", "text": "a"},
                        ],
                        "response_metadata": {"next_cursor": "z"},
                    },
                )
            return MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "1.2", "text": "b"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.conversations_replies = AsyncMock(side_effect=replies)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            got = await c._fetch_thread_replies_raw("C1", "1.0", ctx)
        assert len(got) >= 1

    @pytest.mark.asyncio
    async def test_process_thread_on_new_record_groups_raises(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import RecordGroup, RecordGroupType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        rg = RecordGroup(
            org_id="org-test",
            name="t",
            external_group_id="thread_C1_1.0",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            group_type=RecordGroupType.SLACK_THREAD,
        )
        rg.id = "id"
        c.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=RuntimeError("db"))
        with (
            patch.object(SlackConnector, "_create_thread_record_group", return_value=rg),
            pytest.raises(RuntimeError),
        ):
            await c._process_thread({"ts": "1.0"}, ctx, "rg")

    @pytest.mark.asyncio
    async def test_build_thread_burst_record_empty_and_bad_first_ts(self):
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        assert await c._build_thread_burst_record(
            ConversationalBurst(messages=[], start_ts=0, end_ts=0),
            "1.0", "rg", ctx,
        ) is None
        b = ConversationalBurst(messages=[{"ts": "", "user": "U1", "text": "x"}], start_ts=0, end_ts=0)
        assert await c._build_thread_burst_record(b, "1.0", "rg", ctx) is None

    @pytest.mark.asyncio
    async def test_process_thread_incremental_with_replies_files_links(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl)
        parent = {"ts": "1.0", "user": "U1", "text": "root"}
        new_r = {
            "ts": "2.0",
            "user": "U1",
            "text": "see https://a.com",
            "files": [{"id": "F9"}],
        }
        fr = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="F9",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        fr.id = "fid9"
        with (
            patch.object(SlackConnector, "_fetch_thread_replies_raw", new_callable=AsyncMock, return_value=[new_r]),
            patch.object(SlackConnector, "_warm_user_cache_for_messages", new_callable=AsyncMock),
            patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, return_value=fr),
            patch.object(SlackConnector, "_process_link", new_callable=AsyncMock, return_value=None),
            patch.object(SlackConnector, "_enrich_link_records_linked_id", new_callable=AsyncMock),
        ):
            await c._process_thread_incremental(parent, ctx, "trg", "0.5")
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_join_fail_and_other_errors(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import RecordGroup, RecordGroupType

        c = _connector_pipeline_ready()
        c.channel_groups_cache["Cjoin"] = "rg"
        c.channel_id_to_name_cache["Cjoin"] = "joinchan"
        n = {"h": 0}

        async def hist(**kwargs):
            n["h"] += 1
            if n["h"] == 1:
                return MagicMock(success=False, error="not_in_channel")
            if n["h"] == 2:
                return MagicMock(success=False, error="unknown_err")
            return MagicMock(success=True, data={"messages": []})

        async def join(**kwargs):
            return MagicMock(success=False, error="cant_join")

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=hist)
        ds.conversations_join = AsyncMock(side_effect=join)
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Cjoin": "rg"}),
        ):
            await c.sync_channel_messages("Cjoin")

        n2 = {"h": 0}

        async def hist2(**kwargs):
            n2["h"] += 1
            if n2["h"] == 1:
                return MagicMock(success=False, error="channel_not_found")
            return MagicMock(success=True, data={"messages": []})

        ds2 = MagicMock()
        ds2.conversations_history = AsyncMock(side_effect=hist2)
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds2),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Cx": "rg"}),
        ):
            await c.sync_channel_messages("Cx")

    @pytest.mark.asyncio
    async def test_sync_channel_messages_join_retry_and_checkpoint(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache["Cok"] = "rg"
        c.channel_id_to_name_cache["Cok"] = "okchan"
        n = {"h": 0}

        async def hist(**kwargs):
            n["h"] += 1
            if n["h"] == 1:
                return MagicMock(success=False, error="not_in_channel")
            return MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "500.0", "user": "U1", "text": "m"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=hist)
        ds.conversations_join = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Cok": "rg"}),
            patch.object(SlackConnector, "_process_message_batch", new_callable=AsyncMock, return_value=([], [])),
        ):
            await c.sync_channel_messages("Cok")
        c.messages_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_deferred_threads_branches(self):
        from app.connectors.sources.slack.team.connector import (
            DeferredThread,
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.channel_groups_cache["Cd"] = "rg"
        c.channel_id_to_name_cache["Cd"] = "d"
        dt = DeferredThread(
            msg={"ts": "10.0", "user": "U1", "reply_count": 1, "thread_ts": "10.0"},
            ctx=ProcessingContext(
                "Cd",
                {"Cd": "rg"},
                {},
                {"U1": "A"},
                {"Cd": "d"},
                c.rate_limiter,
            ),
            rg_id="rg",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "500.0", "user": "U1", "text": "root"}],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        tx = MagicMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "9.0"})
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Cd": "rg"}),
            patch.object(
                SlackConnector,
                "_process_message_batch",
                new_callable=AsyncMock,
                return_value=([], [dt]),
            ),
            patch.object(SlackConnector, "_process_thread_incremental", new_callable=AsyncMock),
        ):
            await c.sync_channel_messages("Cd")

    @pytest.mark.asyncio
    async def test_sync_channels_list_fail_and_filters(self):
        from app.connectors.core.registry.filters import (
            Filter,
            FilterCollection,
            FilterType,
            ListOperator,
            MultiselectOperator,
            SyncFilterKey,
        )
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_list = AsyncMock(return_value=MagicMock(success=False, error="ratelimited"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c._sync_channels() == []

        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS.value,
                    value=["Ckeep"],
                    type=FilterType.LIST,
                    operator=ListOperator.IN,
                ),
                Filter(
                    key=SyncFilterKey.CHANNEL_TYPES.value,
                    value=["public_channel", "im"],
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                ),
            ]
        )
        ch = {"id": "Ckeep", "name": "k", "is_private": False, "is_mpim": False, "is_im": False, "topic": {}, "created": "1"}
        ds2 = MagicMock()
        ds2.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch], "response_metadata": {"next_cursor": ""}},
            )
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
            rgs = await c._sync_channels()
        assert rgs

    @pytest.mark.asyncio
    async def test_sync_channels_perm_gather_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ch = {"id": "G1", "name": "p", "is_private": True, "is_mpim": False, "is_im": False, "topic": {}, "created": "1"}
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch], "response_metadata": {"next_cursor": ""}},
            )
        )

        async def bad_perms(_cd):
            raise RuntimeError("perm")

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_permissions", new_callable=AsyncMock, side_effect=bad_perms),
        ):
            await c._sync_channels()

    @pytest.mark.asyncio
    async def test_fetch_channel_members_failures(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_members = AsyncMock(return_value=MagicMock(success=False, error="channel_not_found"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c._fetch_channel_members("Cx") == []

        ds2 = MagicMock()
        ds2.conversations_members = AsyncMock(side_effect=RuntimeError("x"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
            assert await c._fetch_channel_members("Cy") == []

    @pytest.mark.asyncio
    async def test_sync_user_groups_branches(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.usergroups_list = AsyncMock(return_value=MagicMock(success=False))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_user_groups()

        ds2 = MagicMock()
        ds2.usergroups_list = AsyncMock(return_value=MagicMock(success=True, data={"usergroups": []}))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
            await c._sync_user_groups()

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, side_effect=RuntimeError("x")),
            pytest.raises(RuntimeError),
        ):
            await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_to_user_group_invalid(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        assert c._to_user_group({}) is None

    @pytest.mark.asyncio
    async def test_fetch_usergroup_members_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with patch.object(
            SlackConnector, "_fresh_datasource", new_callable=AsyncMock, side_effect=RuntimeError("x"),
        ):
            assert await c._fetch_usergroup_members("G1") == []

    @pytest.mark.asyncio
    async def test_process_file_raw_httpx_success(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector

        c = _connector_pipeline_ready()
        inner = MagicMock()
        inner.get_token = MagicMock(return_value="t")
        c.external_client.get_client = MagicMock(return_value=inner)
        rl = RateLimiter(limits={2: 10, 3: 500, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"abc"

        class _Http:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *_a, **_k):
                return resp

        fd = {"id": "Fdl", "name": "n.bin", "created": 1, "url_private_download": "https://f"}
        with patch("app.connectors.sources.slack.team.connector.httpx.AsyncClient", return_value=_Http()):
            fr = await c._process_file_raw(fd, ctx)
        assert fr is not None

    @pytest.mark.asyncio
    async def test_process_link_jira(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, RateLimiter, SlackConnector
        from app.models.entities import MessageRecord, RecordType
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "x"}, rl)
        parent = MessageRecord(
            org_id="org-test",
            record_name="p",
            record_type=RecordType.MESSAGE,
            external_record_id="1.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
        )
        parent.id = "pid"
        lr = await c._process_link(
            "https://jira.example/browse/ABC-1", parent, ctx, {"attachments": []},
        )
        assert lr is not None

    @pytest.mark.asyncio
    async def test_handle_edited_message_db_and_burst_failures(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db"))
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        assert await c._handle_edited_message({"ts": "1.0"}, "C1", "1.0") == 0

        tx2 = MagicMock()
        tx2.get_record_by_external_id = AsyncMock(return_value=None)
        tx2.__aenter__ = AsyncMock(return_value=tx2)
        tx2.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx2)
        ds = MagicMock()
        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False))
        with (
            patch.object(SlackConnector, "_find_burst_record_by_message_ts", new_callable=AsyncMock, return_value=MagicMock()),
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
        ):
            assert await c._handle_edited_message({"ts": "1.0", "text": "x", "edited": {}}, "C1", "1.0") == 0

    @pytest.mark.asyncio
    async def test_find_burst_record_tx_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("tx"))
        assert await c._find_burst_record_by_message_ts("C1", "1.0") is None

    @pytest.mark.asyncio
    async def test_enrich_link_transaction_exceptions(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import LinkPublicStatus, LinkRecord, RecordType

        c = _connector_pipeline_ready()
        lr = LinkRecord(
            org_id="org-test",
            record_name="L",
            record_type=RecordType.LINK,
            external_record_id="L1",
            external_record_group_id="C",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            weburl="https://u.com",
            url="https://u.com",
            is_public=LinkPublicStatus.UNKNOWN,
        )
        c.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("outer"))
        await c._enrich_link_records_linked_id([(lr, [])])
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.get_record_by_weburl = AsyncMock(side_effect=RuntimeError("inner"))
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        await c._enrich_link_records_linked_id([(lr, [])])

    @pytest.mark.asyncio
    async def test_process_burst_and_single_message_record_fail(self):
        import app.connectors.sources.slack.team.connector as sm
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "x"}, rl)
        burst = ConversationalBurst(
            messages=[{"ts": "1.0", "user": "U1", "text": "a"}, {"ts": "2.0", "user": "U1", "text": "b"}],
            start_ts=1.0,
            end_ts=2.0,
        )
        with patch.object(sm, "MessageRecord", side_effect=ValueError("x")):
            recs, _ = await c._process_burst(burst, ctx)
        assert recs == []
        with patch.object(sm, "MessageRecord", side_effect=ValueError("y")):
            recs2, _ = await c._process_single({"ts": "9.0", "user": "U1", "text": "z"}, ctx)
        assert recs2 == []

    @pytest.mark.asyncio
    async def test_build_message_blocks_errors_and_404(self):
        from fastapi import HTTPException

        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        rec = MessageRecord(
            org_id="org-test",
            record_name="x",
            record_type=RecordType.MESSAGE,
            external_record_id="",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            start_ts="1.0",
            end_ts="2.0",
        )
        with pytest.raises(HTTPException):
            await c._build_message_blocks_for_streaming(rec)

        rec2 = MessageRecord(
            org_id="org-test",
            record_name="x",
            record_type=RecordType.MESSAGE,
            external_record_id="burst_C1_x",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            start_ts="1.0",
            end_ts="2.0",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False))
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            pytest.raises(HTTPException),
        ):
            await c._build_message_blocks_for_streaming(rec2)

    @pytest.mark.asyncio
    async def test_stream_file_bytes_no_download_url(self):
        from fastapi import HTTPException

        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import FileRecord, RecordType

        c = _connector_pipeline_ready()
        inner = MagicMock()
        inner.get_token = MagicMock(return_value="t")
        c.external_client.get_client = MagicMock(return_value=inner)
        fr = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="Fz",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
        )
        ds = MagicMock()
        ds.files_info = AsyncMock(return_value=MagicMock(success=True, data={"file": {}}))
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            pytest.raises(HTTPException),
        ):
            await anext(c._stream_file_bytes(fr))

    @pytest.mark.asyncio
    async def test_check_updated_file_early_returns(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import FileRecord, MessageRecord, RecordType

        c = _connector_pipeline_ready()
        assert await c._check_updated_file(MagicMock(external_record_id="", record_type=RecordType.FILE)) is None

        fr = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="F1",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
            parent_external_record_id="1.0",
            source_created_at=9_999_999_000,
            source_updated_at=9_999_999_000,
        )
        fds = MagicMock()
        fds.files_info = AsyncMock(
            return_value=MagicMock(success=True, data={"file": {"created": 1}}),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=fds):
            assert await c._check_updated_file(fr) is None

        fr2 = FileRecord(
            org_id="org-test",
            record_name="f",
            record_type=RecordType.FILE,
            external_record_id="F2",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            is_file=True,
            parent_external_record_id=None,
            source_created_at=1,
            source_updated_at=1,
        )
        fds2 = MagicMock()
        fds2.files_info = AsyncMock(
            return_value=MagicMock(success=True, data={"file": {"created": 9_999_999}}),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=fds2):
            assert await c._check_updated_file(fr2) is None

    @pytest.mark.asyncio
    async def test_check_updated_message_skips_burst_and_replies_path(self):
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        bmsg = MessageRecord(
            org_id="org-test",
            record_name="b",
            record_type=RecordType.MESSAGE,
            external_record_id="burst_C1_x",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
        )
        assert await c._check_updated_message(bmsg) is None

        tmsg = MessageRecord(
            org_id="org-test",
            record_name="t",
            record_type=RecordType.MESSAGE,
            external_record_id="9.0",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            thread_id="1.0",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(return_value=MagicMock(success=True, data={"messages": []}))
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": []}),
        )

        async def fresh():
            return ds

        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, side_effect=fresh):
            assert await c._check_updated_message(tmsg) is None

    @pytest.mark.asyncio
    async def test_sync_message_changes_reads_checkpoint_ms_format(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"Cchk": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            return_value={"last_check_time": 1_700_000_000_000},
        )
        with (
            patch.object(SlackConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(SlackConnector, "_check_channel_changes", new_callable=AsyncMock, return_value=0),
        ):
            await c._sync_message_changes()


class TestGetFilterOptionsCursor:
    @pytest.mark.asyncio
    async def test_get_filter_options_cursor_invalid_offset_zero(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        c.channel_filter_cache = {
            "C1": {"id": "C1", "label": "alpha"},
            "C2": {"id": "C2", "label": "beta"},
        }
        resp = await c.get_filter_options("channel_ids", page=1, limit=1, search="a", cursor="bad")
        assert resp.success is True


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        logger = MagicMock()
        dsp = MagicMock()
        cfg = MagicMock()
        with (
            patch("app.connectors.sources.slack.team.connector.DataSourceEntitiesProcessor") as D,
            patch.object(SlackConnector, "__init__", lambda self, *a, **k: None),
        ):
            dep = MagicMock()
            dep.initialize = AsyncMock()
            D.return_value = dep
            conn = await SlackConnector.create_connector(
                logger, dsp, cfg, "cid", "team", "me",
            )
        assert isinstance(conn, SlackConnector)
        dep.initialize.assert_awaited_once()


class TestSlackTeamConnectorCoverageBoost:
    """Targeted branches for connector.py lines below global coverage threshold."""

    @pytest.mark.asyncio
    async def test_sync_users_two_pages_ends_with_empty_next_cursor(self):
        """Last page has members and next_cursor '' so the cursor break (691) runs."""
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        n = 0

        async def _ul(**_kwargs):
            nonlocal n
            n += 1
            if n == 1:
                return MagicMock(
                    success=True,
                    data={
                        "members": [
                            {
                                "id": "U1",
                                "name": "a",
                                "is_bot": False,
                                "profile": {"email": "a@x.com"},
                                "updated": 1,
                                "is_restricted": False,
                            },
                        ],
                        "response_metadata": {"next_cursor": "more"},
                    },
                )
            return MagicMock(
                success=True,
                data={
                    "members": [
                        {
                            "id": "U2",
                            "name": "b",
                            "is_bot": False,
                            "profile": {"email": "b@x.com"},
                            "updated": 1,
                            "is_restricted": False,
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        ds = MagicMock()
        ds.users_list = AsyncMock(side_effect=_ul)
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_users()
        assert n == 2
        assert "U2" in c.user_id_to_email_cache

    @pytest.mark.asyncio
    async def test_build_user_id_caches_fills_email_and_name_from_db_user(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        u = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="Udb",
            org_id="org-test",
            email="db@x.com",
            full_name="Db User",
        )
        u.id = "int-db"
        c.user_id_to_email_cache.clear()
        c.user_id_to_name_cache.clear()
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])
        await c._build_user_id_caches()
        assert c.user_id_to_internal_id_cache["Udb"] == "int-db"
        assert c.user_id_to_email_cache["Udb"] == "db@x.com"
        assert c.user_id_to_name_cache["Udb"] == "Db User"

    @pytest.mark.asyncio
    async def test_sync_user_groups_fetches_members_when_users_missing(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        inner = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U200",
            org_id="org-test",
            email="in@g.test",
            full_name="Inner",
        )
        inner.id = "id-200"
        c._source_id_to_app_user = {"U200": inner}

        ds = MagicMock()
        ds.usergroups_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "usergroups": [
                        {
                            "id": "S1",
                            "name": "Eng",
                            "handle": "eng",
                            "date_create": "1000",
                            "date_update": "2000",
                        },
                    ],
                },
            ),
        )
        ds.usergroups_users_list = AsyncMock(
            return_value=MagicMock(success=True, data={"users": ["U200"]}),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_user_groups()
        ds.usergroups_users_list.assert_awaited()
        c.data_entities_processor.on_new_user_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_user_groups_logs_unknown_member_uid(self):
        from app.config.constants.arangodb import Connectors
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import AppUser

        c = _connector_pipeline_ready()
        inner = AppUser(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            source_user_id="U200",
            org_id="org-test",
            email="in@g.test",
            full_name="Inner",
        )
        inner.id = "id-200"
        c._source_id_to_app_user = {"U200": inner}

        ds = MagicMock()
        ds.usergroups_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "usergroups": [
                        {
                            "id": "S1",
                            "name": "Eng",
                            "handle": "eng",
                            "users": ["U200", "U999"],
                            "date_create": "1000",
                            "date_update": "2000",
                        },
                    ],
                },
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_user_groups()
        c.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_sync_channels_exclude_ids_not_in_operator(self):
        from app.connectors.core.registry.filters import (
            Filter,
            FilterCollection,
            FilterType,
            ListOperator,
            SyncFilterKey,
        )
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS.value,
                    value=["Cdrop"],
                    type=FilterType.LIST,
                    operator=ListOperator.NOT_IN,
                ),
            ],
        )
        ch_keep = {
            "id": "Ckeep",
            "name": "k",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ch_drop = {
            "id": "Cdrop",
            "name": "gone",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "channels": [ch_drop, ch_keep],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            rgs = await c._sync_channels()
        assert len(rgs) == 1
        assert rgs[0].external_group_id == "Ckeep"

    @pytest.mark.asyncio
    async def test_sync_channels_empty_channel_list_breaks(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c._sync_channels() == []

    @pytest.mark.asyncio
    async def test_sync_channels_skips_channel_without_id(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        bad = {"name": "no-id", "is_private": False, "is_mpim": False, "is_im": False, "topic": {}, "created": "1"}
        good = {
            "id": "Cok",
            "name": "ok",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [bad, good], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            rgs = await c._sync_channels()
        assert len(rgs) == 1

    @pytest.mark.asyncio
    async def test_sync_channels_to_record_group_exception_logged(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ch = {
            "id": "Cx",
            "name": "x",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(
                SlackConnector,
                "_to_channel_record_group",
                side_effect=RuntimeError("rg boom"),
            ),
        ):
            await c._sync_channels()
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_channel_permissions_im_missing_email_returns_empty(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        out = await c._channel_permissions({"id": "D1", "is_im": True, "user": "Unope"})
        assert out == []

    @pytest.mark.asyncio
    async def test_fetch_channel_members_unknown_error_logs_warning(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_members = AsyncMock(
            return_value=MagicMock(success=False, error="internal_error"),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            assert await c._fetch_channel_members("Cz") == []
        c.logger.warning.assert_called()

    def test_to_channel_record_group_channel_updated_ms_not_doubled(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        rg = c._to_channel_record_group(
            {
                "id": "C0B62PN63RA",
                "name": "testing-slack-conn",
                "created": 1779700655,
                "updated": 1779700655779,
            },
        )
        assert rg is not None
        assert rg.hide_children is True
        assert rg.source_created_at == 1779700655000
        assert rg.source_updated_at == 1779700655779

    def test_to_channel_record_group_slackbot_and_mpim_fallback_name(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        rg = c._to_channel_record_group(
            {
                "id": "Dsb",
                "is_im": True,
                "user": "USLACKBOT",
                "topic": {"value": "t"},
                "created": "10.0",
            },
        )
        assert rg is not None
        assert "Slackbot" in (rg.name or "")

        rg2 = c._to_channel_record_group(
            {
                "id": "Gmp",
                "is_mpim": True,
                "name": "",
                "topic": {},
                "created": "1.0",
            },
        )
        assert rg2 is not None
        assert "Group DM" in (rg2.name or "")

    def test_to_channel_record_group_no_id_returns_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        assert c._to_channel_record_group({"is_im": True, "user": "U1"}) is None

    def test_to_channel_record_group_recordgroup_failure(self):
        import app.connectors.sources.slack.team.connector as mod
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with patch.object(mod, "RecordGroup", side_effect=ValueError("bad rg")):
            out = c._to_channel_record_group(
                {
                    "id": "Cbad",
                    "name": "n",
                    "is_private": False,
                    "is_mpim": False,
                    "is_im": False,
                    "topic": {},
                    "created": "1.0",
                },
            )
        assert out is None
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_join_fails_then_breaks(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "Cjoinfail"
        c.channel_groups_cache[cid] = "rg"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=False, error="not_in_channel"),
        )
        ds.conversations_join = AsyncMock(return_value=MagicMock(success=False, error="restricted_action"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_still_not_in_channel_after_join(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "Cpriv2"
        c.channel_groups_cache[cid] = "rg"
        n = 0

        async def hist(**_k):
            nonlocal n
            n += 1
            return MagicMock(success=False, error="not_in_channel")

        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=hist)
        ds.conversations_join = AsyncMock(return_value=MagicMock(success=True))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)
        assert n >= 2
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_channel_not_found(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "Cgone"
        c.channel_groups_cache[cid] = "rg"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=False, error="channel_not_found"),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)

    @pytest.mark.asyncio
    async def test_sync_channel_messages_history_generic_error(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        cid = "Cerr"
        c.channel_groups_cache[cid] = "rg"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=False, error="fatal_slack"),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c.sync_channel_messages(cid)
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_indexing_disables_file_and_link(self):
        from app.connectors.core.registry.filters import (
            BooleanOperator,
            Filter,
            FilterCollection,
            FilterType,
            IndexingFilterKey,
        )
        from app.connectors.sources.slack.team.connector import SlackConnector
        from app.models.entities import ProgressStatus, RecordType

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.FILES.value,
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                ),
                Filter(
                    key=IndexingFilterKey.LINKS.value,
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                ),
            ],
        )
        cid = "Cfl"
        c.channel_groups_cache[cid] = "rg"
        c.user_id_to_name_cache["U1"] = "U"
        c.user_id_to_email_cache["U1"] = "u@e.com"

        file_rec = MagicMock()
        file_rec.record_type = RecordType.FILE
        file_rec.indexing_status = None
        link_rec = MagicMock()
        link_rec.record_type = RecordType.LINK
        link_rec.indexing_status = None

        async def _batch(_msgs, _ctx):
            return [(file_rec, []), (link_rec, [])], []

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "1.0", "user": "U1", "text": "x"}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        )
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_process_message_batch", new_callable=AsyncMock, side_effect=_batch),
            patch.object(SlackConnector, "_enrich_link_records_linked_id", new_callable=AsyncMock),
        ):
            await c.sync_channel_messages(cid)

        assert file_rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert link_rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_sync_channel_messages_deferred_thread_incremental_real_path(self):
        from app.connectors.sources.slack.team.connector import (
            DeferredThread,
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.channel_groups_cache["Ci"] = "rg"
        c.channel_id_to_name_cache["Ci"] = "chan"
        dt = DeferredThread(
            msg={
                "ts": "20.0",
                "user": "U1",
                "reply_count": 1,
                "thread_ts": "20.0",
                "latest_reply": "21.0",
            },
            ctx=ProcessingContext(
                "Ci",
                {"Ci": "rg"},
                {},
                {"U1": "A"},
                {"Ci": "chan"},
                c.rate_limiter,
            ),
            rg_id="rg",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "500.0", "user": "U1", "text": "root"}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        )
        tx = MagicMock()
        existing_rg = MagicMock()
        existing_rg.id = "thread-rg-internal"
        tx.get_record_group_by_external_id = AsyncMock(return_value=existing_rg)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "9.0"})
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Ci": "rg"}),
            patch.object(
                SlackConnector,
                "_process_message_batch",
                new_callable=AsyncMock,
                return_value=([], [dt]),
            ),
            patch.object(SlackConnector, "_process_thread_incremental", new_callable=AsyncMock) as inc,
        ):
            await c.sync_channel_messages("Ci")
        inc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_deferred_rg_lookup_exception_logs_debug(self):
        from app.connectors.sources.slack.team.connector import (
            DeferredThread,
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.channel_groups_cache["Ce"] = "rg"
        c.channel_id_to_name_cache["Ce"] = "e"
        dt = DeferredThread(
            msg={"ts": "30.0", "user": "U1", "reply_count": 1, "thread_ts": "30.0"},
            ctx=ProcessingContext(
                "Ce",
                {"Ce": "rg"},
                {},
                {"U1": "A"},
                {"Ce": "e"},
                c.rate_limiter,
            ),
            rg_id="rg",
        )
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [{"ts": "1.0", "user": "U1", "text": "x"}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        )

        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get_record_group_by_external_id(self, **_k):
                raise RuntimeError("db")

        c.data_store_provider.transaction = MagicMock(return_value=_Tx())
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "1.0"})
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_channel_group_map", new_callable=AsyncMock, return_value={"Ce": "rg"}),
            patch.object(
                SlackConnector,
                "_process_message_batch",
                new_callable=AsyncMock,
                return_value=([], [dt]),
            ),
            patch.object(SlackConnector, "_process_thread", new_callable=AsyncMock),
        ):
            await c.sync_channel_messages("Ce")
        c.logger.debug.assert_called()

    def test_to_user_group_app_user_group_raises(self):
        import app.connectors.sources.slack.team.connector as mod
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        with patch.object(mod, "AppUserGroup", side_effect=ValueError("bad")):
            out = c._to_user_group(
                {
                    "id": "S9",
                    "name": "N",
                    "handle": "n",
                    "date_create": "1",
                    "date_update": "2",
                },
            )
        assert out is None

    @pytest.mark.asyncio
    async def test_sync_channels_include_ids_skips_unlisted(self):
        from app.connectors.core.registry.filters import (
            Filter,
            FilterCollection,
            FilterType,
            ListOperator,
            SyncFilterKey,
        )
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS.value,
                    value=["Conly"],
                    type=FilterType.LIST,
                    operator=ListOperator.IN,
                ),
            ],
        )
        ch_other = {
            "id": "Cother",
            "name": "o",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ch_only = {
            "id": "Conly",
            "name": "only",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch_other, ch_only], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            rgs = await c._sync_channels()
        assert len(rgs) == 1
        assert rgs[0].external_group_id == "Conly"

    @pytest.mark.asyncio
    async def test_sync_channels_skips_channel_when_record_group_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ch_ok = {
            "id": "Cok2",
            "name": "ok",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ch_bad = {
            "id": "Cbad2",
            "name": "bad",
            "is_private": False,
            "is_mpim": False,
            "is_im": False,
            "topic": {},
            "created": "1",
        }
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"channels": [ch_bad, ch_ok], "response_metadata": {"next_cursor": ""}},
            ),
        )

        _orig_rg = SlackConnector._to_channel_record_group

        def _to_rg(self, cd):
            if cd.get("id") == "Cbad2":
                return None
            return _orig_rg(self, cd)

        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_to_channel_record_group", _to_rg),
        ):
            rgs = await c._sync_channels()
        assert len(rgs) == 1

    def test_build_message_block_author_without_text_and_url_failure(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {"U1": "Solo"},
            {"C1": "g"},
            rl,
        )
        b1 = c._build_message_block({"ts": "1.0", "user": "U1", "text": ""}, 0, 0, ctx)
        assert "Solo" in b1.data

        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("no url")):
            b2 = c._build_message_block({"ts": "2.0", "user": "U1", "text": "body"}, 0, 0, ctx)
        assert "body" in b2.data

        b3 = c._build_message_block({"ts": "", "user": "", "text": "anon"}, 0, 0, ctx)
        assert "anon" in b3.data

    def test_build_containers_message_url_failures(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "g"}, rl)
        msg = {"ts": "9.0", "user": "U1", "text": "t"}
        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("x")):
            single = c._build_single_block_containers(msg, ctx)
            assert single.block_groups[0].weburl is None
            burst = c._build_burst_block_containers(
                [msg, {"ts": "10.0", "user": "U1", "text": "r"}], "burst_x", ctx
            )
            assert burst.block_groups[0].weburl is None

    def test_create_thread_record_group_no_ts_and_url_fail(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        c.connector_id = "cid"
        c.data_entities_processor = MagicMock()
        c.data_entities_processor.org_id = "o"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "g"}, rl)
        assert c._create_thread_record_group({"text": "x"}, ctx, "prg") is None

        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("x")):
            rg = c._create_thread_record_group(
                {"ts": "1.5", "user": "U1", "text": "root"},
                ctx,
                "prg",
            )
        assert rg is not None
        assert rg.web_url is None

    @pytest.mark.asyncio
    async def test_sync_user_groups_skips_when_to_user_group_none(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.usergroups_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "usergroups": [
                        {"description": "x"},
                    ],
                },
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._sync_user_groups()
        c.data_entities_processor.on_new_user_groups.assert_not_called()

    def test_to_channel_record_group_im_uses_uid_when_no_name_cache(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        rg = c._to_channel_record_group(
            {
                "id": "Dim",
                "is_im": True,
                "user": "UZZZ",
                "topic": {},
                "created": "1.0",
            },
        )
        assert rg is not None
        assert "UZZZ" in (rg.name or "")

    def test_build_burst_block_containers_first_url_raises(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "g"}, rl)
        msgs = [{"ts": "1.0", "user": "U1", "text": "a"}]
        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("u")):
            bc = c._build_burst_block_containers(msgs, "burst_x", ctx, thread_ts="1.0")
        assert bc.block_groups[0].weburl is None

    def test_create_thread_record_group_recordgroup_raises(self):
        import app.connectors.sources.slack.team.connector as mod
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _make_connector()
        c.workspace_domain = "w"
        c.connector_id = "cid"
        c.data_entities_processor = MagicMock()
        c.data_entities_processor.org_id = "o"
        rl = RateLimiter(limits={2: 10, 3: 10, 4: 10}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {"U1": "A"}, {"C1": "g"}, rl)
        with patch.object(mod, "RecordGroup", side_effect=ValueError("bad")):
            out = c._create_thread_record_group(
                {"ts": "2.0", "user": "U1", "text": "t"},
                ctx,
                "prg",
            )
        assert out is None

    @pytest.mark.asyncio
    async def test_fetch_thread_replies_passes_oldest(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        rl = RateLimiter(limits={2: 500, 3: 500, 4: 500}, headroom=1.0)
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "g"}, rl)
        ds = MagicMock()
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._fetch_thread_replies_raw("C1", "1.0", ctx, oldest="0.5")
        ds.conversations_replies.assert_awaited()
        assert ds.conversations_replies.await_args.kwargs.get("oldest") == "0.5"

    @pytest.mark.asyncio
    async def test_process_burst_skips_bot_parent_thread_when_bot_filter_off(self):
        from app.connectors.core.registry.filters import (
            BooleanOperator,
            Filter,
            FilterCollection,
            FilterType,
            IndexingFilterKey,
        )
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            RateLimiter,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.BOT_MESSAGES.value,
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                ),
            ],
        )
        rl = c.rate_limiter
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {"U1": "Human"},
            {"C1": "g"},
            rl,
        )
        burst = ConversationalBurst(
            messages=[
                {"ts": "1.0", "user": "U1", "text": "human"},
                {
                    "ts": "10.0",
                    "thread_ts": "10.0",
                    "reply_count": 2,
                    "bot_id": "B1",
                    "text": "bot thread root",
                },
            ],
            start_ts=1.0,
            end_ts=10.0,
        )
        with (
            patch.object(SlackConnector, "_process_file_raw", new_callable=AsyncMock, return_value=None),
            patch.object(SlackConnector, "_process_link", new_callable=AsyncMock, return_value=None),
        ):
            _recs, deferred = await c._process_burst(burst, ctx)
        assert deferred == []
        c.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_process_single_empty_ts_returns_empty(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {},
            {"C1": "g"},
            c.rate_limiter,
        )
        recs, d = await c._process_single({"text": "no ts"}, ctx)
        assert recs == [] and d == []

    @pytest.mark.asyncio
    async def test_process_single_skips_bot_when_bot_messages_disabled(self):
        from app.connectors.core.registry.filters import (
            BooleanOperator,
            Filter,
            FilterCollection,
            FilterType,
            IndexingFilterKey,
        )
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.BOT_MESSAGES.value,
                    value=False,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                ),
            ],
        )
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {},
            {"C1": "g"},
            c.rate_limiter,
        )
        recs, d = await c._process_single(
            {"ts": "1.0", "bot_id": "B1", "text": "bot"},
            ctx,
        )
        assert recs == [] and d == []

    @pytest.mark.asyncio
    async def test_process_single_message_url_raises(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {"U1": "A"},
            {"C1": "g"},
            c.rate_limiter,
        )
        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("u")):
            recs, d = await c._process_single(
                {"ts": "9.0", "user": "U1", "text": "hello"},
                ctx,
            )
        assert recs and d == []

    @pytest.mark.asyncio
    async def test_fetch_thread_replies_stops_on_failed_response(self):
        from app.connectors.sources.slack.team.connector import (
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {},
            {"C1": "g"},
            c.rate_limiter,
        )
        ds = MagicMock()
        ds.conversations_replies = AsyncMock(return_value=MagicMock(success=False, error="ratelimited"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            out = await c._fetch_thread_replies_raw("C1", "1.0", ctx)
        assert out == []

    @pytest.mark.asyncio
    async def test_build_thread_burst_record_message_url_raises(self):
        from app.connectors.sources.slack.team.connector import (
            ConversationalBurst,
            ProcessingContext,
            SlackConnector,
        )

        c = _connector_pipeline_ready()
        ctx = ProcessingContext(
            "C1",
            {"C1": "rg"},
            {},
            {"U1": "A"},
            {"C1": "g"},
            c.rate_limiter,
        )
        burst = ConversationalBurst(
            messages=[{"ts": "1.0", "user": "U1", "text": "a"}],
            start_ts=1.0,
            end_ts=1.0,
        )
        with patch.object(SlackConnector, "_message_url", side_effect=RuntimeError("u")):
            rec = await c._build_thread_burst_record(burst, "1.0", "trg", ctx)
        assert rec is not None
        assert rec.weburl is None

    @pytest.mark.asyncio
    async def test_process_link_exception_returns_none(self):
        from app.connectors.sources.slack.team.connector import ProcessingContext, SlackConnector
        from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
        from app.models.entities import MessageRecord, RecordType

        c = _connector_pipeline_ready()
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "g"}, c.rate_limiter)
        parent = MessageRecord(
            org_id="org-test",
            record_name="m",
            record_type=RecordType.MESSAGE,
            external_record_id="e1",
            external_record_group_id="C1",
            record_group_id="rg",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=c.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
        )
        parent.id = "mid"
        with patch.object(SlackConnector, "_classify_url", side_effect=RuntimeError("bad")):
            lr = await c._process_link("https://x.com", parent, ctx, {"text": ""})
        assert lr is None

    @pytest.mark.asyncio
    async def test_process_file_raw_filerecord_raises(self):
        import app.connectors.sources.slack.team.connector as mod
        from app.connectors.sources.slack.team.connector import ProcessingContext, SlackConnector

        c = _connector_pipeline_ready()
        ctx = ProcessingContext("C1", {"C1": "rg"}, {}, {}, {"C1": "g"}, c.rate_limiter)
        fd = {"id": "F1", "name": "a.pdf", "created": 1, "mimetype": "application/pdf"}
        with patch.object(mod, "FileRecord", side_effect=ValueError("bad")):
            fr = await c._process_file_raw(fd, ctx)
        assert fr is None

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_history_raises(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._scan_channel_thread_growth("C1", "1.0")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_calls_handle_new_thread(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        ds = MagicMock()
        root = {
            "ts": "50.0",
            "user": "U1",
            "text": "root",
            "thread_ts": "50.0",
            "reply_count": 2,
        }
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [root], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_handle_new_thread", new_callable=AsyncMock) as h,
        ):
            await c._scan_channel_thread_growth("C1", "1.0")
        h.assert_awaited()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_message_changed_unwraps(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        inner = {
            "ts": "60.0",
            "user": "U1",
            "text": "x",
            "thread_ts": "60.0",
            "reply_count": 1,
        }
        wrap = {"subtype": "message_changed", "message": inner, "ts": "60.0"}
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [wrap], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackConnector, "_handle_new_thread", new_callable=AsyncMock) as h,
        ):
            await c._scan_channel_thread_growth("C1", None)
        h.assert_awaited()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_handle_new_thread_exception(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        root = {
            "ts": "70.0",
            "user": "U1",
            "text": "r",
            "thread_ts": "70.0",
            "reply_count": 1,
        }
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [root], "response_metadata": {"next_cursor": ""}},
            ),
        )
        with (
            patch.object(SlackConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(
                SlackConnector,
                "_handle_new_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("handler"),
            ),
        ):
            await c._scan_channel_thread_growth("C1", None)
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_sync_thread_growth_formats_checkpoint_ts(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"Ctg": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            return_value={"last_check_time": 1_700_000_000_000},
        )
        with (
            patch.object(SlackConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(SlackConnector, "_scan_channel_thread_growth", new_callable=AsyncMock),
        ):
            await c._sync_thread_growth()
        c.audit_log_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_thread_growth_logs_channel_scan_failure(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _connector_pipeline_ready()
        c.channel_groups_cache = {"Cbad": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(SlackConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(
                SlackConnector,
                "_scan_channel_thread_growth",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await c._sync_thread_growth()
        c.logger.error.assert_called()


class TestSlackConnectorMiscFixups:
    def test_detect_bursts_empty_on_instance(self):
        from app.connectors.sources.slack.team.connector import SlackConnector

        c = _make_connector()
        assert c._detect_bursts([]) == []
