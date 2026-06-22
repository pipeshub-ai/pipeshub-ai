"""Unit tests for code call-graph post-sync hook integration.

Tests cover:
- _cancel_code_graph_build: noop when no task; cancels in-flight task
- _schedule_code_graph_build_after_sync: creates a task; does not reschedule if running
- _run_code_graph_build_after_sync: no projects → no builders; one project → runs IR + CGB;
  no record group → skipped; project resolution exception → handled gracefully;
  ImportResolver built with correct args
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector(projects=None, fake_rg=None):
    """Return a minimal GitLabConnector-like object for testing the post-sync hook."""
    from app.models.entities import RecordGroup

    connector = MagicMock()
    connector.connector_id = "conn-test"
    connector.logger = MagicMock()
    connector._code_graph_build_task = None
    connector._code_file_timestamp_backfill_task = None

    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.org_id = "org-test"

    # Build a tx_store mock
    tx_store = AsyncMock()
    tx_store.graph_provider = AsyncMock()
    _rg = fake_rg if fake_rg is not None else MagicMock(spec=RecordGroup, id="rg-internal-1")
    tx_store.get_record_group_by_external_id = AsyncMock(return_value=_rg)

    # Each call to transaction() must return a *new* async context manager.
    # Using side_effect=lambda ensures a fresh generator each time.
    @asynccontextmanager
    async def _fake_ctx():
        yield tx_store

    connector.data_store_provider = MagicMock()
    connector.data_store_provider.transaction = MagicMock(side_effect=lambda: _fake_ctx())

    if projects is None:
        projects = []
    connector._resolve_projects_with_filters = AsyncMock(return_value=projects)

    # Bind real methods from the actual class
    from app.connectors.sources.gitlab.connector import GitLabConnector

    connector._cancel_code_graph_build = GitLabConnector._cancel_code_graph_build.__get__(
        connector, type(connector)
    )
    connector._schedule_code_graph_build_after_sync = (
        GitLabConnector._schedule_code_graph_build_after_sync.__get__(
            connector, type(connector)
        )
    )
    connector._run_code_graph_build_after_sync = (
        GitLabConnector._run_code_graph_build_after_sync.__get__(
            connector, type(connector)
        )
    )
    return connector, tx_store


def _make_project(project_id: int = 1) -> MagicMock:
    proj = MagicMock()
    proj.id = project_id
    return proj


# ---------------------------------------------------------------------------
# Cancel / schedule tests
# ---------------------------------------------------------------------------


class TestCodeGraphBuildCancelAndSchedule:
    @pytest.mark.asyncio
    async def test_cancel_noop_when_no_task(self):
        connector, _ = _make_connector()
        await connector._cancel_code_graph_build()
        assert connector._code_graph_build_task is None

    @pytest.mark.asyncio
    async def test_cancel_cancels_running_task(self):
        connector, _ = _make_connector()

        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        connector._code_graph_build_task = task
        await connector._cancel_code_graph_build()
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_schedule_creates_task(self):
        connector, _ = _make_connector()
        assert connector._code_graph_build_task is None
        connector._schedule_code_graph_build_after_sync()
        task = connector._code_graph_build_task
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_schedule_does_not_reschedule_if_already_running(self):
        connector, _ = _make_connector()
        connector._schedule_code_graph_build_after_sync()
        task1 = connector._code_graph_build_task
        connector._schedule_code_graph_build_after_sync()
        task2 = connector._code_graph_build_task
        assert task1 is task2
        task1.cancel()
        await asyncio.gather(task1, return_exceptions=True)


# ---------------------------------------------------------------------------
# _run_code_graph_build_after_sync tests
# ---------------------------------------------------------------------------

# The patch target is the connector module itself since ImportResolver and
# CallGraphBuilder are imported at the top of the connector module.
_IR_PATCH = "app.connectors.sources.gitlab.connector.ImportResolver"
_CG_PATCH = "app.connectors.sources.gitlab.connector.CallGraphBuilder"


class TestRunCodeGraphBuildAfterSync:
    @pytest.mark.asyncio
    async def test_no_projects_nothing_called(self):
        connector, _ = _make_connector(projects=[])
        with patch(_IR_PATCH) as MockIR, patch(_CG_PATCH) as MockCG:
            await connector._run_code_graph_build_after_sync()
        MockIR.assert_not_called()
        MockCG.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_project_runs_import_resolver_and_call_graph_builder(self):
        project = _make_project(42)
        connector, _ = _make_connector(projects=[project])

        mock_ir_instance = AsyncMock()
        mock_ir_instance.resolve_all = AsyncMock(return_value={"edges_created": 3})
        mock_cg_instance = AsyncMock()
        mock_cg_instance.resolve_all = AsyncMock(return_value={"edges_created": 5})

        with patch(_IR_PATCH, return_value=mock_ir_instance), \
             patch(_CG_PATCH, return_value=mock_cg_instance):
            await connector._run_code_graph_build_after_sync()

        mock_ir_instance.resolve_all.assert_awaited_once()
        mock_cg_instance.resolve_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_project_with_no_record_group_skipped(self):
        project = _make_project(99)
        connector, tx_store = _make_connector(projects=[project])
        tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)

        with patch(_IR_PATCH) as MockIR, patch(_CG_PATCH) as MockCG:
            await connector._run_code_graph_build_after_sync()

        MockIR.assert_not_called()
        MockCG.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_projects_exception_handled_gracefully(self):
        connector, _ = _make_connector()
        connector._resolve_projects_with_filters = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        try:
            await connector._run_code_graph_build_after_sync()
        except RuntimeError:
            pytest.fail("_run_code_graph_build_after_sync propagated exception")

    @pytest.mark.asyncio
    async def test_import_resolver_built_with_correct_args(self):
        project = _make_project(7)
        from app.models.entities import RecordGroup

        rg = MagicMock(spec=RecordGroup)
        rg.id = "rg-007"
        connector, tx_store = _make_connector(projects=[project], fake_rg=rg)

        mock_ir = AsyncMock()
        mock_ir.resolve_all = AsyncMock(return_value={})
        mock_cg = AsyncMock()
        mock_cg.resolve_all = AsyncMock(return_value={})

        with patch(_IR_PATCH, return_value=mock_ir) as IR_cls, \
             patch(_CG_PATCH, return_value=mock_cg):
            await connector._run_code_graph_build_after_sync()

        IR_cls.assert_called_once()
        kwargs = IR_cls.call_args.kwargs
        assert kwargs["org_id"] == "org-test"
        assert kwargs["record_group_id"] == "rg-007"
        assert kwargs["connector_id"] == "conn-test"
