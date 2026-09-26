"""Behaviour tests for the Jira agent tools.

Each test drives a tool the way the agent does and checks what Jira would
receive and what the agent is told back. See ``jira_tool_fakes`` for what is
real and what is faked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from jira_tool_fakes import (
    API_TOKEN,
    BASIC,
    SITE,
    FakeJiraApi,
    RecordedRequest,
    build_jira_tool,
    issue,
    result,
    user,
)

if TYPE_CHECKING:
    from app.agents.actions.jira.jira import Jira

ASSIGNABLE = "/user/assignable/search"
CREATED = (201, {"id": "10001", "key": "PA-7", "self": f"{SITE}/rest/api/3/issue/10001"})


@pytest.fixture
def api() -> FakeJiraApi:
    return FakeJiraApi()


@pytest.fixture
def jira(api: FakeJiraApi) -> Jira:
    return build_jira_tool(api)


def assert_safe_error(payload: dict[str, Any]) -> str:
    """The error is plain text the agent can relay: no credentials, no raw dumps."""
    message = payload["error"]
    assert isinstance(message, str) and message
    for leaked in (API_TOKEN, BASIC, "Basic ", "Traceback", "{"):
        assert leaked not in message, f"{leaked!r} leaked into: {message}"
    return message


class TestAssigneeLookup:
    """assignee_query must name exactly one person before anything is written."""

    async def test_exact_name_among_several_matches_is_the_one_assigned(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, [user("acc-ann", "Ann Lee"), user("acc-annabel", "Annabel Smith")])
        api.on("POST", "/issue", CREATED)

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="Ann Lee"))

        assert ok is True, data
        lookup = api.calls("GET", ASSIGNABLE)[0]
        assert (lookup.query["project"], lookup.query["query"]) == ("PA", "Ann Lee")
        assert lookup.headers["authorization"] == f"Basic {BASIC}"
        assert api.calls("POST", "/issue")[0].body["fields"]["assignee"] == {"accountId": "acc-ann"}

    async def test_email_picks_the_person_with_that_address(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, [user("acc-1", "Ann Lee", "ann.other@acme.test"), user("acc-2", "Ann Lee", "ann@acme.test")])
        api.on("POST", "/issue", CREATED)

        ok, _ = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="ANN@acme.test"))

        assert ok is True
        assert api.calls("POST", "/issue")[0].body["fields"]["assignee"] == {"accountId": "acc-2"}

    async def test_a_single_partial_match_is_unambiguous(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, [user("acc-ann", "Ann Lee")])
        api.on("POST", "/issue", CREATED)

        ok, _ = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="ann"))

        assert ok is True
        assert api.calls("POST", "/issue")[0].body["fields"]["assignee"] == {"accountId": "acc-ann"}

    async def test_several_people_matching_is_refused_and_names_them(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, [user("acc-ann", "Ann Lee"), user("acc-annabel", "Annabel Smith")])

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="Ann"))

        assert ok is False
        message = assert_safe_error(data)
        assert "Ann Lee" in message and "Annabel Smith" in message
        assert "assignee_account_id" in message
        assert api.writes() == []

    async def test_nobody_matching_is_refused(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, [])

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="Zed"))

        assert ok is False
        assert "No one who can be assigned issues in PA matches 'Zed'" in assert_safe_error(data)
        assert api.writes() == []

    async def test_a_failed_lookup_creates_nothing(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, (503, {"errorMessages": ["Service unavailable"]}))

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="Ann Lee"))

        assert ok is False
        assert "could not look up 'Ann Lee'" in assert_safe_error(data)
        assert api.writes() == []

    async def test_a_lookup_that_cannot_reach_jira_creates_nothing(self, jira, api) -> None:
        api.on("GET", ASSIGNABLE, httpx.ConnectError(f"connect failed for Basic {BASIC}"))

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_query="Ann Lee"))

        assert ok is False
        assert "could not look up 'Ann Lee'" in assert_safe_error(data)
        assert api.writes() == []

    async def test_update_with_an_ambiguous_assignee_changes_nothing(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", ASSIGNABLE, [user("acc-ann", "Ann Lee"), user("acc-annabel", "Annabel Smith")])

        ok, data = result(await jira.update_issue("PA-7", summary="New title", assignee_query="Ann"))

        assert ok is False
        assert "Annabel Smith" in assert_safe_error(data)
        assert api.writes() == []

    async def test_update_with_an_ambiguous_reporter_changes_nothing(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", ASSIGNABLE, [user("acc-bo", "Bo Chen"), user("acc-bob", "Bob Stone")])

        ok, data = result(await jira.update_issue("PA-7", reporter_query="Bo"))

        assert ok is False
        assert "Bob Stone" in assert_safe_error(data)
        assert api.writes() == []

    async def test_update_whose_issue_cannot_be_read_does_not_guess_the_assignee(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", (503, {"errorMessages": ["Service unavailable"]}))

        ok, data = result(await jira.update_issue("PA-7", summary="New title", assignee_query="Ann Lee"))

        assert ok is False
        assert "could not look up 'Ann Lee'" in assert_safe_error(data)
        assert api.writes() == []

    async def test_update_assigns_the_one_matching_person(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", ASSIGNABLE, [user("acc-ann", "Ann Lee")])
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, _ = result(await jira.update_issue("PA-7", assignee_query="Ann Lee"))

        assert ok is True
        assert api.calls("GET", ASSIGNABLE)[0].query["project"] == "PA"
        assert api.calls("PUT", "/issue/PA-7")[0].body["fields"] == {"assignee": {"accountId": "acc-ann"}}


class TestRefusedPeople:
    """Jira can refuse just the assignee or reporter; the reply must say it was left out."""

    async def test_created_without_the_refused_assignee_says_so(self, jira, api) -> None:
        api.on("POST", "/issue",
               (400, {"errorMessages": [], "errors": {"assignee": "User 'acc-x' cannot be assigned issues."}}),
               CREATED)

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", assignee_account_id="acc-x"))

        assert ok is True
        first, second = api.calls("POST", "/issue")
        assert first.body["fields"]["assignee"] == {"accountId": "acc-x"}
        assert "assignee" not in second.body["fields"]
        assert data["message"].startswith("Issue created, but nobody was assigned")
        assert "cannot be assigned issues" in data["warning"]

    async def test_created_without_the_refused_reporter_says_so(self, jira, api) -> None:
        api.on("POST", "/issue",
               (400, {"errors": {"reporter": "Field 'reporter' cannot be set."}}),
               CREATED)

        ok, data = result(await jira.create_issue(
            "PA", "Login fails", "Bug", custom_fields={"reporter": {"accountId": "acc-r"}},
        ))

        assert ok is True
        assert "the reporter was not changed" in data["warning"]

    async def test_update_without_the_refused_reporter_says_so(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("PUT", "/issue/PA-7", (400, {"errors": {"reporter": "Field 'reporter' cannot be set."}}), (204, None))

        ok, data = result(await jira.update_issue("PA-7", summary="New title", reporter_account_id="acc-r"))

        assert ok is True
        assert api.calls("PUT", "/issue/PA-7")[-1].body["fields"] == {"summary": "New title"}
        assert data["message"].startswith("Issue updated successfully, but the reporter was not changed")

    async def test_created_issue_with_nothing_refused_has_no_warning(self, jira, api) -> None:
        api.on("POST", "/issue", CREATED)

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug", description="Steps: <@U123> saw it"))

        assert ok is True
        assert data["message"] == "Issue created successfully" and "warning" not in data
        assert data["data"]["url"] == f"{SITE}/browse/PA-7"
        body = api.calls("POST", "/issue")[0].body["fields"]
        assert body["description"]["content"][0]["content"][0]["text"] == "Steps: @U123 saw it"


def transitions(*names: str) -> dict[str, Any]:
    return {"transitions": [{"id": str(i + 11), "name": f"Move to {n}", "to": {"name": n}} for i, n in enumerate(names)]}


class TestStatusChanges:
    async def test_status_moves_through_the_matching_transition(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", transitions("In Progress", "Done"))
        api.on("POST", "/issue/PA-7/transitions", (204, None))

        ok, data = result(await jira.update_issue("PA-7", status="done"))

        assert ok is True, data
        assert api.calls("POST", "/issue/PA-7/transitions")[0].body == {"transition": {"id": "12"}}
        assert data["message"] == "Issue updated successfully"

    async def test_unreachable_status_alone_changes_nothing_and_lists_the_options(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", transitions("In Progress", "Done"))

        ok, data = result(await jira.update_issue("PA-7", status="Closed"))

        assert ok is False
        assert assert_safe_error(data) == (
            "Nothing was changed: PA-7 cannot move to 'Closed' from its current status. It can move to: Done, In Progress."
        )
        assert api.writes() == []

    async def test_unreachable_status_with_other_changes_says_the_status_was_not_changed(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", transitions("Done"))
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, data = result(await jira.update_issue("PA-7", summary="New title", status="Closed"))

        assert ok is True
        assert data["message"] == (
            "Issue updated successfully, but the status was not changed: PA-7 cannot move to 'Closed' "
            "from its current status. It can move to: Done"
        )
        assert api.calls("POST", "/issue/PA-7/transitions") == []

    async def test_unreadable_transitions_are_not_reported_as_a_status_change(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", (503, {"errorMessages": ["Service unavailable"]}))
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, data = result(await jira.update_issue("PA-7", labels=["urgent"], status="Done"))

        assert ok is True
        assert "the status was not changed" in data["message"]
        assert "could not be read" in data["message"]

    async def test_a_refused_transition_is_reported(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", transitions("Done"))
        api.on("POST", "/issue/PA-7/transitions", (400, {"errorMessages": ["Resolution is required."]}))

        ok, data = result(await jira.update_issue("PA-7", status="Done"))

        assert ok is True
        assert "status transition failed: Resolution is required." in data["message"]


SEARCH = "/search/jql"


def search_pages(*pages: tuple[list[str], str | None]) -> object:
    """Serve pages by the token in the request body: page n answers token f"t{n}"."""
    def answer(request: RecordedRequest) -> dict[str, Any]:
        token = (request.body or {}).get("nextPageToken")
        index = 0 if token is None else int(token[1:])
        keys, next_token = pages[index]
        page: dict[str, Any] = {"issues": [issue(k) for k in keys], "isLast": next_token is None}
        if next_token:
            page["nextPageToken"] = next_token
        return page
    return answer


class TestSearchPaging:
    async def test_a_limit_beyond_one_page_reads_the_next_page(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1", "PA-2"], "t1"), (["PA-3"], "t2"), (["PA-4"], None)))

        ok, data = result(await jira.search_issues('project = "PA" AND updated >= -7d', maxResults=3))

        assert ok is True
        assert [i["key"] for i in data["data"]["issues"]] == ["PA-1", "PA-2", "PA-3"]
        first, second = api.calls("POST", SEARCH)
        assert (first.body["maxResults"], "nextPageToken" not in first.body) == (3, True)
        assert (second.body["maxResults"], second.body["nextPageToken"]) == (1, "t1")
        assert data["has_more"] is True
        assert "Showing the first 3 matching issues; more match" in data["message"]

    async def test_the_last_page_is_complete(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1"], None)))

        ok, data = result(await jira.search_issues('project = "PA"', maxResults=10))

        assert ok is True
        assert data["has_more"] is False
        assert data["message"] == "Issues fetched successfully"
        assert len(api.calls("POST", SEARCH)) == 1

    async def test_a_failed_second_page_keeps_the_first_and_says_the_list_is_incomplete(self, jira, api) -> None:
        pages = search_pages((["PA-1", "PA-2"], "t1"))
        api.on("POST", SEARCH, pages, (429, {"errorMessages": ["Rate limit exceeded"]}, {"Retry-After": "20"}))

        ok, data = result(await jira.search_issues('project = "PA"', maxResults=5))

        assert ok is True
        assert [i["key"] for i in data["data"]["issues"]] == ["PA-1", "PA-2"]
        assert data["has_more"] is True
        assert "Only the first 2 matching issues could be read" in data["message"]
        assert "incomplete" in data["message"]

    async def test_a_repeated_page_token_ends_the_read(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1"], "t1"), (["PA-2"], "t1")))

        ok, data = result(await jira.search_issues('project = "PA"', maxResults=10))

        assert ok is True
        assert [i["key"] for i in data["data"]["issues"]] == ["PA-1", "PA-2"]
        assert len(api.calls("POST", SEARCH)) == 2
        assert data["has_more"] is True
        assert "Only the first 2 matching issues could be read" in data["message"]
        assert "incomplete" in data["message"] and "more match" not in data["message"]

    async def test_an_unreadable_later_page_keeps_the_issues_already_read(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1", "PA-2"], "t1")), (200, ["not", "a", "page"]))

        ok, data = result(await jira.search_issues('project = "PA"', maxResults=10))

        assert ok is True
        assert [i["key"] for i in data["data"]["issues"]] == ["PA-1", "PA-2"]
        assert data["has_more"] is True
        assert "could not be read" in data["message"] and "incomplete" in data["message"]

    async def test_project_issues_say_when_more_match(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1", "PA-2"], "t1"), (["PA-3"], None)))

        ok, data = result(await jira.get_issues("PA", days=7, max_results=2))

        assert ok is True
        assert api.calls("POST", SEARCH)[0].body["jql"] == 'project = "PA" AND updated >= -7d ORDER BY updated DESC'
        assert [i["key"] for i in data["data"]["issues"]] == ["PA-1", "PA-2"]
        assert data["has_more"] is True


class TestSearchUsers:
    async def test_more_matches_than_shown_are_reported(self, jira, api) -> None:
        api.on("GET", "/user/picker", {
            "users": [
                {"accountId": "acc-1", "displayName": "Ann Lee", "html": "<strong>Ann</strong> Lee (ann@acme.test)"},
                {"accountId": "acc-2", "displayName": "Ann Park", "html": "Ann Park"},
            ],
            "total": 57, "header": "Showing 2 of 57 matching users",
        })

        ok, data = result(await jira.search_users("  ann ", max_results=2))

        assert ok is True
        assert api.calls("GET", "/user/picker")[0].query == {"query": "ann", "maxResults": "2"}
        assert data["data"]["results"][0]["emailAddress"] == "ann@acme.test"
        assert (data["data"]["total"], data["data"]["returned"], data["data"]["has_more"]) == (57, 2, True)
        assert data["message"].startswith("Showing 2 of 57 matching users")

    async def test_every_match_shown_is_complete(self, jira, api) -> None:
        api.on("GET", "/user/picker", {"users": [{"accountId": "acc-1", "displayName": "Ann Lee"}, {"displayName": "no id"}], "total": 1})

        ok, data = result(await jira.search_users("ann"))

        assert ok is True
        assert (data["data"]["total"], data["data"]["has_more"]) == (1, False)
        assert data["message"] == "Users fetched successfully"

    async def test_blank_query_is_refused_before_jira(self, jira, api) -> None:
        ok, data = result(await jira.search_users("   "))

        assert ok is False
        assert assert_safe_error(data).startswith("Query parameter is required")
        assert api.requests == []


class TestFailuresInPlainLanguage:
    async def test_rate_limit_says_how_long_to_wait(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", (429, {"errorMessages": ["Rate limit exceeded."]}, {"Retry-After": "17"}))

        ok, data = result(await jira.get_issue("PA-7"))

        assert ok is False
        assert assert_safe_error(data) == "Jira is receiving too many requests right now. Wait 17 seconds and try again."
        assert data["status_code"] == 429
        assert "rate limiting" in data["guidance"]

    async def test_rate_limit_without_retry_after_says_a_minute(self, jira, api) -> None:
        api.on("POST", "/issue/PA-7/comment", (429, {"errorMessages": []}))

        ok, data = result(await jira.add_comment("PA-7", "Looking into it"))

        assert ok is False
        assert "Wait a minute and try again" in assert_safe_error(data)

    async def test_rejected_sign_in_says_to_reconnect(self, jira, api) -> None:
        api.on("GET", "/project", (401, {"message": "Client must be authenticated to access this resource."}))

        ok, data = result(await jira.get_projects())

        assert ok is False
        message = assert_safe_error(data)
        assert "did not accept the saved sign-in" in message and "Reconnect the Jira toolset" in message

    async def test_missing_issue_says_how_to_find_it(self, jira, api) -> None:
        ok, data = result(await jira.get_comments("PA-404"))

        assert ok is False
        message = assert_safe_error(data)
        assert "Issue does not exist or you do not have permission to see it." in message
        assert "search_issues" in message

    async def test_forbidden_says_to_ask_an_admin(self, jira, api) -> None:
        api.on("GET", "/project/PA", (403, {"errorMessages": ["You do not have permission to view this project."]}))

        ok, data = result(await jira.get_project("PA"))

        assert ok is False
        assert "Ask a Jira admin for access" in assert_safe_error(data)

    async def test_bad_jql_relays_jiras_reason_and_the_query(self, jira, api) -> None:
        api.on("POST", SEARCH, (400, {"errorMessages": ["Field 'sprintt' does not exist."], "errors": {}}))

        ok, data = result(await jira.search_issues("sprintt = 5"))

        assert ok is False
        assert "Field 'sprintt' does not exist." in assert_safe_error(data)
        assert data["jql_query"] == "sprintt = 5"
        assert "JQL" in data["guidance"]

    async def test_server_error_says_to_try_again(self, jira, api) -> None:
        api.on("GET", "/project/PA", (502, "<html>Bad gateway</html>"))

        ok, data = result(await jira.get_project_metadata("PA"))

        assert ok is False
        assert assert_safe_error(data) == "Jira is having a temporary problem. Try again in a moment."

    async def test_unreachable_jira_is_explained_without_the_library_text(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", httpx.ConnectError(f"All connection attempts failed for Basic {BASIC}"))

        ok, data = result(await jira.get_issue("PA-7"))

        assert ok is False
        assert assert_safe_error(data) == "Jira could not be reached while getting issue. Try again in a moment."

    async def test_unreachable_jira_during_search_keeps_the_query(self, jira, api) -> None:
        api.on("POST", SEARCH, httpx.ReadTimeout("timed out"))

        ok, data = result(await jira.search_issues('project = "PA"'))

        assert ok is False
        assert "could not be reached" in assert_safe_error(data)
        assert data["jql_query"] == 'project = "PA"'


class TestUpdateIssue:
    async def test_a_transition_that_cannot_reach_jira_is_explained_without_the_library_text(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/PA-7/transitions", transitions("Done"))
        api.on("POST", "/issue/PA-7/transitions", httpx.ConnectError(f"reset while sending Basic {BASIC}"))

        ok, data = result(await jira.update_issue("PA-7", status="Done"))

        assert ok is True
        assert "status transition failed: Jira could not be reached" in data["message"]
        assert BASIC not in data["message"]

    async def test_type_change_goes_first_then_the_other_fields(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/createmeta/PA/issuetypes", {"issueTypes": [{"name": "Bug"}, {"name": "New Feature"}]})
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, data = result(await jira.update_issue(
            "PA-7", issue_type_name="feature", custom_fields={"customfield_10016": 5}, priority_name="High",
        ))

        assert ok is True, data
        first, second = api.calls("PUT", "/issue/PA-7")
        assert first.body["fields"] == {"issuetype": {"name": "New Feature"}}
        assert second.body["fields"] == {"priority": {"name": "High"}, "customfield_10016": 5}

    async def test_a_refused_type_change_writes_nothing_else(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/createmeta/PA/issuetypes", {"issueTypes": [{"name": "Story"}]})
        api.on("PUT", "/issue/PA-7", (400, {"errorMessages": ["The issue type selected is invalid."], "errors": {}}))

        ok, data = result(await jira.update_issue("PA-7", issue_type_name="Story", summary="New"))

        assert ok is False
        assert "The issue type selected is invalid." in assert_safe_error(data)
        assert len(api.calls("PUT", "/issue/PA-7")) == 1

    async def test_missing_required_fields_are_named_for_the_new_type(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("GET", "/issue/createmeta/PA/issuetypes", {"issueTypes": [{"name": "Story"}]})
        api.on("GET", "/field", [{"id": "customfield_10016", "name": "Story Points"}])
        api.on("PUT", "/issue/PA-7", (204, None), (400, {"errors": {"customfield_10016": "Story Points is required."}}))

        ok, data = result(await jira.update_issue("PA-7", issue_type_name="story", summary="New"))

        assert ok is False
        assert data["field_errors"] == {"Story Points (customfield_10016)": "Story Points is required."}
        assert "get_create_issue_fields(project_key='PA', issue_type_name='story')" in data["guidance"]

    async def test_description_is_sent_as_a_document(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"))
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, data = result(await jira.update_issue("PA-7", description="Fixed in 2.3"))

        assert ok is True
        sent = api.calls("PUT", "/issue/PA-7")[0].body["fields"]["description"]
        assert sent["type"] == "doc" and sent["content"][0]["content"][0]["text"] == "Fixed in 2.3"
        assert data["data"]["url"] == f"{SITE}/browse/PA-7"

    async def test_an_updated_issue_that_cannot_be_reread_still_reports_the_update(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7", issue("PA-7"), (503, {"errorMessages": ["busy"]}))
        api.on("PUT", "/issue/PA-7", (204, None))

        ok, data = result(await jira.update_issue("PA-7", labels=["urgent"]))

        assert ok is True
        assert data["data"] == {"key": "PA-7", "url": f"{SITE}/browse/PA-7"}


CREATEMETA = "/issue/createmeta/PA/issuetypes"
BUG_TYPES = {"issueTypes": [{"id": "1", "name": "Bug"}, {"id": "2", "name": "User Story"}]}


def meta_field(field_id: str, name: str, *, required: bool, kind: str = "string", **extra: object) -> dict[str, Any]:
    return {"fieldId": field_id, "name": name, "required": required, "schema": {"type": kind}, **extra}


class TestCreateIssueFields:
    async def test_required_and_optional_fields_across_pages(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1",
               {"fields": [meta_field("summary", "Summary", required=True),
                           meta_field("customfield_10020", "Team", required=True, kind="option",
                                      allowedValues=[{"id": "31", "value": "Payments"}])],
                "total": 3},
               {"fields": [meta_field("customfield_10016", "Story Points", required=False, kind="number")], "total": 3})

        ok, data = result(await jira.get_create_issue_fields("PA", "bug"))

        assert ok is True, data
        starts = [c.query["startAt"] for c in api.calls("GET", f"{CREATEMETA}/1")]
        assert starts == ["0", "2"]
        blob = json_text(data)
        assert "customfield_10020" in blob and "Payments" in blob and "customfield_10016" in blob

    async def test_an_unread_page_of_fields_is_a_failure_not_a_short_list(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1",
               {"fields": [meta_field("summary", "Summary", required=True)], "total": 2},
               (503, {"errorMessages": ["busy"]}))

        ok, data = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert ok is False
        assert "required fields are not known yet" in assert_safe_error(data)

    async def test_a_page_without_a_field_list_is_a_failure_and_not_remembered(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1",
               {"fields": [meta_field("summary", "Summary", required=True)], "total": 2},
               {"fields": None, "total": 2},
               {"fields": [meta_field("summary", "Summary", required=True),
                           meta_field("customfield_10020", "Team", required=True)], "total": 2})

        first_ok, first = result(await jira.get_create_issue_fields("PA", "Bug"))
        second_ok, second = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert first_ok is False
        assert "required fields are not known yet" in assert_safe_error(first)
        assert second_ok is True and "customfield_10020" in json_text(second)

    async def test_an_empty_page_before_the_total_is_a_failure_and_not_remembered(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1",
               {"fields": [meta_field("summary", "Summary", required=True)], "total": 2},
               {"fields": [], "total": 2},
               {"fields": [meta_field("summary", "Summary", required=True),
                           meta_field("customfield_10020", "Team", required=True)], "total": 2})

        first_ok, first = result(await jira.get_create_issue_fields("PA", "Bug"))
        second_ok, second = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert first_ok is False
        assert "required fields are not known yet" in assert_safe_error(first)
        assert second_ok is True and "customfield_10020" in json_text(second)

    async def test_an_empty_page_at_the_total_ends_the_list(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1", {"fields": [], "total": 0})

        ok, _ = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert ok is True

    async def test_a_failed_read_is_not_remembered(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)
        api.on("GET", f"{CREATEMETA}/1", (503, {"errorMessages": ["busy"]}),
               {"fields": [meta_field("customfield_10020", "Team", required=True)], "total": 1})

        first_ok, _ = result(await jira.get_create_issue_fields("PA", "Bug"))
        second_ok, data = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert (first_ok, second_ok) == (False, True)
        assert "customfield_10020" in json_text(data)

    async def test_unreadable_issue_types_are_not_reported_as_a_missing_type(self, jira, api) -> None:
        api.on("GET", CREATEMETA, (503, {"errorMessages": ["busy"]}))

        ok, data = result(await jira.get_create_issue_fields("PA", "Bug"))

        assert ok is False
        message = assert_safe_error(data)
        assert "could not list the issue types" in message and "not found" not in message

    async def test_an_unknown_type_lists_the_projects_types(self, jira, api) -> None:
        api.on("GET", CREATEMETA, BUG_TYPES)

        ok, data = result(await jira.get_create_issue_fields("PA", "Initiative"))

        assert ok is False
        assert assert_safe_error(data) == "Issue type 'Initiative' not found in project 'PA'. Available: Bug, User Story"


def json_text(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload)


class TestReadingIssues:
    async def test_issue_details_keep_comments_and_attachments_and_link_back(self, jira, api) -> None:
        comments = [{"id": str(n), "body": f"note {n}", "author": {"displayName": "Ann", "accountId": "x"}, "created": f"2026-09-0{n}"}
                    for n in range(1, 6)]
        api.on("GET", "/issue/PA-7", issue(
            "PA-7",
            status={"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
            comment={"comments": comments, "total": 5},
            attachment=[{"id": "a1", "filename": "trace.log", "size": 10, "mimeType": "text/plain", "created": "2026-09-01",
                         "content": "https://x/att", "author": {"displayName": "Ann"}}],
            customfield_10014="PA-1", customfield_10099=None, labels=[],
        ))
        api.on("GET", "/field", [{"id": "customfield_10014", "name": "Epic Link"}])

        ok, data = result(await jira.get_issue("PA-7"))

        assert ok is True
        fields = data["data"]["fields"]
        assert "note 5" in [c["body"] for c in fields["comment"]["comments"]]
        assert fields["attachment"][0]["filename"] == "trace.log"
        assert "customfield_10099" not in fields
        assert data["data"]["url"] == f"{SITE}/browse/PA-7"
        assert fields["epic_link"] == "PA-1"

    async def test_jql_resolution_unresolved_is_rewritten_and_reported(self, jira, api) -> None:
        api.on("POST", SEARCH, search_pages((["PA-1"], None)))

        ok, data = result(await jira.search_issues("project = PA AND resolution = Unresolved AND status = Open"))

        assert ok is True
        sent = api.calls("POST", SEARCH)[0].body["jql"]
        assert "resolution IS EMPTY" in sent
        assert data["fixed_jql"] == sent and "resolution IS EMPTY" in data["warning"]

    async def test_projects_link_to_their_pages(self, jira, api) -> None:
        api.on("GET", "/project", [{"id": "1", "key": "PA", "name": "Payments", "avatarUrls": {"48x48": "x"}}])

        ok, data = result(await jira.get_projects())

        assert ok is True
        assert data["data"] == [{"id": "1", "key": "PA", "name": "Payments", "url": f"{SITE}/projects/PA"}]

    async def test_project_and_its_metadata(self, jira, api) -> None:
        api.on("GET", "/project/PA", {
            "key": "PA", "name": "Payments", "lead": {"displayName": "Ann Lee"},
            "issueTypes": [{"id": "1", "name": "Bug", "subtask": False}], "components": [{"id": "9", "name": "API"}],
        })

        ok, project = result(await jira.get_project("PA"))
        ok_meta, meta = result(await jira.get_project_metadata("PA"))

        assert (ok, ok_meta) == (True, True)
        assert project["data"]["url"] == f"{SITE}/projects/PA"
        assert meta["metadata"]["lead"] == "Ann Lee"
        assert meta["metadata"]["issue_types"][0]["name"] == "Bug"
        assert meta["metadata"]["components"] == [{"id": "9", "name": "API", "description": None}]


class TestComments:
    async def test_plain_text_comment_is_sent_as_a_document(self, jira, api) -> None:
        api.on("POST", "/issue/PA-7/comment", (201, {"id": "100", "body": {"type": "doc"}, "self": "x"}))

        ok, data = result(await jira.add_comment("PA-7", "Fixed in 2.3"))

        assert ok is True
        sent = api.calls("POST", "/issue/PA-7/comment")[0].body["body"]
        assert sent["content"][0]["content"][0]["text"] == "Fixed in 2.3"
        assert data["data"]["id"] == "100"

    async def test_empty_comment_is_refused_before_jira(self, jira, api) -> None:
        ok, data = result(await jira.add_comment("PA-7", ""))

        assert ok is False
        assert "cannot be empty" in data["guidance"]
        assert api.requests == []

    async def test_comment_already_in_document_format_is_sent_as_is(self, jira, api) -> None:
        doc = {"type": "doc", "version": 1, "content": []}
        api.on("POST", "/issue/PA-7/comment", (201, {"id": "101"}))

        ok, _ = result(await jira.add_comment("PA-7", doc))

        assert ok is True
        assert api.calls("POST", "/issue/PA-7/comment")[0].body["body"] == doc

    async def test_comments_are_listed(self, jira, api) -> None:
        api.on("GET", "/issue/PA-7/comment", {"comments": [{"id": "1", "body": "hi", "self": "x"}], "total": 1, "startAt": 0})

        ok, data = result(await jira.get_comments("PA-7"))

        assert ok is True
        assert data["data"]["comments"] == [{"id": "1", "body": "hi"}]


class TestSignedInUser:
    async def test_current_user_and_connection_check_read_myself(self, jira, api) -> None:
        api.on("GET", "/myself", user("acc-me", "Me Person", "me@acme.test"))

        ok, me = result(await jira.get_current_user())
        ok_conn, conn = result(await jira.validate_connection())

        assert (ok, ok_conn) == (True, True)
        assert me["data"]["accountId"] == "acc-me"
        assert conn["message"] == "JIRA connection is valid"
        assert {c.path for c in api.requests} == {"/rest/api/3/myself"}


class TestCreateIssueFieldErrors:
    async def test_missing_fields_are_named_with_a_way_to_find_them(self, jira, api) -> None:
        api.on("POST", "/issue", (400, {"errorMessages": [], "errors": {"customfield_10020": "Team is required."}}))
        api.on("GET", "/field", [{"id": "customfield_10020", "name": "Team"}])

        ok, data = result(await jira.create_issue("PA", "Login fails", "Bug"))

        assert ok is False
        assert data["field_errors"] == {"Team (customfield_10020)": "Team is required."}
        assert "get_create_issue_fields(project_key='PA', issue_type_name='Bug')" in data["guidance"]

    async def test_blank_summary_is_refused_before_jira(self, jira, api) -> None:
        ok, data = result(await jira.create_issue("PA", "", "Bug"))

        assert ok is False
        assert data["validation_error"] == "Summary is required"
        assert api.requests == []


EVERY_TOOL = [
    ("validate_connection", {}),
    ("get_current_user", {}),
    ("get_create_issue_fields", {"project_key": "PA", "issue_type_name": "Bug"}),
    ("create_issue", {"project_key": "PA", "summary": "Login fails", "issue_type_name": "Bug"}),
    ("update_issue", {"issue_key": "PA-7", "summary": "New title"}),
    ("get_projects", {}),
    ("get_project", {"project_key": "PA"}),
    ("get_issues", {"project_key": "PA"}),
    ("get_issue", {"issue_key": "PA-7"}),
    ("search_issues", {"jql": 'project = "PA"'}),
    ("add_comment", {"issue_key": "PA-7", "comment": "On it"}),
    ("get_comments", {"issue_key": "PA-7"}),
    ("search_users", {"query": "ann"}),
    ("get_project_metadata", {"project_key": "PA"}),
]


class TestEveryToolFailsHonestly:
    @pytest.mark.parametrize(("tool_name", "args"), EVERY_TOOL, ids=[t[0] for t in EVERY_TOOL])
    async def test_a_rate_limit_is_never_reported_as_success(self, jira, api, tool_name, args) -> None:
        for verb in ("GET", "POST", "PUT"):
            api.on(verb, ".*", (429, {"errorMessages": ["Rate limit exceeded."]}, {"Retry-After": "9"}))

        ok, data = result(await getattr(jira, tool_name)(**args))

        assert ok is False
        message = assert_safe_error(data)
        assert "try again" in message.lower()
