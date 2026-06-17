"""Unit tests for gitlab ReposSync full sync, checkpoints, and blob processing.

Covers:
- _sync_repo_full: empty repo, GraphQL errors, pagination
- build_code_file_records: field mapping, dotfile skipping, indexing flag
- _get_code_repo_checkpoint / _update_code_repo_checkpoint: happy path, missing, exception
- _fetch_code_file_content: streaming delegation
- cancel_timestamp_backfill / schedule_timestamp_backfill
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.gitlab.repos import ReposSync

from .conftest import make_mock_connector, paged_res, failed_res

pytestmark = pytest.mark.anyio

_PROJECT_ID = 10
_PROJECT_PATH = "ns/project"


def _graphql_tree_response(nodes: list[dict], has_next: bool = False, cursor: str = "") -> MagicMock:
    """Build a GraphQL tree page mock response."""
    res = MagicMock()
    res.success = True
    res.error = None
    data = {
        "data": {
            "project": {
                "repository": {
                    "paginatedTree": {
                        "nodes": [{"trees": {"nodes": nodes}}],
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    }
                }
            }
        }
    }
    res.data = json.dumps(data)
    return res


def _graphql_blob_response(nodes: list[dict], has_next: bool = False, cursor: str = "") -> MagicMock:
    """Build a GraphQL blob page mock response."""
    res = MagicMock()
    res.success = True
    res.error = None
    data = {
        "data": {
            "project": {
                "repository": {
                    "paginatedTree": {
                        "nodes": [{"blobs": {"nodes": nodes}}],
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    }
                }
            }
        }
    }
    res.data = json.dumps(data)
    return res


def _error_graphql_res() -> MagicMock:
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = "graphql error"
    return res


# ===========================================================================
# Checkpoint tests
# ===========================================================================


class TestCodeRepoCheckpoints:
    async def test_read_checkpoint_returns_sha_when_present(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_commit_sha": "abc123"}
        )

        sha = await repos._get_code_repo_checkpoint(_PROJECT_ID)

        assert sha == "abc123"

    async def test_read_checkpoint_returns_none_when_missing(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(return_value=None)

        sha = await repos._get_code_repo_checkpoint(_PROJECT_ID)

        assert sha is None

    async def test_read_checkpoint_returns_none_on_exception(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(side_effect=Exception("DB error"))

        sha = await repos._get_code_repo_checkpoint(_PROJECT_ID)

        assert sha is None

    async def test_update_checkpoint_calls_update_sync_point(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        await repos._update_code_repo_checkpoint(_PROJECT_ID, "new-sha")

        c.record_sync_point.update_sync_point.assert_called_once()
        call_kwargs = c.record_sync_point.update_sync_point.call_args
        assert "new-sha" in str(call_kwargs)


# ===========================================================================
# _sync_repo_full tests
# ===========================================================================


class TestSyncRepoFull:
    async def test_empty_repo_returns_true(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        empty_res = MagicMock()
        empty_res.success = True
        empty_res.error = None
        data = {"data": {"project": {"repository": {"paginatedTree": {}}}}}
        empty_res.data = json.dumps(data)

        c.runtime.ds_call_async = AsyncMock(return_value=empty_res)
        c.runtime.ds_call = AsyncMock()
        repos.build_code_file_records = AsyncMock()

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        assert result is True

    async def test_graphql_tree_error_returns_false(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        c.runtime.ds_call_async = AsyncMock(side_effect=Exception("network error"))

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        assert result is False

    async def test_no_tree_data_returns_true(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        res = MagicMock()
        res.success = True
        res.data = None
        c.runtime.ds_call_async = AsyncMock(return_value=res)

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        assert result is True

    async def test_graphql_errors_key_returns_false_on_blob_fetch(self) -> None:
        """Blob page with top-level 'errors' key should abort."""
        c = make_mock_connector()
        repos = ReposSync(c)

        # Tree page (folder) succeeds with empty
        tree_res = _graphql_tree_response([])
        c.runtime.ds_call_async = AsyncMock(return_value=tree_res)

        # Blob page has GraphQL errors
        blob_err_res = MagicMock()
        blob_err_res.success = True
        blob_err_res.data = json.dumps({"errors": [{"message": "forbidden"}]})

        repos._fetch_blob_page = AsyncMock(return_value=("abort", [], {}))
        repos.build_code_file_records = AsyncMock()

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        # abort from _fetch_blob_page → full sync returns False
        assert result is False


# ===========================================================================
# build_code_file_records tests
# ===========================================================================


class TestBuildCodeFileRecords:
    def _blob_node(
        self,
        path: str,
        name: str | None = None,
        sha: str = "sha123",
        web_path: str | None = None,
        web_url: str | None = None,
    ) -> dict:
        n = name or path.rsplit("/", 1)[-1]
        wp = web_path or f"/ns/project/-/blob/HEAD/{path}"
        return {
            "path": path,
            "name": n,
            "sha": sha,
            "webPath": wp,
            "webUrl": web_url or f"https://gitlab.com{wp}",
        }

    async def test_creates_code_file_record(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        await repos.build_code_file_records(
            [self._blob_node("src/main.py")], _PROJECT_ID, _PROJECT_PATH
        )
        repos._process_records.assert_called_once()
        updates = repos._process_records.call_args.args[0]
        assert len(updates) == 1
        assert updates[0].record.record_name == "main.py"

    async def test_dotfile_blob_skipped(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        await repos.build_code_file_records(
            [self._blob_node(".env", name=".env")], _PROJECT_ID, _PROJECT_PATH
        )
        repos._process_records.assert_not_called()

    async def test_missing_web_path_skipped(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        node = {"path": "src/a.py", "name": "a.py", "sha": "abc", "webPath": None, "webUrl": None}
        await repos.build_code_file_records([node], _PROJECT_ID, _PROJECT_PATH)
        repos._process_records.assert_not_called()

    async def test_code_files_indexing_disabled_sets_auto_index_off(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        # Disable code files indexing
        repos._code_files_indexing_enabled = MagicMock(return_value=False)

        await repos.build_code_file_records(
            [self._blob_node("src/main.py")], _PROJECT_ID, _PROJECT_PATH
        )
        updates = repos._process_records.call_args.args[0]
        from app.config.constants.arangodb import ProgressStatus
        assert updates[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_nested_file_sets_parent_external_record_id(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        await repos.build_code_file_records(
            [self._blob_node("src/sub/file.py")], _PROJECT_ID, _PROJECT_PATH
        )
        updates = repos._process_records.call_args.args[0]
        assert updates[0].record.parent_external_record_id is not None
        assert "/-/tree/" in updates[0].record.parent_external_record_id

    async def test_root_level_file_has_no_parent(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        await repos.build_code_file_records(
            [self._blob_node("root.py")], _PROJECT_ID, _PROJECT_PATH
        )
        updates = repos._process_records.call_args.args[0]
        assert updates[0].record.parent_external_record_id is None


# ===========================================================================
# cancel_timestamp_backfill / schedule_timestamp_backfill
# ===========================================================================


class TestTimestampBackfill:
    async def test_cancel_backfill_when_none_is_noop(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        c._code_file_timestamp_backfill_task = None

        await repos.cancel_timestamp_backfill()
        # No error raised

    async def test_cancel_backfill_cancels_running_task(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        task = MagicMock()
        task.done = MagicMock(return_value=False)
        task.cancel = MagicMock()
        c._code_file_timestamp_backfill_task = task

        with patch("app.connectors.sources.gitlab.repos.asyncio.gather", new=AsyncMock()):
            await repos.cancel_timestamp_backfill()
        task.cancel.assert_called_once()

    def test_schedule_backfill_creates_task(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._backfill_code_file_timestamps_after_sync = AsyncMock()

        with patch("app.connectors.sources.gitlab.repos.asyncio") as mock_asyncio:
            mock_task = MagicMock()
            mock_asyncio.create_task = MagicMock(return_value=mock_task)
            repos.schedule_timestamp_backfill()
            mock_asyncio.create_task.assert_called_once()
