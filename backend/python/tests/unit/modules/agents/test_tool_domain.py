"""Unit tests for app.modules.agents.tool_domain.

Locks in the bucketing rules used by both `tool_router.group_tools_by_domain`
and `capability_summary._get_all_tool_domains`. Regressions here would
reintroduce the phantom-domain bug class (e.g. `execute_sql_query` → "execute")
that this module was extracted to fix.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.agents.tool_domain import DOMAIN_ALIASES, derive_tool_domain


def _tool(name: str = "", original_name: str | None = None) -> MagicMock:
    """Build a tool stub. `original_name=None` means _original_name is unset."""
    t = MagicMock(spec=[])  # spec=[] so getattr returns the AttributeError default
    t.name = name
    if original_name is not None:
        t._original_name = original_name
    return t


class TestDeriveToolDomainHappyPath:
    """Toolset tools always have a dotted _original_name set by the registry."""

    def test_dotted_original_name_splits_into_domain_and_action(self):
        assert derive_tool_domain(
            _tool("outlook_search_events", "outlook.search_events")
        ) == ("outlook", "search_events")

    def test_action_keeps_remaining_dots(self):
        # split(".", 1) — only the first dot separates domain from action
        assert derive_tool_domain(
            _tool("a_b_c", "ns.sub.tool")
        ) == ("ns", "sub.tool")

    def test_dotted_name_fallback_when_no_original_name(self):
        # Defensive: tool.name with a dot still works if _original_name is missing.
        assert derive_tool_domain(_tool("slack.send_message")) == (
            "slack",
            "send_message",
        )

    def test_database_sandbox_builtin(self):
        assert derive_tool_domain(
            _tool("database_sandbox_execute_sqlite", "database_sandbox.execute_sqlite")
        ) == ("database_sandbox", "execute_sqlite")

    def test_date_calculator_builtin(self):
        assert derive_tool_domain(
            _tool("date_calculator_get_x", "date_calculator.get_x")
        ) == ("date_calculator", "get_x")


class TestDeriveToolDomainBugClass:
    """Bare-named tools must NOT invent phantom domains via underscore split.

    These cases are the actual bug fix — old code took `execute_sql_query`
    and produced ("execute", "sql_query") via a first-underscore split,
    which the orchestrator validator then rejected because no "execute"
    domain exists in tool_groups.
    """

    def test_execute_sql_query_with_canonical_name_buckets_as_sql(self):
        # `tool_system.py` sets _original_name = "sql.execute_sql_query"
        # at injection time — without that line, this test fails.
        assert derive_tool_domain(
            _tool("execute_sql_query", "sql.execute_sql_query")
        ) == ("sql", "execute_sql_query")

    def test_fetch_full_record_without_original_name_buckets_as_utility(self):
        # No _original_name, no dot in name → must NOT split on first
        # underscore (would invent domain "fetch"). Must fall back to utility.
        assert derive_tool_domain(_tool("fetch_full_record")) == (
            "utility",
            "fetch_full_record",
        )

    def test_arbitrary_bare_name_buckets_as_utility(self):
        # Same protection for any future bare-named tool.
        assert derive_tool_domain(_tool("some_new_helper_tool")) == (
            "utility",
            "some_new_helper_tool",
        )

    def test_empty_original_name_falls_back_to_name(self):
        assert derive_tool_domain(_tool("execute_sql_query", "")) == (
            "utility",
            "execute_sql_query",
        )


class TestDeriveToolDomainAliases:
    def test_googledrive_normalizes_to_google_drive(self):
        assert derive_tool_domain(
            _tool("googledrive_list", "googledrive.list_files")
        ) == ("google_drive", "list_files")

    def test_hyphenated_google_drive_normalizes(self):
        assert derive_tool_domain(
            _tool("x", "google-drive.list_files")
        ) == ("google_drive", "list_files")

    def test_googlecalendar_normalizes(self):
        assert derive_tool_domain(
            _tool("x", "googlecalendar.events")
        ) == ("google_calendar", "events")

    def test_one_drive_normalizes_to_onedrive(self):
        assert derive_tool_domain(_tool("x", "one_drive.upload")) == (
            "onedrive",
            "upload",
        )

    def test_unknown_domain_passes_through(self):
        assert derive_tool_domain(_tool("x", "custom_app.do_thing")) == (
            "custom_app",
            "do_thing",
        )

    def test_aliases_dict_is_lowercase_keyed(self):
        # The lookup key after split is lower-cased; alias keys must match.
        for key in DOMAIN_ALIASES:
            assert key == key.lower()


class TestDeriveToolDomainEdges:
    def test_empty_tool_returns_utility_empty(self):
        assert derive_tool_domain(_tool("")) == ("utility", "")

    def test_uppercase_domain_is_lowercased(self):
        assert derive_tool_domain(_tool("x", "OUTLOOK.search_events")) == (
            "outlook",
            "search_events",
        )

    def test_action_case_is_preserved(self):
        # Only the domain is lower-cased — the action keeps its casing
        # because tool registries may have case-sensitive identifiers.
        domain, action = derive_tool_domain(_tool("x", "outlook.SearchEvents"))
        assert domain == "outlook"
        assert action == "SearchEvents"

    def test_missing_name_attribute_handled(self):
        # getattr with default "" — must not raise even if tool has no .name
        t = MagicMock(spec=[])
        t._original_name = "slack.send"
        assert derive_tool_domain(t) == ("slack", "send")

    def test_none_name_treated_as_empty(self):
        t = MagicMock(spec=[])
        t.name = None
        assert derive_tool_domain(t) == ("utility", "")
