"""How a tool's result restricts who may see the answer it reached."""

from __future__ import annotations

import pytest

from app.modules.agents.privacy.tool_class import (
    LabelClass,
    explicit_class,
    label_class,
    label_class_for_name,
)


@pytest.mark.parametrize(
    "path",
    [
        "/mcp/inst-1/create_issue",
        "/mcp/inst-1/search",
        "/api-toolsets/inst-9/listDeals",
        "/tools/slack/send_message",
        "/tools/jira/search_issues",
        "/tools/gmail/get_message",
        "/tools/mariadb/run_query",
        "/tools/redshift/query",
        "/tools/docs/get_document",
        "/dynamic/somewhere/unknown",
        "/toolsets/memory/memory_read",
        "/tools/database_sandbox/execute_postgresql",
        "/something/new",
        "",
    ],
)
def test_a_tool_on_the_askers_credentials_or_an_unknown_one_is_personal(path: str) -> None:
    assert label_class(path) is LabelClass.PERSONAL


@pytest.mark.parametrize(
    "path", ["/tools/jira/search_issues", "/dynamic/somewhere/unknown", "/toolsets/memory/memory_read", "/x", ""],
)
def test_a_connector_toolsets_tool_or_an_unknown_one_has_no_rule_of_its_own(path: str) -> None:
    assert explicit_class(path) is None


@pytest.mark.parametrize("path", ["/mcp/inst-1/search", "/api-toolsets/inst-9/listDeals"])
def test_the_askers_own_servers_and_api_toolsets_are_personal_by_their_rule(path: str) -> None:
    assert explicit_class(path) is LabelClass.PERSONAL


@pytest.mark.parametrize("path", ["/dynamic/dynamic/web_search", "/dynamic/dynamic/fetch_url"])
def test_the_web_is_public(path: str) -> None:
    assert label_class(path) is LabelClass.PUBLIC


@pytest.mark.parametrize(
    "path",
    [
        "/tools/knowledgegraph/search",
        "/tools/knowledgegraph/navigate",
        "/tools/codegraph/callers",
        "/tools/knowledgehub/list_files",
        "/tools/retrieval/search_internal_knowledge",
        "/tools/storage_pattern_match/search",
        "/tools/artifacts/get_record_download_url",
        "/tools/artifacts/get_artifact_content",
        "/tools/image_generator/generate_image",
        "/dynamic/sql/execute_sql_query",
        "/dynamic/sql/discover_related_tables",
        "/dynamic/slack/fetch_slack_thread",
        "/toolsets/coding_sandbox/run_code",
        "/toolsets/skills/load_skill",
        "/toolsets/skills/skills_list",
        "/toolsets/skills/skill_manage",
        "/dynamic/dynamic/fetch_full_record",
        "/tools/data/retrieve_artifact_content",
    ],
)
def test_a_tool_that_names_what_it_showed_is_records(path: str) -> None:
    assert label_class(path) is LabelClass.RECORDS


@pytest.mark.parametrize(
    "path",
    [
        "/toolsets/builtin/final_answer",
        "/toolsets/builtin/create_plan",
        "/toolsets/builtin/spawn_agent",
        "/toolsets/agents/jira_agent",
        "/tools/calculator/add",
        "/tools/date_calculator/get_exclusion_dates",
        "/tools/internaltools/ask_user_question",
        "/tools/agent_builder/draft_agent",
        "/tools/database_sandbox/execute_sqlite",
    ],
)
def test_a_tool_computed_from_its_own_arguments_adds_nothing(path: str) -> None:
    assert label_class(path) is LabelClass.NONE


def test_an_edition_table_can_classify_its_own_toolsets() -> None:
    assert label_class("/tools/team_notes/mention", extra={"team_notes": LabelClass.NONE}) is LabelClass.NONE
    assert label_class("/tools/team_notes/mention") is LabelClass.PERSONAL


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("final_answer", LabelClass.NONE),
        ("knowledgegraph__search", LabelClass.RECORDS),
        ("dynamic__web_search", LabelClass.PUBLIC),
        ("sql__execute_sql_query", LabelClass.RECORDS),
        ("jira__create_issue", LabelClass.PERSONAL),
        ("crm_server__list_deals", LabelClass.PERSONAL),
        ("mystery", LabelClass.PERSONAL),
        ("knowledgegraph__fetch_record", LabelClass.RECORDS),
        ("retrieve_artifact_content", LabelClass.RECORDS),
        ("skill_manage", LabelClass.NONE),
        ("run_code", LabelClass.NONE),
        ("internal_exploration_agent", LabelClass.NONE),
        ("database_sandbox__execute_sqlite", LabelClass.NONE),
        ("database_sandbox__execute_postgresql", LabelClass.PERSONAL),
    ],
)
def test_a_call_known_only_by_its_name_is_classed_the_same_way(name: str, expected: LabelClass) -> None:
    assert label_class_for_name(name) is expected
