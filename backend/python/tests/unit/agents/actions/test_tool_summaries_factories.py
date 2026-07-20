"""`app/agents/actions/util/tool_summaries.py` — the envelope-aware
formatter factories (`args_template`, `list_summary`, `entity_summary`,
`confirmation`) shared by every connector's `@tool(args_summary=...,
result_summary=...)` declarations. Every factory must degrade to `None`
(or a safe default) on missing keys, malformed JSON, or non-dict data —
never raise — since a single bad payload shape must not break the tool
loop.
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.util.tool_summaries import (
    args_template,
    confirmation,
    entity_summary,
    list_summary,
)


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="tool", content=content, is_error=is_error)


class TestArgsTemplate:
    def test_fills_template_from_args(self) -> None:
        fmt = args_template("Fetching Jira issue {issue_key}", "issue_key")
        assert fmt({"issue_key": "PA-1"}) == "Fetching Jira issue PA-1"

    def test_missing_key_returns_none(self) -> None:
        fmt = args_template("Fetching Jira issue {issue_key}", "issue_key")
        assert fmt({}) is None

    def test_blank_string_value_returns_none(self) -> None:
        fmt = args_template("Fetching Jira issue {issue_key}", "issue_key")
        assert fmt({"issue_key": "   "}) is None

    def test_multiple_keys(self) -> None:
        fmt = args_template("Fetching {owner}/{repo}", "owner", "repo")
        assert fmt({"owner": "acme", "repo": "widgets"}) == "Fetching acme/widgets"

    def test_missing_any_of_multiple_keys_returns_none(self) -> None:
        fmt = args_template("Fetching {owner}/{repo}", "owner", "repo")
        assert fmt({"owner": "acme"}) is None

    def test_non_string_value_still_formats(self) -> None:
        fmt = args_template("Page {page}", "page")
        assert fmt({"page": 2}) == "Page 2"


class TestConfirmation:
    def test_fills_template_from_args(self) -> None:
        fmt = confirmation("Message sent to {channel}", "channel")
        assert fmt({"channel": "#general"}, _result('{"ok": true}')) == "Message sent to #general"

    def test_missing_key_degrades_to_empty_string(self) -> None:
        fmt = confirmation("Message sent to {channel}", "channel")
        assert fmt({}, _result('{"ok": true}')) == "Message sent to "

    def test_error_result_uses_error_message(self) -> None:
        fmt = confirmation("Message sent to {channel}", "channel")
        summary = fmt({"channel": "#general"}, _result('{"error": "channel not found"}', is_error=True))
        assert summary == "Failed: channel not found"

    def test_error_result_with_malformed_json_uses_unknown_error(self) -> None:
        fmt = confirmation("Message sent to {channel}", "channel")
        summary = fmt({"channel": "#general"}, _result("not json", is_error=True))
        assert summary == "Failed: Unknown error"


class TestListSummary:
    def test_string_path_shorthand_nests_under_data(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        content = {"data": {"issues": [{"key": "PA-1"}, {"key": "PA-2"}]}}
        summary = fmt({}, _result(content))
        assert summary == "Found 2 issues\n- PA-1\n- PA-2"

    def test_explicit_tuple_path_for_top_level_list(self) -> None:
        fmt = list_summary(("results",), lambda p: p.get("title", "?"), "page")
        content = {"results": [{"title": "Doc A"}]}
        summary = fmt({}, _result(content))
        assert summary == "Found 1 page\n- Doc A"

    def test_singular_noun_for_one_item(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        content = {"data": {"issues": [{"key": "PA-1"}]}}
        assert fmt({}, _result(content)) == "Found 1 issue\n- PA-1"

    def test_empty_list_returns_no_results_found(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        content = {"data": {"issues": []}}
        assert fmt({}, _result(content)) == "No issues found"

    def test_more_than_five_items_collapses_with_and_n_more(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        content = {"data": {"issues": [{"key": f"PA-{i}"} for i in range(8)]}}
        summary = fmt({}, _result(content))
        assert summary is not None
        assert summary.count("- PA-") == 5
        assert "and 3 more" in summary

    def test_missing_path_returns_none(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        assert fmt({}, _result({"data": {}})) is None

    def test_non_dict_data_returns_none(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        assert fmt({}, _result("not json at all")) is None

    def test_error_result_uses_error_message(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        summary = fmt({}, _result({"error": "unauthorized"}, is_error=True))
        assert summary == "Failed: unauthorized"

    def test_item_label_raising_is_skipped_not_fatal(self) -> None:
        def _boom(item: dict) -> str:
            raise KeyError("missing")

        fmt = list_summary("issues", _boom, "issue")
        content = {"data": {"issues": [{"key": "PA-1"}]}}
        summary = fmt({}, _result(content))
        assert summary == "Found 1 issue"

    def test_non_dict_items_in_list_are_skipped(self) -> None:
        fmt = list_summary("issues", lambda i: i.get("key", "?"), "issue")
        content = {"data": {"issues": ["not-a-dict", {"key": "PA-1"}]}}
        summary = fmt({}, _result(content))
        # The count in the header reflects the raw list length (2); only
        # the dict item produces a bullet, and the skipped non-dict entry
        # still counts toward "and N more" via bullet_list's `total`.
        assert summary == "Found 2 issues\n- PA-1\n- and 1 more"


class TestEntitySummary:
    def test_default_path_unwraps_data(self) -> None:
        fmt = entity_summary(lambda e: f"Created {e.get('key')}")
        content = {"message": "Issue created successfully", "data": {"key": "PA-1"}}
        assert fmt({}, _result(content)) == "Created PA-1"

    def test_explicit_empty_path_uses_root(self) -> None:
        fmt = entity_summary(lambda e: f"Event created: {e.get('subject')}", path=())
        content = {"message": "Event created successfully", "subject": "Kickoff"}
        assert fmt({}, _result(content)) == "Event created: Kickoff"

    def test_explicit_nested_path(self) -> None:
        fmt = entity_summary(lambda e: f"PR #{e.get('number')}", path=("data", "pr"))
        content = {"data": {"pr": {"number": 42}}}
        assert fmt({}, _result(content)) == "PR #42"

    def test_missing_entity_returns_none(self) -> None:
        fmt = entity_summary(lambda e: f"Created {e.get('key')}")
        assert fmt({}, _result({"message": "ok"})) is None

    def test_non_dict_entity_returns_none(self) -> None:
        fmt = entity_summary(lambda e: f"Created {e.get('key')}")
        assert fmt({}, _result({"data": "not-a-dict"})) is None

    def test_error_result_uses_error_message(self) -> None:
        fmt = entity_summary(lambda e: f"Created {e.get('key')}")
        summary = fmt({}, _result({"error": "validation failed"}, is_error=True))
        assert summary == "Failed: validation failed"

    def test_label_raising_returns_none(self) -> None:
        def _boom(entity: dict) -> str:
            raise RuntimeError("bug")

        fmt = entity_summary(_boom)
        content = {"data": {"key": "PA-1"}}
        assert fmt({}, _result(content)) is None
