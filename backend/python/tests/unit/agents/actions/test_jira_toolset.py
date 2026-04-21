"""Comprehensive unit tests for `app.agents.actions.jira.jira`.

Covers:
* 11 Pydantic input schemas + `model_validator` normalisers
* Pure helpers: `_get_error_guidance`, `_convert_text_to_adf`,
  `_normalize_description`, `_normalize_field_name`, `_validate_and_fix_jql`,
  `_clean_issue_fields`, `_add_urls_to_issue_references`,
  `_normalize_issues_in_response`, `_validate_issue_fields`, `_handle_response`
* Async helpers: `_resolve_user_to_account_id`, `_fetch_and_cache_field_schema`
* All 14 `@tool` methods: success + API-error + exception paths plus the
  auth-error retry branches for `create_issue` / `update_issue`, the
  status-transition branch of `update_issue`, the JQL auto-fix path in
  `search_issues`, and the comment-ADF branches.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.jira.jira import (
    AddCommentInput,
    ConvertTextToAdfInput,
    CreateIssueInput,
    GetCommentsInput,
    GetIssueInput,
    GetIssuesInput,
    GetProjectInput,
    GetProjectMetadataInput,
    Jira,
    SearchIssuesInput,
    SearchUsersInput,
    UpdateIssueInput,
)
from app.sources.client.http.exception.exception import HttpStatusCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status: int, body=None, is_json: bool = True):
    """Mimic `HTTPResponse` enough for `_handle_response` and callers."""
    resp = MagicMock()
    resp.status = status
    resp.is_json = is_json
    resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = MagicMock(return_value=json.dumps(body) if body is not None else "")
    return resp


def _build_jira(client_methods: dict | None = None) -> Jira:
    """Instantiate Jira bypassing the ToolsetBuilder decorator; stub the
    underlying `JiraDataSource` (``self.client``) with AsyncMocks so each test
    can set return values per method."""
    jira = Jira.__new__(Jira)
    client = MagicMock()
    for name, value in (client_methods or {}).items():
        setattr(client, name, value)
    jira.client = client
    jira._site_url = None
    jira._field_schema_cache = None
    return jira


# ===========================================================================
# Pydantic input schemas
# ===========================================================================

class TestCreateIssueInput:
    def test_canonical_keys_pass_through(self):
        data = CreateIssueInput(project_key="P", summary="s", issue_type_name="Task")
        assert data.project_key == "P"
        assert data.issue_type_name == "Task"

    def test_project_alias_extracted(self):
        data = CreateIssueInput(project="P", summary="s", issue_type_name="Task")
        assert data.project_key == "P"

    def test_projectKey_camel_alias_extracted(self):
        data = CreateIssueInput(projectKey="P", summary="s", issue_type_name="Task")
        assert data.project_key == "P"

    def test_issuetype_nested_dict_with_name_extracted(self):
        data = CreateIssueInput(project_key="P", summary="s", issuetype={"name": "Bug"})
        assert data.issue_type_name == "Bug"

    def test_issuetype_nested_dict_without_name_stringified(self):
        data = CreateIssueInput(project_key="P", summary="s", issuetype={"id": 10})
        assert data.issue_type_name == "{'id': 10}"

    def test_issue_type_snake_alias_extracted(self):
        data = CreateIssueInput(project_key="P", summary="s", issue_type="Task")
        assert data.issue_type_name == "Task"

    def test_non_dict_input_returned_as_is(self):
        assert CreateIssueInput.extract_nested_values("not a dict") == "not a dict"

    def test_extra_keys_ignored(self):
        data = CreateIssueInput(project_key="P", summary="s", issue_type_name="Task", foo="bar")
        assert not hasattr(data, "foo")


class TestGetIssuesInput:
    def test_project_key_canonical(self):
        data = GetIssuesInput(project_key="P")
        assert data.project_key == "P"

    def test_project_alias_extracted(self):
        data = GetIssuesInput(project="P")
        assert data.project_key == "P"

    def test_projectKey_camel_alias_extracted(self):
        data = GetIssuesInput(projectKey="P")
        assert data.project_key == "P"

    def test_non_dict_input_returned_as_is(self):
        assert GetIssuesInput.extract_project_key(None) is None


class TestGetIssueInput:
    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_all_issue_key_aliases_extracted(self, alias):
        data = GetIssueInput(**{alias: "P-1"})
        assert data.issue_key == "P-1"

    def test_non_dict_input_returned_as_is(self):
        assert GetIssueInput.extract_issue_key(123) == 123


class TestSearchIssuesInput:
    def test_defaults(self):
        data = SearchIssuesInput(jql="project = P")
        assert data.maxResults == 50

    def test_custom_max_results(self):
        data = SearchIssuesInput(jql="project = P", maxResults=200)
        assert data.maxResults == 200


class TestAddCommentInput:
    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_all_issue_key_aliases(self, alias):
        data = AddCommentInput(comment="hi", **{alias: "P-1"})
        assert data.issue_key == "P-1"

    def test_non_dict_passthrough(self):
        assert AddCommentInput.extract_issue_key([1, 2]) == [1, 2]


class TestSearchUsersInput:
    def test_default_max_results(self):
        data = SearchUsersInput(query="alice")
        assert data.max_results == 50


class TestUpdateIssueInput:
    def test_canonical_issue_key(self):
        data = UpdateIssueInput(issue_key="P-1")
        assert data.issue_key == "P-1"

    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_alias_issue_key(self, alias):
        data = UpdateIssueInput(**{alias: "P-1"})
        assert data.issue_key == "P-1"

    def test_direct_fields_wrapper_extracted(self):
        data = UpdateIssueInput(
            issue_key="P-1",
            fields={"summary": "new", "description": "d", "priority_name": "High", "status": "Done"},
        )
        assert data.summary == "new"
        assert data.description == "d"
        assert data.priority_name == "High"
        assert data.status == "Done"

    def test_update_wrapper_with_fields(self):
        data = UpdateIssueInput(
            issue_key="P-1",
            update={"fields": {"summary": "x", "labels": ["a"]}},
        )
        assert data.summary == "x"
        assert data.labels == ["a"]

    def test_update_wrapper_provides_issue_key(self):
        data = UpdateIssueInput(update={"issueKey": "P-1", "fields": {"summary": "x"}})
        assert data.issue_key == "P-1"

    def test_updateData_wrapper_alias(self):
        data = UpdateIssueInput(
            issue_key="P-1",
            updateData={"fields": {"description": "d"}},
        )
        assert data.description == "d"

    def test_non_dict_passthrough(self):
        assert UpdateIssueInput.extract_nested_values(None) is None


class TestGetProjectMetadataInput:
    def test_project_alias_extracted(self):
        data = GetProjectMetadataInput(project="P")
        assert data.project_key == "P"

    def test_non_dict_passthrough(self):
        assert GetProjectMetadataInput.extract_project_key(None) is None


class TestMiscSchemas:
    def test_get_project_input(self):
        assert GetProjectInput(project_key="P").project_key == "P"

    def test_convert_text_to_adf_input(self):
        assert ConvertTextToAdfInput(text="hi").text == "hi"

    def test_get_comments_input(self):
        assert GetCommentsInput(issue_key="P-1").issue_key == "P-1"


# ===========================================================================
# Pure helper methods
# ===========================================================================

class TestGetErrorGuidance:
    @pytest.mark.parametrize("status", [410, 401, 403, 404, 400])
    def test_known_codes_return_guidance(self, status):
        jira = _build_jira()
        assert jira._get_error_guidance(status) is not None

    def test_unknown_code_returns_none(self):
        jira = _build_jira()
        assert jira._get_error_guidance(418) is None


class TestConvertTextToAdf:
    def test_empty_text_returns_none(self):
        assert _build_jira()._convert_text_to_adf("") is None

    def test_produces_valid_adf_doc(self):
        adf = _build_jira()._convert_text_to_adf("hello")
        assert adf["type"] == "doc"
        assert adf["version"] == 1
        assert adf["content"][0]["content"][0]["text"] == "hello"


class TestNormalizeDescription:
    def test_slack_mention_markup_replaced(self):
        out = _build_jira()._normalize_description("ping <@U12345> please")
        assert out == "ping @U12345 please"

    def test_plain_text_unchanged(self):
        out = _build_jira()._normalize_description("no mentions here")
        assert out == "no mentions here"


class TestNormalizeFieldName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Story Points", "story_points"),
            ("Issue Type", "issue_type"),
            ("Epic Link!", "epic_link"),
            ("Multi-Word Field", "multi_word_field"),
            ("", ""),
            ("  Leading-Trailing  ", "leading_trailing"),
        ],
    )
    def test_normalisation(self, raw, expected):
        assert _build_jira()._normalize_field_name(raw) == expected


class TestValidateAndFixJql:
    def test_empty_jql_passthrough(self):
        jql, warn = _build_jira()._validate_and_fix_jql("")
        assert jql == "" and warn is None

    def test_fixes_resolution_unresolved(self):
        jql, warn = _build_jira()._validate_and_fix_jql("resolution = Unresolved")
        assert "IS EMPTY" in jql
        assert warn is not None
        assert "resolution" in warn

    def test_fixes_current_user_without_parens(self):
        jql, warn = _build_jira()._validate_and_fix_jql("assignee = currentUser AND updated >= -7d")
        assert "currentUser()" in jql
        assert warn is not None

    def test_no_warning_when_query_already_correct(self):
        jql, warn = _build_jira()._validate_and_fix_jql(
            "project = \"P\" AND assignee = currentUser() AND resolution IS EMPTY"
        )
        assert warn is None


class TestValidateIssueFields:
    def test_missing_project_key(self):
        ok, msg = _build_jira()._validate_issue_fields({"summary": "s", "issuetype": {"name": "Task"}})
        assert ok is False
        assert "Project key" in msg

    def test_missing_summary(self):
        ok, msg = _build_jira()._validate_issue_fields({"project": {"key": "P"}, "issuetype": {"name": "Task"}})
        assert ok is False
        assert "Summary" in msg

    def test_missing_issue_type_name(self):
        ok, msg = _build_jira()._validate_issue_fields({"project": {"key": "P"}, "summary": "s"})
        assert ok is False
        assert "Issue type" in msg

    def test_description_string_converts_to_adf(self):
        fields = {"project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
                  "description": "hello"}
        ok, _ = _build_jira()._validate_issue_fields(fields)
        assert ok is True
        assert isinstance(fields["description"], dict)
        assert fields["description"]["type"] == "doc"

    def test_description_invalid_type_rejected(self):
        fields = {"project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
                  "description": 123}
        ok, msg = _build_jira()._validate_issue_fields(fields)
        assert ok is False
        assert "Description" in msg

    def test_assignee_missing_account_id(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "assignee": {"foo": "bar"},
        })
        assert ok is False
        assert "Assignee" in msg

    def test_reporter_missing_account_id(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "reporter": "not-a-dict",
        })
        assert ok is False
        assert "Reporter" in msg

    def test_priority_missing_name(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "priority": {"id": "1"},
        })
        assert ok is False
        assert "Priority" in msg

    def test_components_not_list(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "components": "not-a-list",
        })
        assert ok is False
        assert "Components" in msg

    def test_components_missing_name(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "components": [{"foo": "bar"}],
        })
        assert ok is False

    def test_all_valid(self):
        ok, _ = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "assignee": {"accountId": "aid"},
            "reporter": {"accountId": "rid"},
            "priority": {"name": "High"},
            "components": [{"name": "c1"}],
        })
        assert ok is True


# ===========================================================================
# _handle_response (pure)
# ===========================================================================

class TestHandleResponse:
    def test_success_200_with_body(self):
        jira = _build_jira()
        ok, payload = jira._handle_response(
            _mock_response(HttpStatusCode.SUCCESS.value, {"hello": "world"}), "ok",
        )
        data = json.loads(payload)
        assert ok is True
        assert data["message"] == "ok"
        assert data["data"] == {"hello": "world"}

    def test_success_204_no_content_returns_empty_data(self):
        jira = _build_jira()
        ok, payload = jira._handle_response(
            _mock_response(HttpStatusCode.NO_CONTENT.value), "ok",
        )
        assert ok is True
        assert json.loads(payload)["data"] == {}

    def test_success_json_parse_error_falls_back(self):
        jira = _build_jira()
        resp = _mock_response(HttpStatusCode.SUCCESS.value)
        resp.json = MagicMock(side_effect=ValueError("broken"))
        ok, payload = jira._handle_response(resp, "ok")
        assert ok is True
        assert json.loads(payload)["data"] == {}

    def test_error_with_errorMessages_list_sets_error(self):
        """When errorMessages is a list, the ternary in _handle_response
        returns errorMessages[0] as the structured error message (non-obvious
        operator-precedence behaviour)."""
        jira = _build_jira()
        resp = _mock_response(500, {"errorMessages": ["first msg"]})
        ok, payload = jira._handle_response(resp, "ignored", include_guidance=False)
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "first msg"
        assert data["status_code"] == 500

    def test_error_with_error_key_but_no_errorMessages_list_falls_back(self):
        """Current behaviour: when errorMessages is not a list, the whole
        ternary evaluates to None and error_message is never set, so the
        error string falls back to ``HTTP <status>``."""
        jira = _build_jira()
        resp = _mock_response(500, {"error": "boom", "errors": {"field": "bad"}})
        ok, payload = jira._handle_response(resp, "ignored", include_guidance=False)
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "HTTP 500"
        assert data["status_code"] == 500

    def test_error_guidance_attached_for_known_status(self):
        jira = _build_jira()
        resp = _mock_response(401, {"error": "unauth"})
        ok, payload = jira._handle_response(resp, "x", include_guidance=True)
        data = json.loads(payload)
        assert data["guidance"] and "Authentication" in data["guidance"]

    def test_error_with_errorMessages_list(self):
        jira = _build_jira()
        resp = _mock_response(400, {"errorMessages": ["Bad JQL"]})
        ok, payload = jira._handle_response(resp, "x")
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "Bad JQL"

    def test_error_non_dict_json_body(self):
        jira = _build_jira()
        resp = _mock_response(500, "string body")
        ok, payload = jira._handle_response(resp, "x")
        data = json.loads(payload)
        assert ok is False
        assert "HTTP 500" in data["error"]

    def test_error_non_json_response(self):
        jira = _build_jira()
        resp = _mock_response(500, is_json=False)
        resp.text = MagicMock(return_value="plain text error")
        ok, payload = jira._handle_response(resp, "x")
        data = json.loads(payload)
        assert ok is False
        assert "HTTP 500" in data["error"]

    def test_error_parsing_exception_fallback(self):
        jira = _build_jira()
        resp = _mock_response(500, is_json=True)
        resp.json = MagicMock(side_effect=RuntimeError("parse fail"))
        resp.text = MagicMock(return_value="raw text")
        ok, payload = jira._handle_response(resp, "x")
        data = json.loads(payload)
        assert ok is False
        assert "raw text" in data["details"]


# ===========================================================================
# _clean_issue_fields
# ===========================================================================

class TestCleanIssueFields:
    def test_non_dict_passthrough(self):
        assert _build_jira()._clean_issue_fields("not a dict") == "not a dict"

    def test_missing_fields_passthrough(self):
        out = _build_jira()._clean_issue_fields({"key": "P-1"})
        assert out == {"key": "P-1"}

    def test_non_dict_fields_passthrough(self):
        out = _build_jira()._clean_issue_fields({"key": "P-1", "fields": "bad"})
        assert out["fields"] == "bad"

    def test_removes_none_customfields_and_empty_strings(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {
                "customfield_1": None,
                "customfield_2": "value",
                "description": "",
                "summary": "kept",
            },
        })
        assert "customfield_1" not in out["fields"]
        assert out["fields"]["customfield_2"] == "value"
        assert "description" not in out["fields"]
        assert out["fields"]["summary"] == "kept"

    def test_simplifies_project_status_priority_issuetype(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {
                "project": {"key": "P", "name": "Proj", "extra": "junk"},
                "status": {"name": "Open", "id": "1", "extra": "junk"},
                "priority": {"name": "High", "id": "2", "extra": "junk"},
                "issuetype": {"name": "Bug", "id": "3", "extra": "junk"},
            },
        })
        for field in ("project", "status", "priority", "issuetype"):
            assert "extra" not in out["fields"][field]

    def test_user_fields_simplified(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {
                "assignee": {"accountId": "a", "displayName": "A", "emailAddress": "a@x"},
                "reporter": {"junk": "removed"},
                "creator": {"accountId": "c"},
            },
        })
        assert "reporter" not in out["fields"]  # stripped — no useful fields
        assert out["fields"]["assignee"]["accountId"] == "a"
        assert out["fields"]["creator"] == {"accountId": "c"}

    def test_parent_simplified(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-2",
            "fields": {
                "parent": {
                    "key": "P-1", "id": "10",
                    "fields": {"summary": "parent sum", "status": {"name": "Open"}},
                }
            },
        })
        parent = out["fields"]["parent"]
        assert parent["key"] == "P-1"
        assert parent["summary"] == "parent sum"
        assert parent["status"] == {"name": "Open"}

    def test_empty_comment_block_removed(self):
        """Empty comments dict is stripped by the first `elif` branch."""
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"comment": {"comments": []}},
        })
        assert "comment" not in out["fields"]

    def test_empty_worklog_block_removed(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"worklog": {"worklogs": []}},
        })
        assert "worklog" not in out["fields"]

    def test_populated_comment_block_preserved_as_is(self):
        """Current behaviour: when comments list is non-empty, the earlier
        ``elif field_key in ["comment", "worklog"]`` branch matches but does
        nothing, which prevents the trim-to-3 simplification below from ever
        running. Documented here so a future refactor has a regression guard."""
        comments = [{"id": str(i), "body": f"c{i}"} for i in range(5)]
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"comment": {"comments": comments}},
        })
        assert out["fields"]["comment"]["comments"] == comments

    def test_attachments_simplified(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"attachment": [{"id": "1", "filename": "f", "size": 10, "junk": "no"}]},
        })
        assert out["fields"]["attachment"][0]["filename"] == "f"
        assert "junk" not in out["fields"]["attachment"][0]


# ===========================================================================
# _normalize_issues_in_response
# ===========================================================================

class TestNormalizeIssuesInResponse:
    @pytest.mark.asyncio
    async def test_non_dict_passthrough(self):
        out = await _build_jira()._normalize_issues_in_response("not-a-dict", {})
        assert out == "not-a-dict"

    @pytest.mark.asyncio
    async def test_issues_list_normalised(self):
        field_schema = {"customfield_10063": {"name": "Story Points", "normalized": "story_points"}}
        data = {"issues": [{"fields": {"customfield_10063": 5, "customfield_other": None}}]}
        out = await _build_jira()._normalize_issues_in_response(data, field_schema)
        assert out["issues"][0]["fields"]["story_points"] == 5

    @pytest.mark.asyncio
    async def test_single_issue_normalised(self):
        field_schema = {"customfield_10063": {"name": "Story Points", "normalized": "story_points"}}
        data = {"fields": {"customfield_10063": 3}}
        out = await _build_jira()._normalize_issues_in_response(data, field_schema)
        assert out["fields"]["story_points"] == 3

    @pytest.mark.asyncio
    async def test_none_custom_field_not_added(self):
        field_schema = {"customfield_10063": {"name": "SP", "normalized": "sp"}}
        data = {"fields": {"customfield_10063": None}}
        out = await _build_jira()._normalize_issues_in_response(data, field_schema)
        assert "sp" not in out["fields"]


# ===========================================================================
# _add_urls_to_issue_references
# ===========================================================================

class TestAddUrlsToIssueReferences:
    def test_noop_when_no_site_url(self):
        issue = {"fields": {"parent": {"key": "P-1"}}}
        _build_jira()._add_urls_to_issue_references(issue, None)
        assert "url" not in issue["fields"]["parent"]

    def test_noop_when_issue_not_dict(self):
        _build_jira()._add_urls_to_issue_references("not-a-dict", "https://x")  # no raise

    def test_noop_when_fields_not_dict(self):
        issue = {"fields": "bad"}
        _build_jira()._add_urls_to_issue_references(issue, "https://x")  # no raise

    def test_parent_gets_url(self):
        issue = {"fields": {"parent": {"key": "P-1"}}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["parent"]["url"] == "https://site/browse/P-1"

    def test_custom_field_epic_link_gets_url(self):
        issue = {"fields": {"customfield_10014": {"key": "P-2", "summary": "epic"}}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["customfield_10014"]["url"] == "https://site/browse/P-2"

    def test_list_of_issue_refs_gets_urls(self):
        issue = {"fields": {"related": [{"key": "P-3"}, {"key": "P-4"}, {"other": "no key"}]}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["related"][0]["url"] == "https://site/browse/P-3"
        assert issue["fields"]["related"][1]["url"] == "https://site/browse/P-4"
        assert "url" not in issue["fields"]["related"][2]

    def test_skipped_fields_untouched(self):
        issue = {"fields": {"key": "ignore", "self": "ignore", "parent": {"key": "P-1"}}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["key"] == "ignore"


# ===========================================================================
# _resolve_user_to_account_id
# ===========================================================================

class TestResolveUserToAccountId:
    @pytest.mark.asyncio
    async def test_assignable_user_found_first(self):
        client = MagicMock()
        client.find_assignable_users = AsyncMock(
            return_value=_mock_response(200, [{"accountId": "a1"}]),
        )
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_account_id("P", "alice") == "a1"

    @pytest.mark.asyncio
    async def test_falls_back_to_global_search(self):
        client = MagicMock()
        client.find_assignable_users = AsyncMock(return_value=_mock_response(200, []))
        client.find_users_by_query = AsyncMock(
            return_value=_mock_response(200, [{"accountId": "g1"}]),
        )
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_account_id("P", "alice") == "g1"

    @pytest.mark.asyncio
    async def test_none_when_no_match(self):
        client = MagicMock()
        client.find_assignable_users = AsyncMock(return_value=_mock_response(200, []))
        client.find_users_by_query = AsyncMock(return_value=_mock_response(200, []))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_account_id("P", "alice") is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        client = MagicMock()
        client.find_assignable_users = AsyncMock(side_effect=RuntimeError("boom"))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_account_id("P", "alice") is None


# ===========================================================================
# _fetch_and_cache_field_schema
# ===========================================================================

class TestFetchAndCacheFieldSchema:
    @pytest.mark.asyncio
    async def test_success_builds_mapping(self):
        client = MagicMock()
        client.get_fields = AsyncMock(return_value=_mock_response(200, [
            {"id": "customfield_10063", "name": "Story Points"},
            {"id": "customfield_10064", "name": "Epic Link"},
            {"id": "", "name": "ignored"},  # missing id → skipped
        ]))
        jira = _build_jira()
        jira.client = client
        schema = await jira._fetch_and_cache_field_schema()
        assert schema["customfield_10063"]["normalized"] == "story_points"
        assert schema["customfield_10064"]["normalized"] == "epic_link"

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        client = MagicMock()
        client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        jira = _build_jira()
        jira.client = client
        await jira._fetch_and_cache_field_schema()
        await jira._fetch_and_cache_field_schema()
        client.get_fields.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self):
        client = MagicMock()
        client.get_fields = AsyncMock(return_value=_mock_response(200, {"not": "a list"}))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}

    @pytest.mark.asyncio
    async def test_non_success_response_returns_empty(self):
        client = MagicMock()
        client.get_fields = AsyncMock(return_value=_mock_response(500, {"err": "x"}))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        client = MagicMock()
        client.get_fields = AsyncMock(side_effect=RuntimeError("boom"))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}


# ===========================================================================
# Tool: validate_connection
# ===========================================================================

class TestValidateConnection:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(return_value=_mock_response(200, {
            "accountId": "a1", "displayName": "Alice", "emailAddress": "a@x",
        }))
        ok, payload = await jira.validate_connection()
        data = json.loads(payload)
        assert ok is True
        assert data["user"]["accountId"] == "a1"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(return_value=_mock_response(401, {"error": "x"}))
        ok, payload = await jira.validate_connection()
        assert ok is False
        assert "guidance" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_exception_path(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.validate_connection()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: get_current_user
# ===========================================================================

class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(return_value=_mock_response(200, {
            "accountId": "a", "displayName": "A", "emailAddress": "a@x",
        }))
        ok, payload = await jira.get_current_user()
        assert ok is True
        assert json.loads(payload)["data"]["accountId"] == "a"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(return_value=_mock_response(403, {"error": "x"}))
        ok, _ = await jira.get_current_user()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_current_user = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.get_current_user()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: convert_text_to_adf
# ===========================================================================

class TestConvertTextToAdfTool:
    @pytest.mark.asyncio
    async def test_success(self):
        ok, payload = await _build_jira().convert_text_to_adf("hello")
        data = json.loads(payload)
        assert ok is True
        assert data["adf_document"]["type"] == "doc"

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        with patch.object(jira, "_convert_text_to_adf", side_effect=RuntimeError("boom")):
            ok, payload = await jira.convert_text_to_adf("hi")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: create_issue
# ===========================================================================

class TestCreateIssue:
    @pytest.mark.asyncio
    async def test_success_minimum_fields(self):
        jira = _build_jira()
        jira.client.create_issue = AsyncMock(return_value=_mock_response(201, {"key": "P-1", "id": "1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.create_issue("P", "s", "Task")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_resolves_assignee_query(self):
        jira = _build_jira()
        jira.client.create_issue = AsyncMock(return_value=_mock_response(201, {"key": "P-1"}))
        with patch.object(jira, "_resolve_user_to_account_id", AsyncMock(return_value="aid")), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.create_issue("P", "s", "Task", assignee_query="alice")
        assert ok is True
        called_fields = jira.client.create_issue.call_args.kwargs["fields"]
        assert called_fields["assignee"]["accountId"] == "aid"

    @pytest.mark.asyncio
    async def test_all_optional_fields_passthrough(self):
        jira = _build_jira()
        jira.client.create_issue = AsyncMock(return_value=_mock_response(201, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.create_issue(
                "P", "s", "Task",
                description="d", assignee_account_id="aid",
                priority_name="High", labels=["l1"], components=["c1"],
                parent_key="P-0",
            )
        fields = jira.client.create_issue.call_args.kwargs["fields"]
        assert fields["priority"] == {"name": "High"}
        assert fields["labels"] == ["l1"]
        assert fields["components"] == [{"name": "c1"}]
        assert fields["parent"] == {"key": "P-0"}
        assert isinstance(fields["description"], dict)

    @pytest.mark.asyncio
    async def test_validation_failure(self):
        jira = _build_jira()
        # No create_issue call expected — validation blocks early.
        with patch.object(jira, "_validate_issue_fields", return_value=(False, "nope")):
            ok, payload = await jira.create_issue("P", "s", "Task")
        assert ok is False
        assert "validation_error" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_retries_without_reporter_on_400(self):
        jira = _build_jira()
        # First call returns 400 with reporter error; inject "reporter" into fields via patch.
        first_resp = _mock_response(400, {"errors": {"reporter": "not allowed"}})
        second_resp = _mock_response(201, {"key": "P-1"})
        jira.client.create_issue = AsyncMock(side_effect=[first_resp, second_resp])
        # Force the reporter into fields via custom _validate_issue_fields stub that adds it.
        orig_validate = jira._validate_issue_fields
        def _stub(fields):
            fields["reporter"] = {"accountId": "r"}
            return orig_validate(fields)
        with patch.object(jira, "_validate_issue_fields", side_effect=_stub), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.create_issue("P", "s", "Task")
        assert ok is True
        assert jira.client.create_issue.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_without_assignee_on_400(self):
        jira = _build_jira()
        first = _mock_response(400, {"errors": {"assignee": "not assignable"}})
        second = _mock_response(201, {"key": "P-1"})
        jira.client.create_issue = AsyncMock(side_effect=[first, second])
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.create_issue("P", "s", "Task", assignee_account_id="aid")
        assert ok is True

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.create_issue = AsyncMock(return_value=_mock_response(500, {"error": "x"}))
        ok, _ = await jira.create_issue("P", "s", "Task")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.create_issue = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.create_issue("P", "s", "Task")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: update_issue
# ===========================================================================

class TestUpdateIssue:
    @pytest.mark.asyncio
    async def test_no_updates_returns_error(self):
        jira = _build_jira()
        ok, payload = await jira.update_issue("P-1")
        assert ok is False
        assert "No updates provided" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_summary_update_success(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.update_issue("P-1", summary="new")
        assert ok is True
        data = json.loads(payload)
        assert data["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_description_string_converted_to_adf(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.update_issue("P-1", description="plain text")
        sent_fields = jira.client.edit_issue.call_args.kwargs["fields"]
        assert isinstance(sent_fields["description"], dict)

    @pytest.mark.asyncio
    async def test_description_dict_passes_through(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        adf = {"type": "doc", "version": 1, "content": []}
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.update_issue("P-1", description=adf)
        assert jira.client.edit_issue.call_args.kwargs["fields"]["description"] is adf

    @pytest.mark.asyncio
    async def test_description_invalid_type_error(self):
        jira = _build_jira()
        ok, payload = await jira.update_issue("P-1", description=42)
        assert ok is False
        assert "Description must be" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_status_transition_found(self):
        jira = _build_jira()
        transitions_body = {"transitions": [{"id": "11", "to": {"name": "Done"}}]}
        jira.client.get_transitions = AsyncMock(return_value=_mock_response(200, transitions_body))
        jira.client.do_transition = AsyncMock(return_value=_mock_response(204))
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        jira.client.do_transition.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_transition_not_found_warning(self):
        jira = _build_jira()
        jira.client.get_transitions = AsyncMock(
            return_value=_mock_response(200, {"transitions": [{"to": {"name": "Open"}, "id": "1"}]})
        )
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", summary="x", status="Closed")
        assert ok is True

    @pytest.mark.asyncio
    async def test_retry_without_reporter_on_400(self):
        jira = _build_jira()
        first = _mock_response(400, {"errors": {"reporter": "not allowed"}})
        second = _mock_response(204)
        jira.client.edit_issue = AsyncMock(side_effect=[first, second])
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", summary="x", reporter_account_id="rid")
        assert ok is True
        assert jira.client.edit_issue.await_count == 2

    @pytest.mark.asyncio
    async def test_update_fails_fetch_also_fails_minimal_response(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        jira.client.get_issue = AsyncMock(return_value=_mock_response(404, {"error": "x"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.update_issue("P-1", summary="x")
        assert ok is True
        assert json.loads(payload)["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_edit_api_error(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(500, {"error": "x"}))
        ok, _ = await jira.update_issue("P-1", summary="x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_assignee_query_resolved(self):
        jira = _build_jira()
        jira.client.get_issue = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),  # for resolve
            _mock_response(200, {"key": "P-1"}),                         # final fetch
        ])
        jira.client.edit_issue = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_resolve_user_to_account_id", AsyncMock(return_value="aid")), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", assignee_query="alice")
        assert ok is True
        assert jira.client.edit_issue.call_args.kwargs["fields"]["assignee"]["accountId"] == "aid"

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.edit_issue = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.update_issue("P-1", summary="x")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: get_projects / get_project / get_project_metadata
# ===========================================================================

class TestGetProjects:
    @pytest.mark.asyncio
    async def test_success_list(self):
        jira = _build_jira()
        jira.client.get_all_projects = AsyncMock(
            return_value=_mock_response(200, [{"key": "P", "name": "Proj"}]),
        )
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_projects()
        data = json.loads(payload)
        assert ok is True
        assert data["data"][0]["url"] == "https://s/projects/P"

    @pytest.mark.asyncio
    async def test_success_single_dict(self):
        jira = _build_jira()
        jira.client.get_all_projects = AsyncMock(return_value=_mock_response(200, {"key": "P"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_projects()
        assert ok is True
        assert json.loads(payload)["data"]["url"] == "https://s/projects/P"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_all_projects = AsyncMock(return_value=_mock_response(500, {"error": "x"}))
        ok, _ = await jira.get_projects()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_all_projects = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_projects()
        assert ok is False


class TestGetProject:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(return_value=_mock_response(200, {"key": "P"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_project("P")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/projects/P"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(return_value=_mock_response(404, {"error": "x"}))
        ok, _ = await jira.get_project("X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_project("X")
        assert ok is False


class TestGetProjectMetadata:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(return_value=_mock_response(200, {
            "key": "P", "name": "Proj",
            "issueTypes": [{"id": "1", "name": "Task", "description": "", "subtask": False}],
            "components": [{"id": "c1", "name": "Core", "description": "c"}],
            "lead": {"displayName": "Alice"},
        }))
        ok, payload = await jira.get_project_metadata("P")
        data = json.loads(payload)
        assert ok is True
        assert data["metadata"]["project_key"] == "P"
        assert data["metadata"]["lead"] == "Alice"
        assert data["metadata"]["issue_types"][0]["name"] == "Task"
        assert data["metadata"]["components"][0]["name"] == "Core"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(return_value=_mock_response(404, {"error": "x"}))
        ok, _ = await jira.get_project_metadata("X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_project = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_project_metadata("X")
        assert ok is False


# ===========================================================================
# Tool: get_issues / get_issue
# ===========================================================================

class TestGetIssues:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(200, {"issues": [{"key": "P-1", "fields": {}}]}),
        )
        jira.client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_issues("P")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["issues"][0]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(500, {"error": "x"}),
        )
        ok, _ = await jira.get_issues("P")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_issues("P")
        assert ok is False

    @pytest.mark.asyncio
    async def test_days_parameter_used_in_jql(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(200, {"issues": []}),
        )
        jira.client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.get_issues("P", days=7, max_results=10)
        called_kwargs = jira.client.search_and_reconsile_issues_using_jql_post.call_args.kwargs
        assert "-7d" in called_kwargs["jql"]
        assert called_kwargs["maxResults"] == 10


class TestGetIssue:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_issue = AsyncMock(return_value=_mock_response(200, {
            "key": "P-1", "fields": {"summary": "x", "parent": {"key": "P-0"}},
        }))
        jira.client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_issue("P-1")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_issue = AsyncMock(return_value=_mock_response(404, {"error": "x"}))
        ok, _ = await jira.get_issue("P-1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_issue = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_issue("P-1")
        assert ok is False


# ===========================================================================
# Tool: search_issues
# ===========================================================================

class TestSearchIssues:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(200, {"issues": [{"key": "P-1", "fields": {}}]}),
        )
        jira.client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.search_issues("project = \"P\" AND updated >= -7d")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["issues"][0]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_jql_auto_fix_warning_included(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(200, {"issues": []}),
        )
        jira.client.get_fields = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.search_issues("resolution = Unresolved")
        data = json.loads(payload)
        assert ok is True
        assert "warning" in data
        assert data["fixed_jql"] != data["original_jql"]

    @pytest.mark.asyncio
    async def test_success_json_parse_failure(self):
        jira = _build_jira()
        resp = _mock_response(200)
        resp.json = MagicMock(side_effect=ValueError("broken"))
        resp.text = MagicMock(return_value="not json")
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=resp)
        ok, payload = await jira.search_issues("project = P AND updated >= -7d")
        assert ok is False
        assert "Failed to parse response" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_mock_response(400, {"errorMessages": ["bad"]}),
        )
        ok, payload = await jira.search_issues("bad jql")
        data = json.loads(payload)
        assert ok is False
        assert data.get("jql_query") == "bad jql"

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.search_and_reconsile_issues_using_jql_post = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.search_issues("jql")
        data = json.loads(payload)
        assert ok is False
        assert data["jql_query"] == "jql"


# ===========================================================================
# Tool: add_comment / get_comments
# ===========================================================================

class TestAddComment:
    @pytest.mark.asyncio
    async def test_string_comment_success(self):
        jira = _build_jira()
        jira.client.add_comment = AsyncMock(return_value=_mock_response(201, {"id": "c1"}))
        ok, _ = await jira.add_comment("P-1", "hi")
        assert ok is True
        sent = jira.client.add_comment.call_args.kwargs["body_body"]
        assert sent["type"] == "doc"

    @pytest.mark.asyncio
    async def test_dict_adf_passthrough(self):
        jira = _build_jira()
        jira.client.add_comment = AsyncMock(return_value=_mock_response(201, {"id": "c1"}))
        adf = {"type": "doc", "version": 1, "content": []}
        ok, _ = await jira.add_comment("P-1", adf)
        assert ok is True
        assert jira.client.add_comment.call_args.kwargs["body_body"] is adf

    @pytest.mark.asyncio
    async def test_empty_string_comment_rejected(self):
        jira = _build_jira()
        ok, payload = await jira.add_comment("P-1", "")
        assert ok is False
        assert "cannot be empty" in json.loads(payload)["guidance"]

    @pytest.mark.asyncio
    async def test_invalid_comment_type_rejected(self):
        jira = _build_jira()
        ok, payload = await jira.add_comment("P-1", 42)
        assert ok is False
        assert "Invalid comment type" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.add_comment = AsyncMock(return_value=_mock_response(500, {"error": "x"}))
        ok, _ = await jira.add_comment("P-1", "hi")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.add_comment = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.add_comment("P-1", "hi")
        assert ok is False


class TestGetComments:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_comments = AsyncMock(
            return_value=_mock_response(200, {"comments": [{"id": "1", "body": "hi"}]}),
        )
        ok, payload = await jira.get_comments("P-1")
        assert ok is True
        assert json.loads(payload)["data"]["comments"][0]["body"] == "hi"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_comments = AsyncMock(return_value=_mock_response(404, {"error": "x"}))
        ok, _ = await jira.get_comments("P-1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_comments = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_comments("P-1")
        assert ok is False


# ===========================================================================
# Tool: search_users
# ===========================================================================

class TestSearchUsers:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.find_users_for_picker = AsyncMock(
            return_value=_mock_response(200, {
                "users": [
                    {"accountId": "a1", "displayName": "Alice", "html": "Alice (a@x)"},
                    {"accountId": None, "displayName": "skip"},
                ],
            }),
        )
        ok, payload = await jira.search_users("alice")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        assert data["data"]["results"][0]["emailAddress"] == "a@x"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        jira = _build_jira()
        ok, payload = await jira.search_users("   ")
        assert ok is False
        assert "cannot be empty" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_users_returned_as_list_not_dict(self):
        jira = _build_jira()
        jira.client.find_users_for_picker = AsyncMock(
            return_value=_mock_response(200, [{"accountId": "a1", "displayName": "A"}]),
        )
        ok, payload = await jira.search_users("alice")
        assert ok is True
        data = json.loads(payload)["data"]
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.find_users_for_picker = AsyncMock(return_value=_mock_response(500, {"error": "x"}))
        ok, _ = await jira.search_users("alice")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.find_users_for_picker = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.search_users("alice")
        assert ok is False
