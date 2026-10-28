"""get_issues must reject a filter it cannot parse instead of querying unfiltered; get_teams
returns every member of each team, not only the 10 the teams query lists."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.linear.linear import Linear
from app.sources.client.graphql.response import GraphQLResponse


def _tool() -> Linear:
    tool = Linear(MagicMock())
    tool.client = MagicMock()
    tool.client.issues = AsyncMock(return_value=MagicMock(success=True, data={"issues": []}))
    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "", "{not json", "[1, 2]", '"text"', "42", "null", "   ", '"{\\"state\\": 1}"',
    "true", "1.5", "NaN", '{"a": 1} x', '[{"a": 1}]', "\ufeff{}",
    pytest.param("[" * 200_000, id="deeply-nested"),
    pytest.param(42, id="int-not-a-string"),
    pytest.param({"state": {"name": {"eq": "Done"}}}, id="dict-not-a-string"),
])
async def test_get_issues_rejects_unparsable_filter(bad: object) -> None:
    tool = _tool()
    ok, out = await tool.get_issues(filter=bad)
    assert ok is False
    assert "Invalid filter" in json.loads(out)["error"]
    tool.client.issues.assert_not_called()


@pytest.mark.asyncio
async def test_get_issues_passes_valid_filter_through() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues(first=5, filter='{"state": {"name": {"eq": "Done"}}}')
    assert ok is True
    tool.client.issues.assert_awaited_once_with(
        first=5, after=None, filter={"state": {"name": {"eq": "Done"}}}
    )


@pytest.mark.asyncio
async def test_get_issues_without_filter_queries_unfiltered() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues()
    assert ok is True
    tool.client.issues.assert_awaited_once_with(first=None, after=None, filter=None)


@pytest.mark.asyncio
async def test_get_issues_passes_an_empty_filter_object_through() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues(filter="{}")
    assert ok is True
    tool.client.issues.assert_awaited_once_with(first=None, after=None, filter={})


def _member_page(emails: list, end_cursor=None) -> dict:
    return {"nodes": [{"email": e} for e in emails],
            "pageInfo": {"hasNextPage": end_cursor is not None, "endCursor": end_cursor}}


def _teams_tool(*teams: dict) -> Linear:
    tool = Linear(MagicMock())
    tool.client = MagicMock()
    tool.client.teams = AsyncMock(return_value=GraphQLResponse(
        success=True, data={"teams": {"nodes": list(teams), "pageInfo": {"hasNextPage": False}}}))
    tool.client.execute_operation = AsyncMock()
    return tool


@pytest.mark.asyncio
async def test_get_teams_reads_members_beyond_the_first_ten() -> None:
    emails = [f"m{i}@x.com" for i in range(25)]
    tool = _teams_tool({"id": "t-big", "members": _member_page(emails[:10], "m-10")})
    tool.client.execute_operation.side_effect = [
        GraphQLResponse(success=True, data={"team": {"members": _member_page(emails[10:20], "m-20")}}),
        GraphQLResponse(success=True, data={"team": {"members": _member_page(emails[20:])}}),
    ]

    ok, out = await tool.get_teams()

    assert ok is True
    [team] = json.loads(out)["data"]["teams"]["nodes"]
    assert [m["email"] for m in team["members"]["nodes"]] == emails
    assert team["members"]["pageInfo"]["hasNextPage"] is False
    assert [c.args for c in tool.client.execute_operation.await_args_list] == [
        ("teamMembers", {"id": "t-big", "first": 100, "after": "m-10"}),
        ("teamMembers", {"id": "t-big", "first": 100, "after": "m-20"}),
    ]


@pytest.mark.asyncio
async def test_get_teams_with_ten_members_makes_no_member_request() -> None:
    tool = _teams_tool({"id": "t-small", "members": _member_page(["a@x.com"])})

    ok, out = await tool.get_teams(first=5)

    assert ok is True
    assert json.loads(out)["data"]["teams"]["nodes"][0]["members"]["nodes"] == [{"email": "a@x.com"}]
    tool.client.teams.assert_awaited_once_with(first=5, after=None)
    tool.client.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("member_page", "error"), [
    (GraphQLResponse(success=False, message="Query too complex", status_code=400), "Query too complex"),
    (GraphQLResponse(success=True, data={"team": {"members": {
        "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "m-10"}}}}), "without a cursor"),
    (GraphQLResponse(success=True, data=None), "no members page"),
    (GraphQLResponse(success=True, data={"team": None}), "no members page"),
    (GraphQLResponse(success=True, data={"team": {"members": None}}), "no members page"),
])
async def test_get_teams_fails_rather_than_return_part_of_a_roster(member_page, error) -> None:
    tool = _teams_tool({"id": "t-big", "members": _member_page(["a@x.com"], "m-10")})
    tool.client.execute_operation.return_value = member_page

    ok, out = await tool.get_teams()

    assert ok is False
    assert error in json.loads(out)["error"]

