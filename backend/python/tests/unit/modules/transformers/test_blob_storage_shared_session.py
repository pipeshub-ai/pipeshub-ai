"""Shared download session + cached config reads (blob_storage).

Guards the two properties that make the shared session safe: the session is
reused rather than rebuilt per record, and the per-request trace id is still
stamped fresh on every call (it must never be cached alongside the token).
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    DOWNLOAD_CONNECTION_LIMIT_DEFAULT,
    BlobStorage,
    close_shared_session,
    download_connection_limit,
    get_shared_session,
)
from app.utils.request_context import HEADER_REQUEST_ID, reset_context, set_context


def _make_blob_storage() -> BlobStorage:
    logger = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret-value-for-tests"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": "local"},
        ]
    )
    return BlobStorage(logger=logger, config_service=config_service, graph_provider=MagicMock())


@pytest.fixture(autouse=True)
def _clear_sessions():
    bs_mod._shared_sessions.clear()
    yield
    bs_mod._shared_sessions.clear()


@pytest.fixture(autouse=True)
def _clear_connection_limit_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PIPESHUB_STORAGE_CONNECTION_LIMIT", raising=False)


class TestConnectionLimit:
    """An unbounded pool overran the storage API's listen backlog under load."""

    @pytest.mark.asyncio
    async def test_pool_is_bounded_by_default(self) -> None:
        fake = MagicMock()
        fake.closed = False
        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=fake), patch.object(
            bs_mod.aiohttp, "TCPConnector"
        ) as connector:
            get_shared_session()

        assert connector.call_args.kwargs["limit"] == DOWNLOAD_CONNECTION_LIMIT_DEFAULT
        assert DOWNLOAD_CONNECTION_LIMIT_DEFAULT > 0, "0 means unbounded"

    def test_env_overrides_the_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_STORAGE_CONNECTION_LIMIT", "250")
        assert download_connection_limit() == 250

    def test_env_can_restore_unbounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_STORAGE_CONNECTION_LIMIT", "0")
        assert download_connection_limit() == 0

    @pytest.mark.parametrize("bad", ["", "  ", "abc", "-5"])
    def test_unusable_values_fall_back_to_the_default(
        self, bad: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PIPESHUB_STORAGE_CONNECTION_LIMIT", bad)
        assert download_connection_limit() == DOWNLOAD_CONNECTION_LIMIT_DEFAULT


class TestSharedSession:
    @pytest.mark.asyncio
    async def test_same_session_returned_within_a_loop(self) -> None:
        fake = MagicMock()
        fake.closed = False
        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=fake) as ctor:
            first = get_shared_session()
            second = get_shared_session()

        assert first is second
        assert ctor.call_count == 1, "session must be built once per loop, not per call"

    @pytest.mark.asyncio
    async def test_closed_session_is_replaced(self) -> None:
        stale = MagicMock()
        stale.closed = True
        fresh = MagicMock()
        fresh.closed = False

        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=stale):
            get_shared_session()
        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=fresh):
            replacement = get_shared_session()

        assert replacement is fresh

    @pytest.mark.asyncio
    async def test_close_shared_session_closes_and_forgets(self) -> None:
        fake = MagicMock()
        fake.closed = False
        fake.close = AsyncMock()

        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=fake):
            get_shared_session()

        await close_shared_session()

        fake.close.assert_awaited_once()
        assert not bs_mod._shared_sessions

    @pytest.mark.asyncio
    async def test_close_is_safe_when_nothing_open(self) -> None:
        await close_shared_session()  # must not raise

    @pytest.mark.asyncio
    async def test_download_does_not_close_the_shared_session(self) -> None:
        """The old code closed its session per record; the shared one must survive."""
        blob = _make_blob_storage()
        blob.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-1", "fileSizeBytes": 10}
        )

        record = {"id": "rec-1"}
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"record": record})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=resp)
        session.close = AsyncMock()

        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=session):
            out = await blob.get_record_from_storage("vr-1", "org-1")

        assert out == record
        session.close.assert_not_called()
        assert bs_mod._shared_sessions, "session should stay pooled for the next record"


class TestAuthAndConfigCaching:
    @pytest.mark.asyncio
    async def test_config_reads_use_the_invalidated_cache(self) -> None:
        blob = _make_blob_storage()
        await blob._get_auth_and_config("org-1")

        assert blob.config_service.get_config.await_count == 3
        for call in blob.config_service.get_config.await_args_list:
            assert call.kwargs.get("use_cache") is True, (
                "each read is otherwise an etcd round trip per record download"
            )

    @pytest.mark.asyncio
    async def test_request_id_is_stamped_per_call_not_cached(self) -> None:
        """The trace id comes from a ContextVar; caching headers would leak it."""
        blob = _make_blob_storage()

        token = set_context("request-aaa")
        try:
            headers_a, _, _ = await blob._get_auth_and_config("org-1")
        finally:
            reset_context(token)

        blob.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret-value-for-tests"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        token = set_context("request-bbb")
        try:
            headers_b, _, _ = await blob._get_auth_and_config("org-1")
        finally:
            reset_context(token)

        assert headers_a[HEADER_REQUEST_ID] == "request-aaa"
        assert headers_b[HEADER_REQUEST_ID] == "request-bbb"
        # Same org ⇒ same scoped token, but distinct header dicts.
        assert headers_a["Authorization"] == headers_b["Authorization"]
        assert headers_a is not headers_b


class TestLookupPassthrough:
    @pytest.mark.asyncio
    async def test_pre_resolved_lookup_skips_the_per_record_query(self) -> None:
        blob = _make_blob_storage()
        blob.get_document_id_by_virtual_record_id = AsyncMock()

        record = {"id": "rec-1"}
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"record": record})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=resp)

        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=session):
            out = await blob.get_record_from_storage(
                "vr-1", "org-1", lookup_result={"record_doc_id": "doc-1", "fileSizeBytes": 5}
            )

        assert out == record
        blob.get_document_id_by_virtual_record_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_omitted_lookup_still_queries(self) -> None:
        blob = _make_blob_storage()
        blob.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-1", "fileSizeBytes": 5}
        )

        record = {"id": "rec-1"}
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"record": record})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=resp)

        with patch.object(bs_mod.aiohttp, "ClientSession", return_value=session):
            out = await blob.get_record_from_storage("vr-1", "org-1")

        assert out == record
        blob.get_document_id_by_virtual_record_id.assert_awaited_once_with("vr-1")

    @pytest.mark.asyncio
    async def test_empty_pre_resolved_lookup_returns_none(self) -> None:
        blob = _make_blob_storage()
        blob.get_document_id_by_virtual_record_id = AsyncMock()

        out = await blob.get_record_from_storage("vr-1", "org-1", lookup_result={})

        assert out is None
        # An empty dict is a resolved "not found", so it must still short-circuit
        # rather than fall back to the per-record query.
        blob.get_document_id_by_virtual_record_id.assert_not_called()


class TestEnvelopeDecoding:
    """Records under the compression threshold are plain JSON, so the envelope
    parse IS the record decode -- it moved from msgpack (msgspec) to JSON."""

    def test_decodes_bytes_without_a_utf8_round_trip(self) -> None:
        raw = b'{"isCompressed": false, "record": {"record_name": "n"}, "virtualRecordId": "v"}'
        assert bs_mod._decode_json(raw) == {
            "isCompressed": False,
            "record": {"record_name": "n"},
            "virtualRecordId": "v",
        }

    def test_matches_stdlib_json_exactly(self) -> None:
        import json as _json

        raw = _json.dumps(
            {"isCompressed": False, "record": {"a": [1, 2.5, None, True], "b": "ünïcode"}}
        ).encode()
        assert bs_mod._decode_json(raw) == _json.loads(raw.decode("utf-8"))

    def test_falls_back_to_stdlib_when_msgspec_rejects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A record msgspec cannot parse is still worth trying to read."""
        def _boom(_raw) -> None:
            raise ValueError("nope")

        monkeypatch.setattr(bs_mod.msgspec.json, "decode", _boom)
        assert bs_mod._decode_json(b'{"record": {"x": 1}}') == {"record": {"x": 1}}


class TestKeepAliveTimeout:
    """The shared session must drop idle connections before Node does.

    Node never sets `server.keepAliveTimeout`, so its 5s default applies, while
    aiohttp's connector default is 15s. Reusing a connection in that gap fails
    with "Server disconnected" -- 51 record fetches and one failed tool call in
    one day of logs, and only common once the session became process-wide and
    connections lived long enough to go idle.
    """

    @pytest.mark.asyncio
    async def test_connector_expires_before_the_gateway_does(self) -> None:
        from app.modules.transformers.blob_storage import (
            NODE_KEEPALIVE_MARGIN_SECONDS,
            close_shared_redis,
            get_shared_session,
        )

        session = get_shared_session()
        try:
            assert session.connector._keepalive_timeout == NODE_KEEPALIVE_MARGIN_SECONDS
            assert NODE_KEEPALIVE_MARGIN_SECONDS < 5, (
                "must be under Node's 5s default or the race returns"
            )
        finally:
            await session.close()
            with contextlib.suppress(Exception):
                await close_shared_redis()
