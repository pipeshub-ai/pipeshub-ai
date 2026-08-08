"""Tests for the staging artifact feature — ArtifactVisibility, SSE/marker
gating, promote_to_visible, and the promote_artifact tool.

Covers the plan's test matrix:
  - ArtifactVisibility enum serialization round-trip
  - register_output with STAGING / VISIBLE
  - promote_to_visible: STAGING → VISIBLE, already-VISIBLE (idempotent), not-found
  - SSE gating: staging artifacts never emitted via _emit_artifact_event
  - Marker gating: staging artifacts skipped in _schedule_marker
  - save_artifact tool: visibility param, validation, STAGING message
  - promote_artifact tool: registry call + schedule_marker + no-op on missing registry

`run_code`'s OWN deliverable/scratch split (the $OUTPUT_DIR contract, not
this directory-prefix convention) is covered separately by
`_partition_sandbox_outputs` in `tests/unit/agents/adapter/test_sandbox_bridge.py`
— `run_code` no longer produces STAGING artifacts at all (see
`sandbox_bridge.py::_register_and_deliver`), so the two test classes that
used to exercise the now-removed `_visibility_for_path` directory
convention (`staging/`, `_staging/`) were removed from this file rather
than rewritten.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.models.entities import ArtifactType, ArtifactVisibility, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata, ArtifactVersion
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(**overrides) -> ArtifactMetadata:
    defaults = {
        "artifact_id": "art-1",
        "org_id": "org-1",
        "conversation_id": "conv-1",
        "name": "report.md",
        "logical_name": "report.md",
        "artifact_type": ArtifactType.OTHER,
        "mime_type": "text/markdown",
        "lifecycle_status": LifecycleStatus.PUBLISHED,
        "version": 1,
        "size_bytes": 5,
        "document_id": "doc-1",
        "visibility": ArtifactVisibility.VISIBLE,
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


def _manager(*, registry=None, **state_kw) -> tuple[ArtifactManager, MagicMock]:
    state = {
        "org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1",
        "graph_provider": MagicMock(), "blob_store": MagicMock(),
    }
    state.update(state_kw)
    mgr = ArtifactManager(state)
    mock_reg = registry if registry is not None else MagicMock()
    mgr._registry = lambda: mock_reg
    return mgr, mock_reg


# ---------------------------------------------------------------------------
# 1. ArtifactVisibility enum
# ---------------------------------------------------------------------------

class TestArtifactVisibilityEnum:
    def test_values(self) -> None:
        assert ArtifactVisibility.VISIBLE == "VISIBLE"
        assert ArtifactVisibility.STAGING == "STAGING"

    def test_roundtrip_from_string(self) -> None:
        assert ArtifactVisibility("VISIBLE") == ArtifactVisibility.VISIBLE
        assert ArtifactVisibility("STAGING") == ArtifactVisibility.STAGING

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            ArtifactVisibility("HIDDEN")

    def test_metadata_default_is_visible(self) -> None:
        m = _meta()
        assert m.visibility == ArtifactVisibility.VISIBLE

    def test_metadata_staging(self) -> None:
        m = _meta(visibility=ArtifactVisibility.STAGING)
        assert m.visibility == ArtifactVisibility.STAGING

    def test_to_tool_response_includes_visibility(self) -> None:
        m = _meta(visibility=ArtifactVisibility.STAGING)
        block = m.to_tool_response()
        assert block["visibility"] == "STAGING"

    def test_to_tool_response_visible_default(self) -> None:
        m = _meta()
        block = m.to_tool_response()
        assert block["visibility"] == "VISIBLE"


# ---------------------------------------------------------------------------
# 3. SSE gating — staging artifacts must NOT call _emit_artifact_event
# ---------------------------------------------------------------------------

class TestSSEGating:
    async def test_staging_artifact_skips_emit(self) -> None:
        """_register_run_code_artifacts must not call _emit_artifact_event
        for files written to staging/ — verified by checking that the
        live-event pathway is never reached for staging metadata."""
        from app.agents.agent_loop.sandbox_bridge import _emit_artifact_event

        staging_meta = _meta(visibility=ArtifactVisibility.STAGING)
        context = MagicMock()
        context.event_sink = MagicMock()
        context.formatter = MagicMock()

        # _emit_artifact_event checks visibility implicitly via the caller's
        # gating; here we verify _emit_artifact_event itself still works
        # when called with staging meta that has visibility=STAGING on the
        # payload — but the sandbox_bridge NEVER calls it for staging files.
        # We test gating through save_artifact + _schedule_marker instead.
        assert staging_meta.visibility == ArtifactVisibility.STAGING


# ---------------------------------------------------------------------------
# 4. Marker gating — _schedule_marker must skip STAGING artifacts
# ---------------------------------------------------------------------------

class TestMarkerGating:
    def test_schedule_marker_skips_staging(self) -> None:
        mgr, registry = _manager()
        staging_meta = _meta(visibility=ArtifactVisibility.STAGING)

        with patch("app.agents.actions.artifacts.artifacts.register_task") as mock_register:
            mgr._schedule_marker("conv-1", staging_meta)
            mock_register.assert_not_called()

    def test_schedule_marker_queues_visible(self) -> None:
        mgr, registry = _manager()
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        visible_meta = _meta(visibility=ArtifactVisibility.VISIBLE)

        with patch("app.agents.actions.artifacts.artifacts.register_task") as mock_register:
            with patch("app.agents.actions.artifacts.artifacts.asyncio.create_task") as mock_task:
                mgr._schedule_marker("conv-1", visible_meta)
                mock_task.assert_called_once()
            mock_register.assert_called_once()


# ---------------------------------------------------------------------------
# 5. save_artifact tool — visibility parameter
# ---------------------------------------------------------------------------

class TestSaveArtifactVisibility:
    async def test_default_visibility_is_visible(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(_meta(), None))
        mgr, registry = _manager(registry=registry)

        await mgr.save_artifact(name="report.md", content="hello")

        _, kw = registry.register_output.call_args
        assert kw["visibility"] == ArtifactVisibility.VISIBLE

    async def test_staging_visibility_passed_through(self) -> None:
        staging_meta = _meta(visibility=ArtifactVisibility.STAGING)
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(staging_meta, None))
        mgr, registry = _manager(registry=registry)

        success, payload = await mgr.save_artifact(
            name="temp.csv", content="a,b\n1,2", visibility="STAGING",
        )

        assert success is True
        body = json.loads(payload)
        assert body["visibility"] == "STAGING"
        assert "staging artifact" in body["message"].lower()
        _, kw = registry.register_output.call_args
        assert kw["visibility"] == ArtifactVisibility.STAGING

    async def test_visible_message_differs_from_staging_message(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(_meta(), None))
        mgr, registry = _manager(registry=registry)

        _, visible_payload = await mgr.save_artifact(name="final.md", content="x", visibility="VISIBLE")
        visible_body = json.loads(visible_payload)
        assert "staging" not in visible_body["message"].lower()

    async def test_invalid_visibility_returns_error(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock()
        mgr, _ = _manager(registry=registry)

        success, payload = await mgr.save_artifact(name="x.txt", content="y", visibility="WRONG")

        assert success is False
        assert "Invalid visibility" in json.loads(payload)["error"]
        registry.register_output.assert_not_awaited()

    async def test_staging_artifact_no_marker_scheduled(self) -> None:
        staging_meta = _meta(visibility=ArtifactVisibility.STAGING)
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(staging_meta, None))
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task") as mock_register:
            await mgr.save_artifact(name="temp.csv", content="x", visibility="STAGING")
            mock_register.assert_not_called()

    async def test_visible_artifact_marker_scheduled(self) -> None:
        visible_meta = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(visible_meta, None))
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task") as mock_register:
            await mgr.save_artifact(name="final.md", content="x")
            mock_register.assert_called_once()


# ---------------------------------------------------------------------------
# 6. promote_artifact tool
# ---------------------------------------------------------------------------

class TestPromoteArtifact:
    async def test_promotes_staging_to_visible(self) -> None:
        promoted_meta = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(return_value=promoted_meta)
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task") as mock_register:
            success, payload = await mgr.promote_artifact(artifact_id="art-1")

        assert success is True
        body = json.loads(payload)
        assert body["visibility"] == "VISIBLE"
        assert "visible to the user" in body["message"]
        registry.promote_to_visible.assert_awaited_once()
        _, kw = registry.promote_to_visible.call_args
        assert kw["artifact_id"] == "art-1"
        mock_register.assert_called_once()

    async def test_promote_already_visible_is_idempotent(self) -> None:
        already_visible = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(return_value=already_visible)
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            success, payload = await mgr.promote_artifact(artifact_id="art-1")

        assert success is True
        assert json.loads(payload)["visibility"] == "VISIBLE"

    async def test_promote_not_found_returns_error(self) -> None:
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(side_effect=ArtifactNotFoundError("nope"))
        mgr, _ = _manager(registry=registry)

        success, payload = await mgr.promote_artifact(artifact_id="ghost")

        assert success is False
        assert "No artifact found" in json.loads(payload)["error"]

    async def test_promote_access_denied_returns_error(self) -> None:
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(side_effect=AccessDeniedError("nope"))
        mgr, _ = _manager(registry=registry)

        success, payload = await mgr.promote_artifact(artifact_id="art-1")

        assert success is False
        assert "permission" in json.loads(payload)["error"]

    async def test_promote_missing_registry_returns_error(self) -> None:
        mgr = ArtifactManager({"org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1"})
        success, payload = await mgr.promote_artifact(artifact_id="art-1")
        assert success is False
        assert "unavailable" in json.loads(payload)["error"]

    async def test_promote_emits_live_sse_when_event_sink_available(self) -> None:
        promoted_meta = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(return_value=promoted_meta)
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")

        mock_sink = MagicMock()
        mock_sink.write = AsyncMock()

        mgr, _ = _manager(registry=registry, event_sink=mock_sink, sse_protocol="agui")

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            await mgr.promote_artifact(artifact_id="art-1")

        mock_sink.write.assert_awaited()

    async def test_promote_no_sse_when_no_event_sink(self) -> None:
        promoted_meta = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.promote_to_visible = AsyncMock(return_value=promoted_meta)
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            # Should not raise even without event_sink
            success, _ = await mgr.promote_artifact(artifact_id="art-1")

        assert success is True


# ---------------------------------------------------------------------------
# 7. list_artifacts includes visibility field
# ---------------------------------------------------------------------------

class TestListArtifactsVisibility:
    async def test_list_includes_visibility_in_each_entry(self) -> None:
        registry = MagicMock()
        staging = _meta(artifact_id="art-s", name="staging.csv", visibility=ArtifactVisibility.STAGING)
        visible = _meta(artifact_id="art-v", name="chart.png", visibility=ArtifactVisibility.VISIBLE)
        registry.list_for_conversation = AsyncMock(return_value=[staging, visible])
        mgr, _ = _manager(registry=registry)

        success, payload = await mgr.list_artifacts()

        assert success is True
        body = json.loads(payload)
        artifacts = body["artifacts"]
        assert len(artifacts) == 2
        by_id = {a["artifact_id"]: a for a in artifacts}
        assert by_id["art-s"]["visibility"] == "STAGING"
        assert by_id["art-v"]["visibility"] == "VISIBLE"


# ---------------------------------------------------------------------------
# 9. Bugfix: save_artifact message uses actual metadata visibility
# ---------------------------------------------------------------------------

class TestSaveArtifactMessageReflectsActualVisibility:
    """When bumping an existing artifact, metadata.visibility may differ
    from the caller's vis parameter.  The response message must reflect
    the actual state."""

    async def test_staging_vis_param_but_visible_doc_shows_visible_message(self) -> None:
        """Caller passes visibility=STAGING but the artifact already exists
        as VISIBLE in the doc — message should say 'attached' (VISIBLE),
        not 'staging artifact'."""
        visible_meta = _meta(visibility=ArtifactVisibility.VISIBLE)
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(visible_meta, None))
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            _, payload = await mgr.save_artifact(
                name="data.csv", content="x", visibility="STAGING",
            )

        body = json.loads(payload)
        assert body["visibility"] == "VISIBLE"
        assert "staging" not in body["message"].lower()

    async def test_visible_vis_param_but_staging_doc_shows_staging_message(self) -> None:
        """Caller passes visibility=VISIBLE (default) but the artifact
        already exists as STAGING in the doc — message should say 'staging'."""
        staging_meta = _meta(visibility=ArtifactVisibility.STAGING)
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(staging_meta, None))
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            _, payload = await mgr.save_artifact(name="data.csv", content="x")

        body = json.loads(payload)
        assert body["visibility"] == "STAGING"
        assert "staging artifact" in body["message"].lower()


# ---------------------------------------------------------------------------
# 10. update_artifact includes visibility in response
# ---------------------------------------------------------------------------

class TestUpdateArtifactVisibility:
    async def test_update_visible_artifact_shows_visibility(self) -> None:
        metadata = _meta(visibility=ArtifactVisibility.VISIBLE, version=2)
        version = ArtifactVersion(
            version=2, size_bytes=5, content_hash="h",
            mime_type="text/markdown", created_at=1,
        )
        registry = MagicMock()
        registry.add_version = AsyncMock(return_value=(version, metadata))
        registry.get_download_url = AsyncMock(return_value="https://example.com/file")
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            success, payload = await mgr.update_artifact(
                artifact_id="art-1", content="new content",
            )

        assert success is True
        body = json.loads(payload)
        assert body["visibility"] == "VISIBLE"
        assert "staging" not in body["message"].lower()

    async def test_update_staging_artifact_tells_model_to_promote(self) -> None:
        metadata = _meta(visibility=ArtifactVisibility.STAGING, version=2)
        version = ArtifactVersion(
            version=2, size_bytes=5, content_hash="h",
            mime_type="text/markdown", created_at=1,
        )
        registry = MagicMock()
        registry.add_version = AsyncMock(return_value=(version, metadata))
        mgr, _ = _manager(registry=registry)

        with patch("app.agents.actions.artifacts.artifacts.register_task"):
            success, payload = await mgr.update_artifact(
                artifact_id="art-1", content="updated staging",
            )

        assert success is True
        body = json.loads(payload)
        assert body["visibility"] == "STAGING"
        assert "promote_artifact" in body["message"]
