"""
Unit tests for app.agents.actions.clickup.clickup

The ClickUp toolset runs through the real ClickUpDataSource and the real
ClickUp HTTP client. Only the httpx transport is replaced, so every test sees
the exact method, URL, query string and JSON body ClickUp would receive.
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx
import pytest

from app.agents.actions.clickup.clickup import (
    ClickUp,
    ClickUpEntityType,
    _build_clickup_web_url,
    _clickup_comment_label,
)
from app.sources.client.clickup.clickup import ClickUpClient, ClickUpRESTClientViaOAuth

V2 = "/api/v2"
V3 = "/api/v3"


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: object
    headers: httpx.Headers


@dataclass
class FakeClickUpAPI:
    """httpx transport handler: records requests and replays canned responses."""

    requests: list[RecordedRequest] = field(default_factory=list)
    routes: list[tuple[str, re.Pattern, list[object]]] = field(default_factory=list)

    def on(self, method: str, path_regex: str, *responses: object) -> "FakeClickUpAPI":
        """Each response is (status, json_body), or an exception to raise."""
        self.routes.append((method, re.compile(rf"^{path_regex}$"), list(responses)))
        return self

    def calls(self, method: str | None = None, path_regex: str | None = None) -> list[RecordedRequest]:
        return [
            r for r in self.requests
            if (method is None or r.method == method)
            and (path_regex is None or re.fullmatch(path_regex, r.path))
        ]

    def writes(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method in {"POST", "PUT", "PATCH", "DELETE"}]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        recorded = RecordedRequest(
            method=request.method,
            path=request.url.path,
            query=parse_qs(request.url.query.decode()),
            body=body,
            headers=request.headers,
        )
        self.requests.append(recorded)
        for method, pattern, responses in self.routes:
            if method == recorded.method and pattern.match(recorded.path):
                response = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(response, BaseException):
                    raise response
                status, payload = response
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"err": f"no fake route for {recorded.method} {recorded.path}", "ECODE": "TEST_404"})


@pytest.fixture
def api() -> FakeClickUpAPI:
    return FakeClickUpAPI()


@pytest.fixture
async def clickup(api: FakeClickUpAPI) -> AsyncIterator[ClickUp]:
    http = ClickUpRESTClientViaOAuth("oauth-token")
    http.client = httpx.AsyncClient(transport=httpx.MockTransport(api.handler), headers=http.headers)
    yield ClickUp(ClickUpClient(http))
    await http.close()


def ok(result: tuple[bool, str]) -> dict:
    success, payload = result
    assert success is True, f"expected success, got: {payload}"
    return json.loads(payload)


def fail(result: tuple[bool, str]) -> dict:
    success, payload = result
    assert success is False, f"expected failure, got success: {payload}"
    return json.loads(payload)


# ===========================================================================
# Workspace hierarchy
# ===========================================================================


class TestUserAndWorkspaces:
    @pytest.mark.asyncio
    async def test_authorized_user_sends_bearer_token(self, clickup, api) -> None:
        api.on("GET", f"{V2}/user", (200, {"user": {"id": 7, "username": "ann"}}))
        assert ok(await clickup.get_authorized_user())["data"]["user"]["id"] == 7
        assert api.requests[0].headers["authorization"] == "Bearer oauth-token"

    @pytest.mark.asyncio
    async def test_expired_token_is_reported_with_clickup_reason(self, clickup, api) -> None:
        api.on("GET", f"{V2}/user", (401, {"err": "Token invalid", "ECODE": "OAUTH_025"}))
        data = fail(await clickup.get_authorized_user())
        assert data["message"] == "Failed with status 401"
        assert data["data"]["err"] == "Token invalid"

    @pytest.mark.asyncio
    async def test_network_failure_is_a_failed_result(self, clickup, api) -> None:
        api.on("GET", f"{V2}/user", httpx.ConnectError("connection refused"))
        assert "connection refused" in fail(await clickup.get_authorized_user())["error"]

    @pytest.mark.asyncio
    async def test_workspaces_get_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V2}/team", (200, {"teams": [{"id": 9001, "name": "Acme"}, {"name": "no id"}]}))
        teams = ok(await clickup.get_authorized_teams_workspaces())["data"]["teams"]
        assert teams[0]["web_url"] == "https://app.clickup.com/9001/home"
        assert "web_url" not in teams[1]

    @pytest.mark.asyncio
    async def test_spaces_pass_archived_flag_and_get_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V2}/team/9001/space", (200, {"spaces": [{"id": "s1"}]}))
        data = ok(await clickup.get_spaces("9001", archived=True))
        assert api.requests[0].query == {"archived": ["true"]}
        assert data["data"]["spaces"][0]["web_url"] == "https://app.clickup.com/9001/v/o/s/s1"

    @pytest.mark.asyncio
    async def test_spaces_error_is_passed_through(self, clickup, api) -> None:
        api.on("GET", f"{V2}/team/nope/space", (401, {"err": "Team not authorized", "ECODE": "OAUTH_027"}))
        assert fail(await clickup.get_spaces("nope"))["data"]["err"] == "Team not authorized"

    @pytest.mark.asyncio
    async def test_folders_get_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V2}/space/s1/folder", (200, {"folders": [{"id": "f1"}]}))
        folder = ok(await clickup.get_folders("s1", "9001"))["data"]["folders"][0]
        assert folder["web_url"] == "https://app.clickup.com/9001/v/o/f/f1?pr=s1"

    @pytest.mark.asyncio
    async def test_lists_get_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V2}/folder/f1/list", (200, {"lists": [{"id": "l1"}]}))
        assert ok(await clickup.get_lists("f1", "9001"))["data"]["lists"][0]["web_url"] == (
            "https://app.clickup.com/9001/v/l/li/l1?pr=f1"
        )

    @pytest.mark.asyncio
    async def test_folderless_lists(self, clickup, api) -> None:
        api.on("GET", f"{V2}/space/s1/list", (200, {"lists": [{"id": "l2"}]}))
        data = ok(await clickup.get_folderless_lists("s1", "9001", archived=False))
        assert api.requests[0].query == {"archived": ["false"]}
        assert data["data"]["lists"][0]["id"] == "l2"


class TestCreateHierarchy:
    @pytest.mark.asyncio
    async def test_create_space_posts_name_and_options(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/space", (200, {"id": "s9", "name": "Ops"}))
        data = ok(await clickup.create_space("9001", "Ops", multiple_assignees=True, features={"due_dates": {"enabled": True}}))
        assert api.requests[0].body == {"name": "Ops", "multiple_assignees": True, "features": {"due_dates": {"enabled": True}}}
        assert data["data"]["web_url"] == "https://app.clickup.com/9001/v/o/s/s9"

    @pytest.mark.asyncio
    async def test_create_space_error_is_passed_through(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/space", (400, {"err": "Space name taken", "ECODE": "PROJ_010"}))
        assert fail(await clickup.create_space("9001", "Ops"))["data"]["err"] == "Space name taken"

    @pytest.mark.asyncio
    async def test_create_folder_without_team_has_no_web_url(self, clickup, api) -> None:
        api.on("POST", f"{V2}/space/s1/folder", (200, {"id": "f9"}))
        data = ok(await clickup.create_folder("s1", "Q3"))
        assert api.requests[0].body == {"name": "Q3"}
        assert "web_url" not in data["data"]

    @pytest.mark.asyncio
    async def test_create_list_in_folder(self, clickup, api) -> None:
        api.on("POST", f"{V2}/folder/f1/list", (200, {"id": "l9"}))
        data = ok(await clickup.create_list("Backlog", folder_id="f1", team_id="9001", content="All the things", priority=2))
        assert api.requests[0].body == {"name": "Backlog", "content": "All the things", "priority": 2}
        assert data["data"]["web_url"] == "https://app.clickup.com/9001/v/l/li/l9?pr=f1"

    @pytest.mark.asyncio
    async def test_create_folderless_list_in_space(self, clickup, api) -> None:
        api.on("POST", f"{V2}/space/s1/list", (200, {"id": "l9"}))
        ok(await clickup.create_list("Backlog", space_id="s1"))
        assert [w.path for w in api.writes()] == [f"{V2}/space/s1/list"]

    @pytest.mark.asyncio
    async def test_create_list_without_folder_or_space_sends_nothing(self, clickup, api) -> None:
        message = fail(await clickup.create_list("Backlog"))["error"]
        assert "folder_id" in message and "space_id" in message
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_create_list_accepts_priority_sent_as_text(self, clickup, api) -> None:
        api.on("POST", f"{V2}/folder/f1/list", (200, {"id": "l9"}))
        ok(await clickup.create_list("Backlog", folder_id="f1", priority="2"))
        assert api.requests[0].body == {"name": "Backlog", "priority": 2}

    @pytest.mark.asyncio
    async def test_create_list_with_invalid_priority_sends_nothing(self, clickup, api) -> None:
        assert "is not valid" in fail(await clickup.create_list("Backlog", space_id="s1", priority=9))["error"]
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_list_with_invalid_priority_sends_nothing(self, clickup, api) -> None:
        assert "is not valid" in fail(await clickup.update_list("l1", priority=9))["error"]
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_list_sends_only_given_fields(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/list/l1", (200, {"id": "l1"}))
        ok(await clickup.update_list("l1", name="Renamed", unset_status=True))
        assert api.requests[0].body == {"name": "Renamed", "unset_status": True}


# ===========================================================================
# Tasks
# ===========================================================================


class TestGetTasks:
    @pytest.mark.asyncio
    async def test_filters_use_clickup_array_query_syntax(self, clickup, api) -> None:
        api.on("GET", f"{V2}/team/9001/task", (200, {"tasks": [{"id": "t1"}], "last_page": True}))
        ok(await clickup.get_tasks(
            "9001", page=2, statuses=["to do", "in progress"], assignees=[7, 8], list_ids=["l1"],
            custom_fields=[{"field_id": "f", "operator": "=", "value": "x"}], due_date_lt=1700000000000,
        ))
        query = api.requests[0].query
        assert query["page"] == ["2"]
        assert query["order_by"] == ["updated"]
        assert query["include_closed"] == ["false"]
        assert query["statuses[]"] == ["to do", "in progress"]
        assert query["assignees[]"] == ["7", "8"]
        assert query["list_ids[]"] == ["l1"]
        assert json.loads(query["custom_fields[]"][0]) == [{"field_id": "f", "operator": "=", "value": "x"}]
        assert query["due_date_lt"] == ["1700000000000"]

    @pytest.mark.asyncio
    async def test_defaults_request_first_page_by_updated(self, clickup, api) -> None:
        api.on("GET", f"{V2}/team/9001/task", (200, {"tasks": []}))
        ok(await clickup.get_tasks("9001"))
        assert api.requests[0].query == {"page": ["0"], "order_by": ["updated"], "include_closed": ["false"]}


class TestSearchTasks:
    @pytest.mark.asyncio
    async def test_creates_view_reads_tasks_and_deletes_view(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/view", (200, {"view": {"id": "v-1"}}))
        api.on("GET", f"{V2}/view/v-1/task", (200, {"tasks": [{"id": "t1", "name": "Login bug"}]}))
        api.on("DELETE", f"{V2}/view/v-1", (200, {}))
        data = ok(await clickup.search_tasks("9001", "login bug", show_closed=False, page=1))
        view_body = api.calls("POST")[0].body
        assert view_body["name"] == "Search: login bug"
        assert view_body["filters"]["search"] == "login bug"
        assert view_body["filters"]["show_closed"] is False
        assert api.calls("GET", f"{V2}/view/v-1/task")[0].query == {"page": ["1"]}
        assert data["data"]["tasks"][0]["id"] == "t1"
        assert [r.method for r in api.requests] == ["POST", "GET", "DELETE"]

    @pytest.mark.asyncio
    async def test_long_keyword_is_trimmed_in_view_name(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/view", (200, {"view": {"id": "v-1"}}))
        api.on("GET", f"{V2}/view/v-1/task", (200, {"tasks": []}))
        api.on("DELETE", f"{V2}/view/v-1", (200, {}))
        ok(await clickup.search_tasks("9001", "x" * 80))
        assert api.calls("POST")[0].body["name"] == "Search: " + "x" * 50

    @pytest.mark.asyncio
    async def test_view_is_deleted_even_when_reading_tasks_fails(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/view", (200, {"view": {"id": "v-1"}}))
        api.on("GET", f"{V2}/view/v-1/task", httpx.ReadTimeout("timed out"))
        api.on("DELETE", f"{V2}/view/v-1", (200, {}))
        assert "timed out" in fail(await clickup.search_tasks("9001", "invoice"))["error"]
        assert api.calls("DELETE", f"{V2}/view/v-1")

    @pytest.mark.asyncio
    async def test_failed_view_creation_deletes_nothing(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/view", (403, {"err": "Not authorized", "ECODE": "VIEW_001"}))
        assert fail(await clickup.search_tasks("9001", "invoice"))["data"]["err"] == "Not authorized"
        assert not api.calls("DELETE")

    @pytest.mark.asyncio
    async def test_view_without_id_is_an_error(self, clickup, api) -> None:
        api.on("POST", f"{V2}/team/9001/view", (200, {"view": {}}))
        assert fail(await clickup.search_tasks("9001", "invoice"))["error"] == "Create view did not return view id"
        assert not api.calls("DELETE")


class TestTaskWrites:
    @pytest.mark.asyncio
    async def test_get_task(self, clickup, api) -> None:
        api.on("GET", f"{V2}/task/abc", (200, {"id": "abc", "name": "Ship it"}))
        assert ok(await clickup.get_task("abc"))["data"]["name"] == "Ship it"

    @pytest.mark.asyncio
    async def test_create_task_maps_fields_to_body(self, clickup, api) -> None:
        api.on("POST", f"{V2}/list/l1/task", (200, {"id": "t-new", "name": "Fix login"}))
        data = ok(await clickup.create_task(
            "l1", "Fix login", description="Users see 500", status="to do", priority=1, assignees=[7], parent="t-parent",
        ))
        assert api.requests[0].body == {
            "name": "Fix login", "description": "Users see 500", "status": "to do",
            "priority": 1, "assignees": [7], "parent": "t-parent",
        }
        assert data["data"]["id"] == "t-new"

    @pytest.mark.asyncio
    async def test_create_task_with_out_of_range_priority_sends_nothing(self, clickup, api) -> None:
        # ClickUp's datasource drops an unknown priority, so the task would be created without one.
        message = fail(await clickup.create_task("l1", "Fix login", priority=5))["error"]
        assert message == "priority 5 is not valid. Use 1 (Urgent), 2 (High), 3 (Normal) or 4 (Low)."
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_create_task_accepts_priority_sent_as_text(self, clickup, api) -> None:
        api.on("POST", f"{V2}/list/l1/task", (200, {"id": "t-new"}))
        ok(await clickup.create_task("l1", "Fix login", priority="2"))
        assert api.requests[0].body["priority"] == 2

    @pytest.mark.parametrize("priority", ["--2", "\u00b2", "2.0", ""])
    @pytest.mark.asyncio
    async def test_unparseable_priority_text_is_refused_not_raised(self, clickup, api, priority) -> None:
        assert "is not valid" in fail(await clickup.create_task("l1", "Fix login", priority=priority))["error"]
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_task_with_invalid_priority_sends_nothing(self, clickup, api) -> None:
        assert "is not valid" in fail(await clickup.update_task("abc", priority="high"))["error"]
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_create_task_error_is_passed_through(self, clickup, api) -> None:
        api.on("POST", f"{V2}/list/l1/task", (400, {"err": "Status does not exist", "ECODE": "ITEM_117"}))
        data = fail(await clickup.create_task("l1", "Fix login", status="doing"))
        assert data["data"]["err"] == "Status does not exist"

    @pytest.mark.asyncio
    async def test_update_task_sends_assignee_changes_and_custom_id_query(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/task/DEV-12", (200, {"id": "abc"}))
        ok(await clickup.update_task(
            "DEV-12", status="done", assignees_add=[7], assignees_rem=[8], custom_task_ids=True, team_id="9001",
        ))
        request = api.requests[0]
        assert request.body == {"status": "done", "assignees": {"add": [7], "rem": [8]}}
        assert request.query == {"custom_task_ids": ["true"], "team_id": ["9001"]}


# ===========================================================================
# Comments and checklists
# ===========================================================================


class TestComments:
    @pytest.mark.asyncio
    async def test_task_comments_get_web_urls_and_pagination(self, clickup, api) -> None:
        api.on("GET", f"{V2}/task/t1/comment", (200, {"comments": [{"id": "c1", "comment_text": "hi"}]}))
        data = ok(await clickup.get_comments(task_id="t1", start=1700000000000, start_id="c0"))
        assert api.requests[0].query == {"start": ["1700000000000"], "start_id": ["c0"]}
        assert data["data"]["comments"][0]["web_url"] == "https://app.clickup.com/t/t1?comment=c1"

    @pytest.mark.asyncio
    async def test_comment_replies_get_threaded_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V2}/comment/c1/reply", (200, {"comments": [{"id": "r1"}]}))
        data = ok(await clickup.get_comments(task_id="t1", comment_id="c1"))
        assert data["data"]["comments"][0]["web_url"] == "https://app.clickup.com/t/t1?comment=c1&threadedComment=r1"

    @pytest.mark.asyncio
    async def test_new_comment_on_task(self, clickup, api) -> None:
        api.on("POST", f"{V2}/task/t1/comment", (200, {"id": 555}))
        data = ok(await clickup.create_task_comment("Looks good", task_id="t1", notify_all=True))
        assert api.requests[0].body == {"comment_text": "Looks good", "notify_all": True}
        assert data["data"]["web_url"] == "https://app.clickup.com/t/t1?comment=555"

    @pytest.mark.asyncio
    async def test_reply_to_comment(self, clickup, api) -> None:
        api.on("POST", f"{V2}/comment/c1/reply", (200, {"id": 777}))
        data = ok(await clickup.create_task_comment("Agreed", task_id="t1", comment_id="c1", assignee=7))
        assert [w.path for w in api.writes()] == [f"{V2}/comment/c1/reply"]
        assert api.requests[0].body == {"comment_text": "Agreed", "assignee": 7}
        assert data["data"]["web_url"] == "https://app.clickup.com/t/t1?comment=c1&threadedComment=777"

    @pytest.mark.asyncio
    async def test_comment_without_task_or_comment_sends_nothing(self, clickup, api) -> None:
        message = fail(await clickup.create_task_comment("Looks good"))["error"]
        assert "task_id" in message and "comment_id" in message
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_reading_comments_without_task_or_comment_sends_nothing(self, clickup, api) -> None:
        assert "task_id" in fail(await clickup.get_comments())["error"]
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_comment_error_is_passed_through(self, clickup, api) -> None:
        api.on("POST", f"{V2}/task/t1/comment", (404, {"err": "Task not found", "ECODE": "ITEM_013"}))
        assert fail(await clickup.create_task_comment("hi", task_id="t1"))["data"]["err"] == "Task not found"


class TestChecklists:
    @pytest.mark.asyncio
    async def test_create_checklist(self, clickup, api) -> None:
        api.on("POST", f"{V2}/task/t1/checklist", (200, {"checklist": {"id": "cl1"}}))
        ok(await clickup.create_checklist("t1", "Release"))
        assert api.requests[0].body == {"name": "Release"}

    @pytest.mark.asyncio
    async def test_create_checklist_item(self, clickup, api) -> None:
        api.on("POST", f"{V2}/checklist/cl1/checklist_item", (200, {"checklist": {"id": "cl1"}}))
        ok(await clickup.create_checklist_item("cl1", "Tag release", assignee=7))
        assert api.requests[0].body == {"name": "Tag release", "assignee": 7}

    @pytest.mark.asyncio
    async def test_resolve_checklist_item(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/checklist/cl1/checklist_item/i1", (200, {"checklist": {"id": "cl1"}}))
        ok(await clickup.update_checklist_item("cl1", "i1", resolved=True))
        assert api.requests[0].body == {"resolved": True}


# ===========================================================================
# Docs (API v3)
# ===========================================================================


class TestDocs:
    @pytest.mark.asyncio
    async def test_list_docs_uses_v3_and_adds_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V3}/workspaces/9001/docs", (200, {"docs": [{"id": "d1"}], "next_cursor": "abc"}))
        data = ok(await clickup.get_workspace_docs("9001", limit=10, cursor="prev"))
        assert api.requests[0].query == {"limit": ["10"], "cursor": ["prev"]}
        assert data["data"]["docs"][0]["web_url"] == "https://app.clickup.com/9001/v/dc/d1"

    @pytest.mark.asyncio
    async def test_doc_pages_list_response_is_returned_as_is(self, clickup, api) -> None:
        # ClickUp returns a bare JSON array of pages here, so there is nothing to decorate.
        api.on("GET", f"{V3}/workspaces/9001/docs/d1/pages", (200, [{"id": "p1"}]))
        assert ok(await clickup.get_doc_pages("9001", "d1"))["data"] == [{"id": "p1"}]

    @pytest.mark.asyncio
    async def test_doc_pages_object_response_gets_web_urls(self, clickup, api) -> None:
        api.on("GET", f"{V3}/workspaces/9001/docs/d1/pages", (200, {"pages": [{"id": "p1"}]}))
        assert ok(await clickup.get_doc_pages("9001", "d1"))["data"]["pages"][0]["web_url"] == (
            "https://app.clickup.com/9001/v/dc/d1/p1"
        )

    @pytest.mark.asyncio
    async def test_get_doc_page(self, clickup, api) -> None:
        api.on("GET", f"{V3}/workspaces/9001/docs/d1/pages/p1", (200, {"id": "p1", "content": "# Hi"}))
        assert ok(await clickup.get_doc_page("9001", "d1", "p1"))["data"]["web_url"].endswith("/v/dc/d1/p1")

    @pytest.mark.asyncio
    async def test_create_doc_sends_parent_only_when_complete(self, clickup, api) -> None:
        api.on("POST", f"{V3}/workspaces/9001/docs", (200, {"id": "d9"}))
        ok(await clickup.create_doc("9001", "Runbook", parent_id="s1"))
        ok(await clickup.create_doc("9001", "Runbook", parent_id="s1", parent_type=4, visibility="PRIVATE"))
        first, second = (r.body for r in api.calls("POST"))
        assert first == {"name": "Runbook", "create_page": False}
        assert second == {"name": "Runbook", "create_page": False, "parent": {"id": "s1", "type": 4}, "visibility": "PRIVATE"}

    @pytest.mark.asyncio
    async def test_create_doc_page(self, clickup, api) -> None:
        api.on("POST", f"{V3}/workspaces/9001/docs/d1/pages", (200, {"id": "p9"}))
        data = ok(await clickup.create_doc_page("9001", "d1", name="Intro", content="Hello", parent_page_id="p1"))
        assert api.requests[0].body == {"name": "Intro", "content": "Hello", "content_format": "text/md", "parent_page_id": "p1"}
        assert data["data"]["web_url"].endswith("/v/dc/d1/p9")

    @pytest.mark.asyncio
    async def test_update_doc_page_appends_content(self, clickup, api) -> None:
        api.on("PUT", f"{V3}/workspaces/9001/docs/d1/pages/p1", (200, {}))
        ok(await clickup.update_doc_page("9001", "d1", "p1", content="More", content_edit_mode="append"))
        assert api.requests[0].body == {"content_edit_mode": "append", "content_format": "text/md", "content": "More"}


# ===========================================================================
# Helpers
# ===========================================================================


class TestHelpers:
    def test_web_url_needs_team_for_workspace_entities(self) -> None:
        assert _build_clickup_web_url(ClickUpEntityType.SPACE, team_id=None, space_id="s1") == ""

    def test_web_url_needs_every_id(self) -> None:
        assert _build_clickup_web_url(ClickUpEntityType.FOLDER, team_id="9", folder_id="f1") == ""
        assert _build_clickup_web_url(ClickUpEntityType.PAGE, team_id="9", doc_id="d1") == ""
        assert _build_clickup_web_url(ClickUpEntityType.COMMENT_REPLY, task_id="t", comment_id="c") == ""

    def test_comment_label_prefers_user_and_first_line(self) -> None:
        assert _clickup_comment_label({"user": {"username": "ann"}, "comment_text": "first\nsecond"}) == "ann: first"
        assert _clickup_comment_label({"comment_text": ""}) == "?"


class TestUpdatesWithNothingToChange:
    """An update with no fields would reach ClickUp as an empty change and be reported as done."""

    @pytest.mark.asyncio
    async def test_update_task(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/task/abc", (200, {"id": "abc"}))
        assert fail(await clickup.update_task("abc", name="", assignees_add=[]))["error"].startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.parametrize("field", ["due_date", "start_date", "time_estimate"])
    @pytest.mark.asyncio
    async def test_update_task_with_only_a_zero_date_or_estimate(self, clickup, api, field) -> None:
        # The datasource leaves these out of the body when they are 0.
        api.on("PUT", f"{V2}/task/abc", (200, {"id": "abc"}))
        assert fail(await clickup.update_task("abc", **{field: 0}))["error"].startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_list(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/list/l1", (200, {"id": "l1"}))
        assert fail(await clickup.update_list("l1"))["error"].startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_checklist_item(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/checklist/cl1/checklist_item/i1", (200, {}))
        assert fail(await clickup.update_checklist_item("cl1", "i1"))["error"].startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_update_doc_page(self, clickup, api) -> None:
        api.on("PUT", f"{V3}/workspaces/9001/docs/d1/pages/p1", (200, {}))
        assert fail(await clickup.update_doc_page("9001", "d1", "p1"))["error"].startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_clearing_doc_page_content_counts_as_a_change(self, clickup, api) -> None:
        api.on("PUT", f"{V3}/workspaces/9001/docs/d1/pages/p1", (200, {}))
        ok(await clickup.update_doc_page("9001", "d1", "p1", content=""))
        assert api.requests[0].body["content"] == ""

    @pytest.mark.asyncio
    async def test_resolved_false_counts_as_a_change(self, clickup, api) -> None:
        api.on("PUT", f"{V2}/checklist/cl1/checklist_item/i1", (200, {}))
        ok(await clickup.update_checklist_item("cl1", "i1", resolved=False))
        assert api.requests[0].body == {"resolved": False}
