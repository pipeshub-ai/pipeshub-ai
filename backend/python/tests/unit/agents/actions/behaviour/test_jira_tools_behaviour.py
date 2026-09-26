"""Behaviour tests for the Jira agent tools.

Each test drives a tool the way the agent does and checks what Jira would
receive and what the agent is told back. See ``jira_tool_fakes`` for what is
real and what is faked.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from jira_tool_fakes import API_TOKEN, BASIC, SITE, FakeJiraApi, build_jira_tool, issue, result, user

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
