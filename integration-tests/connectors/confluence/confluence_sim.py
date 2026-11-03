# pyright: ignore-file

"""Helpers for the Confluence suites that drive the connectors simulator.

The simulator (pipeshub-ai/connectors-simulator) serves Confluence Cloud's REST API plus its
own control API under ``/_sim``: fault injection, the call log and the oracle. These suites
change the simulated site through its vendor API (spaces, pages, restrictions, groups,
comments, attachments), switch faults on around one sync, and read what PipesHub made of
it: the graph, the vectors, and the notifications the sync sent.

Everything here talks to the simulator through the published port the test process uses
(``CONFLUENCE_TEST_BASE_URL`` without its ``/wiki/api/v2`` path).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

import requests

from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion
from helper.local_auth import log_in
from helper.source_credentials import source_unavailable

logger = logging.getLogger("confluence-sim")

SYNC_TIMEOUT_SEC = int(os.getenv("CONFLUENCE_SIM_SYNC_TIMEOUT_SEC", "420"))
# A notice is published at the end of the sync and lands in the inbox a moment later.
NOTICE_WAIT_SEC = 60
NO_NOTICE_GRACE_SEC = 8


def sim_origin() -> str:
    parts = urlsplit(os.getenv("CONFLUENCE_TEST_BASE_URL") or "")
    return f"{parts.scheme}://{parts.netloc}"


def _auth() -> tuple[str, str]:
    return os.getenv("CONFLUENCE_TEST_EMAIL") or "", os.getenv("CONFLUENCE_TEST_API_TOKEN") or ""


def require_simulator() -> None:
    """Skip (or fail, on a run meant to cover Confluence) unless the site is the simulator."""
    origin = sim_origin()
    try:
        ok = bool(urlsplit(origin).netloc) and requests.get(f"{origin}/_sim/health", timeout=10).status_code == 200
    except requests.RequestException:
        ok = False
    if not ok:
        source_unavailable(
            "These Confluence tests change the site and inject faults, so they need the "
            "connectors simulator (atlassian-sim profile), not a real Confluence site.",
            secrets=["CONFLUENCE_TEST_BASE_URL"],
        )


def call(method: str, path: str, body: Any = None, **kw: Any) -> requests.Response:
    headers = kw.pop("headers", {})
    return requests.request(
        method, f"{sim_origin()}{path}", json=body, auth=_auth(), headers=headers, timeout=60, **kw,
    )


def ok(method: str, path: str, body: Any = None, **kw: Any) -> Any:
    resp = call(method, path, body, **kw)
    assert resp.status_code < 300, f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:300]}"
    return resp.json() if resp.content else None


def storage(text: str) -> dict[str, str]:
    return {"representation": "storage", "value": f"<p>{text}</p>"}


# ---- faults -----------------------------------------------------------------------------------------------

@contextlib.contextmanager
def faults(*rules: dict[str, Any]) -> Iterator[None]:
    """Switch fault rules on for the block; always cleared after, so no other test inherits them."""
    ok("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
    try:
        yield
    finally:
        ok("DELETE", "/_sim/faults")


def faults_fired() -> list[dict[str, Any]]:
    return ok("GET", "/_sim/faults/log")


# ---- site writes ------------------------------------------------------------------------------------------

def create_space(key: str, name: str) -> dict[str, Any]:
    return ok("POST", "/wiki/api/v2/spaces", {"key": key, "name": name})


def delete_space(key: str) -> None:
    resp = call("DELETE", f"/wiki/rest/api/space/{key}")
    assert resp.status_code in (200, 202, 204, 404), f"delete space {key}: HTTP {resp.status_code}"


def create_page(space_id: str, title: str, text: str, parent_id: str | None = None) -> str:
    body: dict[str, Any] = {"spaceId": space_id, "status": "current", "title": title, "body": storage(text)}
    if parent_id:
        body["parentId"] = parent_id
    return str(ok("POST", "/wiki/api/v2/pages", body)["id"])


def create_folder(space_id: str, title: str, parent_id: str | None = None) -> str:
    body: dict[str, Any] = {"spaceId": space_id, "title": title}
    if parent_id:
        body["parentId"] = parent_id
    return str(ok("POST", "/wiki/api/v2/folders", body)["id"])


def page(page_id: str, status: str = "current") -> dict[str, Any]:
    return ok("GET", f"/wiki/api/v2/pages/{page_id}?status={status}&body-format=storage")


def _put_page(page_id: str, current: dict[str, Any], text: str | None) -> None:
    ok("PUT", f"/wiki/api/v2/pages/{page_id}", {
        "id": page_id, "status": "current", "title": current["title"],
        "body": storage(text) if text is not None else current["body"]["storage"],
        "version": {"number": current["version"]["number"] + 1},
    })


def edit_page(page_id: str, text: str | None = None) -> None:
    """A new version of the page; the same body unless ``text`` is given."""
    _put_page(page_id, page(page_id), text)


def trash_page(page_id: str) -> None:
    ok("DELETE", f"/wiki/api/v2/pages/{page_id}")


def restore_page(page_id: str) -> None:
    """Back from the trash, as Confluence does it: a new version with status current."""
    _put_page(page_id, page(page_id, "trashed"), None)


def page_version(page_id: str) -> int:
    return int(page(page_id)["version"]["number"])


def read_restriction(*, users: list[str] = (), groups: list[str] = ()) -> list[dict[str, Any]]:
    return [{"operation": "read", "restrictions": {
        "user": [{"type": "known", "accountId": a} for a in users],
        "group": [{"type": "group", "id": g} for g in groups]}}]


def restrict(content_id: str, *, users: list[str] = (), groups: list[str] = ()) -> None:
    ok("PUT", f"/wiki/rest/api/content/{content_id}/restriction", read_restriction(users=users, groups=groups))


def unrestrict(content_id: str) -> None:
    resp = call("DELETE", f"/wiki/rest/api/content/{content_id}/restriction")
    assert resp.status_code in (200, 204, 404), f"unrestrict {content_id}: HTTP {resp.status_code}"


def create_group(name: str, member_account_ids: list[str] = ()) -> str:
    group_id = str(ok("POST", "/wiki/rest/api/group", {"name": name})["id"])
    for account_id in member_account_ids:
        ok("POST", f"/wiki/rest/api/group/userByGroupId?groupId={group_id}", {"accountId": account_id})
    return group_id


def delete_group(group_id: str) -> None:
    resp = call("DELETE", f"/wiki/rest/api/group/by-id?id={group_id}")
    assert resp.status_code in (200, 204, 404), f"delete group {group_id}: HTTP {resp.status_code}"


def upload(page_id: str, name: str, data: bytes, media_type: str, *, new_version: bool = False) -> str:
    """Attach a file (``new_version``: a new version of the file with that name); returns the attachment id."""
    body = ok("PUT" if new_version else "POST", f"/wiki/rest/api/content/{page_id}/child/attachment",
              files=[("file", (name, data, media_type))], data={"minorEdit": "true"},
              headers={"X-Atlassian-Token": "no-check"})
    return str(body["results"][0]["id"])


def attachments(page_id: str) -> list[dict[str, Any]]:
    return ok("GET", f"/wiki/rest/api/content/{page_id}/child/attachment?limit=200")["results"]


def delete_attachment(att_id: str) -> None:
    resp = call("DELETE", f"/wiki/api/v2/attachments/{att_id}")
    assert resp.status_code in (200, 204, 404), f"delete attachment {att_id}: HTTP {resp.status_code}"


def footer_comment(text: str, *, page_id: str | None = None, parent_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"body": storage(text)}
    body.update({"pageId": page_id} if page_id else {"parentCommentId": parent_id})
    return ok("POST", "/wiki/api/v2/footer-comments", body)


def inline_comment(text: str, *, page_id: str | None = None, selection: str | None = None,
                   parent_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"body": storage(text)}
    if parent_id:
        body["parentCommentId"] = parent_id
    else:
        body.update(pageId=page_id, inlineCommentProperties={
            "textSelection": selection, "textSelectionMatchCount": 1, "textSelectionMatchIndex": 0})
    return ok("POST", "/wiki/api/v2/inline-comments", body)


def edit_footer_comment(comment: dict[str, Any], text: str) -> None:
    ok("PUT", f"/wiki/api/v2/footer-comments/{comment['id']}",
       {"version": {"number": int(comment["version"]["number"]) + 1}, "body": storage(text)})


def move_page(page_id: str, target_id: str) -> None:
    """The v1 move: puts the page under ``target_id`` (any space) without a new page version."""
    ok("PUT", f"/wiki/rest/api/content/{page_id}/move/append/{target_id}")


def current_account_id() -> str:
    return str(ok("GET", "/wiki/rest/api/user/current")["accountId"])


def other_active_account(exclude: set[str]) -> dict[str, Any]:
    return next(p for p in ok("GET", "/_sim/users") if p["active"] and p["account_id"] not in exclude)


# ---- PipesHub side ----------------------------------------------------------------------------------------

def attachment_external_ids(att_id: str) -> list[str]:
    """Confluence names an attachment ``att123``; accept the record under either spelling."""
    return [att_id, att_id[3:]] if att_id.startswith("att") else [att_id, f"att{att_id}"]


async def find_record(graph_provider: Any, connector_id: str, external_ids: list[str]) -> Any:
    for external_id in external_ids:
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        if record is not None:
            return record
    return None


async def run_sync(pipeshub_client: Any, graph_provider: Any, connector_id: str, *, full: bool = False,
                   timeout: int = SYNC_TIMEOUT_SEC) -> None:
    """One sync (incremental, or full) of an active connector, waited out."""
    if full:
        pipeshub_client.resync_connector(connector_id, full_sync=True)
    else:
        pipeshub_client.toggle_sync(connector_id, enable=True)
    await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=timeout)


async def poll(check: Any, description: str, timeout: float = 120, interval: float = 3) -> Any:
    async def once() -> Any:
        return await check()
    return await async_poll_until(once, timeout=timeout, interval=interval, description=description)


class Inbox:
    """A user's notifications from one connector, read through the product's notification API.

    The connector titles every notice "<connector name>: ...", so the name picks this
    connector's notices out of the inbox.
    """

    def __init__(self, base_url: str, connector_name: str, *, token: str | None = None,
                 email: str | None = None, password: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.prefix = f"{connector_name}:"
        self.token = token or log_in(self.base_url, email or "", password or "")
        self.seen: set[str] = set()

    def all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            params: dict[str, Any] = {"limit": 50}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(f"{self.base_url}/api/v1/notifications", params=params,
                                headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
            assert resp.status_code == 200, f"GET /api/v1/notifications: HTTP {resp.status_code} {resp.text[:200]}"
            body = resp.json()
            out += [n for n in body.get("notifications", []) if str(n.get("title", "")).startswith(self.prefix)]
            cursor = body.get("cursor")
            if not body.get("hasMore") or not cursor:
                break
        return out

    def mark_seen(self) -> None:
        self.seen |= {str(n["_id"]) for n in self.all()}

    def _new(self) -> list[dict[str, Any]]:
        return [n for n in self.all() if str(n["_id"]) not in self.seen]

    async def arrived(self, count: int) -> list[dict[str, Any]]:
        """The notices that arrived since the last call, waiting for ``count`` of them; marks them seen."""
        if count:
            deadline = time.time() + NOTICE_WAIT_SEC
            while len(self._new()) < count and time.time() < deadline:
                await asyncio.sleep(3)
        await asyncio.sleep(NO_NOTICE_GRACE_SEC)
        new = self._new()
        self.seen |= {str(n["_id"]) for n in new}
        return new

    @staticmethod
    def mismatch(new: list[dict[str, Any]], count: int, context: str) -> str | None:
        if len(new) == count:
            return None
        return (f"{context}: expected {count} notification(s) from the connector, got {len(new)}: "
                + "; ".join(f"{n.get('title')!r}: {str(n.get('message'))[:400]!r}" for n in new))

    async def expect(self, count: int, context: str) -> list[dict[str, Any]]:
        """Exactly ``count`` notices arrived since the last call; returns them."""
        new = await self.arrived(count)
        problem = self.mismatch(new, count, context)
        assert problem is None, problem
        return new
