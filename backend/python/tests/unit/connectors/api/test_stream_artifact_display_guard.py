"""User-facing stream hides STAGING / temporary / TOOL_RESULT artifacts."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import stream_record, stream_record_internal
from app.models.entities import ArtifactRecord, ArtifactType, ArtifactVisibility, RecordType


def _artifact_record(**overrides) -> ArtifactRecord:
    defaults = dict(
        id="art-1",
        org_id="org-1",
        record_name="chart.png",
        record_type=RecordType.ARTIFACT,
        external_record_id="doc-1",
        mime_type="image/png",
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.CODING_SANDBOX,
        connector_id="c1",
        artifact_type=ArtifactType.CHART,
        visibility=ArtifactVisibility.VISIBLE,
        is_temporary=False,
    )
    defaults.update(overrides)
    return ArtifactRecord(**defaults)


def _request():
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "org-1", "role": "member"}
    req.app.container = MagicMock()
    return req


async def _call_stream(record, graph_provider):
    return await stream_record(
        _request(),
        record.id,
        convertTo=None,
        version=None,
        graph_provider=graph_provider,
        config_service=AsyncMock(),
    )


def _graph_with_record(record):
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"_key": "org-1"})
    gp.get_record_by_id = AsyncMock(return_value=record)
    gp.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})
    return gp


@pytest.mark.asyncio
async def test_user_stream_staging_returns_404():
    with pytest.raises(HTTPException) as exc:
        await _call_stream(
            _artifact_record(visibility=ArtifactVisibility.STAGING),
            _graph_with_record(_artifact_record(visibility=ArtifactVisibility.STAGING)),
        )
    assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value


@pytest.mark.asyncio
async def test_user_stream_tool_result_returns_404():
    record = _artifact_record(artifact_type=ArtifactType.TOOL_RESULT)
    with pytest.raises(HTTPException) as exc:
        await _call_stream(record, _graph_with_record(record))
    assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value


@pytest.mark.asyncio
async def test_user_stream_temporary_returns_404():
    record = _artifact_record(is_temporary=True)
    with pytest.raises(HTTPException) as exc:
        await _call_stream(record, _graph_with_record(record))
    assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value


@pytest.mark.asyncio
async def test_user_stream_visible_artifact_continues():
    record = _artifact_record()
    gp = _graph_with_record(record)
    with patch("app.connectors.api.router._resolve_record_content_response", new_callable=AsyncMock) as resolve:
        resolve.return_value = SimpleNamespace(status_code=200)
        result = await _call_stream(record, gp)
        resolve.assert_awaited_once()
        assert result is resolve.return_value


@pytest.mark.asyncio
async def test_user_stream_non_artifact_skips_display_policy():
    record = SimpleNamespace(
        id="rec-1",
        org_id="org-1",
        record_type=RecordType.FILE,
        connector_id="c1",
        connector_name="DRIVE",
    )
    gp = _graph_with_record(record)
    with patch("app.connectors.api.router._resolve_record_content_response", new_callable=AsyncMock) as resolve:
        resolve.return_value = SimpleNamespace(status_code=200)
        await _call_stream(record, gp)
        resolve.assert_awaited_once()


def test_internal_stream_source_does_not_apply_display_policy():
    """STAGING artifacts remain readable on the agent-internal path."""
    assert "ArtifactDisplayPolicy" in inspect.getsource(stream_record)
    assert "ArtifactDisplayPolicy" not in inspect.getsource(stream_record_internal)
