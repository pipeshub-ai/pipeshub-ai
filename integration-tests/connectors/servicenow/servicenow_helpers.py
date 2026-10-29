"""What the ServiceNow test modules share: connectors, syncs, and the checks that read PipesHub as a person."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.servicenow.constants import (
    ENV_INSTANCE_URL,
    GRAPH_CLEANUP_TIMEOUT,
    KB_FILTER,
    SERVICENOW_CONNECTOR_TYPE,
    SYNC_TIMEOUT,
)
from connectors.servicenow.servicenow_seed import ServiceNowRest, token_from_env
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.oauth_token_helper import inject_connector_credentials
from helper.record_access import record_access_status
from helper.second_user import SecondUser, source_user_account
from helper.storage_incremental import restart_sync

logger = logging.getLogger("servicenow-it")

ACCESS_TIMEOUT = 240
POLL = 5


@dataclass
class Persona:
    """A test user of the instance, the PipesHub account with its email, and the instance as they see it."""

    name: str
    sys_id: str
    email: str
    rest: ServiceNowRest | None = field(repr=False)  # holds the password
    account: SecondUser | None = field(repr=False)  # holds a session token


def add_account(client: PipeshubClient, accounts: dict[str, SecondUser], key: str, email: str) -> SecondUser:
    """A PipesHub account for a throw-away instance user; ``sn_accounts`` deletes it with the module."""
    accounts[key] = source_user_account(client, email, f"ServiceNow IT {key}")
    return accounts[key]


def kb_filter(kb_ids: Iterable[str], operator: str = "in") -> dict[str, Any]:
    """The ``filters`` payload that scopes a connector to some knowledge bases (empty without the filter)."""
    if not KB_FILTER:
        return {}
    return {"sync": {"values": {"knowledge_bases": {"operator": operator, "type": "list", "value": list(kb_ids)}}}}


def merge_filters(*parts: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        for category, block in part.items():
            out.setdefault(category, {"values": {}})["values"].update(block.get("values", {}))
    return out


async def inject_fresh_token(connector_id: str, *, access_token: str | None = None,
                             refresh_token: str | None = None) -> None:
    """Store an access and a refresh token for the admin account, as the OAuth sign-in would.

    Both are stored: an access token lives 30 minutes, and the connector refreshes it with the
    refresh token after a 401 (through the linked OAuth app). The platform's timed refresh does
    not run for these, since the stored token has no expiry time.
    """
    access, refresh = token_from_env()
    await inject_connector_credentials(connector_id, access_token or access, refresh_token or refresh)


@asynccontextmanager
async def servicenow_connector(
    client: PipeshubClient,
    graph: GraphProviderProtocol,
    oauth_config_id: str,
    *,
    name: str,
    filters: Mapping[str, Any] | None = None,
    min_records: int = 1,
    enable: bool = True,
    credentials: tuple[str | None, str | None] = (None, None),
) -> AsyncIterator[str]:
    """A team ServiceNow connector signed in through the suite's OAuth app; deleted (graph checked) on exit."""
    config: dict[str, Any] = {"auth": {"oauthConfigId": oauth_config_id}}
    if filters:
        config["filters"] = dict(filters)
    instance = client.create_connector(
        connector_type=SERVICENOW_CONNECTOR_TYPE,
        instance_name=f"{name}-{uuid.uuid4().hex[:6]}",
        scope="team",
        auth_type="OAUTH",
        config=config,
    )
    connector_id = instance.connector_id
    assert connector_id, "PipesHub returned no connector id"
    try:
        await inject_fresh_token(connector_id, access_token=credentials[0], refresh_token=credentials[1])
        if enable:
            client.toggle_sync(connector_id, enable=True)
            await wait_for_sync_completion(client, graph, connector_id, timeout=SYNC_TIMEOUT,
                                           min_records=min_records)
        yield connector_id
    finally:
        await delete_connector(client, graph, connector_id)


async def delete_connector(client: PipeshubClient, graph: GraphProviderProtocol, connector_id: str) -> None:
    try:
        client.toggle_sync(connector_id, enable=False)
    except Exception:  # noqa: BLE001 - a deleted or broken connector still has to be deleted
        logger.warning("Could not disable ServiceNow test connector %s", connector_id)
    client.delete_connector(connector_id)
    await graph.assert_all_records_cleaned(connector_id, timeout=GRAPH_CLEANUP_TIMEOUT)


async def sync(client: PipeshubClient, graph: GraphProviderProtocol, connector_id: str, *, full: bool = False) -> int:
    """Run one sync (incremental, or full) and wait until the graph stops changing."""
    if full:
        client.resync_connector(connector_id, full_sync=True, connector_name=SERVICENOW_CONNECTOR_TYPE)
    else:
        restart_sync(client, connector_id)
    return await wait_for_sync_completion(client, graph, connector_id, timeout=SYNC_TIMEOUT)


async def poll(check: Callable[[], Awaitable[Any]], what: str, timeout: float = ACCESS_TIMEOUT) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = await check()
        if last:
            return last
        await asyncio.sleep(POLL)
    raise AssertionError(f"{what} (waited {timeout:.0f}s; last: {last!r})")


async def record(graph: GraphProviderProtocol, connector_id: str, external_id: str) -> Any:
    return await graph.get_record_by_external_id(connector_id, external_id)


async def wait_present(graph: GraphProviderProtocol, connector_id: str, external_id: str,
                       check: Callable[[Any], bool] = lambda r: True, what: str = "", timeout: float = 120) -> Any:
    async def _check() -> Any:
        r = await record(graph, connector_id, external_id)
        return r if r is not None and check(r) else None
    return await poll(_check, what or f"{external_id} should be in the graph", timeout)


async def wait_absent(graph: GraphProviderProtocol, connector_id: str, external_id: str, what: str = "",
                      timeout: float = 120) -> None:
    async def _check() -> bool:
        return await record(graph, connector_id, external_id) is None
    await poll(_check, what or f"{external_id} should be gone from the graph", timeout)


def can_open(user: SecondUser, record_id: str) -> bool | None:
    """True or False when the product answered 200 or a denial; None for anything else."""
    status = record_access_status(user, record_id)
    if status == 200:
        return True
    if status in (403, 404):
        return False
    return None


async def pipeshub_visible(graph: GraphProviderProtocol, connector_id: str, user: SecondUser,
                           numbers: Iterable[str]) -> set[str]:
    """Which of these article numbers ``user`` can open in PipesHub (an unsynced one counts as not)."""
    seen: set[str] = set()
    for number in numbers:
        rec = await record(graph, connector_id, number)
        if rec is not None and await asyncio.to_thread(can_open, user, rec.id):
            seen.add(number)
    return seen


async def assert_same_access(graph: GraphProviderProtocol, connector_id: str, expected: Mapping[str, set[str]],
                             accounts: Mapping[str, SecondUser], scope: Iterable[str], *, label: str,
                             timeout: float = ACCESS_TIMEOUT) -> None:
    """Every person opens exactly the articles of ``scope`` they are expected to, polled while
    permission edges land. ``expected`` maps a person to the numbers ServiceNow lets them read."""
    scope = sorted(set(scope))
    last: dict[str, tuple[set[str], set[str]]] = {}

    async def _check() -> bool:
        last.clear()
        for name, user in accounts.items():
            got = await pipeshub_visible(graph, connector_id, user, scope)
            want = expected.get(name, set()) & set(scope)
            if got != want:
                last[name] = (want - got, got - want)
        return not last

    try:
        await poll(_check, "", timeout)
    except AssertionError:
        lines = [f"  {who}: missing {sorted(miss)}, extra {sorted(extra)}" for who, (miss, extra) in sorted(last.items())]
        raise AssertionError(f"{label}: PipesHub access differs from ServiceNow's\n" + "\n".join(lines)) from None


def kh_record_ids(client: PipeshubClient, user: SecondUser, connector_id: str) -> set[str]:
    """Every record ``user`` sees in the knowledge hub under this connector (the whole tree)."""
    base = "/api/v1/knowledge-hub/nodes" if client._use_connector_direct_api() else "/api/v1/knowledgeBase/knowledge-hub/nodes"
    found: set[str] = set()
    todo = [("app", connector_id)]
    seen: set[tuple[str, str]] = set()
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        page = 1
        while True:
            resp = user.get(f"{base}/{node[0]}/{node[1]}?" + urlencode({"page": page, "limit": 200, "onlyContainers": "false"}))
            if resp.status_code in (403, 404):
                break
            assert resp.status_code == 200, f"knowledge hub {node}: HTTP {resp.status_code} {resp.text[:200]}"
            body = resp.json()
            for item in body.get("items") or []:
                kind = item.get("nodeType")
                if kind == "record":
                    found.add(str(item["id"]))
                if item.get("hasChildren") or kind in ("recordGroup", "folder"):
                    todo.append((kind, str(item["id"])))
            pagination = body.get("pagination") or {}
            if not pagination.get("hasNext"):
                break
            page += 1
    return found


def stream_text(client: PipeshubClient, user: SecondUser, record_id: str) -> str:
    """The record's streamed content as ``user`` sees it, as text (a blocks stream is JSON)."""
    resp = user.get(client._stream_record_path(record_id))
    assert resp.status_code == 200, f"stream of {record_id} as {user.email}: HTTP {resp.status_code}"
    text = resp.content.decode("utf-8", errors="replace")
    try:
        return json.dumps(json.loads(text), ensure_ascii=False)
    except ValueError:
        return text


def instance_host() -> str:
    return os.environ[ENV_INSTANCE_URL].strip().rstrip("/")


def connector_title(client: PipeshubClient, connector_id: str) -> str:
    """The name notices start with: the connector instance's name."""
    c = client.get_connector(connector_id)
    return str(c.get("name") or c.get("instanceName") or "")


def notices(client: PipeshubClient, prefix: str, user: SecondUser | None = None) -> list[dict[str, Any]]:
    """In-app notices whose title starts with ``prefix``, in the admin's inbox or ``user``'s."""
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 50, **({"cursor": cursor} if cursor else {})}
        if user is None:
            resp = client.request("GET", "/api/v1/notifications", params=params)
        else:
            resp = user.get("/api/v1/notifications?" + urlencode(params))
        assert resp.status_code == 200, f"GET /api/v1/notifications: HTTP {resp.status_code} {resp.text[:200]}"
        body = resp.json()
        out += [n for n in body.get("notifications", []) if str(n.get("title", "")).startswith(prefix)]
        cursor = body.get("cursor")
        if not body.get("hasMore") or not cursor:
            return out
    return out


async def files_of(graph: GraphProviderProtocol, connector_id: str, number: str) -> dict[str, tuple[str, str]]:
    """File records under article ``number``: file name -> (external id, record id).

    Files are keyed by article number, file name and content hash, not by the attachment's
    sys_id (a new version copies its files with new sys_ids), so tests find them by name.
    """
    rows = await graph.fetch_records_by_type(connector_id, "FILE")
    return {
        str(r.get("recordName")): (str(r.get("externalRecordId")), str(r.get("_key") or r.get("id")))
        for r in rows if r.get("externalParentId") == number
    }


async def wait_file(graph: GraphProviderProtocol, connector_id: str, number: str, name: str,
                    timeout: float = 300) -> tuple[str, str]:
    """(external id, record id) of the file ``name`` under article ``number``, once it is synced."""
    async def _check() -> tuple[str, str] | None:
        return (await files_of(graph, connector_id, number)).get(name)
    return await poll(_check, f"{name} should be a file record under {number}", timeout)
