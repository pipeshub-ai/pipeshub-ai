# pyright: ignore-file

"""BookStack against the connectors-simulator: an instance the test owns, and who may view what.

The simulator answers BookStack's REST API (v26.05) with its API tokens, content permissions, audit log and
recycle bin. The PipesHub container reaches it by its compose service name (``BOOKSTACK_SIM_CONNECTOR_URL``,
``http://bookstack-sim``): a private address, which PipesHub allows for a self-hosted source unless
PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on. This process drives the simulator through its control API on the
published port (``BOOKSTACK_SIM_URL``).

Each test loads an instance, creates a connector for it, syncs, changes the instance and syncs again. After a
sync the simulator's oracle (``/_sim/bookstack/expected``, BookStack's own permission rules, checked against a
real BookStack) says who may view each page. Each of those people is a PipesHub account with the same email,
logged in, and opens every record of the connector (``GET /api/v1/knowledgeBase/record/:id``, the access
check search and chat rely on). The differences:

  LEAK       a person opens a record BookStack does not let them view
  DENIED     a person can view a page in BookStack but PipesHub refuses the record
  MISSING    a page the token's user may view has no live record
  STALE      a record of a page that is gone, or outside the sync's scope, still opens for someone
  DUPLICATE  two live records for one page
  UNSETTLED  opening a record answered neither 200 nor a refusal (a 5xx, an expired login)
  SIM_UNMODELED / SIM_SPEC  the connector sent a request the simulator does not model, or a response broke
             BookStack's docs.json: the comparison would prove nothing
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests
from connectors.bookstack.sim_instances import base_instance
from helper.record_access import record_access_status
from helper.second_user import (
    NO_ACCESS_STATUSES,
    SecondUser,
    _seed_password,
    _wait_for_graph_user,
    log_in,
)
from helper.source_credentials import source_unavailable
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from app.config.constants.arangodb import AppStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("bookstack-sim")

CONNECTOR_TYPE = "BookStack"
SIM_URL_ENV = "BOOKSTACK_SIM_URL"
CONNECTOR_URL = os.getenv("BOOKSTACK_SIM_CONNECTOR_URL", "http://bookstack-sim")
TOKEN_ID, TOKEN_SECRET = "pipeshubintegrationtokenid", "pipeshub-integration-bookstack-secret"
SYNC_TIMEOUT_SEC = int(os.getenv("BOOKSTACK_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("BOOKSTACK_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
OPEN_CONCURRENCY = 8
# The tests are not about BookStack's rate limit unless they say so.
UNLIMITED = {"requests_per_minute": 1000000}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = "GET", **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in BookStack's error shape."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "status", "code": code}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


class BookStackSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def call(self, method: str, path: str, body: object = None) -> Any:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json()

    def load(self, instance: dict[str, Any], behaviours: dict[str, Any] | None = None) -> dict[str, int]:
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/bookstack/behaviours")
        self.call("PUT", "/_sim/bookstack/behaviours", {**UNLIMITED, **(behaviours or {})})
        keys = self.call("PUT", "/_sim/bookstack/scenario", {"base_url": CONNECTOR_URL, **instance})["keys"]
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/bookstack/stats/reset")
        return keys

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/bookstack/expected")


class BookStackWorld:
    """One test's instance, its connector, and the comparison between them."""

    def __init__(self, sim: BookStackSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, SecondUser]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people
        self.connector_id: str | None = None
        self.keys: dict[str, int] = {}
        self.probed: dict[str, SecondUser] = {}
        self.token_user = 1
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    async def start(self, instance: dict[str, Any], *, behaviours: dict[str, Any] | None = None,
                    filters: dict[str, Any] | None = None, enable: bool = True) -> str | None:
        self.keys = self.sim.load(instance, behaviours)
        expected = self.sim.expected()
        token_key = next((t.get("user", "admin") for t in instance.get("tokens", []) if t.get("token_id") == TOKEN_ID),
                         "admin")
        self.token_user = int(self.keys[token_key])
        self.probed = {}
        for user in expected["users"]:
            email = user["email"].lower()
            if user.get("system_name") == "public" or email not in self.people:
                continue
            self.probed[email] = self.people[email]
        config = {"auth": {"base_url": CONNECTOR_URL, "token_id": TOKEN_ID, "token_secret": TOKEN_SECRET}}
        instance_ = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"bookstack-sim-{uuid.uuid4().hex[:8]}",
            scope="team", config=config, auth_type="API_TOKEN",
        )
        assert instance_.connector_id, "Connector must have a valid ID"
        self.connector_id = instance_.connector_id
        if filters:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))
        stats = self.sim.call("GET", "/_sim/bookstack/stats")
        assert stats["total"] > 0, (
            f"the connector never called the simulator at {CONNECTOR_URL} (sync outcome {outcome}, stats {stats})")
        return outcome

    def remove_connector(self) -> None:
        if os.getenv("BOOKSTACK_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.call("DELETE", "/_sim/faults")
        except (requests.RequestException, RuntimeError):
            logger.exception("Could not clear the simulator's faults")
        if not self.connector_id:
            return
        for action in (lambda: self.client.toggle_sync(self.connector_id, enable=False),
                       lambda: self.client.delete_connector(self.connector_id)):
            try:
                action()
            except (PipeshubClientError, requests.RequestException):
                logger.exception("Teardown of connector %s failed", self.connector_id)

    def key(self, key: str) -> int:
        return int(self.keys[key])

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        result = self.sim.call("POST", "/_sim/bookstack/ops", list(ops))
        self.keys = result["keys"]
        return result["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/bookstack/behaviours", values)

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> None:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing."""
        fired = {entry["rule"] for entry in self.sim.call("GET", "/_sim/faults/log")}
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"

    # ---- PipesHub -------------------------------------------------------------------------------------

    def _connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    async def _wait_idle(self) -> None:
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self._connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Callable[[], Any]) -> str | None:
        """Trigger a run and wait for the product to record its end; return the outcome it recorded."""
        await self._wait_idle()
        before = self._connector().get("lastSyncFinishedAtTimestamp") or 0
        trigger()
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while True:
            doc = self._connector()
            if (doc.get("lastSyncFinishedAtTimestamp") or 0) > before and doc.get("status") == AppStatus.IDLE.value:
                logger.info("Sync of %s ended: %s", self.connector_id, doc.get("lastSyncOutcome"))
                return doc.get("lastSyncOutcome")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no sync of connector {self.connector_id} ended within {SYNC_TIMEOUT_SEC}s")
            await asyncio.sleep(SYNC_POLL_SEC)

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self.client.resync_connector(self.connector_id, full_sync=full))

    async def records(self) -> list[dict[str, Any]]:
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await self.graph.execute_query(
                "FOR r IN records FILTER r.connectorId == @c RETURN {id: r._key, externalRecordId: "
                "r.externalRecordId, recordName: r.recordName, isDeleted: r.isDeleted}", bind_vars=params)
        else:
            rows = await self.graph.client.execute_query(
                "MATCH (r:Record {connectorId: $c}) RETURN r.id AS id, r.externalRecordId AS externalRecordId, "
                "r.recordName AS recordName, r.isDeleted AS isDeleted", params)
        return [r for r in rows or [] if not r.get("isDeleted")]

    async def record_groups(self) -> list[dict[str, Any]]:
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await self.graph.execute_query(
                "FOR g IN recordGroups FILTER g.connectorId == @c RETURN {externalGroupId: g.externalGroupId, "
                "name: g.groupName}", bind_vars=params)
        else:
            rows = await self.graph.client.execute_query(
                "MATCH (g:RecordGroup {connectorId: $c}) RETURN g.externalGroupId AS externalGroupId, "
                "g.groupName AS name", params)
        return list(rows or [])

    # ---- the comparison --------------------------------------------------------------------------------

    async def _who_opens(self, records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
        gate = asyncio.Semaphore(OPEN_CONCURRENCY)

        async def one(email: str, user: SecondUser, record_id: str) -> tuple[tuple[str, str], int]:
            async with gate:
                return (email, record_id), await asyncio.to_thread(record_access_status, user, record_id)

        pairs = [(email, user, r["id"]) for r in records for email, user in self.probed.items()]
        return dict(await asyncio.gather(*(one(*pair) for pair in pairs)))

    async def differences(self, *, scope_books_out: set[int] | None = None) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        email_of = {u["id"]: u["email"].lower() for u in expected["users"]}
        pages = {f"page/{i['id']}": i for i in expected["items"] if i["type"] == "page"}

        def in_scope(page: dict[str, Any]) -> bool:
            return (not page["draft"] and self.token_user in page["viewers"]
                    and not (scope_books_out and page["book_id"] in scope_books_out))

        records = await self.records()
        problems: list[dict[str, Any]] = []
        by_external: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_external[record["externalRecordId"]].append(record)
        for ext, page in pages.items():
            if in_scope(page) and ext not in by_external:
                problems.append({"type": "MISSING", "item": page["name"], "id": ext})
        for ext, recs in by_external.items():
            if len(recs) > 1:
                problems.append({"type": "DUPLICATE", "item": recs[0]["recordName"], "id": ext})
        opened = await self._who_opens(records)
        for record in records:
            page = pages.get(record["externalRecordId"])
            viewers = {email_of[v] for v in (page or {}).get("viewers", []) if v in email_of}
            for email in self.probed:
                status = opened[(email, record["id"])]
                if status != 200 and status not in NO_ACCESS_STATUSES:
                    problems.append({"type": "UNSETTLED", "user": email, "item": record["recordName"],
                                     "status": status})
                    continue
                can_open = status == 200
                if page is None or not in_scope(page):
                    if can_open:
                        problems.append({"type": "STALE", "user": email, "item": record["recordName"]})
                elif can_open and email not in viewers:
                    problems.append({"type": "LEAK", "user": email, "item": record["recordName"]})
                elif email in viewers and not can_open:
                    problems.append({"type": "DENIED", "user": email, "item": record["recordName"],
                                     "status": status})
        # Shelves are not synced: only books, chapters and pages.
        problems += [{"type": "SHELF", "item": g["name"], "id": g["externalGroupId"]}
                     for g in await self.record_groups() if str(g["externalGroupId"]).startswith("bookshelf/")]
        problems += [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/bookstack/unmodeled")]
        problems += [{"type": "SIM_SPEC", **e} for e in self.sim.call("GET", "/_sim/bookstack/spec-problems")]
        return problems

    async def assert_matches_source(self, label: str, **scope: Any) -> None:
        """Every probed person opens exactly the records BookStack lets them view, and nothing is missing,
        stale or stored twice."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            problems = await self.differences(**scope)
            if not problems or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        assert not problems, (
            f"{label}: PipesHub differs from BookStack in {len(problems)} way(s) "
            f"({dict(Counter(p['type'] for p in problems))}): {problems[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def bookstack_sim() -> BookStackSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The BookStack simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = BookStackSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


def _account(client: PipeshubClient, email: str, name: str) -> SecondUser:
    """A logged-in PipesHub account with the BookStack user's email: connector permissions are keyed by it."""
    resp = requests.post(f"{client.base_url}/api/v1/users", headers=client._headers(),
                         json={"fullName": name, "email": email}, timeout=client.timeout_seconds)
    created = resp.json() if resp.status_code < 400 else {}
    graph_user = _wait_for_graph_user(client, email)
    user_id = str(created.get("_id") or created.get("id") or graph_user.get("userId") or "")
    graph_id = str(graph_user.get("id") or "")
    if not user_id or not graph_id:
        raise RuntimeError(f"no usable PipesHub account for {email}: created={created} graph={graph_user}")
    _seed_password(client.org_id, user_id, email)
    token = log_in(client.base_url, email, client.timeout_seconds)
    return SecondUser(user_id=user_id, graph_id=graph_id, email=email, token=token,
                      base_url=client.base_url, timeout=client.timeout_seconds)


@pytest.fixture(scope="session")
def bookstack_sim_people(bookstack_sim: BookStackSim, pipeshub_client: PipeshubClient) -> dict[str, SecondUser]:
    """A PipesHub account for every person of the instances but the guest and Erin (who has none, on purpose).
    Kept after the session, as the SharePoint suite does: a deleted email cannot join the org again."""
    pipeshub_client._ensure_access_token()
    bookstack_sim.load(base_instance())
    return {u["email"].lower(): _account(pipeshub_client, u["email"].lower(), f"BookStack sim {u['name']}")
            for u in bookstack_sim.expected()["users"]
            if u.get("system_name") != "public" and u["email"] not in ("erin@example.com", "admin@admin.com")}


@pytest_asyncio.fixture(loop_scope="session")
async def bookstack_world(
    bookstack_sim: BookStackSim,
    bookstack_sim_people: dict[str, SecondUser],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[BookStackWorld, None]:
    world = BookStackWorld(bookstack_sim, pipeshub_client, graph_provider, bookstack_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
