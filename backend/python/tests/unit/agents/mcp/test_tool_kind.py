"""What a tool does to data (`app.agents.mcp.tool_kind`)."""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.mcp.tool_kind import name_words, tool_kind


class TestTheName:
    @pytest.mark.parametrize("name", [
        "delete_issue", "deleteJiraIssue", "issues.delete", "DROP-TABLE", "removeLabel", "purge_cache",
        "erase", "wipeDevice", "truncate_table", "revokeToken", "uninstall_app", "reset_password",
        "bulk_deletion", "issue removal", "deletedItemsRestore", "remove_user_from_group",
    ])
    def test_a_deleting_word_makes_it_destructive(self, name: str) -> None:
        assert tool_kind(name, None) == ("destructive", "name")

    @pytest.mark.parametrize("name", [
        "get_dropdown_options", "list_presets", "update_address", "createIssue", "search", "eraser_tool",
        "dropbox_upload", "removable_media_info", "resettlement_report",
    ])
    def test_a_word_that_only_contains_one_does_not(self, name: str) -> None:
        assert tool_kind(name, None) == ("write", "none")

    def test_it_beats_the_servers_read_only_hint(self) -> None:
        assert tool_kind("delete_issue", {"readOnlyHint": True}) == ("destructive", "name")

    @pytest.mark.parametrize("name", ["get_issue", "list_issues", "searchJira", "read_file", "fetch"])
    def test_a_reading_name_is_not_read_only_on_its_own(self, name: str) -> None:
        assert tool_kind(name, None) == ("write", "none")

    def test_words(self) -> None:
        assert name_words("deleteJiraIssue") == ["delete", "jira", "issue"]
        assert name_words("DELETE_ISSUE") == ["delete", "issue"]
        assert name_words("issues.delete-2") == ["issues", "delete", "2"]
        assert name_words("") == []


class TestTheServersHints:
    @pytest.mark.parametrize(("annotations", "expected"), [
        ({"readOnlyHint": True}, ("read", "server")),
        ({"readOnlyHint": True, "destructiveHint": True}, ("read", "server")),
        ({"destructiveHint": True}, ("destructive", "server")),
        ({"readOnlyHint": False, "destructiveHint": True}, ("destructive", "server")),
        ({"readOnlyHint": False, "destructiveHint": False}, ("write", "server")),
        ({"destructiveHint": False}, ("write", "server")),
        # The spec reads a missing destructiveHint as true; that alone doesn't deny a tool.
        ({"readOnlyHint": False}, ("write", "server")),
        ({"title": "Create issue", "openWorldHint": True}, ("write", "none")),
        ({}, ("write", "none")),
        (None, ("write", "none")),
    ])
    def test_set_the_kind(self, annotations: dict[str, Any] | None, expected: tuple[str, str]) -> None:
        assert tool_kind("create_issue", annotations) == expected

    @pytest.mark.parametrize("annotations", [
        {"readOnlyHint": "true"}, {"readOnlyHint": 1}, {"destructiveHint": "yes"}, ["readOnlyHint"],
    ])
    def test_only_booleans_count(self, annotations: Any) -> None:  # noqa: ANN401
        assert tool_kind("create_issue", annotations) == ("write", "none")
