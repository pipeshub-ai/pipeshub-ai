"""
Unit tests for SlackIndividualConnector (individual-scope Slack connector).

Targets 100% line coverage of
``app.connectors.sources.slack.individual.connector``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.filters import (
    BooleanOperator,
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterOperator,
    FilterType,
    ListOperator,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.slack.individual.connector import _numeric_epoch_to_ms
from app.models.blocks import ChildRecord, ChildType
from app.models.entities import FileRecord, MessageRecord, Record, RecordGroup, RecordType


def _flexible_base_connector_init(self, *args: Any, **kwargs: Any) -> None:
    """Stub BaseConnector.__init__ for Slack individual (6 super args) and team (8 args)."""
    app, logger, dep, dsp, cfg, cid = args[:6]
    scope = args[6] if len(args) > 6 else ""
    created_by = args[7] if len(args) > 7 else ""
    self.logger = logger
    self.data_entities_processor = dep
    self.data_store_provider = dsp
    self.config_service = cfg
    self.connector_id = cid
    self.app = app
    self.connector_name = app.get_app_name()
    self.scope = scope
    self.created_by = created_by
    self.creator_email = None


def _patch_base_connector_init():
    from app.connectors.core.base.connector.connector_service import BaseConnector

    return patch.object(BaseConnector, "__init__", _flexible_base_connector_init)


def _make_connector() -> Any:
    """Minimal SlackIndividualConnector with heavy deps mocked (team-test pattern)."""
    from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-test"
    data_store = MagicMock()
    config_svc = MagicMock()

    with patch.object(SlackIndividualConnector, "__init__", lambda self, *a, **kw: None):
        c = SlackIndividualConnector.__new__(SlackIndividualConnector)

    c.logger = logger
    c.data_entities_processor = dep
    c.data_store_provider = data_store
    c.config_service = config_svc
    c.connector_id = "conn-abc"
    c.external_client = MagicMock()
    client = MagicMock()
    client.get_token = MagicMock(return_value="xoxp-test-token")
    c.external_client.get_client = MagicMock(return_value=client)
    c.data_source = MagicMock()
    c.workspace_domain = "myworkspace"
    c.team_id = "T12345"
    c.authenticated_user_id = "U111"
    c.authenticated_user_email = "user@example.com"
    c.user_id_to_email_cache = {}
    c.user_id_to_name_cache = {}
    c.user_id_to_internal_id_cache = {}
    c.channel_groups_cache = {}
    c.channel_id_to_name_cache = {}
    c.sync_filters = FilterCollection(filters=[])
    c.indexing_filters = FilterCollection(filters=[])
    msg_sp = AsyncMock()
    msg_sp.read_sync_point = AsyncMock(return_value=None)
    msg_sp.update_sync_point = AsyncMock()
    audit_sp = AsyncMock()
    audit_sp.read_sync_point = AsyncMock(return_value=None)
    audit_sp.update_sync_point = AsyncMock()
    c.messages_sync_point = msg_sp
    c.audit_log_sync_point = audit_sp
    from app.connectors.sources.slack.individual.connector import RateLimiter

    c.rate_limiter = RateLimiter(calls_per_minute=10_000, logger=logger)
    return c


# ---------------------------------------------------------------------------
# Dataclasses & RateLimiter
# ---------------------------------------------------------------------------


class TestConversationalBurst:
    def test_properties(self):
        from app.connectors.sources.slack.individual.connector import ConversationalBurst

        b = ConversationalBurst(messages=[], start_ts=0.0, end_ts=0.0)
        assert b.message_count == 0
        assert b.aggregated_text == ""

        msgs = [{"text": "a"}, {"ts": "2"}, {"text": "b"}]
        b2 = ConversationalBurst(messages=msgs, start_ts=1.0, end_ts=3.0)
        assert b2.message_count == 3
        assert b2.aggregated_text == "a\n\nb"


class TestProcessingContextDataclass:
    def test_fields(self):
        from app.connectors.sources.slack.individual.connector import ProcessingContext, RateLimiter

        rl = RateLimiter()
        ctx = ProcessingContext(
            channel_id="C1",
            channel_groups_map={"C1": "rg1"},
            user_id_to_email={"U1": "a@b.com"},
            user_id_to_name={"U1": "Alice"},
            channel_id_to_name={"C1": "general"},
            rate_limiter=rl,
        )
        assert ctx.channel_id == "C1"


class TestDeferredThread:
    def test_construct(self):
        from app.connectors.sources.slack.individual.connector import (
            DeferredThread,
            ProcessingContext,
            RateLimiter,
        )

        rl = RateLimiter()
        ctx = ProcessingContext(
            "C1", {}, {}, {}, {}, rl,
        )
        dt = DeferredThread(msg={"ts": "1"}, ctx=ctx, rg_id="rg")
        assert dt.msg["ts"] == "1"


class TestRateLimiterIndividual:
    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        from app.connectors.sources.slack.individual.connector import RateLimiter

        rl = RateLimiter(calls_per_minute=5, logger=None)
        for _ in range(5):
            await rl.acquire()
        assert len(rl._call_times) == 5

    @pytest.mark.asyncio
    async def test_acquire_waits_and_logs(self):
        from app.connectors.sources.slack.individual.connector import RateLimiter
        import app.connectors.sources.slack.individual.connector as mod

        log = MagicMock()
        rl = RateLimiter(calls_per_minute=1, logger=log)
        t0 = datetime(2020, 1, 1, 12, 0, 0)

        class _FakeDateTime(datetime):
            _calls = 0

            @classmethod
            def now(cls, tz=None):
                cls._calls += 1
                if cls._calls <= 2:
                    return t0
                return t0 + timedelta(seconds=61)

        with (
            patch.object(mod, "datetime", _FakeDateTime),
            patch.object(mod.asyncio, "sleep", new_callable=AsyncMock) as sl,
        ):
            await rl.acquire()
            await rl.acquire()
            sl.assert_awaited()
            log.debug.assert_called()


# ---------------------------------------------------------------------------
# Static / pure helpers on SlackIndividualConnector
# ---------------------------------------------------------------------------


class TestFileHelpers:
    def test_get_file_extension(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        assert S._get_file_extension("a.PDF") == "pdf"
        assert S._get_file_extension("noext") is None
        assert S._get_file_extension("") is None

    def test_resolve_file_mime_type(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        assert S._resolve_file_mime_type("x.pdf", "application/pdf") == "application/pdf"
        assert S._resolve_file_mime_type("x.pdf", "application/octet-stream") == "application/pdf"
        assert S._resolve_file_mime_type("x.bin", None) in (
            MimeTypes.UNKNOWN.value,
            "application/octet-stream",
        )

    def test_resolve_file_extension(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        assert S._resolve_file_extension("a.md", "binary") == "md"
        assert S._resolve_file_extension("a.md", "markdown") == "markdown"


class TestSlackTsAndUrl:
    def test_slack_ts_to_utc_label_ok_and_fallback(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        out = S._slack_ts_to_utc_label("1609459200.000100")
        assert "2021" in out or "2020" in out
        assert S._slack_ts_to_utc_label("not-a-float") == "not-a-float"

    def test_message_url_variants(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.workspace_domain = "acme"
        assert urlparse(c._message_url("C1", "1.0")).hostname == "acme.slack.com"
        c.workspace_domain = None
        assert urlparse(c._message_url("C1", "1.0")).hostname == "app.slack.com"
        u = c._message_url("C1", "2.0", thread_ts="1.0")
        assert "thread_ts=1.0" in u and "cid=C1" in u


class TestMentionsAndUrls:
    def test_extract_and_classify(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        assert S._extract_user_mentions("hi <@UABC123>") == ["UABC123"]
        assert S._extract_channel_mentions("see <#CXYZ|general>") == ["CXYZ"]
        extracted = S._extract_urls("x <https://a.com|a> y https://b.org/z")
        assert any(urlparse(u).hostname == "a.com" for u in extracted)

        t, meta = S._classify_url("https://x.atlassian.net/browse/ENG-1")
        assert t == "jira" and meta.get("ticket_key") == "ENG-1"

        assert S._classify_url("not-a-url-parse")[0] == "web"

    def test_unfurl_data(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        msg = {"attachments": [{"from_url": "https://u", "title": "T"}]}
        assert S._unfurl_data("https://u", msg)["title"] == "T"
        assert S._unfurl_data("https://missing", msg) == {}


class TestReactionsAndRecordChanged:
    def test_record_changed(self):
        c = _make_connector()
        ex = MagicMock()
        ex.is_edited = False
        ex.source_updated_at = None
        ex.source_created_at = None
        assert c._record_changed({"edited": {}, "text": "x"}, ex) is True
        ex.is_edited = True
        ex.source_updated_at = 1000
        assert c._record_changed({"edited": {"ts": "1.0"}, "text": "old"}, ex) is False
        assert c._record_changed({"edited": {"ts": "2.0"}, "text": "new"}, ex) is True


class TestDetectBursts:
    def test_empty(self):
        c = _make_connector()
        assert c._detect_bursts([]) == []

    def test_bot_break_and_time_gap(self):
        c = _make_connector()
        base = 1_000_000.0
        msgs = [
            {"ts": str(base + 400_000), "user": "U1", "text": "a"},
            {"ts": str(base + 400_001), "user": "U1", "text": "b"},
            {"ts": str(base + 400_002), "bot_id": "B1", "text": "bot"},
            {"ts": str(base + 400_003 + 400_000), "user": "U1", "text": "far"},
        ]
        bursts = c._detect_bursts(msgs)
        assert len(bursts) >= 2

    def test_system_subtype_starts_new_burst(self):
        c = _make_connector()
        t = 1_700_000_000.0
        msgs = [
            {"ts": str(t), "user": "U1", "text": "hi"},
            {"ts": str(t + 1), "subtype": "channel_join", "user": "U1"},
        ]
        bursts = c._detect_bursts(msgs)
        assert len(bursts) == 2


class TestComputeSyncWindow:
    def test_defaults_and_operators(self):
        import time as time_mod
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        with patch.object(time_mod, "time", return_value=1_000_000.0):
            out = c._compute_sync_window_oldest()
            assert float(out) < 1_000_000.0

        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key="sync_window",
                    value=None,
                    type=FilterType.DATETIME,
                    operator=DatetimeOperator.LAST_7_DAYS,
                )
            ]
        )
        with patch.object(time_mod, "time", return_value=2_000_000.0):
            out2 = c._compute_sync_window_oldest()
            assert float(out2) < 2_000_000.0

        sw = MagicMock()
        sw.operator_value = FilterOperator.IS_AFTER
        sw.get_datetime_start = MagicMock(return_value=1_700_000_000_000)
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=sw)
        out3 = c._compute_sync_window_oldest()
        assert float(out3) == 1_700_000_000.0

        sw2 = MagicMock()
        sw2.operator_value = FilterOperator.IS_AFTER
        sw2.get_datetime_start = MagicMock(return_value=None)
        c.sync_filters.get = MagicMock(return_value=sw2)
        with patch.object(time_mod, "time", return_value=3_000_000.0):
            out4 = c._compute_sync_window_oldest()
            assert float(out4) < 3_000_000.0


# ---------------------------------------------------------------------------
# __init__ (real) with BaseConnector patched
# ---------------------------------------------------------------------------


def test_real_init_sets_sync_points():
    from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "o"
    ds = MagicMock()
    cfg = MagicMock()
    with _patch_base_connector_init():
        c = SlackIndividualConnector(logger, dep, ds, cfg, "cid-1")
    assert c.connector_id == "cid-1"
    assert c.messages_sync_point is not None


# ---------------------------------------------------------------------------
# init / _fresh_datasource
# ---------------------------------------------------------------------------


class TestInitAndFreshDatasource:
    @pytest.mark.asyncio
    async def test_init_success_paths(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "o"
        ds_prov = MagicMock()
        cfg = MagicMock()

        with _patch_base_connector_init():
            c = SlackIndividualConnector(logger, dep, ds_prov, cfg, "cid")

        mock_ds = MagicMock()
        mock_ds.team_info = AsyncMock(
            return_value=MagicMock(success=True, data={"team": {"domain": "d", "id": "T9"}})
        )
        mock_ds.auth_test = AsyncMock(
            return_value=MagicMock(success=True, data={"user_id": "U9"})
        )

        with (
            patch(
                "app.connectors.sources.slack.individual.connector.SlackClient.build_from_services",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch.object(
                SlackIndividualConnector,
                "_fresh_datasource",
                new=AsyncMock(return_value=mock_ds),
            ),
        ):
            ok = await c.init()
        assert ok is True
        assert c.workspace_domain == "d"
        assert c.authenticated_user_id == "U9"

    @pytest.mark.asyncio
    async def test_init_auth_failure_and_exception(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "o"
        with _patch_base_connector_init():
            c = SlackIndividualConnector(logger, dep, MagicMock(), MagicMock(), "cid")

        mock_ds = MagicMock()
        mock_ds.team_info = AsyncMock(return_value=None)
        mock_ds.auth_test = AsyncMock(return_value=MagicMock(success=False, error="invalid"))

        with (
            patch(
                "app.connectors.sources.slack.individual.connector.SlackClient.build_from_services",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch.object(
                SlackIndividualConnector,
                "_fresh_datasource",
                new=AsyncMock(return_value=mock_ds),
            ),
        ):
            assert await c.init() is True

        with (
            patch(
                "app.connectors.sources.slack.individual.connector.SlackClient.build_from_services",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            assert await c.init() is False

    @pytest.mark.asyncio
    async def test_fresh_datasource_branches(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "o"
        with _patch_base_connector_init():
            c = SlackIndividualConnector(logger, dep, MagicMock(), MagicMock(), "cid")

        with pytest.raises(RuntimeError, match="Call init"):
            await SlackIndividualConnector._fresh_datasource(c)

        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=MagicMock(get_token=lambda: "x", set_token=MagicMock()))
        c.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(RuntimeError, match="not found"):
            await c._fresh_datasource()

        c.config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": {"access_token": "  "}}
        )
        with pytest.raises(RuntimeError, match="No access token"):
            await c._fresh_datasource()

        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"apiToken": "xoxb-bot"},
                "credentials": {"access_token": "xoxp-user"},
            }
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value="old")
        client.set_token = MagicMock()
        c.external_client.get_client = MagicMock(return_value=client)
        with patch(
            "app.connectors.sources.slack.individual.connector.SlackDataSource",
            return_value=MagicMock(_="ds"),
        ) as sds:
            out = await c._fresh_datasource()
        client.set_token.assert_called_once_with("xoxp-user")
        assert out is not None


# ---------------------------------------------------------------------------
# User / channel helpers
# ---------------------------------------------------------------------------


class TestUserSyncHelpers:
    def test_to_app_user(self):
        c = _make_connector()
        assert c._to_app_user({}) is None
        assert c._to_app_user({"id": "U1", "profile": {}}, fallback_email=" e@e.com ") is not None

        with patch("app.connectors.sources.slack.individual.connector.AppUser", side_effect=ValueError):
            assert c._to_app_user({"id": "U1", "profile": {"email": "a@b.com"}}) is None

    @pytest.mark.asyncio
    async def test_build_user_id_caches_and_refresh(self):
        c = _make_connector()
        u = MagicMock()
        u.source_user_id = "U1"
        u.id = "int1"
        u.email = "a@b.com"
        u.full_name = "A"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])
        await c._build_user_id_caches()
        assert c.user_id_to_internal_id_cache["U1"] == "int1"

        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db"))
        await c._build_user_id_caches()

        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])
        await c._refresh_user_caches()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("x"))
        await c._refresh_user_caches()

    @pytest.mark.asyncio
    async def test_sync_users_and_warm_caches(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "o"
        creator = MagicMock()
        creator.email = "c@e.com"
        creator.id = "UCREATOR"
        creator.full_name = "Creator"
        dep.get_app_creator_user = AsyncMock(return_value=creator)
        dep.on_new_app_users = AsyncMock()
        dep.get_all_app_users = AsyncMock(return_value=[])

        with _patch_base_connector_init():
            c = SlackIndividualConnector(logger, dep, MagicMock(), MagicMock(), "cid")

        mock_ds = MagicMock()
        mock_ds.users_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "members": [{"id": "U2", "profile": {"display_name": "Bob", "email": "b@b.com"}}],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        c.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "xoxp-abc"}}
        )
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=MagicMock(get_token=lambda: "xoxp-abc"))
        with patch.object(SlackIndividualConnector, "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._sync_users()

        mock_ds.users_list = AsyncMock(return_value=MagicMock(success=False, error="ratelimited"))
        with patch.object(SlackIndividualConnector, "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._warm_all_user_caches()

        mock_ds.users_list = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(SlackIndividualConnector, "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._warm_all_user_caches()


class TestChannelHelpers:
    def test_resolve_mpim_name_and_display(self):
        c = _make_connector()
        c.authenticated_user_id = "U0"
        c.user_id_to_name_cache = {"U1": "Alice Smith", "U2": "Bob"}
        name = c._resolve_mpim_name(
            {"id": "G1", "name": "mpdm-alicesmith--bob--1", "members": ["U1", "U0", "U2"]}
        )
        assert "Group DM" in name

        c.user_id_to_name_cache.clear()
        c.user_id_to_email_cache = {"U2": "bob@e.com"}
        name2 = c._resolve_mpim_name({"id": "G2", "name": "other", "members": ["U0", "U2"]})
        assert "bob@e.com" in name2 or "Group DM" in name2

    def test_to_channel_record_group(self):
        c = _make_connector()
        assert c._to_channel_record_group({}) is None
        rg = c._to_channel_record_group(
            {"id": "C1", "name": "general", "topic": {"value": "t"}, "created": "100.0"}
        )
        assert rg is not None
        assert rg.hide_children is True

        rg2 = c._to_channel_record_group(
            {"id": "D1", "is_im": True, "user": "USLACKBOT"}
        )
        assert rg2 and "Slackbot" in (rg2.name or "")

        with patch("app.connectors.sources.slack.individual.connector.RecordGroup", side_effect=TypeError):
            assert c._to_channel_record_group({"id": "C9", "name": "x"}) is None

    @pytest.mark.asyncio
    async def test_channel_permissions_and_fetch_members(self):
        c = _make_connector()
        c.authenticated_user_email = None
        assert await c._channel_permissions({}) == []

        c.authenticated_user_email = "a@b.com"
        perms = await c._channel_permissions({"id": "C1"})
        assert len(perms) == 1

        mock_ds = MagicMock()
        mock_ds.conversations_members = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"members": ["U1"], "response_metadata": {"next_cursor": ""}},
            )
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            m = await c._fetch_channel_members("C1")
        assert m == ["U1"]

        mock_ds.conversations_members = AsyncMock(
            return_value=MagicMock(success=False, error="channel_not_found")
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._fetch_channel_members("C1")

        mock_ds.conversations_members = AsyncMock(side_effect=RuntimeError("x"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._fetch_channel_members("C1")


class TestChannelGroupMap:
    @pytest.mark.asyncio
    async def test_channel_group_map(self):
        c = _make_connector()
        c.channel_groups_cache = {"C1": "rg1"}
        # Only C1 here: including C2 would cache it via the default MagicMock
        # data_store_provider before we install Store() below.
        assert (await c._channel_group_map(["C1"]))["C1"] == "rg1"

        from types import SimpleNamespace

        rg = SimpleNamespace(id="rg2")

        class Tx:
            async def get_record_group_by_external_id(self, **_kwargs):
                return rg

        tx = Tx()

        class _TxCM:
            def __init__(self, inner: Tx):
                self._inner = inner

            async def __aenter__(self) -> Tx:
                return self._inner

            async def __aexit__(self, *a):
                return None

        class Store:
            def transaction(self):
                return _TxCM(tx)

        c.data_store_provider = Store()
        out = await c._channel_group_map(["C2"])
        assert out["C2"] == "rg2"

        async def boom(**_kwargs):
            raise RuntimeError("db")

        tx.get_record_group_by_external_id = boom
        await c._channel_group_map(["C3"])


# ---------------------------------------------------------------------------
# run_sync & incremental / cleanup / signed url / webhook
# ---------------------------------------------------------------------------


class TestRunSyncOrchestration:
    @pytest.mark.asyncio
    async def test_run_sync_happy_path_and_errors(self):
        c = _make_connector()
        c.external_client = MagicMock()

        async def _load(*_a, **_k):
            sf = FilterCollection(filters=[])
            ix = FilterCollection(filters=[])
            c.sync_filters = sf
            c.indexing_filters = ix
            return sf, ix

        ch = MagicMock()
        ch.external_group_id = "C1"
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new=_load,
            ),
            patch.object(type(c), "_sync_users", new=AsyncMock()),
            patch.object(type(c), "_sync_channels", new=AsyncMock(return_value=[ch])),
            patch.object(type(c), "sync_channel_messages", new=AsyncMock()),
            patch.object(type(c), "_sync_thread_growth", new=AsyncMock()),
        ):
            await c.run_sync()

        c.external_client = None
        with pytest.raises(RuntimeError):
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_email_resolution_branches(self):
        c = _make_connector()
        c.external_client = MagicMock()
        c.authenticated_user_email = None

        async def _load(*_a, **_k):
            sf = FilterCollection(filters=[])
            ix = FilterCollection(filters=[])
            c.sync_filters = sf
            c.indexing_filters = ix
            return sf, ix

        creator = MagicMock()
        creator.email = "x@y.com"
        c.data_entities_processor.get_app_creator_user = AsyncMock(return_value=creator)

        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new=_load,
            ),
            patch.object(type(c), "_sync_users", new=AsyncMock()),
            patch.object(type(c), "_sync_channels", new=AsyncMock(return_value=[])),
            patch.object(type(c), "_sync_thread_growth", new=AsyncMock()),
        ):
            await c.run_sync()
        assert c.authenticated_user_email == "x@y.com"

        c.authenticated_user_email = None
        creator2 = MagicMock()
        creator2.email = None
        c.data_entities_processor.get_app_creator_user = AsyncMock(return_value=creator2)
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new=_load,
            ),
            patch.object(type(c), "_sync_users", new=AsyncMock()),
            patch.object(type(c), "_sync_channels", new=AsyncMock(return_value=[])),
            patch.object(type(c), "_sync_thread_growth", new=AsyncMock()),
        ):
            await c.run_sync()

        c.authenticated_user_email = None
        c.data_entities_processor.get_app_creator_user = AsyncMock(side_effect=RuntimeError("nope"))
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new=_load,
            ),
            patch.object(type(c), "_sync_users", new=AsyncMock()),
            patch.object(type(c), "_sync_channels", new=AsyncMock(return_value=[])),
            patch.object(type(c), "_sync_thread_growth", new=AsyncMock()),
        ):
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_run_incremental_cleanup_signed_url_webhook(self):
        c = _make_connector()
        with patch.object(type(c), "run_sync", new=AsyncMock()):
            await c.run_incremental_sync()
        await c.cleanup()
        assert await c.get_signed_url(MagicMock()) == ""
        await c.handle_webhook_notification({})


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_test_connection(self):
        c = _make_connector()
        ds = MagicMock()
        ds.check_token_scopes = AsyncMock(return_value=MagicMock(success=True))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await c.test_connection_and_access() is True
        ds.check_token_scopes = AsyncMock(side_effect=RuntimeError("x"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await c.test_connection_and_access() is False


# ---------------------------------------------------------------------------
# Message pipeline (mocked Slack)
# ---------------------------------------------------------------------------


def _ctx(c: Any, cid: str = "C1") -> Any:
    from app.connectors.sources.slack.individual.connector import ProcessingContext

    return ProcessingContext(
        channel_id=cid,
        channel_groups_map={cid: "rg-int"},
        user_id_to_email=dict(c.user_id_to_email_cache),
        user_id_to_name=dict(c.user_id_to_name_cache),
        channel_id_to_name=dict(c.channel_id_to_name_cache),
        rate_limiter=c.rate_limiter,
    )


class TestReplaceMentions:
    def test_replace_mentions_branches(self):
        c = _make_connector()
        c.user_id_to_email_cache["U99"] = "u99@e.com"
        t = c._replace_mentions_in_text(
            "<@U1|Nick> <@U99> <#C1|chan> <#C2>",
            {"U1": "One"},
            {"C2": "two-channel"},
        )
        assert "@Nick" in t and "@u99@e.com" in t and "#chan" in t


class TestBuildBlocks:
    def test_build_message_block_branches(self):
        c = _make_connector()
        ctx = _ctx(c)
        msg = {
            "ts": "100.0",
            "user": "U1",
            "text": "hi https://ex.com",
            "edited": {"ts": "101.0"},
        }
        c.user_id_to_name_cache["U1"] = "Author"
        b = c._build_message_block(msg, 0, 0, ctx, children_records=None)
        assert b.data

        with patch.object(c, "_message_url", side_effect=RuntimeError):
            b2 = c._build_message_block({"ts": "1.0", "user": "", "text": ""}, 0, 0, ctx)
            assert b2.data == ""

        chs = [ChildRecord(child_type=ChildType.RECORD, child_id="f1", child_name="f.bin")]
        b3 = c._build_message_block(msg, 0, 0, ctx, children_records=chs)
        assert "Attachments" in b3.data

        b4 = c._build_message_block(
            {"ts": "102.0", "user": "U1", "text": "thread", "reply_count": 2},
            0,
            0,
            ctx,
        )
        assert "Reply Count: 2" in b4.data

    def test_build_containers(self):
        c = _make_connector()
        ctx = _ctx(c)
        msgs = [{"ts": "10.0", "user": "U1", "text": "a"}, {"ts": "11.0", "user": "U1", "text": "b"}]
        bc = c._build_burst_block_containers(msgs, "burst_x", ctx)
        assert bc.blocks

        with patch.object(c, "_message_url", side_effect=RuntimeError):
            c._build_burst_block_containers(msgs, "burst_x", ctx)

        single = c._build_single_block_containers({"ts": "5.0", "user": "U1", "text": "solo"}, ctx)
        assert len(single.blocks) == 1


class TestCreateThreadRg:
    def test_create_thread_record_group(self):
        c = _make_connector()
        ctx = _ctx(c)
        assert c._create_thread_record_group({}, ctx, "rg") is None

        rg = c._create_thread_record_group({"ts": "100.0"}, ctx, "rg")
        assert rg is not None

        with patch("app.connectors.sources.slack.individual.connector.RecordGroup", side_effect=TypeError):
            assert c._create_thread_record_group({"ts": "100.0"}, ctx, "rg") is None


class TestMessageRecords:
    @pytest.mark.asyncio
    async def test_to_message_record(self):
        c = _make_connector()
        ctx = _ctx(c)
        assert await c._to_message_record({}, ctx) is None

        msg = {
            "ts": "200.0",
            "user": "U1",
            "text": "hello",
            "thread_ts": "200.0",
            "reply_count": 1,
            "blocks": [{"type": "section"}],
        }
        rec = await c._to_message_record(msg, ctx)
        assert rec is not None

        with patch(
            "app.connectors.sources.slack.individual.connector.MessageRecord",
            side_effect=ValueError,
        ):
            assert await c._to_message_record(msg, ctx) is None


class TestProcessFileAndLink:
    @pytest.mark.asyncio
    async def test_process_file_raw_and_process_file(self):
        c = _make_connector()
        ctx = _ctx(c)
        assert await c._process_file_raw({}, ctx) is None

        fd = {"id": "F1", "name": "a.txt", "mimetype": "text/plain", "created": 1, "size": 3}
        fr = await c._process_file_raw(fd, ctx)
        assert fr is not None

        class _Resp:
            status_code = 200
            content = b"abc"

        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.get = AsyncMock(return_value=_Resp())

        fd2 = {
            "id": "F2",
            "name": "b.bin",
            "url_private_download": "https://files.slack.com/f",
            "created": 1,
        }
        with patch("app.connectors.sources.slack.individual.connector.httpx.AsyncClient", return_value=fake_client):
            await c._process_file_raw(fd2, ctx)

        with patch("app.connectors.sources.slack.individual.connector.httpx.AsyncClient", side_effect=RuntimeError):
            await c._process_file_raw(fd2, ctx)

        with patch(
            "app.connectors.sources.slack.individual.connector.FileRecord",
            side_effect=ValueError,
        ):
            assert await c._process_file_raw(fd, ctx) is None

        parent = MagicMock(spec=MessageRecord)
        parent.id = "mid"
        parent.external_record_id = "200.0"
        out = await c._process_file(fd, parent, ctx)
        assert out and out.parent_node_id == "mid"

    @pytest.mark.asyncio
    async def test_process_link(self):
        c = _make_connector()
        ctx = _ctx(c)
        parent = MagicMock(spec=MessageRecord)
        parent.id = "p"
        parent.external_record_id = "1.0"
        lr = await c._process_link(
            "https://x.atlassian.net/browse/ABC-9", parent, ctx, {"attachments": []}
        )
        assert lr is not None

        with patch(
            "app.connectors.sources.slack.individual.connector.LinkRecord",
            side_effect=ValueError,
        ):
            assert await c._process_link("https://z", parent, ctx, {}) is None

    @pytest.mark.asyncio
    async def test_enrich_link_records(self):
        c = _make_connector()
        lr = MagicMock()
        lr.record_type = RecordType.LINK
        lr.weburl = "https://u"
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        rel = MagicMock()
        rel.id = "rid"
        tx.get_record_by_weburl = AsyncMock(return_value=rel)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        await c._enrich_link_records_linked_id([(lr, [])])

        tx.get_record_by_weburl = AsyncMock(side_effect=RuntimeError("inner"))
        await c._enrich_link_records_linked_id([(lr, [])])

        c.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("outer"))
        await c._enrich_link_records_linked_id([(lr, [])])

        await c._enrich_link_records_linked_id([(MagicMock(record_type=RecordType.MESSAGE), [])])


# ---------------------------------------------------------------------------
# Bursts / singles / threads (mocked)
# ---------------------------------------------------------------------------


class TestProcessBurstSingle:
    @pytest.mark.asyncio
    async def test_process_burst_and_single(self):
        c = _make_connector()
        ctx = _ctx(c)
        from app.connectors.sources.slack.individual.connector import ConversationalBurst

        burst = ConversationalBurst(
            messages=[
                {"ts": "10.0", "user": "U1", "text": "a", "thread_ts": "10.0", "reply_count": 1},
                {"ts": "11.0", "user": "U1", "text": "https://ex.com b"},
            ],
            start_ts=10.0,
            end_ts=11.0,
        )
        recs, def_t = await c._process_burst(burst, ctx)
        assert recs
        assert def_t

        empty_b = ConversationalBurst(messages=[{"ts": "", "user": "U1"}], start_ts=0.0, end_ts=0.0)
        assert await c._process_burst(empty_b, ctx) == ([], [])

        bad = ConversationalBurst(messages=[{"ts": ""}], start_ts=0.0, end_ts=0.0)
        r2, d2 = await c._process_burst(bad, ctx)
        assert r2 == [] and d2 == []

        with patch(
            "app.connectors.sources.slack.individual.connector.MessageRecord",
            side_effect=ValueError,
        ):
            r3, d3 = await c._process_burst(burst, ctx)
            assert r3 == [] and d3 == []

        srec, sdef = await c._process_single({"ts": "", "text": "x"}, ctx)
        assert srec == [] and sdef == []

        single_msg = {"ts": "20.0", "user": "U1", "text": "solo", "thread_ts": "20.0", "reply_count": 1}
        srec2, sdef2 = await c._process_single(single_msg, ctx)
        assert srec2 and sdef2

        with patch(
            "app.connectors.sources.slack.individual.connector.MessageRecord",
            side_effect=ValueError,
        ):
            srec3, sdef3 = await c._process_single(single_msg, ctx)
            assert srec3 == [] and sdef3 == []


class TestThreadBurstRecord:
    @pytest.mark.asyncio
    async def test_build_thread_burst_record_edges(self):
        from app.connectors.sources.slack.individual.connector import ConversationalBurst

        c = _make_connector()
        ctx = _ctx(c)
        b = ConversationalBurst([], 0.0, 0.0)
        assert await c._build_thread_burst_record(b, "1.0", "rg", ctx) is None

    @pytest.mark.asyncio
    async def test_build_thread_burst_record_async(self):
        from app.connectors.sources.slack.individual.connector import ConversationalBurst

        c = _make_connector()
        ctx = _ctx(c)
        burst = ConversationalBurst(
            messages=[{"ts": "1.0", "user": "U1", "text": "z"}],
            start_ts=1.0,
            end_ts=1.0,
        )
        rec = await c._build_thread_burst_record(burst, "1.0", "rg", ctx)
        assert rec is not None

        burst2 = ConversationalBurst(
            messages=[{"ts": "", "user": "U1"}],
            start_ts=0.0,
            end_ts=0.0,
        )
        assert await c._build_thread_burst_record(burst2, "1.0", "rg", ctx) is None

        with patch(
            "app.connectors.sources.slack.individual.connector.MessageRecord",
            side_effect=ValueError,
        ):
            assert await c._build_thread_burst_record(burst, "1.0", "rg", ctx) is None


class TestFetchThreadReplies:
    @pytest.mark.asyncio
    async def test_fetch_thread_replies_raw(self):
        c = _make_connector()
        ctx = _ctx(c)
        ds = MagicMock()
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"ts": "1.0"},
                        {"ts": "1.1", "user": "U2"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            reps = await c._fetch_thread_replies_raw("C1", "1.0", ctx, oldest="0.5")
        assert len(reps) == 1 and reps[0]["ts"] == "1.1"


class TestProcessThread:
    @pytest.mark.asyncio
    async def test_process_thread_full_and_incremental(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection(filters=[])
        ctx = _ctx(c)
        parent = {"ts": "1.0", "user": "U1", "text": "root", "reply_count": 1}

        with patch.object(c, "_create_thread_record_group", return_value=None):
            await c._process_thread(parent, ctx, "rg")

        rg = MagicMock()
        rg.id = "trg"
        rg.external_group_id = "thread_C1_1.0"
        rg.group_type = "SLACK_THREAD"

        async def _replies(*_a, **_k):
            return [{"ts": "1.1", "user": "U2", "text": "rep"}]

        with (
            patch.object(c, "_create_thread_record_group", return_value=rg),
            patch.object(c, "_fetch_thread_replies_raw", new=AsyncMock(side_effect=_replies)),
            patch.object(c.data_entities_processor, "on_new_record_groups", new=AsyncMock()),
            patch.object(c.data_entities_processor, "on_new_records", new=AsyncMock()),
        ):
            await c._process_thread(parent, ctx, "ch-rg")

        with (
            patch.object(c, "_create_thread_record_group", return_value=rg),
            patch.object(c.data_entities_processor, "on_new_record_groups", new=AsyncMock(side_effect=RuntimeError)),
        ):
            with pytest.raises(RuntimeError):
                await c._process_thread(parent, ctx, "ch-rg")

        with patch.object(c, "_fetch_thread_replies_raw", new=AsyncMock(return_value=[])):
            await c._process_thread_incremental(parent, ctx, "trg", "0.5")

        with patch.object(c, "_fetch_thread_replies_raw", new=AsyncMock(return_value=[{"ts": "2.0", "user": "U2", "text": "n"}])):
            with (
                patch.object(c.data_entities_processor, "on_new_records", new=AsyncMock()),
            ):
                await c._process_thread_incremental(parent, ctx, "trg", "0.5")


# ---------------------------------------------------------------------------
# sync_channel_messages & thread growth & change detection
# ---------------------------------------------------------------------------


class TestSyncChannelMessages:
    @pytest.mark.asyncio
    async def test_sync_channel_messages_flow(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection(filters=[])
        c.messages_sync_point.read_sync_point = AsyncMock(return_value=None)

        msg = {"ts": "99.0", "user": "U1", "text": "hello"}
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [msg], "response_metadata": {"next_cursor": ""}},
            )
        )
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([(MagicMock(record_type=RecordType.MESSAGE), [])], []))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
            patch.object(c.data_entities_processor, "on_new_records", new=AsyncMock()),
        ):
            await c.sync_channel_messages("C1")

        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False, error="channel_not_found"))
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
        ):
            await c.sync_channel_messages("C1")

        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False, error="other"))
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
        ):
            await c.sync_channel_messages("C1")

    @pytest.mark.asyncio
    async def test_sync_channel_messages_deferred_thread_branches(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection(filters=[])
        c.messages_sync_point.read_sync_point = AsyncMock(return_value=None)
        from app.connectors.sources.slack.individual.connector import DeferredThread

        parent = {"ts": "10.0", "user": "U1", "text": "t", "reply_count": 2, "thread_ts": "10.0"}
        ctx_d = _ctx(c)
        deferred = DeferredThread(msg=parent, ctx=ctx_d, rg_id="rg")
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [parent], "response_metadata": {"next_cursor": ""}},
            )
        )
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([], [deferred]))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
            patch.object(type(c), "_process_thread", new=AsyncMock()),
        ):
            await c.sync_channel_messages("C1")

        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "9.0"})
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        erg = MagicMock()
        erg.id = "thread-rg"
        tx.get_record_group_by_external_id = AsyncMock(return_value=erg)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([], [deferred]))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
            patch.object(type(c), "_process_thread_incremental", new=AsyncMock()),
        ):
            await c.sync_channel_messages("C1")

        c.audit_log_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("sp"))
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([], [deferred]))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
        ):
            await c.sync_channel_messages("C1")


class TestSyncChannels:
    @pytest.mark.asyncio
    async def test_sync_channels_filters_and_errors(self):
        c = _make_connector()
        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS,
                    value=["C1"],
                    type=FilterType.LIST,
                    operator=ListOperator.IN,
                ),
                Filter(
                    key=SyncFilterKey.CHANNEL_TYPES,
                    value=["public_channel"],
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                ),
            ]
        )
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "channels": [
                        {"id": "C1", "name": "x", "created": "1.0"},
                        {"id": "C2", "name": "y"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(c.data_entities_processor, "on_new_record_groups", new=AsyncMock()),
        ):
            rgs = await c._sync_channels()
        assert rgs

        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS,
                    value=["C9"],
                    type=FilterType.LIST,
                    operator=ListOperator.NOT_IN,
                ),
            ]
        )
        ds.conversations_list = AsyncMock(return_value=MagicMock(success=False, error="e"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await c._sync_channels() == []

        ds.conversations_list = AsyncMock(
            return_value=MagicMock(success=True, data={"channels": [], "response_metadata": {}})
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await c._sync_channels() == []


class TestThreadGrowthAndChangeDetection:
    @pytest.mark.asyncio
    async def test_sync_thread_growth_and_scan(self):
        c = _make_connector()
        c.channel_groups_cache = {"": "x", "C1": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(type(c), "_refresh_user_caches", new=AsyncMock()),
            patch.object(type(c), "_scan_channel_thread_growth", new=AsyncMock(side_effect=RuntimeError("x"))),
            patch.object(type(c), "_compute_sync_window_oldest", return_value="1.0"),
        ):
            await c._sync_thread_growth()

        with patch.object(type(c), "_refresh_user_caches", new=AsyncMock(side_effect=RuntimeError("outer"))):
            await c._sync_thread_growth()

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"subtype": "message_changed", "message": {"ts": "1.0", "user": "U1", "reply_count": 1, "thread_ts": "1.0"}},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_handle_new_thread", new=AsyncMock(side_effect=RuntimeError("bad"))),
        ):
            await c._scan_channel_thread_growth("C1", "0.0")

        ds.conversations_history = AsyncMock(side_effect=RuntimeError("hist"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            await c._scan_channel_thread_growth("C1", "0.0")

    @pytest.mark.asyncio
    async def test_sync_message_changes_and_check_channel(self):
        c = _make_connector()
        c.channel_groups_cache = {"C1": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(type(c), "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(type(c), "_refresh_user_caches", new=AsyncMock()),
            patch.object(type(c), "_check_channel_changes", new=AsyncMock(return_value=1)),
        ):
            await c._sync_message_changes()

        with patch.object(type(c), "_refresh_user_caches", new=AsyncMock(side_effect=RuntimeError("x"))):
            await c._sync_message_changes()

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"ts": "1.0", "user": "U1", "edited": {}, "text": "x"},
                        {"ts": "2.0", "user": "U1", "subtype": "bot_message"},
                        {"ts": "3.0", "user": "U1", "subtype": "message_deleted"},
                        {"ts": "4.0", "user": "U1", "thread_ts": "1.0"},
                        {"subtype": "message_changed", "message": {}},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_handle_edited_message", new=AsyncMock(return_value=0)),
            patch.object(type(c), "_handle_new_thread", new=AsyncMock()),
        ):
            n = await c._check_channel_changes("C1", "0.0")
        assert isinstance(n, int)

        ds.conversations_history = AsyncMock(side_effect=RuntimeError("p"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            await c._check_channel_changes("C1", "0.0")

        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=False, error="channel_not_found")
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            await c._check_channel_changes("C1", "0.0")

        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False, error="other"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            await c._check_channel_changes("C1", "0.0")


class TestFindBurstAndHandleEdited:
    @pytest.mark.asyncio
    async def test_find_burst_record(self):
        c = _make_connector()
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.find_slack_burst_record_by_ts = AsyncMock(return_value=MagicMock())
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        assert await c._find_burst_record_by_message_ts("C", "1.0") is not None

        tx.find_slack_burst_record_by_ts = AsyncMock(side_effect=RuntimeError)
        assert await c._find_burst_record_by_message_ts("C", "1.0") is None

    @pytest.mark.asyncio
    async def test_handle_edited_message_cases(self):
        c = _make_connector()
        md = {"ts": "1.0", "user": "U1", "text": "new", "edited": {"ts": "2.0"}}

        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db"))
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        assert await c._handle_edited_message(md, "C1", "1.0") == 0

        existing = MagicMock()
        existing.id = "rid"
        existing.version = 1
        existing.source_created_at = 1000
        existing.weburl = "u"
        existing.record_group_id = "rg"
        tx.get_record_by_external_id = AsyncMock(return_value=existing)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with patch.object(c, "_record_changed", return_value=False):
            assert await c._handle_edited_message(md, "C1", "1.0") == 0

        with (
            patch.object(c, "_record_changed", return_value=True),
            patch.object(c.data_entities_processor, "on_record_content_update", new=AsyncMock()),
        ):
            assert await c._handle_edited_message(md, "C1", "1.0") == 1

        tx.get_record_by_external_id = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        burst = MagicMock()
        burst.record_group_id = "rg"
        burst.start_ts = "0.5"
        burst.end_ts = "2.0"
        burst.external_record_id = "burst_x"
        burst.version = 1
        burst.content = ""
        burst.block_containers = None
        with (
            patch.object(c, "_find_burst_record_by_message_ts", new=AsyncMock(return_value=burst)),
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=MagicMock(
                conversations_history=AsyncMock(return_value=MagicMock(
                    success=True,
                    data={"messages": [{"ts": "1.0", "text": "n"}]},
                )),
            ))),
            patch.object(c, "_build_burst_block_containers", return_value=MagicMock()),
            patch.object(c.data_entities_processor, "on_record_content_update", new=AsyncMock()),
        ):
            assert await c._handle_edited_message(md, "C1", "1.0") == 1

        with (
            patch.object(c, "_find_burst_record_by_message_ts", new=AsyncMock(return_value=burst)),
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=MagicMock(
                conversations_history=AsyncMock(return_value=MagicMock(success=False)),
            ))),
        ):
            assert await c._handle_edited_message(md, "C1", "1.0") == 0


class TestHandleNewThread:
    @pytest.mark.asyncio
    async def test_handle_new_thread_paths(self):
        c = _make_connector()
        md = {"ts": "1.0", "user": "U1", "text": "t", "reply_count": 1, "thread_ts": "1.0", "latest_reply": "1.1"}

        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(type(c), "_process_thread", new=AsyncMock()),
        ):
            await c._handle_new_thread(md, "C1", "1.0")

        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "0.5"})
        tx.get_record_group_by_external_id = AsyncMock(
            side_effect=[None, MagicMock(id="trg")]
        )
        with (
            patch.object(type(c), "_process_thread_incremental", new=AsyncMock()),
        ):
            await c._handle_new_thread(md, "C1", "1.0")

        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_reply_ts": "0.5"})
        tx.get_record_group_by_external_id = AsyncMock(side_effect=[RuntimeError("x"), MagicMock(id="trg")])
        with patch.object(type(c), "_process_thread_incremental", new=AsyncMock()):
            await c._handle_new_thread(md, "C1", "1.0")


# ---------------------------------------------------------------------------
# Streaming & reindex
# ---------------------------------------------------------------------------


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_record_variants(self):
        c = _make_connector()
        mr = MagicMock(spec=MessageRecord)
        mr.record_type = RecordType.MESSAGE
        mr.external_record_id = "1.0"
        mr.external_record_group_id = "C1"
        mr.id = "mid"
        with patch.object(c, "_build_message_blocks_for_streaming", new=AsyncMock(return_value=b"{}")):
            resp = await c.stream_record(mr)
        assert resp is not None

        fr = MagicMock(spec=FileRecord)
        fr.record_type = RecordType.FILE
        fr.record_name = "f.bin"
        fr.mime_type = "application/octet-stream"
        fr.external_record_id = "F1"
        fr.id = "fid"
        async def _gen():
            yield b"x"
        with patch.object(c, "_stream_file_bytes", return_value=_gen()):
            with patch(
                "app.connectors.sources.slack.individual.connector.create_stream_record_response",
                return_value=MagicMock(),
            ):
                await c.stream_record(fr)

        lr = MagicMock()
        lr.record_type = RecordType.LINK
        lr.external_record_id = "L1"
        lr.record_name = "L"
        lr.weburl = "https://w.com"
        r = await c.stream_record(lr)
        assert r is not None

        bad = MagicMock()
        bad.record_type = RecordType.DATASOURCE
        with pytest.raises(HTTPException):
            await c.stream_record(bad)

        with patch.object(c, "_build_message_blocks_for_streaming", new=AsyncMock(side_effect=ValueError("z"))):
            with pytest.raises(HTTPException):
                await c.stream_record(mr)

    @pytest.mark.asyncio
    async def test_stream_link_missing_weburl(self):
        c = _make_connector()
        lr = MagicMock()
        lr.record_type = RecordType.LINK
        lr.weburl = None
        lr.external_record_id = "L1"
        with pytest.raises(HTTPException):
            await c.stream_record(lr)


class TestBuildMessageBlocksForStreaming:
    @pytest.mark.asyncio
    async def test_build_message_blocks_branches(self):
        c = _make_connector()
        mr = MagicMock(spec=MessageRecord)
        mr.id = "id"
        mr.external_record_id = None
        mr.external_record_group_id = "C1"
        with pytest.raises(HTTPException):
            await c._build_message_blocks_for_streaming(mr)

        mr.external_record_id = "burst_C1_abc"
        mr.start_ts = None
        mr.end_ts = "2"
        mr.external_record_group_id = "C1"
        with pytest.raises(HTTPException):
            await c._build_message_blocks_for_streaming(mr)

        mr.start_ts = "1"
        mr.end_ts = "2"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(return_value=MagicMock(success=False))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            with pytest.raises(HTTPException):
                await c._build_message_blocks_for_streaming(mr)

        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": []})
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            with pytest.raises(HTTPException):
                await c._build_message_blocks_for_streaming(mr)

        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.5", "user": "U1", "text": "x"}]},
            )
        )
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.get_records_by_parent = AsyncMock(return_value=[])
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            out = await c._build_message_blocks_for_streaming(mr)
        assert isinstance(out, bytes)

        mr2 = MagicMock(spec=MessageRecord)
        mr2.id = "id"
        mr2.external_record_id = "thread_burst_x"
        mr2.start_ts = "1"
        mr2.end_ts = "2"
        mr2.thread_id = "0.5"
        mr2.external_record_group_id = "C1"
        ds2 = MagicMock()
        ds2.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.1", "user": "U1", "text": "r"}]},
            )
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds2)):
            out2 = await c._build_message_blocks_for_streaming(mr2)
        assert isinstance(out2, bytes)

        mr3 = MagicMock(spec=MessageRecord)
        mr3.id = "id"
        mr3.external_record_id = "10.0"
        mr3.external_record_group_id = "thread_C1_9.0"
        mr3.thread_id = None
        ds3 = MagicMock()
        ds3.conversations_history = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": [{"ts": "10.0", "user": "U1", "text": "h"}]})
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds3)):
            out3 = await c._build_message_blocks_for_streaming(mr3)
        assert isinstance(out3, bytes)

        mr4 = MagicMock(spec=MessageRecord)
        mr4.id = "id"
        mr4.external_record_id = "10.1"
        mr4.external_record_group_id = "C1"
        mr4.thread_id = "10.0"
        ds4 = MagicMock()
        ds4.conversations_history = AsyncMock(return_value=MagicMock(success=True, data={"messages": []}))
        ds4.conversations_replies = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": [{"ts": "10.1", "user": "U1"}]})
        )
        fresh = AsyncMock(side_effect=[ds4, ds4])
        with patch.object(type(c), "_fresh_datasource", fresh):
            out4 = await c._build_message_blocks_for_streaming(mr4)
        assert isinstance(out4, bytes)


class TestStreamFileBytes:
    @pytest.mark.asyncio
    async def test_stream_file_bytes_paths(self):
        c = _make_connector()
        rec = MagicMock(spec=FileRecord)
        rec.external_record_id = None
        rec.id = "id"
        gen0 = c._stream_file_bytes(rec)
        with pytest.raises(HTTPException):
            await gen0.__anext__()

        rec.external_record_id = "F1"
        ds = MagicMock()
        ds.files_info = AsyncMock(return_value=MagicMock(success=False))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            gen = c._stream_file_bytes(rec)
            with pytest.raises(HTTPException):
                await gen.__anext__()

        ds.files_info = AsyncMock(
            return_value=MagicMock(success=True, data={"file": {}})
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            gen = c._stream_file_bytes(rec)
            with pytest.raises(HTTPException):
                await gen.__anext__()

        class _StreamResp:
            status_code = 403
            headers = {"content-type": "text/html"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def aiter_bytes(self, n: int):
                if False:
                    yield b""

        class _HttpClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, *a, **k):
                return _StreamResp()

        ds.files_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"file": {"url_private_download": "https://f"}},
            )
        )
        c.external_client.get_client = MagicMock(return_value=MagicMock(get_token=lambda: "tok"))
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch(
                "app.connectors.sources.slack.individual.connector.httpx.AsyncClient",
                return_value=_HttpClient(),
            ),
        ):
            gen = c._stream_file_bytes(rec)
            with pytest.raises(HTTPException):
                await gen.__anext__()


class TestReindexAndCheckUpdated:
    @pytest.mark.asyncio
    async def test_reindex_records(self):
        c = _make_connector()
        await c.reindex_records([])

        r = MagicMock()
        r.id = "r1"
        r.record_type = RecordType.MESSAGE
        with (
            patch.object(c, "_check_updated", new=AsyncMock(side_effect=[("u", []), None, RuntimeError])),
            patch.object(c.data_entities_processor, "on_record_content_update", new=AsyncMock()),
            patch.object(c.data_entities_processor, "on_updated_record_permissions", new=AsyncMock()),
            patch.object(c.data_entities_processor, "reindex_existing_records", new=AsyncMock()),
        ):
            await c.reindex_records([r, r, r])

    @pytest.mark.asyncio
    async def test_check_updated_message_and_file(self):
        c = _make_connector()
        mr = MagicMock()
        mr.record_type = RecordType.MESSAGE
        mr.external_record_id = None
        assert await c._check_updated(mr) is None

        mr.external_record_id = "burst_C1_abc"
        mr.external_record_group_id = "C1"
        assert await c._check_updated_message(mr) is None

        mr.external_record_id = "thread_burst_x"
        assert await c._check_updated_message(mr) is None

        mr.external_record_id = "10.0"
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": []})
        )
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(success=True, data={"messages": []})
        )
        mr.thread_id = "9.0"
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await c._check_updated_message(mr) is None

        mr.thread_id = None
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "10.0", "user": "U1", "text": "same"}]},
            )
        )
        mr.content = "same"
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(c, "_record_changed", return_value=False),
        ):
            assert await c._check_updated_message(mr) is None

        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(c, "_record_changed", return_value=True),
            patch.object(c, "_to_message_record", new=AsyncMock(return_value=None)),
        ):
            assert await c._check_updated_message(mr) is None

        upd = MagicMock()
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(c, "_record_changed", return_value=True),
            patch.object(c, "_to_message_record", new=AsyncMock(return_value=upd)),
        ):
            tup = await c._check_updated_message(mr)
        assert tup is not None

        fr = MagicMock()
        fr.record_type = RecordType.FILE
        fr.external_record_id = None
        assert await c._check_updated_file(fr) is None

        fr.external_record_id = "F1"
        fr.source_updated_at = 9_999_999_999_000
        ds2 = MagicMock()
        ds2.files_info = AsyncMock(
            return_value=MagicMock(success=True, data={"file": {"created": 1}})
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds2)):
            assert await c._check_updated_file(fr) is None

        fr.source_updated_at = None
        fr.source_created_at = 0
        fr.parent_external_record_id = "10.0"
        fr.external_record_group_id = "C1"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=RuntimeError)
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds2)):
            assert await c._check_updated_file(fr) is None


class TestFilterOptions:
    @pytest.mark.asyncio
    async def test_get_filter_options_and_ensure_caches(self):
        c = _make_connector()
        with pytest.raises(ValueError):
            await c.get_filter_options("other")

        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "channels": [
                        {"id": "C1", "name": "general"},
                        {"id": None},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(type(c), "_ensure_user_caches_for_filters", new=AsyncMock()),
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
        ):
            r = await c.get_filter_options("channel_ids", search="gen")
        assert r.success

        ds.conversations_list = AsyncMock(return_value=MagicMock(success=False, error="bad"))
        with (
            patch.object(type(c), "_ensure_user_caches_for_filters", new=AsyncMock()),
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
        ):
            r2 = await c.get_filter_options("channel_ids")
        assert r2.success is False

    @pytest.mark.asyncio
    async def test_ensure_user_caches_for_filters_fallback(self):
        c = _make_connector()
        c.user_id_to_name_cache.clear()
        with (
            patch.object(type(c), "_refresh_user_caches", new=AsyncMock()),
            patch.object(type(c), "_warm_all_user_caches", new=AsyncMock()),
        ):
            await c._ensure_user_caches_for_filters()


class TestChannelDisplayName:
    def test_channel_display_name_variants(self):
        c = _make_connector()
        c.user_id_to_name_cache["U1"] = "Alice"
        assert "Alice" in c._channel_display_name({"id": "D1", "is_im": True, "user": "U1"})
        assert "Slackbot" in c._channel_display_name({"id": "D1", "is_im": True, "user": "USLACKBOT"})
        c.user_id_to_name_cache.clear()
        c.user_id_to_email_cache["U2"] = "u2@e.com"
        assert "u2@e.com" in c._channel_display_name({"id": "D1", "is_im": True, "user": "U2"})
        c._resolve_mpim_name = MagicMock(return_value="MPIM")
        assert c._channel_display_name({"id": "G1", "is_mpim": True}) == "MPIM"
        assert c._channel_display_name({"id": "C9"}) == "Channel: C9"


class TestIsEdited:
    def test_is_edited(self):
        c = _make_connector()
        assert c._is_edited({"edited": {}}) is True
        assert c._is_edited({}) is False


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        with (
            patch(
                "app.connectors.sources.slack.individual.connector.DataSourceEntitiesProcessor",
            ) as dep_cls,
            _patch_base_connector_init(),
        ):
            dep = MagicMock()
            dep.org_id = "o"
            dep.initialize = AsyncMock()
            dep_cls.return_value = dep
            conn = await SlackIndividualConnector.create_connector(
                MagicMock(), MagicMock(), MagicMock(), "cid2",
            )
        assert isinstance(conn, SlackIndividualConnector)


class TestSlackIndividualCoverageBoost:
    """Raise line coverage for ``individual.connector`` (``_fresh_datasource``, ``run_sync`` email)."""

    @pytest.mark.asyncio
    async def test_fresh_datasource_prefers_xoxp_from_credentials(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={
                "credentials": {"access_token": "xoxp-first"},
                "auth": {"apiToken": "xoxp-second"},
            },
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value="xoxp-first")
        c.external_client.get_client = MagicMock(return_value=client)
        from app.connectors.sources.slack.individual.connector import SlackDataSource

        ds = await c._fresh_datasource()
        assert isinstance(ds, SlackDataSource)

    @pytest.mark.asyncio
    async def test_fresh_datasource_fallback_first_non_empty_token(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={
                "credentials": {"access_token": ""},
                "auth": {"apiToken": "xoxb-fallback"},
            },
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value="xoxb-fallback")
        c.external_client.get_client = MagicMock(return_value=client)
        await c._fresh_datasource()
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_fresh_datasource_calls_set_token_on_mismatch(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "xoxp-abc"}},
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value="other")
        client.set_token = MagicMock()
        c.external_client.get_client = MagicMock(return_value=client)
        await c._fresh_datasource()
        client.set_token.assert_called_once_with("xoxp-abc")

    @pytest.mark.asyncio
    async def test_fresh_datasource_no_token_raises(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": ""}, "auth": {}},
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value=None)
        c.external_client.get_client = MagicMock(return_value=client)
        with pytest.raises(RuntimeError, match="No access token"):
            await c._fresh_datasource()

    @pytest.mark.asyncio
    async def test_run_sync_sets_email_from_creator(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.authenticated_user_email = ""
        cr = MagicMock()
        cr.email = "creator@example.com"
        c.data_entities_processor.get_app_creator_user = AsyncMock(return_value=cr)
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackIndividualConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_sync_channels", new_callable=AsyncMock, return_value=[]),
            patch.object(SlackIndividualConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()
        assert c.authenticated_user_email == "creator@example.com"

    @pytest.mark.asyncio
    async def test_run_sync_creator_without_email_warns(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.authenticated_user_email = ""
        cr = MagicMock()
        cr.email = None
        c.data_entities_processor.get_app_creator_user = AsyncMock(return_value=cr)
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackIndividualConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_sync_channels", new_callable=AsyncMock, return_value=[]),
            patch.object(SlackIndividualConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_run_sync_no_creator_warns(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.authenticated_user_email = ""
        c.data_entities_processor.get_app_creator_user = AsyncMock(return_value=None)
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackIndividualConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_sync_channels", new_callable=AsyncMock, return_value=[]),
            patch.object(SlackIndividualConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_run_sync_get_creator_raises_warns(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.authenticated_user_email = ""
        c.data_entities_processor.get_app_creator_user = AsyncMock(side_effect=RuntimeError("db"))
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.load_connector_filters",
                new_callable=AsyncMock,
                return_value=(c.sync_filters, c.indexing_filters),
            ),
            patch.object(SlackIndividualConnector, "_sync_users", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_sync_channels", new_callable=AsyncMock, return_value=[]),
            patch.object(SlackIndividualConnector, "_sync_thread_growth", new_callable=AsyncMock),
        ):
            await c.run_sync()
        c.logger.warning.assert_called()


class TestSlackIndividualThreadScan:
    """Thread-growth scan paths for ``SlackIndividualConnector``."""

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_history_raises(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        ds = MagicMock()
        ds.conversations_history = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(SlackIndividualConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            await c._scan_channel_thread_growth("C1", "1.0")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_calls_handle_new_thread(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
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
            patch.object(SlackIndividualConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackIndividualConnector, "_handle_new_thread", new_callable=AsyncMock) as h,
        ):
            await c._scan_channel_thread_growth("C1", "1.0")
        h.assert_awaited()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_message_changed_unwraps(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
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
            patch.object(SlackIndividualConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackIndividualConnector, "_handle_new_thread", new_callable=AsyncMock) as h,
        ):
            await c._scan_channel_thread_growth("C1", None)
        h.assert_awaited()

    @pytest.mark.asyncio
    async def test_scan_channel_thread_growth_handle_new_thread_exception(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
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
            patch.object(SlackIndividualConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(
                SlackIndividualConnector,
                "_handle_new_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("handler"),
            ),
        ):
            await c._scan_channel_thread_growth("C1", None)
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_sync_thread_growth_checkpoint_and_per_channel_error(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.channel_groups_cache = {"Ctg": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            return_value={"last_check_time": 1_700_000_000_000},
        )
        with (
            patch.object(SlackIndividualConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackIndividualConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_scan_channel_thread_growth", new_callable=AsyncMock),
        ):
            await c._sync_thread_growth()
        c.audit_log_sync_point.update_sync_point.assert_awaited()

        c2 = _make_connector()
        c2.channel_groups_cache = {"Cbad": "rg"}
        c2.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(SlackIndividualConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackIndividualConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(
                SlackIndividualConnector,
                "_scan_channel_thread_growth",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await c2._sync_thread_growth()
        c2.logger.error.assert_called()


class TestNumericEpochToMs:
    """Slack channel created/updated timestamp normalization."""

    def test_seconds_to_ms(self) -> None:
        assert _numeric_epoch_to_ms(1779700655) == 1779700655000

    def test_milliseconds_unchanged(self) -> None:
        assert _numeric_epoch_to_ms(1779700655779) == 1779700655779

    def test_slack_channel_pair(self) -> None:
        created_ms = _numeric_epoch_to_ms(1779700655)
        updated_ms = _numeric_epoch_to_ms(1779700655779)
        assert created_ms == 1779700655000
        assert updated_ms == 1779700655779
        assert updated_ms > created_ms

    def test_none(self) -> None:
        assert _numeric_epoch_to_ms(None) is None


class TestSlackIndividualAdditionalCoverage:
    """Target remaining uncovered branches in individual.connector."""

    def test_compute_sync_window_relative_operator(self):
        import time as time_mod
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key="sync_window",
                    value=None,
                    type=FilterType.DATETIME,
                    operator=FilterOperator.LAST_7_DAYS,
                )
            ]
        )
        with patch.object(time_mod, "time", return_value=1_000_000.0):
            out = float(c._compute_sync_window_oldest())
        assert out == pytest.approx(1_000_000.0 - 7 * 86400, rel=1e-3)

    @pytest.mark.asyncio
    async def test_warm_user_caches_success_with_pagination(self):
        c = _make_connector()
        page1 = MagicMock(
            success=True,
            data={
                "members": [
                    {
                        "id": "U1",
                        "profile": {
                            "real_name": "Alice",
                            "email": "alice@e.com",
                        },
                    },
                    {"id": "", "profile": {}},
                ],
                "response_metadata": {"next_cursor": "cur2"},
            },
        )
        page2 = MagicMock(
            success=True,
            data={
                "members": [
                    {
                        "id": "U2",
                        "profile": {"display_name": "Bob"},
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
        mock_ds = MagicMock()
        mock_ds.users_list = AsyncMock(side_effect=[page1, page2])
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._warm_all_user_caches()
        assert c.user_id_to_name_cache["U1"] == "Alice"
        assert c.user_id_to_email_cache["U1"] == "alice@e.com"
        assert c.user_id_to_name_cache["U2"] == "Bob"

    @pytest.mark.asyncio
    async def test_sync_channels_include_exclude_and_pagination(self):
        c = _make_connector()
        c.authenticated_user_email = "me@e.com"
        c.sync_filters = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS,
                    value=["C1"],
                    type=FilterType.LIST,
                    operator=ListOperator.IN,
                ),
                Filter(
                    key=SyncFilterKey.CHANNEL_IDS,
                    value=["C9"],
                    type=FilterType.LIST,
                    operator=ListOperator.NOT_IN,
                ),
                Filter(
                    key=SyncFilterKey.CHANNEL_TYPES,
                    value=["public_channel"],
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                ),
            ]
        )
        page1 = MagicMock(
            success=True,
            data={
                "channels": [
                    {"id": "C1", "name": "general", "created": "1.0"},
                    {"id": "C9", "name": "skip-me"},
                    {"id": "", "name": "no-id"},
                ],
                "response_metadata": {"next_cursor": "next"},
            },
        )
        page2 = MagicMock(
            success=True,
            data={
                "channels": [{"id": "C2", "name": "other"}],
                "response_metadata": {"next_cursor": ""},
            },
        )
        mock_ds = MagicMock()
        mock_ds.conversations_list = AsyncMock(side_effect=[page1, page2])

        def _to_rg(cd):
            if cd.get("id") != "C1":
                raise RuntimeError("bad channel")
            rg = MagicMock()
            rg.external_group_id = "C1"
            rg.name = "general"
            rg.id = "rg-new"
            return rg

        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)),
            patch.object(type(c), "_to_channel_record_group", side_effect=_to_rg),
            patch.object(c.data_entities_processor, "on_new_record_groups", new=AsyncMock()),
        ):
            rgs = await c._sync_channels()
        assert len(rgs) == 1
        assert c.channel_groups_cache["C1"] == "rg-new"
        assert c.channel_id_to_name_cache["C1"] == "general"

    @pytest.mark.asyncio
    async def test_sync_channels_list_failure(self):
        c = _make_connector()
        mock_ds = MagicMock()
        mock_ds.conversations_list = AsyncMock(return_value=MagicMock(success=False, error="fail"))
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await c._sync_channels() == []

    @pytest.mark.asyncio
    async def test_fetch_channel_members_pagination_and_errors(self):
        c = _make_connector()
        mock_ds = MagicMock()
        mock_ds.conversations_members = AsyncMock(
            side_effect=[
                MagicMock(
                    success=True,
                    data={"members": ["U1"], "response_metadata": {"next_cursor": "c2"}},
                ),
                MagicMock(
                    success=True,
                    data={"members": ["U2"], "response_metadata": {"next_cursor": ""}},
                ),
            ]
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await c._fetch_channel_members("C1") == ["U1", "U2"]

        mock_ds.conversations_members = AsyncMock(
            return_value=MagicMock(success=False, error="missing_scope")
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._fetch_channel_members("C1")

        mock_ds.conversations_members = AsyncMock(
            return_value=MagicMock(success=False, error="ratelimited")
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            await c._fetch_channel_members("C1")

    def test_resolve_mpim_name_from_handle_match(self):
        c = _make_connector()
        c.authenticated_user_id = "U0"
        c.user_id_to_name_cache = {"U1": "Alice Smith", "U2": "Bob Jones"}
        name = c._resolve_mpim_name(
            {"id": "G1", "name": "mpdm-alicesmith--bobjones-1"},
        )
        assert "Alice Smith" in name
        assert "Bob Jones" in name

    def test_format_reactions_line(self):
        c = _make_connector()
        line = c._format_reactions_line([{"name": "thumbsup", "count": 3}])
        assert line and "thumbsup" in line
        assert c._format_reactions_line([]) is None

    def test_build_message_block_reactions_links_author_only(self):
        c = _make_connector()
        ctx = _ctx(c)
        ctx.user_id_to_name["U1"] = "Author"
        msg = {
            "ts": "100.0",
            "user": "U1",
            "text": "see https://ex.com",
            "reactions": [{"name": "eyes", "count": 1}],
        }
        block = c._build_message_block(msg, 0, 0, ctx)
        assert "Reactions:" in block.data
        assert "Links:" in block.data

        msg2 = {"ts": "101.0", "user": "U1", "text": ""}
        block2 = c._build_message_block(msg2, 0, 0, ctx)
        assert block2.data.startswith("**Author**:")

    def test_classify_url_github_and_gdrive(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector as S

        t, meta = S._classify_url("https://github.com/o/r/issues/42")
        assert t == "github" and meta["number"] == "42"
        t2, meta2 = S._classify_url("https://docs.google.com/document/d/abc123/edit")
        assert t2 == "gdrive" and meta2["file_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_process_message_batch(self):
        c = _make_connector()
        ctx = _ctx(c)
        msgs = [
            {"ts": "10.0", "user": "U1", "text": "a"},
            {"ts": "11.0", "user": "U1", "text": "b"},
        ]
        recs, deferred = await c._process_message_batch(msgs, ctx)
        assert recs
        assert isinstance(deferred, list)

    @pytest.mark.asyncio
    async def test_process_single_with_files_and_links(self):
        c = _make_connector()
        ctx = _ctx(c)
        msg = {
            "ts": "30.0",
            "user": "U1",
            "text": "file https://github.com/o/r/issues/1",
            "files": [{"id": "F1", "name": "a.txt", "mimetype": "text/plain", "created": 1}],
        }
        recs, deferred = await c._process_single(msg, ctx)
        assert len(recs) >= 2
        assert deferred == []

    @pytest.mark.asyncio
    async def test_process_burst_with_files(self):
        from app.connectors.sources.slack.individual.connector import ConversationalBurst

        c = _make_connector()
        ctx = _ctx(c)
        burst = ConversationalBurst(
            messages=[
                {
                    "ts": "40.0",
                    "user": "U1",
                    "text": "burst",
                    "files": [{"id": "F3", "name": "c.txt", "mimetype": "text/plain", "created": 1}],
                },
                {"ts": "41.0", "user": "U1", "text": "more"},
            ],
            start_ts=40.0,
            end_ts=41.0,
        )
        recs, deferred = await c._process_burst(burst, ctx)
        assert recs
        assert deferred == []

    @pytest.mark.asyncio
    async def test_process_thread_with_files_links_and_indexing_off(self):
        from app.config.constants.arangodb import ProgressStatus

        c = _make_connector()
        c.indexing_filters = FilterCollection(
            filters=[
                Filter(key="threads", value=False, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
                Filter(key="files", value=False, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
                Filter(key="links", value=False, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
            ]
        )
        ctx = _ctx(c)
        parent = {
            "ts": "50.0",
            "user": "U1",
            "text": "root https://ex.com",
            "reply_count": 1,
            "thread_ts": "50.0",
            "files": [{"id": "F4", "name": "d.txt", "mimetype": "text/plain", "created": 1}],
        }
        rg = MagicMock()
        rg.id = "trg"
        rg.external_group_id = "thread_C1_50.0"
        rg.group_type = "SLACK_THREAD"

        async def _replies(*_a, **_k):
            return [
                {
                    "ts": "50.1",
                    "user": "U2",
                    "text": "reply https://ex.com/2",
                },
            ]

        with (
            patch.object(c, "_create_thread_record_group", return_value=rg),
            patch.object(c, "_fetch_thread_replies_raw", new=AsyncMock(side_effect=_replies)),
            patch.object(c.data_entities_processor, "on_new_record_groups", new=AsyncMock()),
            patch.object(c.data_entities_processor, "on_new_records", new=AsyncMock()) as on_new,
            patch.object(c, "_enrich_link_records_linked_id", new=AsyncMock()),
        ):
            await c._process_thread(parent, ctx, "ch-rg")

        batch = on_new.await_args[0][0]
        for rec, _ in batch:
            if rec.record_type in (RecordType.MESSAGE, RecordType.FILE, RecordType.LINK):
                assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_scan_thread_growth_skips_non_actionable(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {"ts": "1.0", "subtype": "bot_message", "bot_id": "B1"},
                        {"ts": "2.0", "bot_id": "B1"},
                        {"ts": "3.0", "user": "U1", "thread_ts": "1.0", "text": "reply"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(SlackIndividualConnector, "_fresh_datasource", new_callable=AsyncMock, return_value=ds),
            patch.object(SlackIndividualConnector, "_handle_new_thread", new_callable=AsyncMock) as h,
        ):
            await c._scan_channel_thread_growth("C1", "0.0")
        h.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_message_changes_per_channel_checkpoint(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        c = _make_connector()
        c.channel_groups_cache = {"C1": "rg"}
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            return_value={"last_check_time": 1_700_000_000_000},
        )
        with (
            patch.object(SlackIndividualConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackIndividualConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(SlackIndividualConnector, "_check_channel_changes", new_callable=AsyncMock, return_value=2),
        ):
            await c._sync_message_changes()
        c.audit_log_sync_point.update_sync_point.assert_awaited()

        c2 = _make_connector()
        c2.channel_groups_cache = {"Cbad": "rg"}
        with (
            patch.object(SlackIndividualConnector, "_compute_sync_window_oldest", return_value="1.0"),
            patch.object(SlackIndividualConnector, "_refresh_user_caches", new_callable=AsyncMock),
            patch.object(
                SlackIndividualConnector,
                "_check_channel_changes",
                new_callable=AsyncMock,
                side_effect=RuntimeError("scan failed"),
            ),
        ):
            await c2._sync_message_changes()
        c2.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_refresh_user_caches_populates_all_fields(self):
        c = _make_connector()
        u = MagicMock()
        u.source_user_id = "U9"
        u.email = "u9@e.com"
        u.id = "int9"
        u.full_name = "Nine"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u])
        await c._refresh_user_caches()
        assert c.user_id_to_email_cache["U9"] == "u9@e.com"
        assert c.user_id_to_internal_id_cache["U9"] == "int9"
        assert c.user_id_to_name_cache["U9"] == "Nine"

    @pytest.mark.asyncio
    async def test_build_message_blocks_burst_with_file_children(self):
        c = _make_connector()
        mr = MagicMock(spec=MessageRecord)
        mr.id = "id"
        mr.external_record_id = "burst_C1_abc12345"
        mr.start_ts = "1.0"
        mr.end_ts = "2.0"
        mr.external_record_group_id = "C1"
        mr.record_group_id = "rg"

        child = MagicMock()
        child.id = "fid"
        child.external_record_id = "F1"
        child.record_name = "doc.pdf"

        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        tx.get_records_by_parent = AsyncMock(return_value=[child])
        c.data_store_provider.transaction = MagicMock(return_value=tx)

        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "messages": [
                        {
                            "ts": "1.5",
                            "user": "U1",
                            "text": "with file",
                            "files": [{"id": "F1"}],
                        },
                    ],
                },
            )
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            out = await c._build_message_blocks_for_streaming(mr)
        assert isinstance(out, bytes)

    @pytest.mark.asyncio
    async def test_check_updated_file_success(self):
        c = _make_connector()
        fr = MagicMock()
        fr.record_type = RecordType.FILE
        fr.external_record_id = "F1"
        fr.source_updated_at = None
        fr.source_created_at = 0
        fr.parent_external_record_id = "10.0"
        fr.external_record_group_id = "C1"
        fr.id = "fid"
        fr.version = 1
        fr.record_group_id = "rg"

        parent = MagicMock(spec=MessageRecord)
        parent.id = "mid"
        parent.external_record_id = "10.0"

        ds = MagicMock()
        ds.files_info = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "file": {
                        "id": "F1",
                        "name": "u.txt",
                        "created": 99999,
                        "mimetype": "text/plain",
                    },
                },
            )
        )
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=parent)
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            result = await c._check_updated_file(fr)
        assert result is not None

    @pytest.mark.asyncio
    async def test_init_without_team_info(self):
        from app.connectors.sources.slack.individual.connector import SlackIndividualConnector

        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "o"
        with _patch_base_connector_init():
            c = SlackIndividualConnector(logger, dep, MagicMock(), MagicMock(), "cid")

        mock_ds = MagicMock()
        mock_ds.team_info = AsyncMock(return_value=None)
        mock_ds.auth_test = AsyncMock(
            return_value=MagicMock(success=True, data={"user_id": "U9"}),
        )
        with (
            patch(
                "app.connectors.sources.slack.individual.connector.SlackClient.build_from_services",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch.object(
                SlackIndividualConnector,
                "_fresh_datasource",
                new=AsyncMock(return_value=mock_ds),
            ),
        ):
            assert await c.init() is True

    @pytest.mark.asyncio
    async def test_fresh_datasource_auth_access_token(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(
            return_value={
                "credentials": {"access_token": ""},
                "auth": {"accessToken": "xoxp-from-auth"},
            },
        )
        client = MagicMock()
        client.get_token = MagicMock(return_value="xoxp-from-auth")
        c.external_client.get_client = MagicMock(return_value=client)
        await c._fresh_datasource()

    @pytest.mark.asyncio
    async def test_sync_channel_messages_checkpoint_and_deferred_incremental(self):
        from app.connectors.sources.slack.individual.connector import DeferredThread

        c = _make_connector()
        c.indexing_filters = FilterCollection(filters=[])
        c.messages_sync_point.read_sync_point = AsyncMock(return_value=None)
        parent = {"ts": "99.0", "user": "U1", "text": "t", "reply_count": 1, "thread_ts": "99.0", "latest_reply": "99.1"}
        ctx_d = _ctx(c)
        deferred = DeferredThread(msg=parent, ctx=ctx_d, rg_id="rg")
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [parent], "response_metadata": {"next_cursor": ""}},
            )
        )
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            return_value={"last_reply_ts": "98.0"},
        )
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        erg = MagicMock()
        erg.id = "thread-rg"
        tx.get_record_group_by_external_id = AsyncMock(return_value=erg)
        c.data_store_provider.transaction = MagicMock(return_value=tx)
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([], [deferred]))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
            patch.object(type(c), "_process_thread_incremental", new=AsyncMock()) as inc,
        ):
            await c.sync_channel_messages("C1")
        inc.assert_awaited()
        c.messages_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_channel_deferred_thread_failure_logged(self):
        from app.connectors.sources.slack.individual.connector import DeferredThread

        c = _make_connector()
        c.indexing_filters = FilterCollection(filters=[])
        c.messages_sync_point.read_sync_point = AsyncMock(return_value=None)
        parent = {"ts": "99.0", "user": "U1", "text": "t", "reply_count": 1, "thread_ts": "99.0"}
        ctx_d = _ctx(c)
        deferred = DeferredThread(msg=parent, ctx=ctx_d, rg_id="rg")
        ds = MagicMock()
        ds.conversations_history = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [parent], "response_metadata": {"next_cursor": ""}},
            )
        )
        c.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        with (
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
            patch.object(type(c), "_channel_group_map", new=AsyncMock(return_value={"C1": "rg"})),
            patch.object(type(c), "_process_message_batch", new=AsyncMock(return_value=([], [deferred]))),
            patch.object(type(c), "_enrich_link_records_linked_id", new=AsyncMock()),
            patch.object(type(c), "_process_thread", new=AsyncMock(side_effect=RuntimeError("thread fail"))),
        ):
            await c.sync_channel_messages("C1")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_build_message_blocks_thread_burst_path(self):
        c = _make_connector()
        mr = MagicMock(spec=MessageRecord)
        mr.id = "id"
        mr.external_record_id = "thread_burst_xyz"
        mr.start_ts = "1.0"
        mr.end_ts = "2.0"
        mr.thread_id = "0.5"
        mr.external_record_group_id = "thread_C1_0.5"
        mr.record_group_id = "rg"
        ds = MagicMock()
        ds.conversations_replies = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"messages": [{"ts": "1.5", "user": "U1", "text": "tb"}]},
            )
        )
        with patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)):
            out = await c._build_message_blocks_for_streaming(mr)
        assert isinstance(out, bytes)

    @pytest.mark.asyncio
    async def test_get_filter_options_dm_label_and_warm_fallback(self):
        c = _make_connector()
        c.user_id_to_name_cache.clear()
        c.user_id_to_email_cache["Udm"] = "dm@e.com"
        ds = MagicMock()
        ds.conversations_list = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "channels": [
                        {"id": "D1", "is_im": True, "user": "Udm"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        )
        with (
            patch.object(type(c), "_refresh_user_caches", new=AsyncMock()),
            patch.object(type(c), "_warm_all_user_caches", new=AsyncMock()),
            patch.object(type(c), "_fresh_datasource", new=AsyncMock(return_value=ds)),
        ):
            resp = await c.get_filter_options("channel_ids")
        assert resp.success
        assert resp.options[0].label.startswith("DM:")
