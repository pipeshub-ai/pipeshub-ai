"""Comprehensive unit tests for `app.agents.actions.jira_data_center.jira_data_center`.

The Data Center toolset is a standalone sibling of the Cloud ``Jira`` action. It
wraps ``JiraDataSource`` and calls only its ``*_v2`` (REST v2) methods, applying
the Data Center deltas: plain/wiki bodies (no ADF), name/key users (no accountId),
offset-paged JQL search, and browse URLs built from the instance base URL.

Covers:
* the Pydantic input schemas + `model_validator` normalisers
* pure helpers: `_get_error_guidance`, `_normalize_description`,
  `_normalize_field_name`, `_validate_and_fix_jql`, `_clean_issue_fields`,
  `_add_urls_to_issue_references`, `_normalize_issues_in_response`,
  `_validate_issue_fields`, `_handle_response`, `_parse_bean`,
  `_parse_allowed_values`, `_comment_url`, `_clean_search_response`
* async helpers: `_resolve_user_to_name`, `_get_site_url`,
  `_fetch_and_cache_field_schema`, `_fetch_issue_types`, `_fetch_create_fields`
* every `@tool` method: success + API-error + exception paths, plus the
  assignee/reporter-drop retry branches, the status-transition branch, the
  issue-type-first edit ordering, the JQL auto-fix path, the truncation note in
  `search_issues`, and the DC user-picker email-scrape branch.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.jira_data_center.jira_data_center import (
    AddCommentInput,
    CreateIssueInput,
    GetCommentsInput,
    GetCreateIssueFieldsInput,
    GetIssueInput,
    GetIssuesInput,
    GetProjectInput,
    GetProjectMetadataInput,
    JiraDataCenter,
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


def _build_jira(client_methods: dict | None = None) -> JiraDataCenter:
    """Instantiate JiraDataCenter bypassing the ToolsetBuilder decorator; stub the
    underlying `JiraDataSource` (``self.client``) with a MagicMock so each test can
    set per-method return values."""
    jira = JiraDataCenter.__new__(JiraDataCenter)
    client = MagicMock()
    for name, value in (client_methods or {}).items():
        setattr(client, name, value)
    jira.client = client
    jira._site_url = None
    jira._field_schema_cache = None
    jira._create_fields_cache = {}
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
        assert GetIssuesInput(project_key="P").project_key == "P"

    def test_project_alias_extracted(self):
        assert GetIssuesInput(project="P").project_key == "P"

    def test_projectKey_camel_alias_extracted(self):
        assert GetIssuesInput(projectKey="P").project_key == "P"

    def test_non_dict_input_returned_as_is(self):
        assert GetIssuesInput.extract_project_key(None) is None


class TestGetIssueInput:
    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_all_issue_key_aliases_extracted(self, alias):
        assert GetIssueInput(**{alias: "P-1"}).issue_key == "P-1"

    def test_non_dict_input_returned_as_is(self):
        assert GetIssueInput.extract_issue_key(123) == 123


class TestSearchIssuesInput:
    def test_defaults(self):
        assert SearchIssuesInput(jql="project = P").maxResults == 50

    def test_custom_max_results(self):
        assert SearchIssuesInput(jql="project = P", maxResults=200).maxResults == 200


class TestAddCommentInput:
    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_all_issue_key_aliases(self, alias):
        assert AddCommentInput(comment="hi", **{alias: "P-1"}).issue_key == "P-1"

    def test_non_dict_passthrough(self):
        assert AddCommentInput.extract_issue_key([1, 2]) == [1, 2]


class TestSearchUsersInput:
    def test_default_max_results(self):
        assert SearchUsersInput(query="alice").max_results == 50


class TestUpdateIssueInput:
    def test_canonical_issue_key(self):
        assert UpdateIssueInput(issue_key="P-1").issue_key == "P-1"

    @pytest.mark.parametrize("alias", ["issueId", "issueIdOrKey", "issue_id", "issueKey"])
    def test_alias_issue_key(self, alias):
        assert UpdateIssueInput(**{alias: "P-1"}).issue_key == "P-1"

    def test_issuetype_dict_alias_extracted(self):
        data = UpdateIssueInput(issue_key="P-1", issuetype={"name": "Story"}, summary="x")
        assert data.issue_type_name == "Story"

    def test_issuetype_string_alias_extracted(self):
        data = UpdateIssueInput(issue_key="P-1", issue_type="Story")
        assert data.issue_type_name == "Story"

    def test_direct_fields_wrapper_extracted(self):
        data = UpdateIssueInput(
            issue_key="P-1",
            fields={"summary": "new", "description": "d", "priority_name": "High",
                    "status": "Done", "reporter_name": "bob"},
        )
        assert data.summary == "new"
        assert data.description == "d"
        assert data.priority_name == "High"
        assert data.status == "Done"
        assert data.reporter_name == "bob"

    def test_non_dict_passthrough(self):
        assert UpdateIssueInput.extract_nested_values(None) is None


class TestGetProjectMetadataInput:
    def test_project_alias_extracted(self):
        assert GetProjectMetadataInput(project="P").project_key == "P"

    def test_non_dict_passthrough(self):
        assert GetProjectMetadataInput.extract_project_key(None) is None


class TestMiscSchemas:
    def test_get_project_input(self):
        assert GetProjectInput(project_key="P").project_key == "P"

    def test_get_comments_input(self):
        assert GetCommentsInput(issue_key="P-1").issue_key == "P-1"

    def test_get_create_issue_fields_input_aliases(self):
        obj = GetCreateIssueFieldsInput.model_validate({"projectKey": "PA", "issueType": "Bug"})
        assert obj.project_key == "PA"
        assert obj.issue_type_name == "Bug"

    def test_get_create_issue_fields_input_non_dict(self):
        assert GetCreateIssueFieldsInput.normalize("x") == "x"


# ===========================================================================
# Pure helper methods
# ===========================================================================

class TestGetErrorGuidance:
    @pytest.mark.parametrize("status", [401, 403, 404, 400])
    def test_known_codes_return_guidance(self, status):
        assert _build_jira()._get_error_guidance(status) is not None

    def test_unknown_code_returns_none(self):
        assert _build_jira()._get_error_guidance(418) is None


class TestNormalizeDescription:
    def test_slack_mention_markup_replaced(self):
        assert _build_jira()._normalize_description("ping <@U12345> please") == "ping @U12345 please"

    def test_plain_text_unchanged(self):
        assert _build_jira()._normalize_description("no mentions here") == "no mentions here"


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
        assert warn is not None and "resolution" in warn

    def test_fixes_current_user_without_parens(self):
        jql, warn = _build_jira()._validate_and_fix_jql("assignee = currentUser AND updated >= -7d")
        assert "currentUser()" in jql
        assert warn is not None

    def test_no_warning_when_query_already_correct(self):
        jql, warn = _build_jira()._validate_and_fix_jql(
            'project = "P" AND assignee = currentUser() AND resolution IS EMPTY'
        )
        assert warn is None


class TestValidateIssueFields:
    def test_missing_project_key(self):
        ok, msg = _build_jira()._validate_issue_fields({"summary": "s", "issuetype": {"name": "Task"}})
        assert ok is False and "Project key" in msg

    def test_missing_summary(self):
        ok, msg = _build_jira()._validate_issue_fields({"project": {"key": "P"}, "issuetype": {"name": "Task"}})
        assert ok is False and "Summary" in msg

    def test_missing_issue_type_name(self):
        ok, msg = _build_jira()._validate_issue_fields({"project": {"key": "P"}, "summary": "s"})
        assert ok is False and "Issue type" in msg

    def test_description_string_accepted(self):
        fields = {"project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
                  "description": "plain wiki text"}
        ok, _ = _build_jira()._validate_issue_fields(fields)
        assert ok is True
        # DC keeps the description a plain string (no ADF conversion).
        assert fields["description"] == "plain wiki text"

    def test_description_non_string_rejected(self):
        fields = {"project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
                  "description": {"type": "doc"}}
        ok, msg = _build_jira()._validate_issue_fields(fields)
        assert ok is False and "plain-text string" in msg

    def test_assignee_missing_name(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "assignee": {"foo": "bar"},
        })
        assert ok is False and "assignee" in msg

    def test_reporter_not_dict_rejected(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "reporter": "not-a-dict",
        })
        assert ok is False and "reporter" in msg

    def test_priority_missing_name(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "priority": {"id": "1"},
        })
        assert ok is False and "Priority" in msg

    def test_components_not_list(self):
        ok, msg = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "components": "not-a-list",
        })
        assert ok is False and "Components" in msg

    def test_components_missing_name(self):
        ok, _ = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "components": [{"foo": "bar"}],
        })
        assert ok is False

    def test_all_valid(self):
        ok, _ = _build_jira()._validate_issue_fields({
            "project": {"key": "P"}, "summary": "s", "issuetype": {"name": "Task"},
            "assignee": {"name": "jdoe"},
            "reporter": {"name": "bob"},
            "priority": {"name": "High"},
            "components": [{"name": "c1"}],
        })
        assert ok is True

    def test_exception_path(self):
        # `fields` is not a dict → `.get` raises inside the try and is caught.
        ok, msg = _build_jira()._validate_issue_fields(None)
        assert ok is False and "Validation error" in msg


# ===========================================================================
# _handle_response (pure)
# ===========================================================================

class TestHandleResponse:
    def test_success_200_with_body(self):
        ok, payload = _build_jira()._handle_response(
            _mock_response(HttpStatusCode.SUCCESS.value, {"hello": "world"}), "ok",
        )
        data = json.loads(payload)
        assert ok is True
        assert data["message"] == "ok"
        assert data["data"] == {"hello": "world"}

    def test_success_204_no_content_returns_empty_data(self):
        ok, payload = _build_jira()._handle_response(
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

    def test_error_message_key(self):
        ok, payload = _build_jira()._handle_response(
            _mock_response(500, {"message": "boom"}), "ignored",
        )
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "boom"
        assert data["status_code"] == 500

    def test_error_with_errorMessages_list(self):
        ok, payload = _build_jira()._handle_response(_mock_response(400, {"errorMessages": ["Bad JQL"]}), "x")
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "Bad JQL"

    def test_error_message_with_details_appended(self):
        ok, payload = _build_jira()._handle_response(
            _mock_response(400, {"message": "bad", "errors": {"field": "x"}}), "y",
        )
        data = json.loads(payload)
        assert ok is False
        assert "bad" in data["details"]

    def test_error_json_dict_without_known_keys_dumps_body(self):
        ok, payload = _build_jira()._handle_response(_mock_response(500, {"weird": "shape"}), "x")
        data = json.loads(payload)
        assert ok is False
        assert "HTTP 500" in data["error"]
        assert "weird" in data["details"]

    def test_error_non_dict_json_body(self):
        ok, payload = _build_jira()._handle_response(_mock_response(500, "string body"), "x")
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
        assert "plain text error" in data["details"]

    def test_error_guidance_attached_for_known_status(self):
        ok, payload = _build_jira()._handle_response(_mock_response(401, {"message": "unauth"}), "x", include_guidance=True)
        data = json.loads(payload)
        assert "guidance" in data and "Authentication" in data["guidance"]

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
# _parse_bean
# ===========================================================================

class TestParseBean:
    def test_extracts_readable_sprint_fields(self):
        raw = ("com.atlassian.greenhopper.service.sprint.Sprint@1a2b3c["
               "id=5,name=Sprint 1,state=ACTIVE,startDate=2026-01-01,endDate=<null>,goal=ship]")
        out = JiraDataCenter._parse_bean(raw)
        assert out["name"] == "Sprint 1"
        assert out["state"] == "ACTIVE"
        assert out["startDate"] == "2026-01-01"
        # <null> values are dropped.
        assert "endDate" not in out

    def test_returns_none_when_nothing_readable(self):
        assert JiraDataCenter._parse_bean("SomeBean@0000[foo=bar]") is None


# ===========================================================================
# _parse_allowed_values
# ===========================================================================

class TestParseAllowedValues:
    def test_normalises_dict_entries(self):
        result = _build_jira()._parse_allowed_values([
            {"id": 1, "name": "High"},
            {"value": "Low"},
            "skip",
            {},
        ])
        assert result == [{"id": "1", "name": "High"}, {"name": "Low"}]

    def test_caps_at_twenty_entries(self):
        raw = [{"id": str(i), "name": f"Opt{i}"} for i in range(25)]
        assert len(_build_jira()._parse_allowed_values(raw)) == 20


# ===========================================================================
# _comment_url
# ===========================================================================

class TestCommentUrl:
    def test_builds_focused_comment_permalink(self):
        url = _build_jira()._comment_url("https://jira.co", "PA-1", "999")
        assert url.startswith("https://jira.co/browse/PA-1?focusedCommentId=999")
        assert "#comment-999" in url


# ===========================================================================
# _clean_issue_fields
# ===========================================================================

class TestCleanIssueFields:
    def test_non_dict_passthrough(self):
        assert _build_jira()._clean_issue_fields("not a dict") == "not a dict"

    def test_missing_fields_passthrough(self):
        assert _build_jira()._clean_issue_fields({"key": "P-1"}) == {"key": "P-1"}

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

    def test_removes_empty_list(self):
        out = _build_jira()._clean_issue_fields({"key": "P-1", "fields": {"labels": []}})
        assert "labels" not in out["fields"]

    def test_removes_java_bean_string(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"customfield_dev": "SummaryBean@742a6b3[fixed=0]"},
        })
        assert "customfield_dev" not in out["fields"]

    def test_removes_lexorank_token(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"customfield_rank": "0|i0004v:"},
        })
        assert "customfield_rank" not in out["fields"]

    def test_simplifies_sprint_bean_list(self):
        sprint = ("com.x.Sprint@abcd[id=1,name=Sprint 2,state=CLOSED,"
                  "startDate=2026-02-01,endDate=2026-02-14,goal=done]")
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"customfield_sprint": [sprint]},
        })
        assert out["fields"]["customfield_sprint"][0]["name"] == "Sprint 2"

    def test_unparseable_bean_list_removed(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"customfield_sprint": ["Bean@ffff[foo=bar]"]},
        })
        assert "customfield_sprint" not in out["fields"]

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

    def test_user_fields_simplified_dc_name_key(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {
                "assignee": {"name": "jdoe", "key": "jdoe", "displayName": "J Doe", "emailAddress": "j@x"},
                "reporter": {"junk": "removed"},
                "creator": {"name": "svc"},
            },
        })
        assert "reporter" not in out["fields"]  # no useful DC identity fields
        assert out["fields"]["assignee"]["name"] == "jdoe"
        assert out["fields"]["creator"] == {"name": "svc"}

    def test_parent_simplified(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-2",
            "fields": {"parent": {"key": "P-1", "id": "10", "fields": {"summary": "parent sum"}}},
        })
        parent = out["fields"]["parent"]
        assert parent["key"] == "P-1"
        assert parent["summary"] == "parent sum"

    def test_parent_non_dict_inner_fields(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-2",
            "fields": {"parent": {"key": "P-1", "id": "10", "fields": "bad"}},
        })
        assert out["fields"]["parent"]["summary"] is None

    def test_comment_block_trimmed_to_last_three(self):
        comments = [{"id": str(i), "body": f"c{i}", "created": "2026",
                     "author": {"name": f"u{i}", "displayName": f"U{i}"}} for i in range(5)]
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"comment": {"comments": comments, "total": 5}},
        })
        trimmed = out["fields"]["comment"]["comments"]
        assert len(trimmed) == 3
        assert trimmed[0]["body"] == "c2"
        assert trimmed[0]["author"]["name"] == "u2"
        assert out["fields"]["comment"]["total"] == 5

    def test_comment_block_with_non_dict_entries_skipped(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"comment": {"comments": ["not-a-dict", {"body": "ok"}]}},
        })
        bodies = [c["body"] for c in out["fields"]["comment"]["comments"]]
        assert bodies == ["ok"]

    def test_empty_comment_block_removed(self):
        out = _build_jira()._clean_issue_fields({"key": "P-1", "fields": {"comment": {"comments": []}}})
        assert "comment" not in out["fields"]

    def test_attachments_simplified(self):
        out = _build_jira()._clean_issue_fields({
            "key": "P-1",
            "fields": {"attachment": [
                {"id": "1", "filename": "f", "size": 10, "mimeType": "text/plain", "junk": "no"},
                "not-a-dict",
            ]},
        })
        assert out["fields"]["attachment"][0]["filename"] == "f"
        assert "junk" not in out["fields"]["attachment"][0]


# ===========================================================================
# _normalize_issues_in_response + _apply_field_aliases
# ===========================================================================

class TestNormalizeIssuesInResponse:
    @pytest.mark.asyncio
    async def test_non_dict_passthrough(self):
        assert await _build_jira()._normalize_issues_in_response("not-a-dict", {}) == "not-a-dict"

    @pytest.mark.asyncio
    async def test_issues_list_normalised(self):
        field_schema = {"customfield_10063": {"name": "Story Points", "normalized": "story_points"}}
        data = {"issues": [{"fields": {"customfield_10063": 5, "customfield_other": None}}]}
        out = await _build_jira()._normalize_issues_in_response(data, field_schema)
        assert out["issues"][0]["fields"]["story_points"] == 5

    @pytest.mark.asyncio
    async def test_single_issue_normalised(self):
        field_schema = {"customfield_10063": {"name": "Story Points", "normalized": "story_points"}}
        out = await _build_jira()._normalize_issues_in_response({"fields": {"customfield_10063": 3}}, field_schema)
        assert out["fields"]["story_points"] == 3

    @pytest.mark.asyncio
    async def test_none_custom_field_not_added(self):
        field_schema = {"customfield_10063": {"name": "SP", "normalized": "sp"}}
        out = await _build_jira()._normalize_issues_in_response({"fields": {"customfield_10063": None}}, field_schema)
        assert "sp" not in out["fields"]

    def test_alias_not_applied_when_target_already_present(self):
        field_schema = {"customfield_1": {"name": "SP", "normalized": "sp"}}
        fields = {"customfield_1": 5, "sp": "already"}
        JiraDataCenter._apply_field_aliases(fields, field_schema)
        # existing "sp" wins; raw customfield_1 stays untouched.
        assert fields["sp"] == "already"
        assert fields["customfield_1"] == 5


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
        _build_jira()._add_urls_to_issue_references({"fields": "bad"}, "https://x")  # no raise

    def test_parent_gets_url(self):
        issue = {"fields": {"parent": {"key": "PA-1"}}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["parent"]["url"] == "https://site/browse/PA-1"

    def test_only_issue_key_shaped_refs_get_urls(self):
        # DC: a user key ("jdoe") or project key ("PA", single-letter-then-nothing)
        # must NOT be stamped with /browse — only genuine "PROJ-123" issue refs.
        issue = {"fields": {
            "customfield_epic": {"key": "PA-2", "summary": "epic"},
            "assignee": {"key": "jdoe"},
            "project": {"key": "PA"},
        }}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["customfield_epic"]["url"] == "https://site/browse/PA-2"
        assert "url" not in issue["fields"]["assignee"]
        assert "url" not in issue["fields"]["project"]

    def test_list_of_issue_refs_gets_urls(self):
        issue = {"fields": {"related": [{"key": "PA-3"}, {"key": "PA-4"}, {"other": "no key"}]}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["related"][0]["url"] == "https://site/browse/PA-3"
        assert issue["fields"]["related"][1]["url"] == "https://site/browse/PA-4"
        assert "url" not in issue["fields"]["related"][2]

    def test_skipped_fields_untouched(self):
        issue = {"fields": {"key": "ignore", "self": "ignore", "parent": {"key": "PA-1"}}}
        _build_jira()._add_urls_to_issue_references(issue, "https://site")
        assert issue["fields"]["key"] == "ignore"


# ===========================================================================
# _clean_search_response
# ===========================================================================

class TestCleanSearchResponse:
    def test_strips_pagination_and_cleans_issue_fields(self):
        data = {
            "startAt": 0, "maxResults": 50, "total": 1, "expand": "schema",
            "issues": [{"key": "P-1", "self": "x", "fields": {"summary": "s", "labels": []}}],
        }
        out = _build_jira()._clean_search_response(data)
        assert "total" not in out
        assert "startAt" not in out
        assert "labels" not in out["issues"][0]["fields"]


# ===========================================================================
# _resolve_user_to_name
# ===========================================================================

class TestResolveUserToName:
    @pytest.mark.asyncio
    async def test_assignable_user_found_first(self):
        client = MagicMock()
        client.find_assignable_users_v2 = AsyncMock(return_value=_mock_response(200, [{"name": "jdoe"}]))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_name("P", "alice") == "jdoe"

    @pytest.mark.asyncio
    async def test_falls_back_to_key(self):
        client = MagicMock()
        client.find_assignable_users_v2 = AsyncMock(return_value=_mock_response(200, [{"key": "k1"}]))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_name("P", "alice") == "k1"

    @pytest.mark.asyncio
    async def test_falls_back_to_global_search(self):
        client = MagicMock()
        client.find_assignable_users_v2 = AsyncMock(return_value=_mock_response(200, []))
        client.get_user_search_v2 = AsyncMock(return_value=_mock_response(200, [{"name": "gjdoe"}]))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_name("P", "alice") == "gjdoe"

    @pytest.mark.asyncio
    async def test_none_when_no_match(self):
        client = MagicMock()
        client.find_assignable_users_v2 = AsyncMock(return_value=_mock_response(200, []))
        client.get_user_search_v2 = AsyncMock(return_value=_mock_response(200, []))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_name("P", "alice") is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        client = MagicMock()
        client.find_assignable_users_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        jira = _build_jira()
        jira.client = client
        assert await jira._resolve_user_to_name("P", "alice") is None


# ===========================================================================
# _get_site_url
# ===========================================================================

class TestGetSiteUrl:
    @pytest.mark.asyncio
    async def test_uses_base_url_stripped(self):
        jira = _build_jira()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(return_value="https://jira.co/")
        jira.client._client = client_obj
        assert await jira._get_site_url() == "https://jira.co"
        assert jira._site_url == "https://jira.co"

    @pytest.mark.asyncio
    async def test_cached_url_returned_without_call(self):
        jira = _build_jira()
        jira._site_url = "https://cached.co"
        assert await jira._get_site_url() == "https://cached.co"

    @pytest.mark.asyncio
    async def test_no_get_base_url_attr_returns_none(self):
        jira = _build_jira()
        jira.client._client = MagicMock(spec=[])
        assert await jira._get_site_url() is None

    @pytest.mark.asyncio
    async def test_empty_base_url_returns_none(self):
        jira = _build_jira()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(return_value="")
        jira.client._client = client_obj
        assert await jira._get_site_url() is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        jira = _build_jira()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(side_effect=RuntimeError("boom"))
        jira.client._client = client_obj
        assert await jira._get_site_url() is None


# ===========================================================================
# _fetch_and_cache_field_schema
# ===========================================================================

class TestFetchAndCacheFieldSchema:
    @pytest.mark.asyncio
    async def test_success_builds_mapping(self):
        client = MagicMock()
        client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, [
            {"id": "customfield_10063", "name": "Story Points"},
            {"id": "customfield_10064", "name": "Epic Link"},
            {"id": "", "name": "ignored"},
        ]))
        jira = _build_jira()
        jira.client = client
        schema = await jira._fetch_and_cache_field_schema()
        assert schema["customfield_10063"]["normalized"] == "story_points"
        assert schema["customfield_10064"]["normalized"] == "epic_link"

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        client = MagicMock()
        client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        jira = _build_jira()
        jira.client = client
        await jira._fetch_and_cache_field_schema()
        await jira._fetch_and_cache_field_schema()
        client.get_fields_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self):
        client = MagicMock()
        client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, {"not": "a list"}))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}

    @pytest.mark.asyncio
    async def test_non_success_response_returns_empty(self):
        client = MagicMock()
        client.get_fields_v2 = AsyncMock(return_value=_mock_response(500, {"err": "x"}))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        client = MagicMock()
        client.get_fields_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        jira = _build_jira()
        jira.client = client
        assert await jira._fetch_and_cache_field_schema() == {}


# ===========================================================================
# _fetch_issue_types (paginated)
# ===========================================================================

class TestFetchIssueTypes:
    @pytest.mark.asyncio
    async def test_paginates_until_total_reached(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 2}),
            _mock_response(200, {"values": [{"id": "2", "name": "Task"}], "total": 2}),
        ])
        out = await jira._fetch_issue_types("P")
        assert [t["name"] for t in out] == ["Bug", "Task"]

    @pytest.mark.asyncio
    async def test_issue_types_key_variant(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"issueTypes": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        out = await jira._fetch_issue_types("P")
        assert out[0]["name"] == "Bug"

    @pytest.mark.asyncio
    async def test_http_error_breaks(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(return_value=_mock_response(500, {}))
        assert await jira._fetch_issue_types("P") == []

    @pytest.mark.asyncio
    async def test_exception_breaks(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(side_effect=RuntimeError("net"))
        assert await jira._fetch_issue_types("P") == []

    @pytest.mark.asyncio
    async def test_json_parse_failure_breaks(self):
        jira = _build_jira()
        bad = _mock_response(200)
        bad.json = MagicMock(side_effect=ValueError("bad"))
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(return_value=bad)
        assert await jira._fetch_issue_types("P") == []

    @pytest.mark.asyncio
    async def test_non_list_page_breaks(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": "bad", "total": 5}),
        )
        assert await jira._fetch_issue_types("P") == []


# ===========================================================================
# _fetch_create_fields
# ===========================================================================

class TestFetchCreateFields:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_api(self):
        jira = _build_jira()
        cached = [{"field_id": "summary", "name": "Summary", "required": True,
                   "schema_type": "string", "allowed_values": [], "has_default_value": False,
                   "operations": []}]
        jira._create_fields_cache["PROJ:bug"] = cached
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == cached

    @pytest.mark.asyncio
    async def test_exact_match_and_pagination(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "10001", "name": "Bug"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(side_effect=[
            _mock_response(200, {
                "values": [{"fieldId": "summary", "name": "Summary", "required": True,
                            "schema": {"type": "string"}}],
                "total": 2,
            }),
            _mock_response(200, {
                "values": [{"fieldId": "customfield_100", "name": "Severity", "required": False,
                            "schema": {"type": "option"},
                            "allowedValues": [{"id": "1", "value": "Critical"}]}],
                "total": 2,
            }),
        ])
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert len(fields) == 2
        assert jira._create_fields_cache["PROJ:bug"]

    @pytest.mark.asyncio
    async def test_fuzzy_issue_type_match(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "10002", "name": "User Story"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [], "total": 0}),
        )
        fields, err = await jira._fetch_create_fields("PROJ", "story")
        assert err is None
        assert fields == []

    @pytest.mark.asyncio
    async def test_issue_type_not_found_lists_available(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}, {"id": "2", "name": "Task"}], "total": 2}),
        )
        fields, err = await jira._fetch_create_fields("PROJ", "Epic")
        assert fields == []
        assert "Available: Bug, Task" in err

    @pytest.mark.asyncio
    async def test_no_issue_types_at_all(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [], "total": 0}),
        )
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert fields == []
        assert "not found" in err and "Available" not in err

    @pytest.mark.asyncio
    async def test_fields_http_error_returns_partial(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(return_value=_mock_response(500, {}))
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == []

    @pytest.mark.asyncio
    async def test_fields_fetch_exception_breaks(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(side_effect=RuntimeError("net"))
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == []

    @pytest.mark.asyncio
    async def test_fields_json_parse_failure(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        bad = _mock_response(200)
        bad.json = MagicMock(side_effect=ValueError("bad"))
        jira.client.get_create_meta_field_options_v2 = AsyncMock(return_value=bad)
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == []

    @pytest.mark.asyncio
    async def test_non_list_fields_payload_stops(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": "bad", "total": 0}),
        )
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == []

    @pytest.mark.asyncio
    async def test_skips_fields_without_id(self):
        jira = _build_jira()
        jira.client.get_create_meta_issue_types_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"id": "1", "name": "Bug"}], "total": 1}),
        )
        jira.client.get_create_meta_field_options_v2 = AsyncMock(
            return_value=_mock_response(200, {"values": [{"name": "NoId", "required": False, "schema": {"type": "string"}}], "total": 1}),
        )
        fields, err = await jira._fetch_create_fields("PROJ", "Bug")
        assert err is None
        assert fields == []


# ===========================================================================
# Tool: validate_connection
# ===========================================================================

class TestValidateConnection:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_current_user_v2 = AsyncMock(return_value=_mock_response(200, {
            "name": "jdoe", "key": "jdoe", "displayName": "J Doe", "emailAddress": "j@x",
        }))
        ok, payload = await jira.validate_connection()
        data = json.loads(payload)
        assert ok is True
        assert data["user"]["name"] == "jdoe"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_current_user_v2 = AsyncMock(return_value=_mock_response(401, {"message": "x"}))
        ok, payload = await jira.validate_connection()
        assert ok is False
        assert "guidance" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_exception_path(self):
        jira = _build_jira()
        jira.client.get_current_user_v2 = AsyncMock(side_effect=RuntimeError("boom"))
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
        jira.client.get_current_user_v2 = AsyncMock(return_value=_mock_response(200, {
            "name": "jdoe", "key": "jdoe", "displayName": "J", "emailAddress": "j@x",
        }))
        ok, payload = await jira.get_current_user()
        assert ok is True
        assert json.loads(payload)["data"]["name"] == "jdoe"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_current_user_v2 = AsyncMock(return_value=_mock_response(403, {"message": "x"}))
        ok, _ = await jira.get_current_user()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_current_user_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.get_current_user()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: get_create_issue_fields
# ===========================================================================

class TestGetCreateIssueFields:
    @pytest.mark.asyncio
    async def test_success_formats_required_and_optional(self):
        jira = _build_jira()
        jira._fetch_create_fields = AsyncMock(return_value=([
            {"field_id": "summary", "name": "Summary", "required": True,
             "schema_type": "string", "allowed_values": [], "has_default_value": False},
            {"field_id": "customfield_100", "name": "Severity", "required": True,
             "schema_type": "option", "allowed_values": [{"id": "1", "name": "High"}], "has_default_value": False},
            {"field_id": "reporter", "name": "Reporter", "required": True,
             "schema_type": "user", "allowed_values": [], "has_default_value": True},
            {"field_id": "duedate", "name": "Due Date", "required": False,
             "schema_type": "date", "allowed_values": [], "has_default_value": False},
            {"field_id": "customfield_points", "name": "Points", "required": False,
             "schema_type": "number", "allowed_values": [], "has_default_value": False},
            {"field_id": "customfield_dt", "name": "When", "required": False,
             "schema_type": "datetime", "allowed_values": [], "has_default_value": False},
            {"field_id": "labels", "name": "Labels", "required": False,
             "schema_type": "array", "allowed_values": [], "has_default_value": False},
            {"field_id": "assignee", "name": "Assignee", "required": False,
             "schema_type": "user", "allowed_values": [], "has_default_value": False},
            {"field_id": "customfield_str", "name": "Note", "required": False,
             "schema_type": "string", "allowed_values": [], "has_default_value": False},
        ], None))
        ok, payload = await jira.get_create_issue_fields("PROJ", "Bug")
        data = json.loads(payload)
        assert ok is True
        req_ids = {f["field_id"] for f in data["required_fields"]}
        assert "summary" in req_ids
        assert "customfield_100" in req_ids
        assert "reporter" not in req_ids  # auto-filled default dropped from required
        # pass_via routing: standard field vs custom_fields
        opt = {f["field_id"]: f for f in data["optional_fields"]}
        assert opt["labels"]["pass_via"] == "named_parameter"
        assert opt["customfield_points"]["pass_via"] == "custom_fields"
        assert "Jira will auto-fill" not in json.dumps(data["required_fields"])

    @pytest.mark.asyncio
    async def test_error_from_fetch(self):
        jira = _build_jira()
        jira._fetch_create_fields = AsyncMock(return_value=([], "Issue type 'X' not found"))
        ok, payload = await jira.get_create_issue_fields("PROJ", "X")
        assert ok is False
        assert "not found" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira._fetch_create_fields = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await jira.get_create_issue_fields("PROJ", "Bug")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# Tool: create_issue
# ===========================================================================

class TestCreateIssue:
    @pytest.mark.asyncio
    async def test_success_minimum_fields(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(return_value=_mock_response(201, {"key": "P-1", "id": "1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.create_issue("P", "s", "Task")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_resolves_assignee_query(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(return_value=_mock_response(201, {"key": "P-1"}))
        with patch.object(jira, "_resolve_user_to_name", AsyncMock(return_value="jdoe")), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.create_issue("P", "s", "Task", assignee_query="alice")
        assert ok is True
        fields = jira.client.create_issue_v2.call_args.kwargs["fields"]
        assert fields["assignee"] == {"name": "jdoe"}

    @pytest.mark.asyncio
    async def test_all_optional_fields_passthrough(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(return_value=_mock_response(201, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.create_issue(
                "P", "s", "Task",
                description="d", assignee_name="jdoe", priority_name="High",
                labels=["l1"], components=["c1"], parent_key="P-0",
                custom_fields={"customfield_10016": 5},
            )
        fields = jira.client.create_issue_v2.call_args.kwargs["fields"]
        assert fields["priority"] == {"name": "High"}
        assert fields["labels"] == ["l1"]
        assert fields["components"] == [{"name": "c1"}]
        assert fields["parent"] == {"key": "P-0"}
        assert fields["description"] == "d"  # stays a plain string on DC
        assert fields["customfield_10016"] == 5

    @pytest.mark.asyncio
    async def test_validation_failure(self):
        jira = _build_jira()
        with patch.object(jira, "_validate_issue_fields", return_value=(False, "nope")):
            ok, payload = await jira.create_issue("P", "s", "Task")
        assert ok is False
        assert "validation_error" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_retries_without_assignee_on_400(self):
        jira = _build_jira()
        first = _mock_response(400, {"errors": {"assignee": "not assignable"}})
        second = _mock_response(201, {"key": "P-1"})
        jira.client.create_issue_v2 = AsyncMock(side_effect=[first, second])
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.create_issue("P", "s", "Task", assignee_name="jdoe")
        assert ok is True
        assert jira.client.create_issue_v2.await_count == 2
        assert "warning" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_bad_request_translates_field_errors(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(
            return_value=_mock_response(400, {"errors": {"customfield_100": "Required"}}),
        )
        with patch.object(jira, "_fetch_and_cache_field_schema",
                          AsyncMock(return_value={"customfield_100": {"name": "Severity", "normalized": "severity"}})):
            ok, payload = await jira.create_issue("P", "s", "Bug")
        data = json.loads(payload)
        assert ok is False
        assert "Severity (customfield_100)" in data["field_errors"]
        assert "get_create_issue_fields" in data["guidance"]

    @pytest.mark.asyncio
    async def test_custom_fields_do_not_override_standard(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(return_value=_mock_response(201, {"key": "P-1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.create_issue("P", "s", "Bug", custom_fields={"summary": "ignored", "customfield_1": "v"})
        fields = jira.client.create_issue_v2.call_args.kwargs["fields"]
        assert fields["summary"] == "s"
        assert fields["customfield_1"] == "v"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.create_issue("P", "s", "Task")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.create_issue_v2 = AsyncMock(side_effect=RuntimeError("boom"))
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
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        ok, payload = await jira.update_issue("P-1")
        assert ok is False
        assert "No updates provided" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_summary_update_success(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1", "fields": {"summary": "new"}}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.update_issue("P-1", summary="new")
        assert ok is True
        assert json.loads(payload)["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_description_stays_plain_string(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.update_issue("P-1", description="plain text")
        sent = jira.client.edit_issue_v2.call_args.kwargs["fields"]
        assert sent["description"] == "plain text"

    @pytest.mark.asyncio
    async def test_issue_type_resolved_and_edited_first(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira._fetch_issue_types = AsyncMock(return_value=[{"id": "5", "name": "Story"}])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", issue_type_name="story", summary="x")
        assert ok is True
        # two edit calls: issuetype first, then the rest
        assert jira.client.edit_issue_v2.await_count == 2
        first_fields = jira.client.edit_issue_v2.call_args_list[0].kwargs["fields"]
        assert first_fields == {"issuetype": {"name": "Story"}}

    @pytest.mark.asyncio
    async def test_issue_type_fuzzy_resolution(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira._fetch_issue_types = AsyncMock(return_value=[{"id": "5", "name": "User Story"}])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", issue_type_name="userstory")
        assert ok is True
        sent = jira.client.edit_issue_v2.call_args.kwargs["fields"]
        assert sent["issuetype"] == {"name": "User Story"}

    @pytest.mark.asyncio
    async def test_issue_type_only_single_edit(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira._fetch_issue_types = AsyncMock(return_value=[{"id": "5", "name": "Story"}])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", issue_type_name="Story")
        assert ok is True
        assert jira.client.edit_issue_v2.await_count == 1

    @pytest.mark.asyncio
    async def test_issue_type_first_edit_fails(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        jira._fetch_issue_types = AsyncMock(return_value=[{"id": "5", "name": "Story"}])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.update_issue("P-1", issue_type_name="Story", summary="x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_status_transition_found(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(200, {"transitions": [{"id": "11", "to": {"name": "Done"}}]}))
        jira.client.do_transition_v2 = AsyncMock(return_value=_mock_response(204))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        jira.client.do_transition_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_transition_not_reachable_warning(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(200, {"transitions": [{"to": {"name": "Open"}, "id": "1"}]}))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", status="Closed")
        assert ok is True
        assert "warnings" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_status_only_not_reachable_returns_error(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(200, {"transitions": [{"to": {"name": "Open"}, "id": "1"}]}))
        ok, payload = await jira.update_issue("P-1", status="Nowhere")
        assert ok is False
        assert "not reachable" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_transitions_fetch_http_error_warning(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(500, {}))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        assert any("transitions" in w for w in json.loads(payload)["warnings"])

    @pytest.mark.asyncio
    async def test_transitions_fetch_exception_warning(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(side_effect=RuntimeError("net"))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        assert "warnings" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_transition_execution_fails_warning(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(200, {"transitions": [{"id": "11", "to": {"name": "Done"}}]}))
        jira.client.do_transition_v2 = AsyncMock(return_value=_mock_response(400, {"errorMessages": ["blocked"]}))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        assert any("blocked" in w for w in json.loads(payload)["warnings"])

    @pytest.mark.asyncio
    async def test_transition_execution_exception_warning(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.get_transitions_v2 = AsyncMock(return_value=_mock_response(200, {"transitions": [{"id": "11", "to": {"name": "Done"}}]}))
        jira.client.do_transition_v2 = AsyncMock(side_effect=RuntimeError("net"))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", status="Done")
        assert ok is True
        assert any("net" in w for w in json.loads(payload)["warnings"])

    @pytest.mark.asyncio
    async def test_retry_without_reporter_on_400(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        first = _mock_response(400, {"errors": {"reporter": "not allowed"}})
        second = _mock_response(204)
        jira.client.edit_issue_v2 = AsyncMock(side_effect=[first, second])
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.update_issue("P-1", summary="x", reporter_name="bob")
        assert ok is True
        assert jira.client.edit_issue_v2.await_count == 2
        assert any("Reporter" in w for w in json.loads(payload)["warnings"])

    @pytest.mark.asyncio
    async def test_assignee_query_resolved(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_resolve_user_to_name", AsyncMock(return_value="jdoe")), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", assignee_query="alice")
        assert ok is True
        assert jira.client.edit_issue_v2.call_args.kwargs["fields"]["assignee"] == {"name": "jdoe"}

    @pytest.mark.asyncio
    async def test_reporter_query_resolved(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_resolve_user_to_name", AsyncMock(return_value="bob")), \
             patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", reporter_query="bobby")
        assert ok is True
        assert jira.client.edit_issue_v2.call_args.kwargs["fields"]["reporter"] == {"name": "bob"}

    @pytest.mark.asyncio
    async def test_field_errors_translated_on_400(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(400, {"errors": {"customfield_1": "Required"}}))
        with patch.object(jira, "_fetch_and_cache_field_schema",
                          AsyncMock(return_value={"customfield_1": {"name": "Sev", "normalized": "sev"}})):
            ok, payload = await jira.update_issue("P-1", summary="x")
        data = json.loads(payload)
        assert ok is False
        assert "Sev (customfield_1)" in data["field_errors"]

    @pytest.mark.asyncio
    async def test_final_fetch_fails_minimal_response(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            _mock_response(200, {"fields": {"project": {"key": "P"}}}),
            _mock_response(404, {"message": "x"}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.update_issue("P-1", summary="x")
        assert ok is True
        assert json.loads(payload)["data"]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_edit_api_error(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.update_issue("P-1", summary="x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_initial_issue_fetch_exception_tolerated(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=[
            RuntimeError("net"),
            _mock_response(200, {"key": "P-1"}),
        ])
        jira.client.edit_issue_v2 = AsyncMock(return_value=_mock_response(204))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await jira.update_issue("P-1", summary="x")
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {"fields": {"project": {"key": "P"}}}))
        jira.client.edit_issue_v2 = AsyncMock(side_effect=RuntimeError("boom"))
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
        jira.client.list_projects_get_v2 = AsyncMock(return_value=_mock_response(200, [{"key": "P", "name": "Proj"}]))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_projects()
        data = json.loads(payload)
        assert ok is True
        assert data["data"][0]["url"] == "https://s/projects/P"

    @pytest.mark.asyncio
    async def test_success_no_site_url(self):
        jira = _build_jira()
        jira.client.list_projects_get_v2 = AsyncMock(return_value=_mock_response(200, [{"key": "P"}]))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.get_projects()
        assert ok is True
        assert "url" not in json.loads(payload)["data"][0]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.list_projects_get_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.get_projects()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.list_projects_get_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_projects()
        assert ok is False


class TestGetProject:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(200, {"key": "P"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_project("P")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/projects/P"

    @pytest.mark.asyncio
    async def test_success_no_key(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(200, {"name": "Proj"}))
        ok, payload = await jira.get_project("P")
        assert ok is True
        assert "url" not in json.loads(payload)["data"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await jira.get_project("X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_project("X")
        assert ok is False


class TestGetProjectMetadata:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(200, {
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
    async def test_lead_not_dict(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(200, {"key": "P", "lead": "someone"}))
        ok, payload = await jira.get_project_metadata("P")
        assert ok is True
        assert json.loads(payload)["metadata"]["lead"] is None

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await jira.get_project_metadata("X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_project_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_project_metadata("X")
        assert ok is False


# ===========================================================================
# Tool: get_issues / get_issue
# ===========================================================================

class TestGetIssues:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(200, {"issues": [{"key": "P-1", "fields": {}}]}))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_issues("P")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["issues"][0]["url"] == "https://s/browse/P-1"

    @pytest.mark.asyncio
    async def test_days_and_max_results_in_jql(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(200, {"issues": []}))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.get_issues("P", days=7, max_results=10)
        kwargs = jira.client.search_issues_post_v2.call_args.kwargs
        assert "-7d" in kwargs["jql"]
        assert kwargs["maxResults"] == 10

    @pytest.mark.asyncio
    async def test_project_key_quotes_escaped_in_jql(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(200, {"issues": []}))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            await jira.get_issues('P"X')
        assert '\\"' in jira.client.search_issues_post_v2.call_args.kwargs["jql"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.get_issues("P")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_issues("P")
        assert ok is False


class TestGetIssue:
    @pytest.mark.asyncio
    async def test_success_with_rendered_description(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {
            "key": "P-1",
            "fields": {"summary": "x", "description": "raw wiki", "parent": {"key": "P-0"}},
            "renderedFields": {"description": "<p>rendered</p>"},
        }))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_issue("P-1")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://s/browse/P-1"
        assert data["data"]["fields"]["description"] == "<p>rendered</p>"

    @pytest.mark.asyncio
    async def test_success_raw_description_fallback(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(200, {
            "key": "P-1",
            "fields": {"summary": "x", "description": "raw wiki"},
        }))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.get_issue("P-1")
        assert json.loads(payload)["data"]["fields"]["description"] == "raw wiki"

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await jira.get_issue("P-1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_issue_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_issue("P-1")
        assert ok is False


# ===========================================================================
# Tool: search_issues
# ===========================================================================

class TestSearchIssues:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(
            return_value=_mock_response(200, {"issues": [{"key": "P-1", "fields": {}}], "total": 1}),
        )
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.search_issues('project = "P" AND updated >= -7d')
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["issues"][0]["url"] == "https://s/browse/P-1"
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_truncation_note_when_partial(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(
            return_value=_mock_response(200, {"issues": [{"key": "P-1", "fields": {}}], "total": 100}),
        )
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.search_issues("project = P")
        data = json.loads(payload)
        assert data["returned"] == 1
        assert "note" in data and "100" in data["note"]

    @pytest.mark.asyncio
    async def test_jql_auto_fix_warning_included(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(200, {"issues": [], "total": 0}))
        jira.client.get_fields_v2 = AsyncMock(return_value=_mock_response(200, []))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.search_issues("resolution = Unresolved")
        data = json.loads(payload)
        assert ok is True
        assert "warning" in data
        assert data["fixed_jql"] != data["original_jql"]

    @pytest.mark.asyncio
    async def test_api_error_includes_jql(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(400, {"errorMessages": ["bad"]}))
        ok, payload = await jira.search_issues("bad jql")
        data = json.loads(payload)
        assert ok is False
        assert data.get("jql_query") == "bad jql"

    @pytest.mark.asyncio
    async def test_api_error_json_reparse_failure(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(return_value=_mock_response(400, {"errorMessages": ["bad"]}))
        # Make the error payload un-reparseable by _handle_response's second json.loads.
        with patch.object(jira, "_handle_response", return_value=(False, "not-json")):
            ok, payload = await jira.search_issues("jql")
        assert ok is False
        assert payload == "not-json"

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.search_issues_post_v2 = AsyncMock(side_effect=RuntimeError("boom"))
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
        jira.client.add_comment_v2 = AsyncMock(return_value=_mock_response(201, {"id": "c1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.add_comment("P-1", "hi")
        data = json.loads(payload)
        assert ok is True
        sent = jira.client.add_comment_v2.call_args.kwargs["body"]
        assert sent == "hi"  # plain string, not ADF
        assert data["issue_url"] == "https://s/browse/P-1"
        assert "comment_url" in data

    @pytest.mark.asyncio
    async def test_success_no_site_url(self):
        jira = _build_jira()
        jira.client.add_comment_v2 = AsyncMock(return_value=_mock_response(201, {"id": "c1"}))
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.add_comment("P-1", "hi")
        assert ok is True
        assert "issue_url" not in json.loads(payload)

    @pytest.mark.asyncio
    async def test_empty_string_comment_rejected(self):
        jira = _build_jira()
        ok, payload = await jira.add_comment("P-1", "   ")
        assert ok is False
        assert "cannot be empty" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.add_comment_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.add_comment("P-1", "hi")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.add_comment_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.add_comment("P-1", "hi")
        assert ok is False


class TestGetComments:
    @pytest.mark.asyncio
    async def test_success(self):
        jira = _build_jira()
        jira.client.get_issue_comments_v2 = AsyncMock(
            return_value=_mock_response(200, {"comments": [{"id": "1", "body": "hi"}]}),
        )
        with patch.object(jira, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await jira.get_comments("P-1")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["comments"][0]["body"] == "hi"
        assert data["data"]["comments"][0]["url"].startswith("https://s/browse/P-1?focusedCommentId=1")

    @pytest.mark.asyncio
    async def test_success_no_site_url(self):
        jira = _build_jira()
        jira.client.get_issue_comments_v2 = AsyncMock(
            return_value=_mock_response(200, {"comments": [{"id": "1", "body": "hi"}]}),
        )
        with patch.object(jira, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await jira.get_comments("P-1")
        assert ok is True
        assert "issue_url" not in json.loads(payload)

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.get_issue_comments_v2 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await jira.get_comments("P-1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.get_issue_comments_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.get_comments("P-1")
        assert ok is False


# ===========================================================================
# Tool: search_users
# ===========================================================================

class TestSearchUsers:
    @pytest.mark.asyncio
    async def test_success_with_direct_email(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(return_value=_mock_response(200, {
            "users": [{"name": "jdoe", "key": "jdoe", "displayName": "J Doe", "emailAddress": "j@x"}],
        }))
        ok, payload = await jira.search_users("alice")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        assert data["data"]["results"][0]["emailAddress"] == "j@x"

    @pytest.mark.asyncio
    async def test_email_scraped_from_html(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(return_value=_mock_response(200, {
            "users": [{"name": "jdoe", "key": "jdoe", "displayName": "J Doe",
                       "html": "John <b>Doe</b> - jdoe@example.com (jdoe)"}],
        }))
        ok, payload = await jira.search_users("john")
        data = json.loads(payload)
        assert data["data"]["results"][0]["emailAddress"] == "jdoe@example.com"

    @pytest.mark.asyncio
    async def test_no_email_when_html_has_none(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(return_value=_mock_response(200, {
            "users": [{"name": "jdoe", "key": "jdoe", "html": "John Doe (jdoe)"}],
        }))
        ok, payload = await jira.search_users("john")
        assert "emailAddress" not in json.loads(payload)["data"]["results"][0]

    @pytest.mark.asyncio
    async def test_users_returned_as_list(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(
            return_value=_mock_response(200, [{"name": "jdoe", "displayName": "J"}]),
        )
        ok, payload = await jira.search_users("alice")
        assert ok is True
        assert json.loads(payload)["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_user_without_name_or_key_skipped(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(return_value=_mock_response(200, {
            "users": [{"displayName": "no id"}],
        }))
        ok, payload = await jira.search_users("x")
        assert json.loads(payload)["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_empty_query(self):
        jira = _build_jira()
        ok, payload = await jira.search_users("   ")
        assert ok is False
        assert "required" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await jira.search_users("alice")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        jira = _build_jira()
        jira.client.find_users_for_picker_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await jira.search_users("alice")
        assert ok is False
