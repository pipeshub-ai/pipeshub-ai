"""Unit tests for github_teams FiltersHelper.

Covers:
- ORG_IDS picker: search filtering, pagination, has_more.
- REPO_IDS picker: search delegates to search_repositories; no-search
  delegates to list_user_repos with in-memory pagination.
- Unsupported filter key raises ValueError to the caller (not converted into a
  failure response).
- Uninitialized data source short-circuits with a failure response.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connectors.sources.github_teams.filters import FiltersHelper
from app.connectors.core.registry.filters import SyncFilterKey

from .conftest import failed_response, make_mock_connector, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestOrgFilterOptions:
    async def test_lists_and_sorts_orgs(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="zebra", name="Zebra Corp"),
            SimpleNamespace(login="acme", name="Acme Inc"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, page=1, limit=20)

        assert resp.success is True
        assert [o.id for o in resp.options] == ["acme", "zebra"]
        assert resp.has_more is False

    async def test_search_filters_by_login_or_name(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="acme", name="Acme Inc"),
            SimpleNamespace(login="other", name="Other Org"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, search="acme")

        assert len(resp.options) == 1
        assert resp.options[0].id == "acme"

    async def test_pagination_has_more(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login=f"org{i}", name=f"Org {i}") for i in range(5)
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, page=1, limit=2)

        assert len(resp.options) == 2
        assert resp.has_more is True

    async def test_list_failure_returns_error_response(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value)

        assert resp.success is False
        assert resp.options == []


class TestRepoFilterOptions:
    async def test_search_uses_search_repositories(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(full_name="acme/widgets"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        assert resp.success is True
        called_method = c.runtime.ds_call.call_args.args[0]
        assert called_method is c.data_source.search_repositories
        assert resp.options[0].id == "acme/widgets"

    async def test_no_search_uses_list_user_repos_with_pagination(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(full_name=f"acme/repo{i}") for i in range(3)
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, page=1, limit=2)

        assert len(resp.options) == 2
        assert resp.has_more is True

    async def test_repo_list_failure_returns_error_response(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("boom")
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert resp.success is False


class TestGetFilterOptionsDispatch:
    async def test_unsupported_key_raises_value_error(self) -> None:
        """ValueError for an unknown filter key is deliberately re-raised (not
        swallowed into a generic failure response) so a caller passing a typo'd
        key gets a loud signal rather than a silently empty picker."""
        c = make_mock_connector()
        helper = FiltersHelper(c)

        with pytest.raises(ValueError):
            await helper.get_filter_options("not-a-real-key")

    async def test_uninitialized_data_source_short_circuits(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value)

        assert resp.success is False
        assert "not initialized" in (resp.message or "").lower()
