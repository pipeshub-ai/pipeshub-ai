"""Unit tests for `JiraFilterAdapter`: filter-only JQL validation, identity
substitution, `execute`, and custom-field discovery.

There is no `FilterSpec`/`build_native_query` translation anymore — the
model authors JQL directly; the adapter only validates and identity-
substitutes it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.filtered_search.adapter import Pagination
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter


def _adapter() -> JiraFilterAdapter:
    return JiraFilterAdapter()


def _page() -> Pagination:
    return Pagination(limit=50)


def test_capabilities_declares_expected_shape() -> None:
    caps = JiraFilterAdapter.capabilities()
    assert caps.connector_type == "JIRA"
    assert caps.record_group_noun == "project"
    assert caps.supports_custom_fields is True


class TestValidateQuery:
    def test_empty_query_is_rejected(self) -> None:
        assert JiraFilterAdapter.validate_query("") is not None
        assert JiraFilterAdapter.validate_query("   ") is not None

    def test_filter_only_query_is_accepted(self) -> None:
        assert JiraFilterAdapter.validate_query('project = "ES" AND status = "Open"') is None

    def test_priority_field_is_a_plain_accepted_filter(self) -> None:
        """Regression: the old FilterSpec design had no way to express
        `priority`, silently misrouting it to content_query — native JQL
        needs no adapter change to support it."""
        assert JiraFilterAdapter.validate_query(
            'assignee = currentUser() AND priority in ("Highest", "High")'
        ) is None

    def test_text_match_operator_on_summary_is_rejected(self) -> None:
        error = JiraFilterAdapter.validate_query('summary ~ "login bug"')
        assert error is not None
        assert "text-match" in error

    def test_text_match_operator_on_description_is_rejected(self) -> None:
        assert JiraFilterAdapter.validate_query('description ~ "urgent"') is not None

    def test_negated_text_match_operator_is_rejected(self) -> None:
        assert JiraFilterAdapter.validate_query('text !~ "spam"') is not None

    def test_tilde_inside_a_quoted_literal_is_not_flagged(self) -> None:
        """A label or value that happens to contain '~' as text, safely
        quoted, must not trip the text-operator heuristic."""
        assert JiraFilterAdapter.validate_query('labels = "foo~bar"') is None

    def test_tilde_operator_on_non_text_field_is_not_valid_jql_but_not_our_concern(self) -> None:
        """`priority ~ "x"` isn't valid JQL at all (Jira would reject it),
        but our validator only guards TEXT-searchable fields — it is not a
        general JQL syntax checker, so this passes through to Jira's own
        error rather than being caught here."""
        assert JiraFilterAdapter.validate_query('priority ~ "High"') is None


class TestIdentitySubstitution:
    def test_has_self_reference_detects_current_user(self) -> None:
        assert JiraFilterAdapter.has_self_reference("assignee = currentUser()") is True
        assert JiraFilterAdapter.has_self_reference('project = "ES"') is False

    def test_substitute_identity_replaces_current_user_with_quoted_account_id(self) -> None:
        query = JiraFilterAdapter.substitute_identity("assignee = currentUser()", "acc-123")
        assert query == 'assignee = "acc-123"'

    def test_substitute_identity_is_noop_without_self_reference(self) -> None:
        query = JiraFilterAdapter.substitute_identity('project = "ES"', "acc-123")
        assert query == 'project = "ES"'


class TestExecute:
    async def test_execute_maps_issues_to_filtered_records(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 200
        response.json.return_value = {
            "issues": [
                {"id": "10001", "key": "CORE-1", "fields": {"summary": "Fix login bug"}},
                {"id": "10002", "key": "CORE-2", "fields": {"summary": "Refactor auth"}},
            ],
            "total": 2,
        }
        client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        universe = await _adapter().execute('status = "Open"', client, page=_page())

        assert universe.connector_type == "JIRA"
        assert len(universe.records) == 2
        assert universe.records[0].external_id == "10001"
        assert universe.records[0].name == "CORE-1: Fix login bug"
        assert universe.truncated is False

    async def test_execute_skips_issues_without_an_id(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 200
        response.json.return_value = {"issues": [{"key": "CORE-1"}], "total": 1}
        client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        universe = await _adapter().execute('status = "Open"', client, page=_page())
        assert universe.records == []

    async def test_execute_raises_on_non_200(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 403
        client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        with pytest.raises(RuntimeError):
            await _adapter().execute('status = "Open"', client, page=_page())


class TestDiscoverCustomFields:
    async def test_discover_custom_fields_filters_to_customfield_prefixed(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 200
        response.json.return_value = [
            {"id": "summary", "name": "Summary", "schema": {"type": "string"}},
            {
                "id": "customfield_10001", "name": "Story Points",
                "schema": {"type": "number"}, "clauseNames": ["cf[10001]", "Story Points"],
            },
        ]
        client.get_fields = AsyncMock(return_value=response)

        fields = await _adapter().discover_custom_fields(client)
        assert len(fields) == 1
        assert fields[0].field_id == "customfield_10001"
        assert fields[0].field_type == "number"
        assert fields[0].clause_name == "cf[10001]"

    async def test_discover_custom_fields_raises_on_non_200(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.status = 500
        client.get_fields = AsyncMock(return_value=response)
        with pytest.raises(RuntimeError):
            await _adapter().discover_custom_fields(client)
