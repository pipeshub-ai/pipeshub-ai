"""Unit tests for `ConfluenceFilterAdapter`: filter-only CQL validation
(including the personal-space regression), identity substitution,
`execute()` against `search_by_cql`, and label discovery as the custom-
field surface."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.filtered_search.adapter import Pagination
from app.agents.actions.filtered_search.adapters.confluence import ConfluenceFilterAdapter


def _adapter() -> ConfluenceFilterAdapter:
    return ConfluenceFilterAdapter()


def _page() -> Pagination:
    return Pagination(limit=50)


def test_capabilities_declares_confluence_specifics() -> None:
    caps = ConfluenceFilterAdapter.capabilities()
    assert caps.connector_type == "CONFLUENCE"
    assert caps.record_group_noun == "space"
    assert caps.people_coverage_note is not None


class TestValidateQuery:
    def test_empty_query_is_rejected(self) -> None:
        assert ConfluenceFilterAdapter.validate_query("") is not None

    def test_filter_only_query_is_accepted(self) -> None:
        assert ConfluenceFilterAdapter.validate_query('space = "ENG" AND type = "page"') is None

    def test_personal_space_key_is_accepted_not_flagged_as_text_operator(self) -> None:
        """Regression: personal space keys begin with '~' (visible via
        list_filter_values) — a naive 'any bare ~' check would reject every
        valid personal-space query. This must pass."""
        query = 'space = ~712020944a176f15f7423c902e3874364c1f13 AND type = "page"'
        assert ConfluenceFilterAdapter.validate_query(query) is None

    def test_personal_space_key_with_in_clause_is_accepted(self) -> None:
        query = 'space in (~712020944a176f15f7423c902e3874364c1f13, "ENG")'
        assert ConfluenceFilterAdapter.validate_query(query) is None

    def test_sitesearch_text_operator_is_rejected(self) -> None:
        error = ConfluenceFilterAdapter.validate_query('siteSearch ~ "runbook"')
        assert error is not None
        assert "text-match" in error

    def test_title_text_operator_is_rejected(self) -> None:
        assert ConfluenceFilterAdapter.validate_query('title ~ "onboarding"') is not None

    def test_text_field_negated_operator_is_rejected(self) -> None:
        assert ConfluenceFilterAdapter.validate_query('text !~ "draft"') is not None

    def test_tilde_inside_quoted_literal_is_not_flagged(self) -> None:
        assert ConfluenceFilterAdapter.validate_query('label = "foo~bar"') is None


class TestIdentitySubstitution:
    def test_has_self_reference_detects_current_user(self) -> None:
        assert ConfluenceFilterAdapter.has_self_reference("contributor = currentUser()") is True
        assert ConfluenceFilterAdapter.has_self_reference('space = "ENG"') is False

    def test_substitute_identity_replaces_current_user(self) -> None:
        query = ConfluenceFilterAdapter.substitute_identity("contributor = currentUser()", "acc-1")
        assert query == 'contributor = "acc-1"'


class TestExecute:
    async def test_execute_maps_results_to_filtered_records(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 200
        response.json.return_value = {
            "results": [
                {"content": {"id": "123", "title": "Runbook"}},
                {"content": {}},  # no id -> skipped
            ],
            "totalSize": 5,
        }
        client.search_by_cql = AsyncMock(return_value=response)

        universe = await _adapter().execute('space = "ENG"', client, page=_page())
        assert universe.connector_type == "CONFLUENCE"
        assert len(universe.records) == 1
        assert universe.records[0].external_id == "123"
        assert universe.records[0].name == "Runbook"
        assert universe.truncated is True
        client.search_by_cql.assert_awaited_once()
        assert client.search_by_cql.call_args.kwargs["cql"] == 'space = "ENG"'

    async def test_execute_raises_on_non_200(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 403
        client.search_by_cql = AsyncMock(return_value=response)
        with pytest.raises(RuntimeError):
            await _adapter().execute('space = "ENG"', client, page=_page())


class TestDiscoverCustomFields:
    async def test_discover_custom_fields_returns_label_catalog(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 200
        response.json.return_value = {"results": [{"name": "roadmap"}, {"name": "internal"}]}
        client.get_labels = AsyncMock(return_value=response)

        fields = await _adapter().discover_custom_fields(client)
        assert len(fields) == 1
        assert fields[0].field_id == "label"
        assert fields[0].allowed_values == ["internal", "roadmap"]

    async def test_discover_custom_fields_returns_empty_on_error_status(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 500
        client.get_labels = AsyncMock(return_value=response)
        assert await _adapter().discover_custom_fields(client) == []
