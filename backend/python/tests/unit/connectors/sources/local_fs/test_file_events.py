"""Tests for Local FS request parsing helpers in connectors router."""

# ruff: noqa: ANN201, ANN202, ANN204

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# Keep router imports lightweight; same guard pattern used in local_fs connector tests.
class _ContainerMeta(type):
    def __getattr__(cls, name):
        return None


class _ConnectorAppContainer(metaclass=_ContainerMeta):
    pass


if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")
    _stub_container.ConnectorAppContainer = _ConnectorAppContainer
    sys.modules["app.containers.connector"] = _stub_container

if "redis" not in sys.modules:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")
    _redis_backoff.ExponentialBackoff = type("ExponentialBackoff", (), {})
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")
    _redis_retry.Retry = type("Retry", (), {})
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

from app.config.constants.http_status_code import HttpStatusCode  # noqa: E402
from app.connectors.api.router import (  # noqa: E402
    submit_connector_file_event_uploads,
    submit_connector_file_events,
)
from app.connectors.sources.local_fs.connector import LocalFsConnector  # noqa: E402
from app.connectors.sources.local_fs.file_events import (  # noqa: E402
    _normalize_connector_type_value,
    _parse_local_fs_file_event_batch_request,
    _parse_local_fs_uploaded_file_event_batch_request,
    _unwrap_local_fs_file_event_payload,
)
from app.connectors.sources.local_fs.models import (  # noqa: E402
    LocalFsFileEvent,
    LocalFsFileEventBatchRequest,
    LocalFsFileEventBatchStats,
)


def _event(path: str = "a.txt") -> dict:
    return {
        "type": "CREATED",
        "path": path,
        "timestamp": 1700000000000,
        "isDirectory": False,
    }


class _UploadPart:
    def __init__(self, data: bytes):
        self._data = data
        self.closed = False

    async def read(self) -> bytes:
        return self._data

    async def close(self) -> None:
        self.closed = True


class _FakeForm(dict):
    def __init__(self, *args, items=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._items = items or []

    def multi_items(self):
        return list(self._items)


def _request_with_body(body: bytes):
    async def _body():
        return body

    return SimpleNamespace(body=_body)


def _request_with_form(form: _FakeForm):
    async def _form():
        return form

    return SimpleNamespace(form=_form)


def _sample_batch_request(**kwargs: object) -> LocalFsFileEventBatchRequest:
    defaults: dict[str, object] = {
        "batchId": "batch-x",
        "events": [
            LocalFsFileEvent(
                type="CREATED",
                path="a.txt",
                timestamp=1,
                isDirectory=False,
            )
        ],
        "timestamp": 1,
        "resetBeforeApply": False,
    }
    defaults.update(kwargs)
    return LocalFsFileEventBatchRequest(**defaults)


def _minimal_local_fs_connector() -> LocalFsConnector:
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


def _connector_request(
    *,
    user_id: str | None = "user-1",
    org_id: str | None = "org-1",
    admin_header: str = "false",
):
    req = MagicMock()
    user_data = {}
    if user_id is not None:
        user_data["userId"] = user_id
    if org_id is not None:
        user_data["orgId"] = org_id
    req.state.user.get = lambda k, default=None: user_data.get(k, default)
    req.headers.get = lambda k, default=None: (
        admin_header if k == "X-Is-Admin" else default
    )
    inner_logger = MagicMock()
    req.app.container = MagicMock()
    req.app.container.logger.return_value = inner_logger
    req.app.state.connector_registry = MagicMock()
    return req


def _connector_request_with_body(body: bytes, **kwargs):
    """Connector-scoped request with async ``body()`` for real JSON parse paths."""
    req = _connector_request(**kwargs)

    async def _body():
        return body

    req.body = _body
    return req


def _connector_request_with_form(form: _FakeForm, **kwargs):
    """Connector-scoped request with async ``form()`` for real multipart parse paths."""
    req = _connector_request(**kwargs)

    async def _form():
        return form

    req.form = _form
    return req


class TestNormalizeConnectorTypeValue:
    def test_strips_underscores_spaces_and_lower(self):
        assert _normalize_connector_type_value("Local_FS") == "localfs"
        assert _normalize_connector_type_value("  Local FS  ") == "localfs"
        assert _normalize_connector_type_value("LOCALFS") == "localfs"

    def test_empty_string(self):
        assert _normalize_connector_type_value("") == ""


class TestUnwrapLocalFsPayload:
    def test_unwraps_nested_body_payload_data(self):
        raw = {"body": {"payload": {"data": {"events": [_event()]}}}}
        out = _unwrap_local_fs_file_event_payload(raw)
        assert isinstance(out, dict)
        assert len(out["events"]) == 1

    def test_unwraps_via_payload_key(self):
        raw = {"payload": {"events": [_event()]}}
        out = _unwrap_local_fs_file_event_payload(raw)
        assert isinstance(out, dict)
        assert len(out["events"]) == 1

    def test_unwraps_via_data_key(self):
        raw = {"data": {"events": [_event()]}}
        out = _unwrap_local_fs_file_event_payload(raw)
        assert isinstance(out, dict)
        assert len(out["events"]) == 1

    def test_prefers_body_over_payload_when_both_present(self):
        raw = {"body": {"events": [_event(path="via-body.txt")]}, "payload": {"events": []}}
        out = _unwrap_local_fs_file_event_payload(raw)
        assert out["events"][0]["path"] == "via-body.txt"

    def test_non_dict_returns_unchanged(self):
        assert _unwrap_local_fs_file_event_payload([1, 2]) == [1, 2]
        assert _unwrap_local_fs_file_event_payload(42) == 42

    def test_whitespace_only_string_returns_unchanged(self):
        assert _unwrap_local_fs_file_event_payload("   \t\n") == "   \t\n"

    def test_invalid_json_string_returns_original_string(self):
        raw = "{not-valid-json"
        assert _unwrap_local_fs_file_event_payload(raw) == raw

    def test_stops_after_max_unwrap_iterations(self):
        leaf = {"events": [_event(path="deep.txt")]}
        nested = {"body": {"body": {"body": leaf}}}
        out = _unwrap_local_fs_file_event_payload(nested)
        assert isinstance(out, dict)
        assert out == leaf

    def test_four_wrappers_returns_candidate_before_final_leaf(self):
        leaf = {"events": [_event(path="deepest.txt")]}
        nested = {"body": {"body": {"body": {"body": leaf}}}}
        out = _unwrap_local_fs_file_event_payload(nested)
        assert isinstance(out, dict)
        assert out.get("body") == leaf

    def test_unwraps_json_string(self):
        raw = json.dumps({"body": {"events": [_event()]}})
        out = _unwrap_local_fs_file_event_payload(raw)
        assert isinstance(out, dict)
        assert "events" in out


@pytest.mark.asyncio
class TestParseLocalFsFileEventBatchRequest:
    async def test_empty_body_raises_422(self):
        req = _request_with_body(b"")
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value

    async def test_list_payload_gets_wrapped_with_defaults(self):
        req = _request_with_body(json.dumps([_event()]).encode("utf-8"))
        parsed = await _parse_local_fs_file_event_batch_request(req)
        assert parsed.events[0].path == "a.txt"
        assert parsed.batchId.startswith("localfs-replay-")
        assert parsed.resetBeforeApply is False

    async def test_non_json_body_decoded_then_validation_error(self):
        req = _request_with_body(b"plain-text-not-json{")
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value

    async def test_dict_missing_batch_id_and_timestamp_get_defaults(self):
        blob = {"events": [_event()], "resetBeforeApply": True}
        req = _request_with_body(json.dumps(blob).encode("utf-8"))
        parsed = await _parse_local_fs_file_event_batch_request(req)
        assert parsed.batchId.startswith("localfs-replay-")
        assert parsed.timestamp > 0
        assert parsed.resetBeforeApply is True

    async def test_invalid_schema_raises_422_with_errors(self):
        req = _request_with_body(json.dumps({"events": "not-a-list"}).encode("utf-8"))
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value
        detail = ei.value.detail
        assert isinstance(detail, dict)
        assert "errors" in detail


@pytest.mark.asyncio
class TestParseLocalFsUploadedFileEventBatchRequest:
    async def test_manifest_required(self):
        form = _FakeForm(items=[])
        req = _request_with_form(form)
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value

    async def test_invalid_manifest_json_raises_422(self):
        bad = "{bad-json"
        form = _FakeForm({"manifest": bad}, items=[("manifest", bad)])
        req = _request_with_form(form)
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value

    async def test_manifest_read_via_upload_part(self):
        manifest = {"events": [_event("via-read.txt")], "batchId": "bm"}
        raw = json.dumps(manifest).encode("utf-8")

        class _ManifestReader:
            async def read(self) -> bytes:
                return raw

        mr = _ManifestReader()
        form = _FakeForm(
            {"manifest": mr},
            items=[
                ("manifest", mr),
            ],
        )
        req = _request_with_form(form)
        parsed, files = await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert parsed.batchId == "bm"
        assert parsed.events[0].path == "via-read.txt"
        assert files == {}

    async def test_list_manifest_wrapped_with_localfs_upload_prefix(self):
        """Multipart manifest may be a bare JSON array; upload path uses localfs-upload-* ids."""
        raw_list = json.dumps([_event(path="list-upload.txt")])
        form = _FakeForm(
            {"manifest": raw_list},
            items=[("manifest", raw_list)],
        )
        req = _request_with_form(form)
        parsed, files = await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert parsed.batchId.startswith("localfs-upload-")
        assert parsed.events[0].path == "list-upload.txt"
        assert files == {}

    async def test_manifest_validation_error_includes_errors(self):
        manifest = {"events": "oops"}
        form = _FakeForm(
            {"manifest": json.dumps(manifest)},
            items=[("manifest", json.dumps(manifest))],
        )
        req = _request_with_form(form)
        with pytest.raises(HTTPException) as ei:
            await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value
        detail = ei.value.detail
        assert isinstance(detail, dict)
        assert detail.get("message")

    async def test_file_part_without_close_skipped_gracefully(self):
        manifest = {"events": [_event()]}

        class _NoClosePart:
            async def read(self) -> bytes:
                return b"z"

        nc = _NoClosePart()
        form = _FakeForm(
            {"manifest": json.dumps(manifest)},
            items=[
                ("manifest", json.dumps(manifest)),
                ("nc", nc),
            ],
        )
        req = _request_with_form(form)
        parsed, files = await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert files == {"nc": b"z"}

    async def test_parses_manifest_and_uploaded_files(self):
        manifest = {"events": [_event("doc.txt")], "batchId": "b1", "timestamp": 1}
        file1 = _UploadPart(b"hello")
        file2 = _UploadPart(b"world")
        form = _FakeForm(
            {"manifest": json.dumps(manifest)},
            items=[
                ("manifest", json.dumps(manifest)),
                ("file_a", file1),
                ("file_b", file2),
            ],
        )
        req = _request_with_form(form)
        parsed, files = await _parse_local_fs_uploaded_file_event_batch_request(req)
        assert parsed.batchId == "b1"
        assert files == {"file_a": b"hello", "file_b": b"world"}
        assert file1.closed is True
        assert file2.closed is True

@pytest.mark.asyncio
class TestSubmitConnectorFileEventsRoutes:
    async def test_submit_file_events_success(self):
        payload = _sample_batch_request()
        conn = _minimal_local_fs_connector()
        conn.apply_file_event_batch = AsyncMock(
            return_value=LocalFsFileEventBatchStats(processed=1, deleted=0)
        )
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "local_fs"}
        )
        gp = MagicMock()

        with (
            patch(
                "app.connectors.api.router._parse_local_fs_file_event_batch_request",
                new_callable=AsyncMock,
                return_value=payload,
            ),
            patch(
                "app.connectors.api.router._ensure_connector_initialized",
                new_callable=AsyncMock,
                return_value=conn,
            ),
            patch(
                "app.connectors.api.router._update_connector_status",
                new_callable=AsyncMock,
            ) as upd,
        ):
            out = await submit_connector_file_events("cid-1", req, gp)

        assert out.success is True
        assert out.batchId == "batch-x"
        assert out.stats.processed == 1
        assert out.stats.deleted == 0
        conn.apply_file_event_batch.assert_awaited_once_with(
            payload.events,
            reset_before_apply=payload.resetBeforeApply,
        )
        assert upd.await_count == 2

    async def test_submit_file_events_unauthenticated(self):
        payload = _sample_batch_request()
        req = _connector_request(user_id=None, org_id="org-1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock()

        with patch(
            "app.connectors.api.router._parse_local_fs_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_submit_file_events_instance_not_found(self):
        payload = _sample_batch_request()
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with patch(
            "app.connectors.api.router._parse_local_fs_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_submit_file_events_wrong_connector_type(self):
        payload = _sample_batch_request()
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "slack"}
        )

        with patch(
            "app.connectors.api.router._parse_local_fs_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_submit_file_events_unauthenticated_real_body(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        body = json.dumps(manifest).encode("utf-8")
        req = _connector_request_with_body(body, user_id=None, org_id="org-1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock()

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_submit_file_events_instance_not_found_real_body(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        body = json.dumps(manifest).encode("utf-8")
        req = _connector_request_with_body(body)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_submit_file_events_wrong_connector_type_real_body(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        body = json.dumps(manifest).encode("utf-8")
        req = _connector_request_with_body(body)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "slack"}
        )

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_submit_file_events_apply_wraps_generic_exception(self):
        payload = _sample_batch_request()
        conn = _minimal_local_fs_connector()
        conn.apply_file_event_batch = AsyncMock(side_effect=RuntimeError("boom"))
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "localfs"}
        )

        with (
            patch(
                "app.connectors.api.router._parse_local_fs_file_event_batch_request",
                new_callable=AsyncMock,
                return_value=payload,
            ),
            patch(
                "app.connectors.api.router._ensure_connector_initialized",
                new_callable=AsyncMock,
                return_value=conn,
            ),
            patch(
                "app.connectors.api.router._update_connector_status",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    async def test_submit_file_events_not_local_fs_after_init(self):
        payload = _sample_batch_request()
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "local_fs"}
        )

        with (
            patch(
                "app.connectors.api.router._parse_local_fs_file_event_batch_request",
                new_callable=AsyncMock,
                return_value=payload,
            ),
            patch(
                "app.connectors.api.router._ensure_connector_initialized",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "app.connectors.api.router._update_connector_status",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_events("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value


@pytest.mark.asyncio
class TestSubmitConnectorFileEventUploadsRoute:
    async def test_submit_uploads_success(self):
        payload = _sample_batch_request(resetBeforeApply=True)
        files = {"part1": b"zzz"}
        conn = _minimal_local_fs_connector()
        conn.apply_uploaded_file_event_batch = AsyncMock(
            return_value=LocalFsFileEventBatchStats(processed=2, deleted=1)
        )
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "Local FS"}
        )
        gp = MagicMock()

        with (
            patch(
                "app.connectors.api.router."
                "_parse_local_fs_uploaded_file_event_batch_request",
                new_callable=AsyncMock,
                return_value=(payload, files),
            ),
            patch(
                "app.connectors.api.router._ensure_connector_initialized",
                new_callable=AsyncMock,
                return_value=conn,
            ),
            patch(
                "app.connectors.api.router._update_connector_status",
                new_callable=AsyncMock,
            ) as upd,
        ):
            out = await submit_connector_file_event_uploads("cid-up", req, gp)

        assert out.success is True
        assert out.stats.processed == 2
        assert out.stats.deleted == 1
        conn.apply_uploaded_file_event_batch.assert_awaited_once_with(
            payload.events,
            files,
            reset_before_apply=True,
        )
        assert upd.await_count == 2

    async def test_submit_uploads_apply_failure(self):
        payload = _sample_batch_request()
        conn = _minimal_local_fs_connector()
        conn.apply_uploaded_file_event_batch = AsyncMock(
            side_effect=ValueError("bad batch")
        )
        req = _connector_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "localfs"}
        )

        with (
            patch(
                "app.connectors.api.router."
                "_parse_local_fs_uploaded_file_event_batch_request",
                new_callable=AsyncMock,
                return_value=(payload, {}),
            ),
            patch(
                "app.connectors.api.router._ensure_connector_initialized",
                new_callable=AsyncMock,
                return_value=conn,
            ),
            patch(
                "app.connectors.api.router._update_connector_status",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                await submit_connector_file_event_uploads(
                    "cid", req, MagicMock()
                )
        assert ei.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    async def test_submit_uploads_unauthenticated_real_multipart(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        raw = json.dumps(manifest)
        form = _FakeForm({"manifest": raw}, items=[("manifest", raw)])
        req = _connector_request_with_form(form, user_id=None, org_id="org-1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock()

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_event_uploads("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_submit_uploads_instance_not_found_real_multipart(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        raw = json.dumps(manifest)
        form = _FakeForm({"manifest": raw}, items=[("manifest", raw)])
        req = _connector_request_with_form(form)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_event_uploads("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_submit_uploads_wrong_connector_type_real_multipart(self):
        manifest = {"events": [_event()], "batchId": "b", "timestamp": 1}
        raw = json.dumps(manifest)
        form = _FakeForm({"manifest": raw}, items=[("manifest", raw)])
        req = _connector_request_with_form(form)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value={"type": "slack"}
        )

        with pytest.raises(HTTPException) as ei:
            await submit_connector_file_event_uploads("cid", req, MagicMock())
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
