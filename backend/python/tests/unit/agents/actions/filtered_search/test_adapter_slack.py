"""Unit tests for `SlackFilterAdapter`: filter-only Slack search-operator
validation, identity substitution for `from:me`/`to:me`, and
message-response mapping using channel/user IDs (never `shortName`, which
is a display string for Slack)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.filtered_search.adapter import Pagination
from app.agents.actions.filtered_search.adapters.slack import SlackFilterAdapter
from app.agents.actions.filtered_search.models import GroupReference


def _adapter() -> SlackFilterAdapter:
    return SlackFilterAdapter()


def _page() -> Pagination:
    return Pagination(limit=50)


def test_capabilities_uses_external_id_group_reference() -> None:
    """Slack's `short_name` is a display string ("DM: Alice") — the
    descriptor must declare EXTERNAL_ID so callers never try to filter by
    the display name."""
    caps = SlackFilterAdapter.capabilities()
    assert caps.group_reference == GroupReference.EXTERNAL_ID
    assert caps.connector_type == "SLACK"
    assert caps.people_coverage_note is not None


class TestValidateQuery:
    def test_empty_query_is_rejected(self) -> None:
        assert SlackFilterAdapter.validate_query("") is not None

    def test_operator_only_query_is_accepted(self) -> None:
        assert SlackFilterAdapter.validate_query("in:<#C0123456> after:2026-07-01") is None

    def test_negated_operator_is_accepted(self) -> None:
        assert SlackFilterAdapter.validate_query("-from:<@U0123> has:link") is None

    def test_bare_free_text_term_is_rejected(self) -> None:
        error = SlackFilterAdapter.validate_query("onboarding docs")
        assert error is not None
        assert "onboarding" in error

    def test_quoted_phrase_is_rejected_as_free_text(self) -> None:
        assert SlackFilterAdapter.validate_query('"exact phrase"') is not None

    def test_mixed_operator_and_free_text_is_rejected(self) -> None:
        assert SlackFilterAdapter.validate_query("in:<#C0123> roadmap") is not None


class TestIdentitySubstitution:
    def test_has_self_reference_detects_from_me_and_to_me(self) -> None:
        assert SlackFilterAdapter.has_self_reference("from:me") is True
        assert SlackFilterAdapter.has_self_reference("to:me has:link") is True
        assert SlackFilterAdapter.has_self_reference("in:<#C1>") is False

    def test_substitute_identity_rewrites_from_me(self) -> None:
        query = SlackFilterAdapter.substitute_identity("from:me has:link", "U999")
        assert query == "from:<@U999> has:link"

    def test_substitute_identity_rewrites_to_me(self) -> None:
        query = SlackFilterAdapter.substitute_identity("to:me", "U999")
        assert query == "to:<@U999>"


class TestExecute:
    async def test_execute_maps_matches_to_filtered_records(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = {
            "messages": {
                "total": 2,
                "matches": [
                    {"ts": "111.222", "text": "hello world", "permalink": "https://x/1", "channel": {"id": "C1"}},
                    {"text": "no ts, skipped"},
                ],
            },
        }
        client.search_messages = AsyncMock(return_value=response)

        universe = await _adapter().execute("in:<#C1>", client, page=_page())
        assert universe.connector_type == "SLACK"
        assert len(universe.records) == 1
        assert universe.records[0].external_id == "111.222"
        assert universe.records[0].external_group_id == "C1"
        assert universe.truncated is True

    async def test_execute_raises_when_response_unsuccessful(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.success = False
        response.data = None
        response.error = "rate_limited"
        client.search_messages = AsyncMock(return_value=response)

        with pytest.raises(RuntimeError, match="rate_limited"):
            await _adapter().execute("in:<#C1>", client, page=_page())

    async def test_discover_custom_fields_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            await _adapter().discover_custom_fields(MagicMock())
