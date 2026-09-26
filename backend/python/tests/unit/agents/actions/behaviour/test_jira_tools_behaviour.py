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
