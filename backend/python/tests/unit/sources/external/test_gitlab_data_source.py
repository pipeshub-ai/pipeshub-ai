"""Unit tests for ``app.sources.external.gitlab.gitlab_data_source.GitLabDataSource``."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.sources.client.gitlab.gitlab import GitLabResponse
from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sdk() -> MagicMock:
    # ``spec`` prevents MagicMock from auto-creating ``get_sdk``, which would
    # make ``GitLabDataSource.__init__`` treat the SDK as a wrapper client.
    sdk = MagicMock(spec=["auth", "user", "projects", "users", "groups", "http_request"])
    sdk.user = MagicMock(id=1, username="tester")
    sdk.projects = MagicMock()
    sdk.users = MagicMock()
    sdk.groups = MagicMock()
    return sdk


@pytest.fixture
def data_source(mock_sdk: MagicMock) -> GitLabDataSource:
    return GitLabDataSource(mock_sdk, base_url="https://gitlab.example.com/")


def _make_wrapper_client(sdk: MagicMock, token: str = "test-token") -> MagicMock:
    wrapper = MagicMock()
    wrapper.get_sdk.return_value = sdk
    wrapper.get_token.return_value = token
    return wrapper


# ---------------------------------------------------------------------------
# Construction and helpers
# ---------------------------------------------------------------------------


class TestGitLabDataSourceInit:
    def test_accepts_raw_sdk(self, mock_sdk: MagicMock) -> None:
        ds = GitLabDataSource(mock_sdk)
        assert ds._sdk is mock_sdk
        assert ds.token is None
        assert ds._base_url == "https://gitlab.com"

    def test_accepts_wrapper_client(self, mock_sdk: MagicMock) -> None:
        wrapper = _make_wrapper_client(mock_sdk, token="abc123")
        ds = GitLabDataSource(wrapper, base_url="https://gl.local")
        assert ds._sdk is mock_sdk
        assert ds.token == "abc123"
        assert ds._base_url == "https://gl.local"

    def test_strips_trailing_slash_from_base_url(self, mock_sdk: MagicMock) -> None:
        ds = GitLabDataSource(mock_sdk, base_url="https://gitlab.example.com/")
        assert ds._base_url == "https://gitlab.example.com"


class TestParamsHelper:
    def test_drops_none_values(self) -> None:
        assert GitLabDataSource._params(a=1, b=None, c="x") == {"a": 1, "c": "x"}

    def test_drops_empty_list_and_dict(self) -> None:
        assert GitLabDataSource._params(labels=[], topics={}, search="q") == {"search": "q"}


class TestHttpClientLifecycle:
    async def test_http_client_is_lazy_and_reused(self, data_source: GitLabDataSource) -> None:
        client_a = data_source.http_client
        client_b = data_source.http_client
        assert client_a is client_b
        assert not client_a.is_closed

    async def test_aclose_closes_client(self, data_source: GitLabDataSource) -> None:
        client = data_source.http_client
        await data_source.aclose()
        assert client.is_closed
        assert data_source._http_client is None


# ---------------------------------------------------------------------------
# User / project reads
# ---------------------------------------------------------------------------


class TestGetUser:
    def test_returns_authenticated_user(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        resp = data_source.get_user()
        mock_sdk.auth.assert_called_once()
        assert resp.success is True
        assert resp.data is mock_sdk.user

    def test_returns_user_by_id(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        user = MagicMock(id=42)
        mock_sdk.users.get.return_value = user
        resp = data_source.get_user(42)
        mock_sdk.users.get.assert_called_once_with(42)
        assert resp.success is True
        assert resp.data is user

    def test_returns_error_on_exception(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        mock_sdk.auth.side_effect = RuntimeError("auth failed")
        resp = data_source.get_user()
        assert resp.success is False
        assert resp.error == "auth failed"


class TestGetProject:
    def test_returns_project(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock(id=99)
        mock_sdk.projects.get.return_value = project
        resp = data_source.get_project("group/repo")
        mock_sdk.projects.get.assert_called_once_with("group/repo")
        assert resp.success is True
        assert resp.data is project

    def test_returns_error_instead_of_raising(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        mock_sdk.projects.get.side_effect = Exception("404 Project Not Found")
        resp = data_source.get_project("missing/repo")
        assert resp.success is False
        assert "404" in resp.error


class TestListProjects:
    def test_forwards_filters_and_returns_list(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        projects = [MagicMock(id=1), MagicMock(id=2)]
        mock_sdk.projects.list.return_value = projects
        resp = data_source.list_projects(
            search="foo",
            membership=True,
            min_access_level=10,
            get_all=True,
            iterator=True,
            pagination="keyset",
            order_by="id",
            sort="asc",
        )
        mock_sdk.projects.list.assert_called_once_with(
            get_all=True,
            iterator=True,
            search="foo",
            membership=True,
            min_access_level=10,
            pagination="keyset",
            order_by="id",
            sort="asc",
        )
        assert resp.success is True
        assert resp.data == projects

    def test_returns_error_on_exception(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        mock_sdk.projects.list.side_effect = RuntimeError("rate limited")
        resp = data_source.list_projects()
        assert resp.success is False
        assert resp.error == "rate limited"


class TestListGroupProjects:
    def test_lists_projects_for_group(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        group = MagicMock()
        projects = [MagicMock(id=7)]
        group.projects.list.return_value = projects
        mock_sdk.groups.get.return_value = group

        resp = data_source.list_group_projects(
            "my-group",
            include_subgroups=False,
            search="api",
            iterator=True,
            simple=True,
        )

        mock_sdk.groups.get.assert_called_once_with("my-group", lazy=True)
        group.projects.list.assert_called_once_with(
            get_all=None,
            iterator=True,
            include_subgroups=False,
            search="api",
            simple=True,
        )
        assert resp.success is True
        assert resp.data == projects


class TestUpdateProject:
    def test_saves_only_when_fields_change(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        mock_sdk.projects.get.return_value = project

        unchanged = data_source.update_project(123)
        project.save.assert_not_called()
        assert unchanged.success is True
        assert unchanged.data is project

        changed = data_source.update_project(123, name="new-name", description="desc")
        assert getattr(project, "name") == "new-name"
        assert getattr(project, "description") == "desc"
        project.save.assert_called_once()
        assert changed.success is True


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


class TestCompareCommits:
    def test_returns_compare_payload(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        compare = {"diffs": [{"old_path": "a.py"}]}
        project.repository_compare.return_value = compare
        mock_sdk.projects.get.return_value = project

        resp = data_source.compare_commits("group/repo", "abc", "def", straight=True)

        project.repository_compare.assert_called_once_with("abc", "def", straight=True)
        assert resp.success is True
        assert resp.data == compare


class TestListCommitsForPath:
    def test_empty_path_returns_zero_count(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        project.commits.path = "/projects/1/repository/commits"
        mock_sdk.projects.get.return_value = project

        first_resp = MagicMock()
        first_resp.raise_for_status.return_value = None
        first_resp.json.return_value = []
        first_resp.headers = {"X-Total-Pages": "1", "X-Total": "0"}
        mock_sdk.http_request.return_value = first_resp

        resp = data_source.list_commits_for_path("group/repo", "README.md", ref_name="main")

        assert resp.success is True
        assert resp.data == {
            "newest_committed_date": None,
            "oldest_committed_date": None,
            "commit_count": 0,
        }

    def test_single_page_commits(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        project.commits.path = "/projects/1/repository/commits"
        mock_sdk.projects.get.return_value = project

        newest = {"committed_date": "2024-01-02T00:00:00Z"}
        oldest = {"committed_date": "2024-01-01T00:00:00Z"}
        first_resp = MagicMock()
        first_resp.raise_for_status.return_value = None
        first_resp.json.return_value = [newest, oldest]
        first_resp.headers = {"X-Total-Pages": "1", "X-Total": "2"}
        mock_sdk.http_request.return_value = first_resp

        resp = data_source.list_commits_for_path("group/repo", "src/main.py")

        assert resp.success is True
        assert resp.data["newest_committed_date"] == "2024-01-02T00:00:00Z"
        assert resp.data["oldest_committed_date"] == "2024-01-01T00:00:00Z"
        assert resp.data["commit_count"] == 2

    def test_multi_page_fetches_last_page_for_oldest(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        project.commits.path = "/projects/1/repository/commits"
        mock_sdk.projects.get.return_value = project

        first_resp = MagicMock()
        first_resp.raise_for_status.return_value = None
        first_resp.json.return_value = [{"committed_date": "2024-06-01T00:00:00Z"}]
        first_resp.headers = {"X-Total-Pages": "3", "X-Total": "250"}

        last_resp = MagicMock()
        last_resp.raise_for_status.return_value = None
        last_resp.json.return_value = [{"committed_date": "2023-01-01T00:00:00Z"}]

        mock_sdk.http_request.side_effect = [first_resp, last_resp]

        resp = data_source.list_commits_for_path("group/repo", "docs/guide.md")

        assert mock_sdk.http_request.call_count == 2
        second_call = mock_sdk.http_request.call_args_list[1]
        assert second_call.kwargs["query_data"]["page"] == 3
        assert resp.success is True
        assert resp.data["oldest_committed_date"] == "2023-01-01T00:00:00Z"
        assert resp.data["commit_count"] == 250

    def test_non_list_payload_returns_error(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        project.commits.path = "/projects/1/repository/commits"
        mock_sdk.projects.get.return_value = project

        first_resp = MagicMock()
        first_resp.raise_for_status.return_value = None
        first_resp.json.return_value = {"message": "invalid ref"}
        mock_sdk.http_request.return_value = first_resp

        resp = data_source.list_commits_for_path("group/repo", "missing.txt", ref_name="nope")

        assert resp.success is False
        assert resp.error == "invalid ref"


class TestListIssues:
    def test_forwards_datetime_filters(self, data_source: GitLabDataSource, mock_sdk: MagicMock) -> None:
        project = MagicMock()
        issues = [MagicMock(iid=1)]
        project.issues.list.return_value = issues
        mock_sdk.projects.get.return_value = project

        updated_after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        updated_before = datetime(2024, 2, 1, tzinfo=timezone.utc)

        resp = data_source.list_issues(
            "group/repo",
            state="opened",
            labels=["bug"],
            updated_after=updated_after,
            updated_before=updated_before,
            get_all=True,
        )

        project.issues.list.assert_called_once_with(
            get_all=True,
            state="opened",
            labels=["bug"],
            updated_after=updated_after,
            updated_before=updated_before,
        )
        assert resp.success is True
        assert resp.data == issues


# ---------------------------------------------------------------------------
# GraphQL and direct HTTP
# ---------------------------------------------------------------------------


class TestGraphQLMethods:
    async def test_get_repo_tree_g_posts_to_graphql(self, data_source: GitLabDataSource) -> None:
        data_source.token = "gql-token"
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b'{"data":{"project":{"name":"repo"}}}'

        with patch.object(data_source.http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            resp = await data_source.get_repo_tree_g("group/repo", ref="main", after_cursor="cursor-1")

        mock_post.assert_awaited_once()
        call_kwargs = mock_post.await_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer gql-token"
        assert mock_post.await_args.args[0] == "https://gitlab.example.com/api/graphql"
        variables = call_kwargs["json"]["variables"]
        assert variables == {"fullPath": "group/repo", "branch": "main", "afterCursor": "cursor-1"}
        assert resp.success is True
        assert resp.data == b'{"data":{"project":{"name":"repo"}}}'

    async def test_get_file_tree_g_returns_error_on_failure(self, data_source: GitLabDataSource) -> None:
        data_source.token = "gql-token"
        with patch.object(
            data_source.http_client,
            "post",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network down"),
        ):
            resp = await data_source.get_file_tree_g("group/repo", ref="main")

        assert resp.success is False
        assert resp.error == "network down"


class TestDirectHttp:
    async def test_get_img_bytes_success(self, data_source: GitLabDataSource) -> None:
        data_source.token = "img-token"
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b"\x89PNG"

        with patch.object(data_source.http_client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            resp = await data_source.get_img_bytes("https://gitlab.example.com/uploads/image.png")

        mock_get.assert_awaited_once()
        headers = mock_get.await_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer img-token"
        assert resp.success is True
        assert resp.data == b"\x89PNG"

    async def test_get_img_bytes_http_status_error(self, data_source: GitLabDataSource) -> None:
        data_source.token = "img-token"
        request = httpx.Request("GET", "https://gitlab.example.com/uploads/missing.png")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("not found", request=request, response=response)

        with patch.object(data_source.http_client, "get", new_callable=AsyncMock, side_effect=error):
            resp = await data_source.get_img_bytes("https://gitlab.example.com/uploads/missing.png")

        assert resp.success is False
        assert "HTTP 404" in resp.error

    async def test_get_attachment_files_content_streams_chunks(self, data_source: GitLabDataSource) -> None:
        data_source.token = "stream-token"

        async def _aiter_bytes(chunk_size: int = 65536):
            yield b"part-1"
            yield b"part-2"

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = _aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None

        with patch.object(data_source.http_client, "stream", return_value=mock_stream_ctx) as mock_stream:
            chunks = [
                chunk
                async for chunk in data_source.get_attachment_files_content(
                    "https://gitlab.example.com/uploads/file.bin"
                )
            ]

        mock_stream.assert_called_once()
        headers = mock_stream.call_args.kwargs["headers"]
        assert headers["Accept"] == "application/octet-stream"
        assert chunks == [b"part-1", b"part-2"]
