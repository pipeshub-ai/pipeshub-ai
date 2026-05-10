"""Tests for the Local FS file-event submission endpoints in
``app.connectors.api.router``:

- ``submit_connector_file_event_uploads`` (multipart/form-data)
- ``submit_connector_file_events``        (application/json)

Both endpoints share the same control flow (auth check, instance lookup,
type guard, status SYNCING -> apply -> IDLE) and are exercised in parallel
to keep regressions symmetric.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import AppStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    submit_connector_file_event_uploads,
    submit_connector_file_events,
)
from app.connectors.sources.local_fs.connector import LocalFsConnector
from app.connectors.sources.local_fs.models import (
    LocalFsFileEvent,
    LocalFsFileEventBatchRequest,
    LocalFsFileEventBatchStats,
)

_ROUTER = "app.connectors.api.router"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _mock_request(
    *,
    user: dict[str, str] | None = None,
    is_admin: bool = False,
    connector_registry: object | None = None,
) -> MagicMock:
    """Minimal FastAPI Request mock for the two endpoints under test."""
    req = MagicMock()

    user_data: dict[str, str] = (
        user if user is not None else {"userId": "user-1", "orgId": "org-1"}
    )
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda key, default=None: user_data.get(key, default)

    headers = {"X-Is-Admin": "true" if is_admin else "false"}
    req.headers = MagicMock()
    req.headers.get = lambda key, default=None: headers.get(key, default)

    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())
    req.app = MagicMock()
    req.app.container = container
    req.app.state.connector_registry = (
        connector_registry if connector_registry is not None else MagicMock()
    )

    return req


def _make_payload(
    *, batch_id: str = "batch-1", reset: bool = False
) -> LocalFsFileEventBatchRequest:
    return LocalFsFileEventBatchRequest(
        batchId=batch_id,
        events=[
            LocalFsFileEvent(
                type="created",
                path="/tmp/a.txt",
                timestamp=0,
                isDirectory=False,
            )
        ],
        timestamp=0,
        resetBeforeApply=reset,
    )


def _make_stats(processed: int = 1, deleted: int = 0) -> LocalFsFileEventBatchStats:
    return LocalFsFileEventBatchStats(processed=processed, deleted=deleted)


def _registry_with_instance(
    instance: dict[str, str] | None = {"type": "localfs"},
) -> MagicMock:
    reg = MagicMock()
    reg.get_connector_instance = AsyncMock(return_value=instance)
    return reg


def _local_fs_connector_mock(
    *,
    apply_uploaded_return: LocalFsFileEventBatchStats | None = None,
    apply_uploaded_side_effect: BaseException | None = None,
    apply_json_return: LocalFsFileEventBatchStats | None = None,
    apply_json_side_effect: BaseException | None = None,
) -> MagicMock:
    """A MagicMock with spec=LocalFsConnector so isinstance() check passes."""
    conn = MagicMock(spec=LocalFsConnector)
    conn.apply_uploaded_file_event_batch = AsyncMock(
        return_value=apply_uploaded_return,
        side_effect=apply_uploaded_side_effect,
    )
    conn.apply_file_event_batch = AsyncMock(
        return_value=apply_json_return,
        side_effect=apply_json_side_effect,
    )
    return conn


# Common patch context that stubs the four router-level helpers used by both
# endpoints. Tests pass `parse_*` for whichever endpoint they invoke.
def _patch_router(
    *,
    parse_uploaded_return: tuple[LocalFsFileEventBatchRequest, dict[str, bytes]]
    | None = None,
    parse_json_return: LocalFsFileEventBatchRequest | None = None,
    normalize_return: str = "localfs",
    ensure_return: object | None = None,
    update_status_side_effect: BaseException | None = None,
):
    return (
        patch(
            f"{_ROUTER}._parse_local_fs_uploaded_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=parse_uploaded_return,
        ),
        patch(
            f"{_ROUTER}._parse_local_fs_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=parse_json_return,
        ),
        patch(f"{_ROUTER}._normalize_connector_type_value", return_value=normalize_return),
        patch(
            f"{_ROUTER}._update_connector_status",
            new_callable=AsyncMock,
            side_effect=update_status_side_effect,
        ),
        patch(
            f"{_ROUTER}._ensure_connector_initialized",
            new_callable=AsyncMock,
            return_value=ensure_return,
        ),
    )


# ---------------------------------------------------------------------------
# submit_connector_file_event_uploads (multipart)
# ---------------------------------------------------------------------------


class TestSubmitConnectorFileEventUploads:
    """Multipart endpoint."""

    @pytest.mark.asyncio
    async def test_success_returns_response_with_stats(self) -> None:
        payload = _make_payload(batch_id="batch-mp")
        files: dict[str, bytes] = {"f1": b"abc"}
        stats = _make_stats(processed=2, deleted=1)
        connector = _local_fs_connector_mock(apply_uploaded_return=stats)
        registry = _registry_with_instance()
        gp = MagicMock()
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, files),
            ensure_return=connector,
        )
        with p_up, p_norm, p_status as mock_status, p_ensure:
            result = await submit_connector_file_event_uploads("conn-1", req, gp)

        assert result.success is True
        assert result.connectorId == "conn-1"
        assert result.batchId == "batch-mp"
        assert result.stats.processed == 2
        assert result.stats.deleted == 1

        # The connector should be invoked with payload events + files dict.
        connector.apply_uploaded_file_event_batch.assert_awaited_once_with(
            payload.events, files, reset_before_apply=False,
        )

        # Status should transition SYNCING -> IDLE.
        assert mock_status.await_count == 2
        assert mock_status.await_args_list[0].args[2] == AppStatus.SYNCING.value
        assert mock_status.await_args_list[1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_propagates_reset_before_apply_flag(self) -> None:
        payload = _make_payload(reset=True)
        files: dict[str, bytes] = {}
        connector = _local_fs_connector_mock(apply_uploaded_return=_make_stats())
        registry = _registry_with_instance()
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, files),
            ensure_return=connector,
        )
        with p_up, p_norm, p_status, p_ensure:
            await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        connector.apply_uploaded_file_event_batch.assert_awaited_once_with(
            payload.events, files, reset_before_apply=True,
        )

    @pytest.mark.asyncio
    async def test_missing_user_id_raises_401(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        # missing userId
        req = _mock_request(user={"orgId": "org-1"}, connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
        )
        with p_up, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value
        assert "not authenticated" in exc.value.detail

    @pytest.mark.asyncio
    async def test_missing_org_id_raises_401(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        req = _mock_request(user={"userId": "user-1"}, connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
        )
        with p_up, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance(instance=None)
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
        )
        with p_up, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "conn-1" in exc.value.detail

    @pytest.mark.asyncio
    async def test_non_local_fs_type_raises_400(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance(instance={"type": "googleDrive"})
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
            normalize_return="googledrive",
        )
        with p_up, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Local FS" in exc.value.detail
        # 400 raised before SYNCING update.
        assert mock_status.await_count == 0

    @pytest.mark.asyncio
    async def test_initialized_connector_not_local_fs_raises_400(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        # plain object (not a LocalFsConnector) — isinstance() will be False
        wrong_connector = SimpleNamespace()
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
            ensure_return=wrong_connector,
        )
        with p_up, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "not a Local FS connector" in exc.value.detail
        # SYNCING was set, then IDLE in finally — both calls must happen.
        assert mock_status.await_count == 2
        assert mock_status.await_args_list[1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_apply_generic_exception_wrapped_as_500(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(
            apply_uploaded_side_effect=RuntimeError("disk full"),
        )
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
            ensure_return=connector,
        )
        with p_up, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "disk full" in exc.value.detail
        # finally block still moves status to IDLE.
        assert mock_status.await_args_list[-1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_apply_http_exception_propagates_unchanged(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        original = HTTPException(
            status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value, detail="too big",
        )
        connector = _local_fs_connector_mock(apply_uploaded_side_effect=original)
        req = _mock_request(connector_registry=registry)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
            ensure_return=connector,
        )
        with p_up, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert exc.value is original  # not re-wrapped

    @pytest.mark.asyncio
    async def test_idle_status_failure_is_suppressed(self) -> None:
        """The IDLE update in `finally` must not mask the success response."""
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(apply_uploaded_return=_make_stats())
        req = _mock_request(connector_registry=registry)

        # First call (SYNCING) succeeds; second call (IDLE) raises.
        update_status = AsyncMock(side_effect=[None, RuntimeError("graph down")])

        with patch(
            f"{_ROUTER}._parse_local_fs_uploaded_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=(payload, {}),
        ), patch(f"{_ROUTER}._normalize_connector_type_value", return_value="localfs"), patch(
            f"{_ROUTER}._update_connector_status", update_status,
        ), patch(
            f"{_ROUTER}._ensure_connector_initialized",
            new_callable=AsyncMock,
            return_value=connector,
        ):
            result = await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        assert result.success is True
        assert update_status.await_count == 2

    @pytest.mark.asyncio
    async def test_admin_header_propagated_to_registry(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(apply_uploaded_return=_make_stats())
        req = _mock_request(connector_registry=registry, is_admin=True)

        p_up, _, p_norm, p_status, p_ensure = _patch_router(
            parse_uploaded_return=(payload, {}),
            ensure_return=connector,
        )
        with p_up, p_norm, p_status, p_ensure:
            await submit_connector_file_event_uploads("conn-1", req, MagicMock())

        registry.get_connector_instance.assert_awaited_once_with(
            connector_id="conn-1",
            user_id="user-1",
            org_id="org-1",
            is_admin=True,
        )


# ---------------------------------------------------------------------------
# submit_connector_file_events (JSON)
# ---------------------------------------------------------------------------


class TestSubmitConnectorFileEvents:
    """JSON endpoint."""

    @pytest.mark.asyncio
    async def test_success_returns_response_with_stats(self) -> None:
        payload = _make_payload(batch_id="batch-json")
        stats = _make_stats(processed=3, deleted=2)
        connector = _local_fs_connector_mock(apply_json_return=stats)
        registry = _registry_with_instance()
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=connector,
        )
        with p_json, p_norm, p_status as mock_status, p_ensure:
            result = await submit_connector_file_events("conn-1", req, MagicMock())

        assert result.success is True
        assert result.connectorId == "conn-1"
        assert result.batchId == "batch-json"
        assert result.stats.processed == 3
        assert result.stats.deleted == 2

        connector.apply_file_event_batch.assert_awaited_once_with(
            payload.events, reset_before_apply=False,
        )
        # The multipart-only apply method must NOT be called.
        connector.apply_uploaded_file_event_batch.assert_not_awaited()

        assert mock_status.await_count == 2
        assert mock_status.await_args_list[0].args[2] == AppStatus.SYNCING.value
        assert mock_status.await_args_list[1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_propagates_reset_before_apply_flag(self) -> None:
        payload = _make_payload(reset=True)
        connector = _local_fs_connector_mock(apply_json_return=_make_stats())
        registry = _registry_with_instance()
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=connector,
        )
        with p_json, p_norm, p_status, p_ensure:
            await submit_connector_file_events("conn-1", req, MagicMock())

        connector.apply_file_event_batch.assert_awaited_once_with(
            payload.events, reset_before_apply=True,
        )

    @pytest.mark.asyncio
    async def test_missing_user_id_raises_401(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        req = _mock_request(user={"orgId": "org-1"}, connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(parse_json_return=payload)
        with p_json, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_missing_org_id_raises_401(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        req = _mock_request(user={"userId": "user-1"}, connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(parse_json_return=payload)
        with p_json, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance(instance=None)
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(parse_json_return=payload)
        with p_json, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "conn-1" in exc.value.detail

    @pytest.mark.asyncio
    async def test_non_local_fs_type_raises_400(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance(instance={"type": "slack"})
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            normalize_return="slack",
        )
        with p_json, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        # No status updates should fire for the wrong connector type.
        assert mock_status.await_count == 0

    @pytest.mark.asyncio
    async def test_initialized_connector_not_local_fs_raises_400(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        wrong_connector = SimpleNamespace()
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=wrong_connector,
        )
        with p_json, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "not a Local FS connector" in exc.value.detail
        # finally block still flips back to IDLE.
        assert mock_status.await_args_list[-1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_apply_generic_exception_wrapped_as_500(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(
            apply_json_side_effect=RuntimeError("kafka unreachable"),
        )
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=connector,
        )
        with p_json, p_norm, p_status as mock_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "kafka unreachable" in exc.value.detail
        assert mock_status.await_args_list[-1].args[2] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_apply_http_exception_propagates_unchanged(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        original = HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value, detail="bad event",
        )
        connector = _local_fs_connector_mock(apply_json_side_effect=original)
        req = _mock_request(connector_registry=registry)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=connector,
        )
        with p_json, p_norm, p_status, p_ensure:
            with pytest.raises(HTTPException) as exc:
                await submit_connector_file_events("conn-1", req, MagicMock())

        assert exc.value is original

    @pytest.mark.asyncio
    async def test_idle_status_failure_is_suppressed(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(apply_json_return=_make_stats())
        req = _mock_request(connector_registry=registry)

        update_status = AsyncMock(side_effect=[None, RuntimeError("graph down")])

        with patch(
            f"{_ROUTER}._parse_local_fs_file_event_batch_request",
            new_callable=AsyncMock,
            return_value=payload,
        ), patch(f"{_ROUTER}._normalize_connector_type_value", return_value="localfs"), patch(
            f"{_ROUTER}._update_connector_status", update_status,
        ), patch(
            f"{_ROUTER}._ensure_connector_initialized",
            new_callable=AsyncMock,
            return_value=connector,
        ):
            result = await submit_connector_file_events("conn-1", req, MagicMock())

        assert result.success is True
        assert update_status.await_count == 2

    @pytest.mark.asyncio
    async def test_admin_header_propagated_to_registry(self) -> None:
        payload = _make_payload()
        registry = _registry_with_instance()
        connector = _local_fs_connector_mock(apply_json_return=_make_stats())
        req = _mock_request(connector_registry=registry, is_admin=True)

        _, p_json, p_norm, p_status, p_ensure = _patch_router(
            parse_json_return=payload,
            ensure_return=connector,
        )
        with p_json, p_norm, p_status, p_ensure:
            await submit_connector_file_events("conn-1", req, MagicMock())

        registry.get_connector_instance.assert_awaited_once_with(
            connector_id="conn-1",
            user_id="user-1",
            org_id="org-1",
            is_admin=True,
        )
