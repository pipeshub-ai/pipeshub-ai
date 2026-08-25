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

from app.connectors.sources.gitlab import repos as repos_module
from app.connectors.sources.gitlab.repos import ReposSync

from .conftest import make_mock_connector, paged_res, failed_res


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the tree-page retry backoff so abort-path tests stay fast."""
    monkeypatch.setattr(repos_module, "_GITLAB_TREE_PAGE_RETRY_BACKOFF_SECONDS", 0)

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

    async def test_no_tree_data_returns_false(self) -> None:
        """An empty response body is a failed request, not an empty repo: the
        checkpoint must not advance past it."""
        c = make_mock_connector()
        repos = ReposSync(c)

        res = MagicMock()
        res.success = True
        res.data = None
        c.runtime.ds_call_async = AsyncMock(return_value=res)

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        assert result is False

    async def test_graphql_errors_key_returns_false(self) -> None:
        """A page carrying a top-level errors key aborts the walk."""
        c = make_mock_connector()
        repos = ReposSync(c)

        err_res = MagicMock()
        err_res.success = True
        err_res.data = json.dumps({"errors": [{"message": "forbidden"}]})
        c.runtime.ds_call_async = AsyncMock(return_value=err_res)
        repos.build_code_file_records = AsyncMock()

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)
        assert result is False

    async def test_folders_and_files_come_from_one_walk(self) -> None:
        """One page carries both node types, so a single-page repo costs exactly
        one request instead of the two full walks this replaced."""
        c = make_mock_connector()
        repos = ReposSync(c)

        page = {
            "data": {"project": {"repository": {"paginatedTree": {
                "nodes": [{
                    "trees": {"nodes": [{
                        "name": "src", "path": "src", "sha": "t1", "type": "tree",
                        "webPath": "/ns/proj/-/tree/HEAD/src", "webUrl": "https://gitlab.com/x",
                    }]},
                    "blobs": {"nodes": [{
                        "name": "a.py", "path": "src/a.py", "sha": "b1", "type": "blob",
                        "webPath": "/ns/proj/-/blob/HEAD/src/a.py", "webUrl": "https://gitlab.com/y",
                    }]},
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": ""},
            }}}}
        }
        res = MagicMock(success=True, error=None, data=json.dumps(page))
        c.runtime.ds_call_async = AsyncMock(return_value=res)
        repos._process_records = AsyncMock()
        repos.build_code_file_records = AsyncMock()

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)

        assert result is True
        assert c.runtime.ds_call_async.await_count == 1
        repos._process_records.assert_awaited_once()
        repos.build_code_file_records.assert_awaited_once()
        assert repos.build_code_file_records.await_args.args[0][0]["path"] == "src/a.py"

    async def test_persist_failure_withholds_the_checkpoint(self) -> None:
        """A swallowed write must not report success. The walk itself completed,
        but if the records never landed and the checkpoint advances, the next run
        goes incremental from that SHA and they are never retried."""
        c = make_mock_connector()
        repos = ReposSync(c)

        page = {
            "data": {"project": {"repository": {"paginatedTree": {
                "nodes": [{
                    "trees": {"nodes": [{
                        "name": "src", "path": "src", "sha": "t1", "type": "tree",
                        "webPath": "/ns/proj/-/tree/HEAD/src", "webUrl": "https://gitlab.com/x",
                    }]},
                    "blobs": {"nodes": [{
                        "name": "a.py", "path": "src/a.py", "sha": "b1", "type": "blob",
                        "webPath": "/ns/proj/-/blob/HEAD/src/a.py", "webUrl": "https://gitlab.com/y",
                    }]},
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": ""},
            }}}}
        }
        res = MagicMock(success=True, error=None, data=json.dumps(page))
        c.runtime.ds_call_async = AsyncMock(return_value=res)
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("db down"))

        assert await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH) is False


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
        assert updates[0].record.extension == "py"
        assert updates[0].record.to_kafka_record()["extension"] == "py"

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

    async def test_blob_record_leaves_version_to_the_processor(self) -> None:
        """The connector always emits 0; _process_record carries the stored version forward."""
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock()

        await repos.build_code_file_records(
            [self._blob_node("src/main.py")], _PROJECT_ID, _PROJECT_PATH
        )
        updates = repos._process_records.call_args.args[0]
        assert updates[0].record.version == 0


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


# ===========================================================================
# Full sync failure does not advance checkpoint
# ===========================================================================


class TestFullSyncFailureNoCheckpoint:
    async def test_full_sync_failure_skips_checkpoint_update(self) -> None:
        """When full sync fails (returns False), checkpoint not advanced."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        repos = ReposSync(c)
        repos._get_code_repo_checkpoint = AsyncMock(return_value=None)
        repos._sync_repo_full = AsyncMock(return_value=False)
        repos._update_code_repo_checkpoint = AsyncMock()

        # Branch call succeeds
        branch_data = MagicMock()
        branch_data.commit = {"id": "abc123"}
        br = MagicMock(success=True, data=branch_data, error=None)
        c.runtime.ds_call = AsyncMock(return_value=br)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        repos._update_code_repo_checkpoint.assert_not_called()


# ===========================================================================
# _fetch_entries_page — error path
# ===========================================================================


class TestFetchEntriesPageRetry:
    async def test_transient_failure_then_success_continues(self) -> None:
        """One flaky page must not abort a walk of a huge repository: the page
        is retried and the walk carries on."""
        c = make_mock_connector()
        repos = ReposSync(c)

        page = {
            "data": {"project": {"repository": {"paginatedTree": {
                "nodes": [{"trees": {"nodes": []}, "blobs": {"nodes": [{
                    "name": "a.py", "path": "a.py", "sha": "b1", "type": "blob",
                    "webPath": "/ns/proj/-/blob/HEAD/a.py", "webUrl": "https://gitlab.com/y",
                }]}}],
                "pageInfo": {"hasNextPage": False, "endCursor": ""},
            }}}}
        }
        ok_res = MagicMock(success=True, error=None, data=json.dumps(page))
        fail_res_ = MagicMock(success=False, error="ReadTimeout: ", data=None)
        c.runtime.ds_call_async = AsyncMock(side_effect=[fail_res_, ok_res])
        repos._process_records = AsyncMock()
        repos.build_code_file_records = AsyncMock()

        result = await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)

        assert result is True
        assert c.runtime.ds_call_async.await_count == 2
        repos.build_code_file_records.assert_awaited_once()

    async def test_graphql_errors_abort_without_retry(self) -> None:
        """A GraphQL ``errors`` payload is semantic (permissions / query shape);
        retrying cannot change it, so the walk aborts on the first attempt."""
        c = make_mock_connector()
        repos = ReposSync(c)

        err_res = MagicMock(success=True, error=None,
                            data=json.dumps({"errors": [{"message": "forbidden"}]}))
        c.runtime.ds_call_async = AsyncMock(return_value=err_res)

        kind, trees, blobs, page_info = await repos._fetch_entries_page(_PROJECT_PATH, _PROJECT_ID, "")
        assert kind == "abort"
        assert c.runtime.ds_call_async.await_count == 1

    async def test_persistent_transport_failure_aborts_after_max_attempts(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)

        c.runtime.ds_call_async = AsyncMock(return_value=MagicMock(success=False, error="boom", data=None))

        kind, trees, blobs, page_info = await repos._fetch_entries_page(_PROJECT_PATH, _PROJECT_ID, "")
        assert kind == "abort"
        assert c.runtime.ds_call_async.await_count == repos_module._GITLAB_TREE_PAGE_MAX_ATTEMPTS


class TestFetchEntriesPageError:
    async def test_ds_call_async_raises_returns_abort(self) -> None:
        """When ds_call_async raises, _fetch_entries_page returns abort."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        repos = ReposSync(c)

        c.runtime.ds_call_async = AsyncMock(side_effect=Exception("GraphQL error"))

        kind, trees, blobs, page_info = await repos._fetch_entries_page(_PROJECT_PATH, _PROJECT_ID, "")
        assert kind == "abort"
        assert trees == []
        assert blobs == []

    async def test_api_failure_returns_abort(self) -> None:
        """When ds_call_async returns failure, _fetch_entries_page returns abort."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        repos = ReposSync(c)

        fail_res = MagicMock(success=False, data=None, error="network error")
        c.runtime.ds_call_async = AsyncMock(return_value=fail_res)

        kind, trees, blobs, page_info = await repos._fetch_entries_page(_PROJECT_PATH, _PROJECT_ID, "")
        assert kind == "abort"


# ===========================================================================
# Module-level static helpers
# ===========================================================================


class TestModuleLevelHelpers:
    def test_branch_head_commit_sha_dict_path(self) -> None:
        """_branch_head_commit_sha returns SHA when commit is a dict."""
        from app.connectors.sources.gitlab.repos import _branch_head_commit_sha
        branch_data = MagicMock()
        branch_data.commit = {"id": "abc123"}
        result = _branch_head_commit_sha(branch_data)
        assert result == "abc123"

    def test_branch_head_commit_sha_none_commit(self) -> None:
        """_branch_head_commit_sha returns None when commit is None."""
        from app.connectors.sources.gitlab.repos import _branch_head_commit_sha
        branch_data = MagicMock()
        branch_data.commit = None
        result = _branch_head_commit_sha(branch_data)
        assert result is None

    def test_gitlab_timestamp_to_ms_valid(self) -> None:
        """ISO timestamp string is converted to epoch ms."""
        from app.connectors.sources.gitlab.repos import _gitlab_timestamp_to_ms
        result = _gitlab_timestamp_to_ms("2024-01-01T00:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_gitlab_timestamp_to_ms_none(self) -> None:
        """None input returns None."""
        from app.connectors.sources.gitlab.repos import _gitlab_timestamp_to_ms
        result = _gitlab_timestamp_to_ms(None)
        assert result is None

    def test_gitlab_timestamp_to_ms_datetime(self) -> None:
        """datetime object is converted to epoch ms."""
        from datetime import datetime, timezone
        from app.connectors.sources.gitlab.repos import _gitlab_timestamp_to_ms
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _gitlab_timestamp_to_ms(dt)
        assert isinstance(result, int)
        assert result == 1704067200000

    def test_repo_path_from_blob_web_url_valid(self) -> None:
        """Valid blob URL returns repo-relative path."""
        from app.connectors.sources.gitlab.repos import _repo_path_from_blob_web_url
        url = "https://gitlab.com/ns/proj/-/blob/main/src/file.py"
        result = _repo_path_from_blob_web_url(url)
        assert result == "src/file.py"

    def test_repo_path_from_blob_web_url_none(self) -> None:
        """None URL returns None."""
        from app.connectors.sources.gitlab.repos import _repo_path_from_blob_web_url
        assert _repo_path_from_blob_web_url(None) is None

    def test_pagination_stop_on_no_next(self) -> None:
        """_should_continue_repo_tree_pagination returns False when hasNextPage=False."""
        from app.connectors.sources.gitlab.repos import _should_continue_repo_tree_pagination
        cont, cursor = _should_continue_repo_tree_pagination({"hasNextPage": False, "endCursor": "abc"})
        assert cont is False

    def test_pagination_continue_on_next(self) -> None:
        """_should_continue_repo_tree_pagination returns True when hasNextPage=True."""
        from app.connectors.sources.gitlab.repos import _should_continue_repo_tree_pagination
        cont, cursor = _should_continue_repo_tree_pagination({"hasNextPage": True, "endCursor": "cursor1"})
        assert cont is True
        assert cursor == "cursor1"

    def test_pagination_continues_on_a_page_with_no_folders(self) -> None:
        """A page of files and zero folders is ordinary mid-walk output, not the
        end of the repository — the walk must not stop there."""
        from app.connectors.sources.gitlab.repos import _should_continue_repo_tree_pagination
        cont, cursor = _should_continue_repo_tree_pagination({"hasNextPage": True, "endCursor": "cursor2"})
        assert cont is True
        assert cursor == "cursor2"
