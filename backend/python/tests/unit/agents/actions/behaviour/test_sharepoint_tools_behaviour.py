"""Behaviour tests for the SharePoint agent tools.

Each test drives a tool the way the agent does and checks what Microsoft Graph
would receive and what the agent is told back. See ``sharepoint_tool_fakes``
for what is real and what is faked.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sharepoint_tool_fakes import (
    DRIVE,
    SITE,
    USER_TOKEN,
    V1,
    build_sharepoint_tool,
    drive_item,
    notebook,
    onenote_page,
    section,
)

from tests.unit.connectors.sources.microsoft.behaviour.ms_graph_fakes import (
    MicrosoftCloudStub,
    bearer,
    graph_error,
    page,
)

if TYPE_CHECKING:
    from app.agents.actions.microsoft.sharepoint.sharepoint import SharePoint

SITE_PATH = f"{V1}/sites/{SITE}"
NOTEBOOKS = f"{SITE_PATH}/onenote/notebooks"


@pytest.fixture
def stub() -> MicrosoftCloudStub:
    return MicrosoftCloudStub()


@pytest.fixture
def sp(monkeypatch: pytest.MonkeyPatch, stub: MicrosoftCloudStub) -> SharePoint:
    return build_sharepoint_tool(monkeypatch, stub)


def result(outcome: tuple[bool, str]) -> tuple[bool, dict[str, Any]]:
    success, text = outcome
    return success, json.loads(text)


def assert_safe_error(payload: dict[str, Any]) -> str:
    message = payload["error"]
    assert isinstance(message, str) and message
    for leaked in (USER_TOKEN, "Bearer", "APIError", "ODataError", "MainError", "Traceback"):
        assert leaked not in message, f"{leaked!r} leaked into: {message}"
    return message


def graph_calls(stub: MicrosoftCloudStub) -> list[tuple[str, str]]:
    return [(r.method, stub.path_of(r)) for r in stub.graph_calls()]


# ---------------------------------------------------------------------------
# Sites, pages, drives, files
# ---------------------------------------------------------------------------


class TestReads:
    async def test_get_site_reads_it_as_the_signed_in_user(self, sp, stub) -> None:
        stub.on("GET", SITE_PATH, {"id": SITE, "displayName": "Engineering", "webUrl": "https://contoso.sharepoint.com/eng"})

        ok, data = result(await sp.get_site(site_id=SITE))

        assert ok is True
        assert data["id"] == SITE
        assert [bearer(r) for r in stub.graph_calls()] == [f"Bearer {USER_TOKEN}"]

    async def test_get_pages_lists_the_sites_pages(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/pages", page([
            {"@odata.type": "#microsoft.graph.sitePage", "id": "p-1", "title": "Home", "name": "Home.aspx"},
            {"@odata.type": "#microsoft.graph.sitePage", "id": "p-2", "title": "Onboarding", "name": "Onboarding.aspx"},
        ]))

        ok, data = result(await sp.get_pages(site_id=SITE, top=5))

        assert ok is True
        assert [(p["page_id"], p["title"]) for p in data["pages"]] == [("p-1", "Home"), ("p-2", "Onboarding")]
        assert stub.query(stub.graph_calls()[0])["$top"] == "5"
        assert data.get("has_more") in (None, False)

    async def test_get_pages_says_when_graph_has_more(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/pages", page(
            [{"@odata.type": "#microsoft.graph.sitePage", "id": "p-1", "title": "Home"}],
            next_link=f"https://graph.microsoft.com{SITE_PATH}/pages?$skiptoken=2",
        ))

        ok, data = result(await sp.get_pages(site_id=SITE, top=1))

        assert ok is True
        assert data["has_more"] is True
        assert "search_pages" in data["note"]

    @pytest.mark.xfail(strict=True, reason=(
        "Left alone: get_pages answers a 404 with success and 'No pages found', which it does on purpose for sites "
        "without the pages API, so a site id that does not exist also reads as a site with no pages."
    ))
    async def test_a_site_that_does_not_exist_is_not_reported_as_empty(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/pages", graph_error(404, "itemNotFound", "The site was not found"))

        ok, _ = result(await sp.get_pages(site_id=SITE))

        assert ok is False

    async def test_list_drives_names_each_library(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/drives", page([{"id": DRIVE, "name": "Documents", "driveType": "documentLibrary"}]))

        ok, data = result(await sp.list_drives(site_id=SITE))

        assert ok is True
        assert [(d["id"], d["name"], d["drive_type"]) for d in data["drives"]] == [(DRIVE, "Documents", "documentLibrary")]

    async def test_list_files_reads_the_root_then_nested_folders_to_the_depth_asked(self, sp, stub) -> None:
        stub.on("GET", f"{V1}/drives/{DRIVE}/root/children", page([
            drive_item("f-1", "Specs", folder=True), drive_item("d-1", "readme.txt"),
        ]))
        stub.on("GET", f"{V1}/drives/{DRIVE}/items/f-1/children", page([drive_item("d-2", "api.txt")]))

        ok, data = result(await sp.list_files(site_id=SITE, drive_id=DRIVE, depth=2, top=20))

        assert ok is True
        assert [f["name"] for f in data["files"]] == ["readme.txt", "api.txt"]
        assert [f["name"] for f in data["folders"]] == ["Specs"]
        assert {stub.query(r)["$top"] for r in stub.graph_calls()} == {"20"}
        assert data.get("has_more") in (None, False)

    async def test_list_files_says_when_a_folder_had_more_than_one_page(self, sp, stub) -> None:
        stub.on("GET", f"{V1}/drives/{DRIVE}/items/f-1/children", page(
            [drive_item("d-1", "a.txt")], next_link=f"https://graph.microsoft.com{V1}/drives/{DRIVE}/items/f-1/children?$skiptoken=x",
        ))

        ok, data = result(await sp.list_files(site_id=SITE, drive_id=DRIVE, folder_id="f-1", top=1))

        assert ok is True
        assert data["has_more"] is True
        assert "search_files" in data["note"]

    async def test_list_files_refuses_a_depth_below_one(self, sp, stub) -> None:
        ok, data = result(await sp.list_files(site_id=SITE, drive_id=DRIVE, depth=-1))

        assert ok is False and "depth" in data["error"]
        assert stub.graph_calls() == []

    async def test_get_file_metadata_describes_the_item(self, sp, stub) -> None:
        stub.on("GET", f"{V1}/drives/{DRIVE}/items/d-1", drive_item("d-1", "notes.txt", size=42, eTag="e-1"))

        ok, data = result(await sp.get_file_metadata(site_id=SITE, drive_id=DRIVE, item_id="d-1"))

        assert ok is True
        assert (data["name"], data["size_bytes"], data["mime_type"], data["content_readable_as_text"]) == (
            "notes.txt", 42, "text/plain", True)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


class TestWrites:
    async def test_create_folder_posts_into_the_parent_and_never_overwrites(self, sp, stub) -> None:
        stub.on("POST", f"{V1}/drives/{DRIVE}/items/f-1/children", drive_item("f-9", "Q3", folder=True))

        ok, data = result(await sp.create_folder(site_id=SITE, drive_id=DRIVE, folder_name="Q3", parent_folder_id="f-1"))

        assert ok is True and data["folder_id"] == "f-9"
        body = json.loads(stub.calls("POST", f"{V1}/drives/{DRIVE}/items/f-1/children")[0].content)
        assert body == {"name": "Q3", "folder": {}, "@microsoft.graph.conflictBehavior": "rename"}

    async def test_a_refused_folder_is_not_reported_as_created(self, sp, stub) -> None:
        stub.on("POST", f"{V1}/drives/{DRIVE}/root/children", graph_error(403, "accessDenied", "Access denied"))

        ok, data = result(await sp.create_folder(site_id=SITE, drive_id=DRIVE, folder_name="Q3"))

        assert ok is False
        assert "created" not in data["error"]

    async def test_move_item_patches_the_new_parent_and_name(self, sp, stub) -> None:
        stub.on("PATCH", f"{V1}/drives/{DRIVE}/items/d-1",
                drive_item("d-1", "final.txt", parentReference={"driveId": DRIVE, "id": "f-2"}))

        ok, data = result(await sp.move_item(site_id=SITE, drive_id=DRIVE, item_id="d-1",
                                            destination_folder_id="f-2", new_name="final.txt"))

        assert ok is True and data["destination_folder_id"] == "f-2"
        body = json.loads(stub.calls("PATCH", f"{V1}/drives/{DRIVE}/items/d-1")[0].content)
        assert body == {"parentReference": {"id": "f-2"}, "name": "final.txt"}

    @pytest.mark.parametrize(("item_id", "destination"), [(" ", "f-2"), ("d-1", " "), ("d-1", "d-1")])
    async def test_move_item_refuses_missing_or_circular_targets(self, sp, stub, item_id, destination) -> None:
        ok, _ = result(await sp.move_item(site_id=SITE, drive_id=DRIVE, item_id=item_id, destination_folder_id=destination))

        assert ok is False
        assert stub.graph_calls() == []

    async def test_update_page_needs_something_to_change(self, sp, stub) -> None:
        ok, _ = result(await sp.update_page(site_id=SITE, page_id="p-1"))

        assert ok is False
        assert stub.graph_calls() == []


# ---------------------------------------------------------------------------
# OneNote
# ---------------------------------------------------------------------------


def notebooks_by_skip(pages_by_skip: dict[str, object]) -> object:
    def respond(request: httpx.Request) -> httpx.Response:
        item = pages_by_skip[MicrosoftCloudStub.query(request).get("$skip", "0")]
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)
    return respond


class TestNotebooks:
    async def test_find_notebook_resolves_a_single_match(self, sp, stub) -> None:
        stub.on("GET", NOTEBOOKS, page([notebook("nb-1", "Roadmap"), notebook("nb-2", "Retro notes")]))

        ok, data = result(await sp.find_notebook(site_id=SITE, notebook_query="roadmap"))

        assert ok is True
        assert (data["resolved"], data["notebook_id"]) == (True, "nb-1")

    async def test_several_matches_are_left_for_the_user_to_choose(self, sp, stub) -> None:
        stub.on("GET", NOTEBOOKS, page([notebook("nb-1", "Plan 2025"), notebook("nb-2", "Plan 2026")]))

        ok, data = result(await sp.find_notebook(site_id=SITE, notebook_query="plan"))

        assert ok is True
        assert data["resolved"] is False
        assert [c["notebook_id"] for c in data["candidates"]] == ["nb-1", "nb-2"]

    async def test_a_notebook_past_the_first_fifty_is_found(self, sp, stub) -> None:
        first = [notebook(f"nb-{i}", f"Team notes {i}") for i in range(50)]
        stub.on("GET", NOTEBOOKS, notebooks_by_skip({"0": page(first), "50": page([notebook("nb-x", "Roadmap")])}))

        ok, data = result(await sp.find_notebook(site_id=SITE, notebook_query="roadmap"))

        assert ok is True
        assert (data["resolved"], data["notebook_id"]) == (True, "nb-x")

    async def test_an_unfinished_notebook_list_resolves_nothing(self, sp, stub) -> None:
        first = [notebook("nb-1", "Roadmap draft")] + [notebook(f"nb-{i}", f"Team {i}") for i in range(2, 51)]
        stub.on("GET", NOTEBOOKS, notebooks_by_skip({
            "0": page(first), "50": graph_error(503, "serviceNotAvailable", "Service unavailable"),
        }))

        ok, data = result(await sp.find_notebook(site_id=SITE, notebook_query="roadmap"))

        assert ok is False
        assert data["resolved"] is False
        assert "notebook_id" not in data

    async def test_a_notebook_without_a_name_matches_nothing(self, sp, stub) -> None:
        stub.on("GET", NOTEBOOKS, page([notebook("nb-0", ""), notebook("nb-1", "Roadmap")]))

        ok, data = result(await sp.find_notebook(site_id=SITE, notebook_query="roadmap"))

        assert ok is True
        assert (data["resolved"], data["notebook_id"]) == (True, "nb-1")

    async def test_list_notebook_pages_groups_pages_by_section(self, sp, stub) -> None:
        stub.on("GET", f"{NOTEBOOKS}/nb-1/sections", page([section("s-1", "Q1"), section("s-2", "Q2")]))
        stub.on("GET", f"{SITE_PATH}/onenote/sections/s-1/pages", page([onenote_page("pg-1", "Kickoff")]))
        stub.on("GET", f"{SITE_PATH}/onenote/sections/s-2/pages", page([onenote_page("pg-2", "Review")]))

        ok, data = result(await sp.list_notebook_pages(site_id=SITE, notebook_id="nb-1"))

        assert ok is True
        assert [(p["page_id"], p["section_name"]) for p in data["pages"]] == [("pg-1", "Q1"), ("pg-2", "Q2")]

    async def test_a_section_whose_pages_could_not_be_read_is_not_shown_as_empty(self, sp, stub) -> None:
        stub.on("GET", f"{NOTEBOOKS}/nb-1/sections", page([section("s-1", "Q1"), section("s-2", "Q2")]))
        stub.on("GET", f"{SITE_PATH}/onenote/sections/s-1/pages", page([onenote_page("pg-1", "Kickoff")]))
        stub.on("GET", f"{SITE_PATH}/onenote/sections/s-2/pages", graph_error(403, "accessDenied", "Access denied"))

        ok, data = result(await sp.list_notebook_pages(site_id=SITE, notebook_id="nb-1"))

        assert ok is True
        assert [s["section_name"] for s in data["unreadable_sections"]] == ["Q2"]
        assert "Q2" in data["note"]
        assert [s["section_id"] for s in data["sections"]] == ["s-1"]

    async def test_page_content_reports_the_pages_it_could_not_read(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/onenote/pages/pg-1", onenote_page("pg-1", "Kickoff"))
        stub.on("GET", f"{SITE_PATH}/onenote/pages/pg-1/content",
                httpx.Response(200, content=b"<html><body><p>Agenda</p></body></html>", headers={"content-type": "text/html"}))
        stub.on("GET", f"{SITE_PATH}/onenote/pages/pg-2", graph_error(404, "itemNotFound", "gone"))

        ok, data = result(await sp.get_notebook_page_content(site_id=SITE, page_ids=["pg-1", "pg-2"]))

        assert ok is True
        assert [p["page_id"] for p in data["pages"]] == ["pg-1"]
        assert "Agenda" in data["pages"][0]["content_text"]
        assert data["failed_page_ids"] == ["pg-2"]

    async def test_page_content_that_failed_for_every_page_is_a_failure(self, sp, stub) -> None:
        stub.on("GET", f"{SITE_PATH}/onenote/pages/pg-1", graph_error(403, "accessDenied", "Access denied"))

        ok, data = result(await sp.get_notebook_page_content(site_id=SITE, page_ids=["pg-1"]))

        assert ok is False
        assert "pg-1" in assert_safe_error(data)

    async def test_page_ids_past_the_limit_are_named_not_dropped(self, sp, stub) -> None:
        ids = [f"pg-{i}" for i in range(22)]
        for pid in ids:
            stub.on("GET", f"{SITE_PATH}/onenote/pages/{pid}", onenote_page(pid, pid))
            stub.on("GET", f"{SITE_PATH}/onenote/pages/{pid}/content", httpx.Response(200, content=b"<p>x</p>"))

        ok, data = result(await sp.get_notebook_page_content(site_id=SITE, page_ids=ids))

        assert ok is True
        assert data["count"] == 20
        assert data["skipped_page_ids"] == ["pg-20", "pg-21"]
        assert "pg-20" in data["note"]


# ---------------------------------------------------------------------------
# Failures: plain language, a next step, nothing secret
# ---------------------------------------------------------------------------


class TestFailures:
    @pytest.mark.parametrize(("response", "expected"), [
        (graph_error(401, "InvalidAuthenticationToken", "Access token has expired or is not yet valid."),
         "Reconnect the SharePoint toolset"),
        (graph_error(403, "accessDenied", "Access denied"), "access"),
        (graph_error(404, "itemNotFound", "The resource could not be found."), "list_files"),
        (graph_error(503, "serviceNotAvailable", "Service unavailable"), "Try again"),
    ])
    @pytest.mark.parametrize("call", ["get_site", "list_files", "create_folder"])
    async def test_failures_are_explained_with_a_next_step(self, sp, stub, call, response, expected) -> None:
        for method, path in (("GET", SITE_PATH), ("GET", f"{V1}/drives/{DRIVE}/root/children"),
                             ("POST", f"{V1}/drives/{DRIVE}/root/children")):
            stub.on(method, path, response)
        args = {"get_site": {"site_id": SITE}, "list_files": {"site_id": SITE, "drive_id": DRIVE},
                "create_folder": {"site_id": SITE, "drive_id": DRIVE, "folder_name": "Q3"}}[call]

        ok, data = result(await getattr(sp, call)(**args))

        assert ok is False
        assert expected in assert_safe_error(data)

    async def test_rate_limit_tells_the_agent_how_long_to_wait(self, sp, stub) -> None:
        stub.on("GET", SITE_PATH, graph_error(429, "TooManyRequests", "Too many requests", {"Retry-After": "12"}))

        ok, data = result(await sp.get_site(site_id=SITE))

        assert ok is False
        assert "Wait 12 seconds" in assert_safe_error(data)
