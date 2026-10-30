"""Expected-record builders agree with what the connectors store.

Each case here failed a nightly connector check because the builder, not the connector,
was out of step: a Jira issue's access rule, the link and times of a Linear file posted
in a comment, and a Drive link that differs only by Google's owner id.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.config.constants.arangodb import AccessRule

from connectors.jira.jira_expected import JiraExpected
from connectors.linear.linear_test_utils import fetch_first_file_in_teams
from validation.graph_entity_validator import _field_values_equal

pytestmark = pytest.mark.unit

_UPLOAD = "https://uploads.linear.app/org/abc/def/report.pdf"


class _JiraIssues:
    def __init__(self, fields: dict[str, Any]) -> None:
        self._fields = fields

    async def get_issue(self, issueIdOrKey: str) -> Any:
        body = {"id": "10001", "key": issueIdOrKey, "fields": self._fields}
        return SimpleNamespace(status=200, json=lambda: body)


async def _ticket(fields: dict[str, Any]) -> tuple[Any, frozenset[str]]:
    base = {"summary": "s", "project": {"id": "10000"}, "issuetype": {"name": "Task", "hierarchyLevel": 0}}
    return await JiraExpected.ticket_record_and_unsettled(
        "QE-1", connector_id="c", datasource=_JiraIssues({**base, **fields}),
        site_base_url="https://example.atlassian.net",
    )


@pytest.mark.asyncio
async def test_a_jira_issue_with_a_null_security_level_is_open_to_its_project_only() -> None:
    ticket, unsettled = await _ticket({"security": None})
    assert ticket.access_rule == AccessRule.STRICT
    assert "access_rule" not in unsettled


@pytest.mark.asyncio
async def test_a_jira_issue_with_a_security_level_is_restricted() -> None:
    ticket, unsettled = await _ticket({"security": {"id": "10100", "name": "Staff"}})
    assert ticket.access_rule == AccessRule.RESTRICTED
    assert "access_rule" not in unsettled


@pytest.mark.asyncio
async def test_a_jira_issue_without_the_security_field_leaves_its_rule_uncompared() -> None:
    """A missing field is not a null level: the connector closes the issue when it
    knows of a security scheme and opens it to the project when there is none."""
    _ticket_record, unsettled = await _ticket({})
    assert "access_rule" in unsettled


class _LinearIssues:
    def __init__(self, issue: dict[str, Any]) -> None:
        self._issue = issue

    async def issues(self, **_: Any) -> Any:
        return SimpleNamespace(success=True, data={"issues": {"nodes": [self._issue], "pageInfo": {}}})

    async def projects(self, **_: Any) -> Any:
        return SimpleNamespace(success=True, data={"projects": {"nodes": [], "pageInfo": {}}})


def _issue(description: str, comment_body: str) -> dict[str, Any]:
    return {
        "id": "issue-1",
        "url": "https://linear.app/org/issue/ENG-1/title",
        "createdAt": "2026-10-26T14:34:12.933Z",
        "updatedAt": "2026-10-26T14:39:08.941Z",
        "description": description,
        "comments": {"nodes": [{
            "id": "comment-1",
            "body": comment_body,
            "url": "https://linear.app/org/issue/ENG-1/title#comment-7e1106a2",
            "createdAt": "2026-10-26T14:38:41.189Z",
            "updatedAt": "2026-10-26T14:38:42.427Z",
        }]},
    }


@pytest.mark.asyncio
async def test_a_linear_file_in_a_comment_takes_the_comments_link_and_times() -> None:
    found = await fetch_first_file_in_teams(_LinearIssues(_issue("", f"[report.pdf]({_UPLOAD})")), ["t"])
    assert found is not None
    assert found["parent_external_id"] == "issue-1"
    assert found["parent_weburl"] == "https://linear.app/org/issue/ENG-1/title#comment-7e1106a2"
    assert (found["parent_created_at"], found["parent_updated_at"]) == (1793025521189, 1793025522427)


@pytest.mark.asyncio
async def test_a_linear_file_in_the_description_takes_the_issues_link_and_times() -> None:
    found = await fetch_first_file_in_teams(_LinearIssues(_issue(f"[report.pdf]({_UPLOAD})", "")), ["t"])
    assert found is not None
    assert found["parent_weburl"] == "https://linear.app/org/issue/ENG-1/title"
    assert (found["parent_created_at"], found["parent_updated_at"]) == (1793025252933, 1793025548941)


_SHEET = "https://docs.google.com/spreadsheets/d/1hTeU/edit?usp=drivesdk&rtpof=true&sd=true"


def test_a_google_file_link_matches_with_or_without_the_owner_id() -> None:
    with_owner = _SHEET.replace("usp=drivesdk&", "usp=drivesdk&ouid=117598620009786072320&")
    assert _field_values_equal("weburl", with_owner, _SHEET)


def test_a_google_file_link_still_differs_on_any_other_parameter() -> None:
    assert not _field_values_equal("weburl", _SHEET, _SHEET.replace("sd=true", "sd=false"))
    assert not _field_values_equal("weburl", _SHEET, _SHEET.replace("/1hTeU/", "/other/"))


def test_other_links_compare_exactly() -> None:
    assert not _field_values_equal("weburl", "https://example.com/a?ouid=1", "https://example.com/a")
    assert not _field_values_equal(
        "weburl", "https://linear.app/org/issue/ENG-1/title#comment-1", "https://linear.app/org/issue/ENG-1/title")


def test_a_google_file_link_keeps_its_encoding_and_fragment() -> None:
    sheet = "https://docs.google.com/spreadsheets/d/1hTeU/edit?q=a%20b&ouid=1#gid=0"
    assert _field_values_equal("weburl", sheet, sheet.replace("&ouid=1", ""))
    assert not _field_values_equal("weburl", sheet, sheet.replace("a%20b", "a+b"))
    assert not _field_values_equal("weburl", sheet, sheet.replace("#gid=0", "#gid=1"))
    assert _field_values_equal(
        "weburl", "https://docs.google.com/document/d/1/edit?ouid=1", "https://docs.google.com/document/d/1/edit")
