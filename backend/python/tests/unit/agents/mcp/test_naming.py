"""LLM-facing MCP tool names (`app.agents.mcp.naming`)."""
from __future__ import annotations

import re

import pytest

from app.agents.mcp.naming import (
    MAX_TOOL_NAME_LENGTH,
    build_namespaced_tool_name,
    instance_tag,
    namespace_key,
)

_PROVIDER_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class TestUnchangedNames:
    """Names that were already valid are persisted by agents, projects and chat selections."""

    @pytest.mark.parametrize(
        ("namespace", "tool", "expected"),
        [
            ("github", "list_issues", "mcp_github_list_issues"),
            ("My Server", "search", "mcp_my_server_search"),
            ("atlassian-rovo", "getJiraIssue", "mcp_atlassian_rovo_getJiraIssue"),
            ("exa", "web-search", "mcp_exa_web-search"),
        ],
    )
    def test_valid_names_are_what_they_always_were(self, namespace: str, tool: str, expected: str) -> None:
        assert build_namespaced_tool_name(namespace, tool) == expected


class TestSanitizing:
    def test_invalid_tool_characters_are_replaced_and_hashed(self) -> None:
        name = build_namespaced_tool_name("pangea", "audit.logs/get")

        assert _PROVIDER_NAME.fullmatch(name)
        assert name.startswith("mcp_pangea_audit_logs_get_")

    def test_tools_that_sanitise_to_the_same_text_stay_distinct(self) -> None:
        assert build_namespaced_tool_name("x", "a.b") != build_namespaced_tool_name("x", "a_b")

    def test_invalid_namespace_characters_are_replaced(self) -> None:
        assert _PROVIDER_NAME.fullmatch(build_namespaced_tool_name("Pangea (prod)", "logs"))

    def test_empty_parts_still_make_a_valid_name(self) -> None:
        assert _PROVIDER_NAME.fullmatch(build_namespaced_tool_name("", ""))


class TestLength:
    def test_long_names_are_cut_to_the_provider_limit(self) -> None:
        name = build_namespaced_tool_name("a_very_long_catalog_type_identifier", "retrieve_" + "x" * 80)

        assert len(name) <= MAX_TOOL_NAME_LENGTH
        assert _PROVIDER_NAME.fullmatch(name)

    def test_two_long_names_sharing_a_prefix_differ(self) -> None:
        base = "list_all_repository_pull_request_review_comments_for_"
        assert build_namespaced_tool_name("github", base + "owner") != build_namespaced_tool_name("github", base + "team")

    def test_deterministic(self) -> None:
        tool = "t" * 100
        assert build_namespaced_tool_name("github", tool) == build_namespaced_tool_name("github", tool)


class TestNamespaceKey:
    def test_type_wins_over_name(self) -> None:
        assert namespace_key("atlassian_rovo", "RovoMCP") == "atlassian_rovo"

    def test_name_is_used_without_a_type(self) -> None:
        assert namespace_key(None, "My-Server") == "my_server"

    def test_instance_tag_is_short_and_stable(self) -> None:
        assert instance_tag("inst-1") == instance_tag("inst-1")
        assert len(instance_tag("inst-1")) == 4
        assert instance_tag("inst-1") != instance_tag("inst-2")
