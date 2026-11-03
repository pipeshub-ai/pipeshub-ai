"""Fakes for behaviour tests of the SharePoint agent tools.

Reuses the Microsoft 365 connector tests' ``MicrosoftCloudStub``: the tool runs
through the real ``SharePointDataSource``, a real ``GraphServiceClient`` built
by ``MSGraphClientWithDelegatedAuth`` from the user's access token, and kiota's
real request adapter and retry middleware. Only httpx's transport is faked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.microsoft.sharepoint.sharepoint import SharePoint
from app.sources.client.microsoft.microsoft import (
    MSGraphClient,
    MSGraphClientWithDelegatedAuth,
)
from tests.unit.connectors.sources.microsoft.behaviour.ms_graph_fakes import (
    MicrosoftCloudStub,
    route_microsoft_http,
)

if TYPE_CHECKING:
    import pytest

USER_TOKEN = "eyJ0eXAi.fake-delegated-graph-token-must-never-leak.sig"
SITE = "contoso.sharepoint.com,site-guid,web-guid"
DRIVE = "b!drive-1"
V1 = "/v1.0"


def build_sharepoint_tool(monkeypatch: pytest.MonkeyPatch, stub: MicrosoftCloudStub, state: object = None) -> SharePoint:
    """The tool as the agent factory builds it for a signed-in user, on a Graph client whose HTTP is ``stub``."""
    route_microsoft_http(monkeypatch, stub)
    monkeypatch.setattr(
        "kiota_http.middleware.retry_handler.RetryHandler.get_delay_time", lambda *_args, **_kwargs: 0,
    )
    graph = MSGraphClientWithDelegatedAuth(USER_TOKEN, "tenant-1", logging.getLogger("sharepoint-tool-tests"))
    return SharePoint(MSGraphClient(graph), state=state)


def notebook(notebook_id: str, name: str) -> dict[str, Any]:
    return {"id": notebook_id, "displayName": name,
            "links": {"oneNoteWebUrl": {"href": f"https://contoso.sharepoint.com/{notebook_id}"}}}


def section(section_id: str, name: str) -> dict[str, Any]:
    return {"id": section_id, "displayName": name}


def onenote_page(page_id: str, title: str, order: int = 0) -> dict[str, Any]:
    return {"id": page_id, "title": title, "order": order}


def drive_item(item_id: str, name: str, *, folder: bool = False, size: int = 10, **extra: object) -> dict[str, Any]:
    facet: dict[str, Any] = {"folder": {"childCount": 0}} if folder else {"file": {"mimeType": "text/plain"}}
    return {"id": item_id, "name": name, "size": size, "webUrl": f"https://contoso.sharepoint.com/{name}",
            "parentReference": {"driveId": DRIVE, "id": "root-id"}, **facet, **extra}
