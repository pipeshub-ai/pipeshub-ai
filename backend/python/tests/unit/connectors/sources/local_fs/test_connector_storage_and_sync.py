"""Tests for the storage-backend interactions and sync lifecycle of
:mod:`app.connectors.sources.local_fs.connector`.

Covers:

* the storage-service HTTP helpers (``_upload_storage_file``,
  ``_delete_storage_document``, ``_stream_storage_record``, plus the
  cached ``_storage_base_url`` / ``_storage_token`` lookups);
* the sync lifecycle (``init``, ``run_sync``, ``apply_file_event_batch``
  and ``apply_uploaded_file_event_batch``);
* the local-file fallback path of ``stream_record``.

Same import shims as ``test_connector.py`` — required before importing the
connector module so the full DI graph is not loaded.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Import shims (must run before connector import) ---
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ConnectorAppContainer:
        pass

    _stub_container.ConnectorAppContainer = _ConnectorAppContainer
    sys.modules["app.containers.connector"] = _stub_container

if "redis" not in sys.modules:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")

    class _ExponentialBackoff:
        pass

    _redis_backoff.ExponentialBackoff = _ExponentialBackoff
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")

    class _Retry:
        pass

    _redis_retry.Retry = _Retry
    sys.modules["redis.asyncio.retry"] = _redis_retry

    _redis_asyncio = types.ModuleType("redis.asyncio")
    _redis_asyncio.Redis = type("Redis", (), {})
    sys.modules["redis.asyncio"] = _redis_asyncio

    _redis = types.ModuleType("redis")
    _redis.asyncio = _redis_asyncio
    sys.modules["redis"] = _redis

if "etcd3" not in sys.modules:
    _etcd3 = types.ModuleType("etcd3")
    _etcd3.client = type("client", (), {})
    sys.modules["etcd3"] = _etcd3
# --- end shims ---

import aiohttp  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.config.constants.arangodb import Connectors  # noqa: E402
from app.config.constants.http_status_code import HttpStatusCode  # noqa: E402
from app.connectors.core.registry.filters import FilterCollection  # noqa: E402
from app.connectors.sources.local_fs.connector import (  # noqa: E402
    LOCAL_FS_STORAGE_PATH_PREFIX,
    LocalFsConnector,
    SYNC_ROOT_PATH_KEY,
)
from app.connectors.sources.local_fs.models import LocalFsFileEvent  # noqa: E402
from app.models.entities import (  # noqa: E402
    FileRecord,
    OriginTypes,
    RecordGroupType,
    RecordType,
    User,
)


# --------------------------------------------------------------------------- #
# Helpers / fakes                                                             #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Minimal aiohttp response that supports `async with` and `.text()`."""

    def __init__(self, status: int, text: str = "", *, raise_on: Optional[Exception] = None) -> None:
        self.status = status
        self._text = text
        self._raise = raise_on

    async def text(self) -> str:
        if self._raise is not None:
            raise self._raise
        return self._text

    async def __aenter__(self) -> "_FakeResponse":
        if self._raise is not None:
            # aiohttp surfaces both connection errors (ClientError) and
            # asyncio.TimeoutError out of the context-manager entry, so we mimic
            # that here regardless of the exception type the caller queued.
            raise self._raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        return None


class _FakeSession:
    """Fake aiohttp.ClientSession.

    Each call to .post()/.get()/.delete() pops the next queued response so a
    single batch test can chain multiple requests. Records the method/url/headers
    of every call for assertions.
    """

    def __init__(self, responses):
        # responses: list of (method, _FakeResponse) — popped in order.
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _take(self, method: str, url: str, **kwargs) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"Unexpected {method} {url}: no response queued")
        expected_method, response = self._responses.pop(0)
        assert expected_method == method, (
            f"Expected next call to be {expected_method}, got {method}"
        )
        return response

    def post(self, url, *, data=None, headers=None):
        return self._take("post", url, data=data, headers=headers)

    def get(self, url, *, headers=None):
        return self._take("get", url, headers=headers)

    def delete(self, url, *, headers=None):
        return self._take("delete", url, headers=headers)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        return None


def _patch_session(monkeypatch, session: _FakeSession) -> None:
    """Make `aiohttp.ClientSession(...)` return our fake session."""
    monkeypatch.setattr(
        "app.connectors.sources.local_fs.connector.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )


@pytest.fixture
def folder_connector() -> LocalFsConnector:
    logger = MagicMock()
    proc = MagicMock()
    proc.org_id = "org-1"
    return LocalFsConnector(
        logger,
        proc,
        MagicMock(),
        MagicMock(),
        "connector-instance-1",
        "personal",
        "test-user",
    )


# --------------------------------------------------------------------------- #
# init() and test_connection_and_access()                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestInit:
    async def test_init_with_valid_config(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {
                    SYNC_ROOT_PATH_KEY: str(tmp_path),
                    "include_subfolders": "false",
                    "batchSize": "7",
                }
            }
        )
        ok = await folder_connector.init()
        assert ok is True
        assert folder_connector.sync_root_path == str(tmp_path)
        assert folder_connector.include_subfolders is False
        assert folder_connector.batch_size == 7
        folder_connector.logger.info.assert_called()

    async def test_init_with_invalid_path_warns(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {SYNC_ROOT_PATH_KEY: "/does/not/exist/local-fs-test"}
            }
        )
        ok = await folder_connector.init()
        assert ok is True
        folder_connector.logger.warning.assert_called()

    async def test_init_with_empty_path_logs_setup_hint(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        ok = await folder_connector.init()
        assert ok is True
        # Logged the "complete setup in the app" info line.
        folder_connector.logger.info.assert_called()

    async def test_init_swallows_exceptions_returns_false(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd down")
        )
        ok = await folder_connector.init()
        assert ok is False
        folder_connector.logger.error.assert_called()


# --------------------------------------------------------------------------- #
# _iter_file_paths                                                            #
# --------------------------------------------------------------------------- #


class TestIterFilePaths:
    def test_recurses_when_include_subfolders_true(self, folder_connector, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b", encoding="utf-8")

        folder_connector.include_subfolders = True
        out = folder_connector._iter_file_paths(tmp_path)

        names = sorted(p.name for p in out)
        assert names == ["a.txt", "b.txt"]

    def test_top_level_only_when_include_subfolders_false(
        self, folder_connector, tmp_path
    ):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b", encoding="utf-8")

        folder_connector.include_subfolders = False
        out = folder_connector._iter_file_paths(tmp_path)

        assert [p.name for p in out] == ["a.txt"]


# --------------------------------------------------------------------------- #
# _storage_base_url and _storage_token                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStorageBaseUrl:
    async def test_uses_endpoint_from_dict_config(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage.local:9000/"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://storage.local:9000"

    async def test_parses_json_string_config(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value=json.dumps({"storage": {"endpoint": "http://s.local"}})
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://s.local"

    async def test_falls_back_to_default_on_bad_json(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value="not-valid-json{{"
        )
        url = await folder_connector._storage_base_url()
        # Default endpoint is whatever DefaultEndpoints.STORAGE_ENDPOINT.value is;
        # the only contract is that we got SOMETHING and it's not the bad string.
        assert url and "{" not in url

    async def test_falls_back_to_default_on_none(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(return_value=None)
        url = await folder_connector._storage_base_url()
        assert url

    async def test_uses_cache_when_set(self, folder_connector):
        folder_connector._batch_storage_url_cache = "http://cached"
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://other"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://cached"
        folder_connector.config_service.get_config.assert_not_awaited()

    async def test_populates_cache_when_attribute_pre_seeded(self, folder_connector):
        # Caller pre-seeds an empty cache to opt in (matches batch context manager).
        folder_connector._batch_storage_url_cache = None
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://x"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://x"
        assert folder_connector._batch_storage_url_cache == "http://x"


@pytest.mark.asyncio
class TestStorageToken:
    async def test_calls_generate_jwt_with_org_and_scope(self, folder_connector):
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "tok"
            gen.assert_awaited_once()
            args, _ = gen.call_args
            assert args[1]["orgId"] == "org-1"
            assert args[1]["scopes"] == ["storage:token"]

    async def test_uses_cache_when_set(self, folder_connector):
        folder_connector._batch_storage_token_cache = "cached-tok"
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="other-tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "cached-tok"
            gen.assert_not_awaited()

    async def test_populates_cache_when_attribute_pre_seeded(self, folder_connector):
        folder_connector._batch_storage_token_cache = None
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="fresh"),
        ):
            tok = await folder_connector._storage_token()
            assert tok == "fresh"
            assert folder_connector._batch_storage_token_cache == "fresh"


# --------------------------------------------------------------------------- #
# _upload_storage_file + _do_upload                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestUploadStorageFile:
    async def test_new_upload_returns_extracted_id(self, folder_connector, monkeypatch):
        # Storage returns {"_id": "doc-new"}
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"_id": "doc-new"})))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a/b.txt",
            content=b"hello",
            mime_type="text/plain",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "doc-new"
        assert session.calls[0]["url"].endswith("/api/v1/document/internal/upload")
        # The Authorization header is forwarded.
        assert (
            session.calls[0]["headers"]["Authorization"] == "Bearer tok"
        )

    async def test_uploadNextVersion_returns_existing_id_unchanged(
        self, folder_connector, monkeypatch
    ):
        session = _FakeSession([("post", _FakeResponse(200, "{}"))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a/b.txt",
            content=b"hi",
            mime_type="text/plain",
            existing_document_id="doc-existing",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "doc-existing"
        assert "uploadNextVersion" in session.calls[0]["url"]

    async def test_resolves_url_token_org_when_omitted(
        self, folder_connector, monkeypatch
    ):
        folder_connector._storage_base_url = AsyncMock(return_value="http://lazy")
        folder_connector._storage_token = AsyncMock(return_value="lazy-tok")
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"id": "id1"})))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="x.txt", content=b"d", mime_type=None,
        )
        assert doc_id == "id1"
        folder_connector._storage_base_url.assert_awaited_once()
        folder_connector._storage_token.assert_awaited_once()

    async def test_non_2xx_raises_bad_gateway(self, folder_connector, monkeypatch):
        session = _FakeSession([("post", _FakeResponse(503, '{"err":"down"}'))])
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_timeout_raises_gateway_timeout(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("post", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.GATEWAY_TIMEOUT.value

    async def test_client_error_raises_bad_gateway(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("post", _FakeResponse(0, "", raise_on=aiohttp.ClientError("dns")))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_caller_session_reused(self, folder_connector):
        """When a caller hands in a session, no new ClientSession is constructed."""
        # Don't patch ClientSession — if our code mistakenly opens one, the real
        # constructor would run. Instead, we hand a fake session in directly.
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"_id": "d"})))])
        doc_id = await folder_connector._upload_storage_file(
            rel_path="a.txt",
            content=b"x",
            mime_type=None,
            org_id="o",
            storage_url="http://x",
            storage_token="t",
            session=session,  # type: ignore[arg-type]
        )
        assert doc_id == "d"
        assert len(session.calls) == 1


# --------------------------------------------------------------------------- #
# _delete_storage_document + _do_delete_blob                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestDeleteStorageDocument:
    async def test_noop_when_document_id_missing(self, folder_connector):
        # Must not even resolve URL/token.
        folder_connector._storage_base_url = AsyncMock()
        folder_connector._storage_token = AsyncMock()
        await folder_connector._delete_storage_document(None)
        await folder_connector._delete_storage_document("")
        folder_connector._storage_base_url.assert_not_awaited()

    async def test_success_calls_delete(self, folder_connector, monkeypatch):
        session = _FakeSession([("delete", _FakeResponse(204, ""))])
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-1")
        assert session.calls[0]["url"].endswith("/document/internal/doc-1/")

    async def test_4xx_swallowed_with_warning(self, folder_connector, monkeypatch):
        session = _FakeSession([("delete", _FakeResponse(404, "missing"))])
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        # Best-effort: must not raise.
        await folder_connector._delete_storage_document("doc-2")
        folder_connector.logger.warning.assert_called()

    async def test_timeout_logged_not_raised(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("delete", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-3")
        folder_connector.logger.warning.assert_called()

    async def test_client_error_logged_not_raised(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("delete", _FakeResponse(0, "", raise_on=aiohttp.ClientError("rst")))]
        )
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-4")
        folder_connector.logger.warning.assert_called()

    async def test_caller_session_reused(self, folder_connector):
        session = _FakeSession([("delete", _FakeResponse(204, ""))])
        await folder_connector._delete_storage_document(
            "doc-5",
            storage_url="http://x",
            storage_token="t",
            session=session,  # type: ignore[arg-type]
        )
        assert session.calls[0]["url"].endswith("/document/internal/doc-5/")


# --------------------------------------------------------------------------- #
# _stream_storage_record                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamStorageRecord:
    def _record(self) -> FileRecord:
        return FileRecord(
            record_name="r.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-1",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )

    async def test_returns_decoded_buffer_on_success(
        self, folder_connector, monkeypatch
    ):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        body = json.dumps({"type": "Buffer", "data": [104, 105]})
        session = _FakeSession([("get", _FakeResponse(200, body))])
        _patch_session(monkeypatch, session)

        resp = await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert resp.body == b"hi"
        assert resp.media_type == "application/octet-stream"

    async def test_non_2xx_raises_bad_gateway(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession([("get", _FakeResponse(500, "boom"))])
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_timeout_raises_gateway_timeout(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession(
            [("get", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.GATEWAY_TIMEOUT.value

    async def test_client_error_raises_bad_gateway(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession(
            [("get", _FakeResponse(0, "", raise_on=aiohttp.ClientError("dns")))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_non_json_body_falls_back_to_raw_bytes(
        self, folder_connector, monkeypatch
    ):
        """A 200 response whose body isn't JSON must be returned as raw bytes,
        not raise a JSONDecodeError."""
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession([("get", _FakeResponse(200, "raw-bytes-body"))])
        _patch_session(monkeypatch, session)
        resp = await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert resp.body == b"raw-bytes-body"


# --------------------------------------------------------------------------- #
# run_sync                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestRunSync:
    async def test_empty_root_warns_and_exits(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        await folder_connector.run_sync()
        folder_connector.logger.warning.assert_called()

    async def test_unreadable_path_logs_and_defers(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: "/nope/local-fs-nowhere"}}
        )
        await folder_connector.run_sync()
        folder_connector.logger.info.assert_called()

    async def test_no_owner_returns_early(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        folder_connector._resolve_owner_user = AsyncMock(return_value=None)
        # Must NOT call on_new_record_groups when there's no owner.
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        await folder_connector.run_sync()
        folder_connector.data_entities_processor.on_new_app_users.assert_not_awaited()
        folder_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    async def test_full_sync_emits_records_and_handles_skips(
        self, folder_connector, tmp_path
    ):
        # Three real files + one symlink that must be skipped.
        f1 = tmp_path / "a.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("b", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        f3 = sub / "c.txt"
        f3.write_text("c", encoding="utf-8")
        sym = tmp_path / "sym.txt"
        try:
            sym.symlink_to(f1)
        except OSError:
            sym = None  # symlink unavailable in this env

        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path), "batchSize": "2"}}
        )
        owner = User(email="o@x.com", id="owner-1", org_id="org-1")
        folder_connector._resolve_owner_user = AsyncMock(return_value=owner)
        folder_connector._reset_existing_records = AsyncMock(return_value=0)
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        folder_connector.data_entities_processor.on_new_records = AsyncMock()

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            await folder_connector.run_sync()

        # batch_size=2 + 3 real files ⇒ at least one mid-iter flush + one final
        # flush ⇒ on_new_records called >= 2 times.
        assert folder_connector.data_entities_processor.on_new_records.await_count >= 2
        folder_connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        folder_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        # Owner must be cleared from instance state.
        assert folder_connector._owner_user_for_permissions is None

    async def test_exception_propagates_and_clears_owner(
        self, folder_connector, tmp_path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        folder_connector._resolve_owner_user = AsyncMock(
            side_effect=RuntimeError("graph down")
        )
        with pytest.raises(RuntimeError):
            await folder_connector.run_sync()
        assert folder_connector._owner_user_for_permissions is None
        folder_connector.logger.error.assert_called()

    async def test_run_incremental_sync_delegates(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        await folder_connector.run_incremental_sync()
        folder_connector.logger.warning.assert_called()

    async def test_per_file_exception_is_logged_and_iteration_continues(
        self, folder_connector, tmp_path
    ):
        """If processing one file blows up, the loop must log + continue, not abort."""
        f1 = tmp_path / "boom.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "ok.txt"
        f2.write_text("b", encoding="utf-8")

        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="o@x.com", id="owner-1", org_id="org-1")
        folder_connector._resolve_owner_user = AsyncMock(return_value=owner)
        folder_connector._reset_existing_records = AsyncMock(return_value=0)
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        folder_connector.data_entities_processor.on_new_records = AsyncMock()

        # Make _extension_allowed raise on the first file only.
        original = folder_connector._extension_allowed
        call_count = {"n": 0}

        def _flaky(path, filters):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated stat error")
            return original(path, filters)

        folder_connector._extension_allowed = _flaky  # type: ignore[assignment]

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            await folder_connector.run_sync()

        # The flaky file was skipped but the second one got through.
        folder_connector.logger.warning.assert_called()
        folder_connector.data_entities_processor.on_new_records.assert_awaited()


# --------------------------------------------------------------------------- #
# Misc small helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestMisc:
    async def test_bulk_get_records_dedupes_and_skips_empty(self, folder_connector):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        seen: list[str] = []

        async def _lookup(connector_id, ext_id):
            seen.append(ext_id)
            return MagicMock(external_record_id=ext_id) if ext_id == "x" else None

        txn.get_record_by_external_id = AsyncMock(side_effect=_lookup)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        result = await folder_connector._bulk_get_records_by_external_ids(
            ["x", "x", "y", "", None]  # type: ignore[list-item]
        )
        # "x" deduped, "" / None skipped, "y" preserved → at most two lookups.
        assert set(seen) <= {"x", "y"}
        assert "x" in result
        assert "y" not in result

    async def test_bulk_get_records_empty_input_short_circuits(
        self, folder_connector
    ):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)
        out = await folder_connector._bulk_get_records_by_external_ids([])
        assert out == {}
        # Transaction must NOT have been opened.
        folder_connector.data_store_provider.transaction.assert_not_called()

    async def test_get_record_by_external_id_delegates(self, folder_connector):
        record = MagicMock(external_record_id="ext-1")
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.get_record_by_external_id = AsyncMock(return_value=record)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._get_record_by_external_id("ext-1")
        assert out is record

    async def test_storage_document_id_for_external_id_resolves_when_record_has_storage_path(
        self, folder_connector
    ):
        record = MagicMock(path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-77")
        folder_connector._get_record_by_external_id = AsyncMock(return_value=record)
        out = await folder_connector._storage_document_id_for_external_id("e-1")
        assert out == "doc-77"

    async def test_storage_document_id_for_external_id_none_when_no_record(
        self, folder_connector
    ):
        folder_connector._get_record_by_external_id = AsyncMock(return_value=None)
        out = await folder_connector._storage_document_id_for_external_id("e-1")
        assert out is None

    async def test_test_connection_and_access_with_valid_path(
        self, folder_connector, tmp_path
    ):
        folder_connector.sync_root_path = str(tmp_path)
        assert await folder_connector.test_connection_and_access() is True
        # No warning when path is fine.
        folder_connector.logger.warning.assert_not_called()


# --------------------------------------------------------------------------- #
# create_connector classmethod                                                #
# --------------------------------------------------------------------------- #


class TestEventDateFilters:
    """Cover the static event-timestamp variant of the date filter."""

    def _filter(self, key, start, end):
        from app.connectors.core.registry.filters import (
            DatetimeOperator,
            Filter,
            FilterType,
        )

        return Filter(
            key=key,
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_BETWEEN,
            value={"start": start, "end": end},
        )

    def test_no_filters_passes(self):
        ev = LocalFsFileEvent(
            type="CREATED", path="x", timestamp=1000, isDirectory=False,
        )
        assert (
            LocalFsConnector._local_fs_event_passes_date_filters(
                ev, FilterCollection(filters=[])
            )
            is True
        )

    def test_modified_in_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=3000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._local_fs_event_passes_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )

    def test_modified_before_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=1000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._local_fs_event_passes_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_modified_after_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=5000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._local_fs_event_passes_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_created_filter_uses_event_timestamp(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="CREATED", path="x", timestamp=1000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.CREATED.value, 5000, 6000)
        assert (
            LocalFsConnector._local_fs_event_passes_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )


@pytest.mark.asyncio
async def test_create_connector_builds_instance():
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = MagicMock()
    proc = MagicMock()
    proc.org_id = "org-classmethod"

    with patch(
        "app.connectors.sources.local_fs.connector."
        "create_initialized_data_source_entities_processor",
        new=AsyncMock(return_value=proc),
    ) as creator:
        conn = await LocalFsConnector.create_connector(
            logger,
            data_store_provider,
            config_service,
            "conn-id-x",
            "personal",
            "kushagra",
        )
        creator.assert_awaited_once()
        assert isinstance(conn, LocalFsConnector)
        assert conn.connector_id == "conn-id-x"
        assert conn.data_entities_processor is proc


# --------------------------------------------------------------------------- #
# apply_file_event_batch — DELETED, RENAMED, unsupported event branches       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyFileEventBatchBranches:
    async def _setup(self, folder_connector, tmp_path: Path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector._delete_external_ids = AsyncMock()
        return owner

    async def test_deleted_event_buffers_and_flushes(
        self, folder_connector, tmp_path
    ):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="DELETED", path="gone.txt", timestamp=1, isDirectory=False,
        )
        stats = await folder_connector.apply_file_event_batch([ev])
        assert stats.deleted == 1
        folder_connector._delete_external_ids.assert_awaited()

    async def test_unsupported_event_type_raises(self, folder_connector, tmp_path):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="WAT", path="a.txt", timestamp=1, isDirectory=False,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([ev])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_empty_path_raises(self, folder_connector, tmp_path):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="CREATED", path="   ", timestamp=1, isDirectory=False,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([ev])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_rename_with_vanished_new_path_downgrades_to_delete(
        self, folder_connector, tmp_path
    ):
        await self._setup(folder_connector, tmp_path)
        # Old file exists so the resolver doesn't trip.
        old = tmp_path / "old.txt"
        old.write_text("x", encoding="utf-8")
        # New file deliberately absent — _prepare_upsert_record returns None.
        ev = LocalFsFileEvent(
            type="RENAMED",
            path="missing_new.txt",
            oldPath="old.txt",
            timestamp=1,
            isDirectory=False,
        )
        # _prepare_upsert_record returning None drives the downgrade branch.
        folder_connector._prepare_upsert_record = MagicMock(return_value=None)
        stats = await folder_connector.apply_file_event_batch([ev])
        # The downgrade enqueues a delete-only for the OLD path.
        assert stats.deleted == 1
        folder_connector._delete_external_ids.assert_awaited()

    async def test_rename_happy_path_upserts_new_then_deletes_old(
        self, folder_connector, tmp_path
    ):
        owner = await self._setup(folder_connector, tmp_path)
        old = tmp_path / "old.txt"
        old.write_text("x", encoding="utf-8")
        new = tmp_path / "new.txt"
        new.write_text("y", encoding="utf-8")
        ev = LocalFsFileEvent(
            type="RENAMED",
            path="new.txt",
            oldPath="old.txt",
            timestamp=1,
            isDirectory=False,
        )
        # Return a sentinel record so the rename takes the "new ext_id != old"
        # path and queues the old path for delete-after-upsert.
        sentinel = MagicMock()
        folder_connector._prepare_upsert_record = MagicMock(return_value=sentinel)
        stats = await folder_connector.apply_file_event_batch([ev])
        assert stats.processed == 1
        assert stats.deleted == 1  # old ext_id deleted via delete_after_upsert
        folder_connector.data_entities_processor.on_new_records.assert_awaited()
        folder_connector._delete_external_ids.assert_awaited()


# --------------------------------------------------------------------------- #
# stream_record — local-file fallback path                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamRecordLocalFile:
    async def test_streams_local_file_chunks(self, folder_connector, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"local-bytes")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="data.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),  # NOT a storage:// path → goes through local-file branch
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        chunks: list[bytes] = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        assert b"".join(chunks) == b"local-bytes"

    async def test_returns_404_when_local_file_missing(
        self, folder_connector, tmp_path
    ):
        ghost = tmp_path / "gone.bin"  # never created
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="gone.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(ghost),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_returns_400_when_root_unconfigured(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        rec = FileRecord(
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="/anywhere/x",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_returns_400_when_root_invalid(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {SYNC_ROOT_PATH_KEY: "/no/such/dir/local-fs-x"}
            }
        )
        rec = FileRecord(
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="/no/such/dir/local-fs-x/x",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value


# --------------------------------------------------------------------------- #
# _resolve_owner_user — missing app doc / missing createdBy / bad user shape  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestResolveOwnerUser:
    def _txn(self, *, app_doc, user_raw=None):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.txn = MagicMock()
        txn.graph_provider.get_document = AsyncMock(return_value=app_doc)
        txn.get_user_by_user_id = AsyncMock(return_value=user_raw)
        return txn

    async def test_returns_none_when_app_doc_missing(self, folder_connector):
        txn = self._txn(app_doc=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()

    async def test_returns_none_when_app_doc_has_no_created_by(self, folder_connector):
        txn = self._txn(app_doc={"_id": "apps/x"})
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()

    async def test_logs_error_when_user_lookup_returns_none(self, folder_connector):
        txn = self._txn(app_doc={"createdBy": "u-1"}, user_raw=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()

    async def test_coerce_user_returns_none_for_unexpected_shape(
        self, folder_connector
    ):
        # raw is neither User nor dict nor None — hits the trailing `return None`.
        out = folder_connector._coerce_user(object())
        assert out is None


# --------------------------------------------------------------------------- #
# _delete_storage_document_for_external_id                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestDeleteStorageDocumentForExternalId:
    async def test_delegates_to_delete_when_record_has_storage_path(
        self, folder_connector
    ):
        record = MagicMock(path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-xyz")
        folder_connector._get_record_by_external_id = AsyncMock(return_value=record)
        folder_connector._delete_storage_document = AsyncMock()

        await folder_connector._delete_storage_document_for_external_id("ext-1")

        folder_connector._delete_storage_document.assert_awaited_once_with("doc-xyz")

    async def test_delegates_with_none_when_record_missing(self, folder_connector):
        folder_connector._get_record_by_external_id = AsyncMock(return_value=None)
        folder_connector._delete_storage_document = AsyncMock()

        await folder_connector._delete_storage_document_for_external_id("ext-1")

        # _delete_storage_document is best-effort and short-circuits on None
        # internally, but it MUST still be invoked so that the helper's
        # delegation contract is exercised.
        folder_connector._delete_storage_document.assert_awaited_once_with(None)


# --------------------------------------------------------------------------- #
# _reset_existing_records — delete_storage_documents=True branch              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestResetExistingRecordsStorageGc:
    async def test_collects_and_deletes_storage_doc_ids(self, folder_connector):
        rec1 = MagicMock(
            external_record_id="e-1",
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-a",
        )
        rec2 = MagicMock(
            external_record_id="e-2",
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-b",
        )
        # Record without a storage-prefixed path → must NOT be GC'd.
        rec3 = MagicMock(
            external_record_id="e-3",
            path="/some/local/path",
        )

        # Two rounds: first returns the three records, second returns [] so
        # the outer while-loop exits after the storage GC fires.
        rounds = [[rec1, rec2, rec3], []]
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.get_records_by_status = AsyncMock(side_effect=rounds)
        txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)
        folder_connector._delete_storage_document = AsyncMock()

        n = await folder_connector._reset_existing_records(
            "owner-1", delete_storage_documents=True
        )

        assert n == 3
        # The two storage-prefixed records were forwarded to GC; the local one wasn't.
        gc_args = [
            call.args[0]
            for call in folder_connector._delete_storage_document.await_args_list
        ]
        assert sorted(gc_args) == ["doc-a", "doc-b"]


# --------------------------------------------------------------------------- #
# apply_uploaded_file_event_batch — SHA-256 mismatch skip-with-warning branch #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyUploadedSha256Mismatch:
    async def test_mismatched_sha_event_skipped_warning_logged(
        self, folder_connector, tmp_path, monkeypatch
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(return_value={})
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        # Should NOT be called for the mismatched event.
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-fresh")
        # The aiohttp.ClientSession context manager opened inside the method —
        # never actually used because we mock out _upload_storage_file, but
        # the `async with` still needs a working object.
        _patch_session(monkeypatch, _FakeSession([]))

        # CREATED with WRONG sha — must be skipped + warning.
        bad_event = LocalFsFileEvent(
            type="CREATED",
            path="bad.txt",
            timestamp=1,
            isDirectory=False,
            contentField="file_bad",
            sha256="00" * 32,  # never matches
            mimeType="text/plain",
        )
        # CREATED whose sha matches — must succeed.
        import hashlib as _hashlib

        good_bytes = b"hello upload"
        good_event = LocalFsFileEvent(
            type="CREATED",
            path="good.txt",
            timestamp=1,
            isDirectory=False,
            contentField="file_good",
            sha256=_hashlib.sha256(good_bytes).hexdigest(),
            mimeType="text/plain",
        )

        stats = await folder_connector.apply_uploaded_file_event_batch(
            [bad_event, good_event],
            {"file_bad": b"actually-different", "file_good": good_bytes},
        )

        # The bad event was skipped, the good one processed.
        assert stats.processed == 1
        assert folder_connector._upload_storage_file.await_count == 1
        # Warning logged for the mismatch.
        assert any(
            "SHA-256 mismatch" in (call.args[0] if call.args else "")
            for call in folder_connector.logger.warning.call_args_list
        )


# --------------------------------------------------------------------------- #
# _do_upload — non-JSON 2xx body falls back to raw text                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestUploadStorageFileNonJsonBody:
    async def test_existing_id_returned_when_body_is_not_json(
        self, folder_connector, monkeypatch
    ):
        # 200 OK but body is plain text — JSONDecodeError branch then
        # existing_document_id short-circuit.
        session = _FakeSession([("post", _FakeResponse(200, "OK plain"))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="x.txt",
            content=b"d",
            mime_type=None,
            existing_document_id="doc-keep",
            org_id="org-1",
            storage_url="http://x",
            storage_token="t",
        )
        assert doc_id == "doc-keep"


# --------------------------------------------------------------------------- #
# stream_record — OSError on local file open                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamRecordLocalFileOsError:
    async def test_open_oserror_raises_400(
        self, folder_connector, tmp_path, monkeypatch
    ):
        f = tmp_path / "data.bin"
        f.write_bytes(b"local-bytes")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="data.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )

        # Force Path.open to raise OSError without touching the real fs.
        from pathlib import Path as _Path

        original_open = _Path.open

        def _boom(self, *args, **kwargs):
            if self == f.resolve():
                raise OSError("permission denied")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "open", _boom)

        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Cannot read file" in str(ei.value.detail)
