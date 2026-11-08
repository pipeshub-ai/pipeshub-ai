# pyright: ignore-file

"""ServiceNow against the connectors-simulator: an instance each test starts from again, and who may read what.

The simulator answers the Table, Aggregate and Attachment APIs of a Zurich instance over a generated instance:
14 knowledge bases, one per access pattern (default criteria, group, department, roles, location, company, user
list, match-all, scripted, no criteria, Cannot Read, an inactive criterion, an inactive knowledge base, a group
whose members are only in child groups), with versions, wiki bodies, attachments and inline images. Its oracle
(``/_sim/servicenow/expected?readers=true``) says who may read each article: ServiceNow's rules, each verified on a
real instance (connectors-simulator docs/SERVICENOW-SIMULATOR.md §5).

The PipesHub container reaches the simulator by its compose service name (``SERVICENOW_SIM_CONNECTOR_URL``,
``http://servicenow-sim``), over plain http, which the stack allows with SERVICENOW_ALLOW_INSECURE_HTTP. The
connector signs in with basic auth as the integration user, an admin. This process drives the simulator through
its control API on the published port (``SERVICENOW_SIM_URL``).

Each test resets the instance, creates a connector, syncs, changes the instance and syncs again. Then every probed
person (a PipesHub account with a ServiceNow user's email) opens every record of the connector
(``GET /api/v1/knowledgeBase/record/:id``, the access check search and chat rely on). The differences:

  LEAK       a person opens an article (or its file) ServiceNow does not let them read
  DENIED     a person may read an article in ServiceNow but PipesHub refuses the record
  MISSING    a published article (or its file) that search shows has no live record
  STALE      a live record of an article or file that is gone, unpublished or out of the sync's scope
  DUPLICATE  two live records for one article number (or file)
  UNSETTLED  opening a record answered neither 200 nor a refusal (a 5xx, an expired login)
  SIM_GAP    the connector sent a request the simulator does not model: the comparison would prove nothing
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
from helper.record_access import record_access_status
from helper.second_user import NO_ACCESS_STATUSES, SecondUser, source_user_account
from helper.source_credentials import source_unavailable
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from app.config.constants.arangodb import AppStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("servicenow-sim")

CONNECTOR_TYPE = "ServiceNow"
SIM_URL_ENV = "SERVICENOW_SIM_URL"
CONNECTOR_URL = os.getenv("SERVICENOW_SIM_CONNECTOR_URL", "http://servicenow-sim")
# The simulator's integration user (an admin); it accepts any password for a user it has.
SYNC_USER, SYNC_PASSWORD = "pipeshub.sync", "sim"
SYNC_TIMEOUT_SEC = int(os.getenv("SERVICENOW_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("SERVICENOW_SIM_SETTLE_TIMEOUT_SEC", "120"))
SETTLE_POLL_SEC = 10
OPEN_CONCURRENCY = 8


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, effect: dict[str, Any], method: str | None = "GET", **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches get ``effect`` (ServiceNow's error shape)."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": effect}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


class ServiceNowSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def call(self, method: str, path: str, body: object = None) -> Any:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.content else None

    def reset(self) -> None:
        """The instance as built: its records, tokens, knobs, clock and rate counts; no faults, no logged calls."""
        self.call("POST", "/_sim/reset")
        self.call("POST", "/_sim/calls/reset")
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/servicenow/gaps")

    def mutate(self, *ops: dict[str, Any]) -> list[Any]:
        """``{"op", "args"}`` changes, in one transaction (``article_version``, ``kb_criterion``, ``member``, ...)."""
        return self.call("POST", "/_sim/servicenow/mutate", list(ops))

    def advance(self, epochs: int = 1, counts: dict[str, int] | None = None) -> Any:
        """Seeded batches of the changes an instance sees: edits, versions, new, retired and deleted articles,
        files, criteria, memberships and deactivated users."""
        return self.call("POST", "/_sim/servicenow/clock/advance",
                         {"epochs": epochs, **({"counts": counts} if counts else {})})

    def table(self, table: str, query: str = "", fields: str = "") -> list[dict[str, Any]]:
        """Rows of an instance table, read as the integration user through the Table API (never faulted here:
        faults are cleared before a test reads)."""
        params = {"sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": "10000",
                  "sysparm_exclude_reference_link": "true"}
        resp = self.http.get(f"{self.base_url}/api/now/table/{table}", params=params,
                             auth=(SYNC_USER, SYNC_PASSWORD), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["result"]

    def oracle(self) -> dict[str, Any]:
        """What a sync must hold, narrowed to what ServiceNow search shows, as the connector syncs: no article of
        an inactive knowledge base and none past its ``valid_to``; readers are active users only."""
        expected = self.call("GET", "/_sim/servicenow/expected?readers=true")
        today = self.call("GET", "/_sim/servicenow/clock")["now"][:10]
        active_kbs = {k["sys_id"] for k in expected["knowledge_bases"] if k["active"]}
        expected["articles"] = [a for a in expected["articles"]
                                if a["kb"] in active_kbs and (a["valid_to"] or "9999") >= today]
        kept = {a["sys_id"] for a in expected["articles"]}
        expected["attachments"] = [a for a in expected["attachments"] if a["article"] in kept]
        return expected


class ServiceNowWorld:
    """One test's instance, its connector, and the comparison between them."""

    def __init__(self, sim: ServiceNowSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, SecondUser]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people
        self.connector_id: str | None = None
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    async def start(self, *, filters: dict[str, Any] | None = None, enable: bool = True) -> str | None:
        self.sim.reset()
        config: dict[str, Any] = {"auth": {"instanceUrl": CONNECTOR_URL, "username": SYNC_USER,
                                           "password": SYNC_PASSWORD}}
        if filters:
            config["filters"] = filters
        instance = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"servicenow-sim-{uuid.uuid4().hex[:8]}",
            scope="team", config=config, auth_type="BASIC_AUTH",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        self.connector_id = instance.connector_id
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))
        calls = self.sim.call("GET", "/_sim/calls")["total"]
        assert calls > 0, f"the connector never called the simulator at {CONNECTOR_URL} (sync outcome {outcome})"
        return outcome

    def remove_connector(self) -> None:
        if os.getenv("SERVICENOW_SIM_KEEP_CONNECTOR") == "1":
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

    # ---- the source -----------------------------------------------------------------------------------

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> None:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing."""
        fired = self.sim.call("GET", "/_sim/faults/log")
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"

    # ---- PipesHub -------------------------------------------------------------------------------------

    def connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    async def _wait_idle(self) -> None:
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self.connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Callable[[], Any]) -> str | None:
        """Trigger a run and wait for the product to record its end; return the outcome it recorded."""
        await self._wait_idle()
        before = self.connector().get("lastSyncFinishedAtTimestamp") or 0
        trigger()
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while True:
            doc = self.connector()
            if (doc.get("lastSyncFinishedAtTimestamp") or 0) > before and doc.get("status") == AppStatus.IDLE.value:
                logger.info("Sync of %s ended: %s", self.connector_id, doc.get("lastSyncOutcome"))
                return doc.get("lastSyncOutcome")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no sync of connector {self.connector_id} ended within {SYNC_TIMEOUT_SEC}s")
            await asyncio.sleep(SYNC_POLL_SEC)

    async def restart(self, filters: dict[str, Any] | None = None) -> str | None:
        """Turn the connector off and on (with new filters, saved the way the app saves them, on a disabled
        connector); return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        return await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self.client.resync_connector(
            self.connector_id, full_sync=full, connector_name=CONNECTOR_TYPE))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records: articles (keyed by number) and their files (parent number, name)."""
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await self.graph.execute_query(
                "FOR r IN records FILTER r.connectorId == @c RETURN {id: r._key, externalRecordId: "
                "r.externalRecordId, externalParentId: r.externalParentId, recordName: r.recordName, "
                "recordType: r.recordType, isDeleted: r.isDeleted}", bind_vars=params)
        else:
            rows = await self.graph.client.execute_query(
                "MATCH (r:Record {connectorId: $c}) RETURN r.id AS id, r.externalRecordId AS externalRecordId, "
                "r.externalParentId AS externalParentId, r.recordName AS recordName, r.recordType AS recordType, "
                "r.isDeleted AS isDeleted", params)
        return [r for r in rows or [] if not r.get("isDeleted")]

    @staticmethod
    def key_of(record: dict[str, Any]) -> str:
        if record.get("recordType") == "FILE":
            return f"{record.get('externalParentId')}/{record.get('recordName')}"
        return str(record["externalRecordId"])

    # ---- the comparison --------------------------------------------------------------------------------

    async def _who_opens(self, records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
        gate = asyncio.Semaphore(OPEN_CONCURRENCY)

        async def one(email: str, user: SecondUser, record_id: str) -> tuple[tuple[str, str], int]:
            async with gate:
                return (email, record_id), await asyncio.to_thread(record_access_status, user, record_id)

        pairs = [(email, user, r["id"]) for r in records for email, user in self.people.items()]
        return dict(await asyncio.gather(*(one(*pair) for pair in pairs)))

    async def differences(self, *, kbs_in_scope: set[str] | None = None) -> list[dict[str, Any]]:
        expected = self.sim.oracle()
        active = {u["sys_id"]: u["email"].lower() for u in expected["users"] if u["active"] and u["email"]}
        articles = [a for a in expected["articles"] if kbs_in_scope is None or a["kb"] in kbs_in_scope]
        # key -> who may read it: an article by number, a file by "<number>/<file name>" (its article's readers)
        want: dict[str, set[str]] = {a["number"]: {active[u] for u in a["readers"] if u in active} for a in articles}
        number_of = {a["sys_id"]: a["number"] for a in articles}
        files: Counter[str] = Counter()
        for f in expected["attachments"]:
            if f["article"] in number_of:
                key = f"{number_of[f['article']]}/{f['file_name']}"
                files[key] += 1
                want[key] = want[number_of[f["article"]]]

        records = await self.records()
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_key[self.key_of(record)].append(record)
        problems: list[dict[str, Any]] = []
        for key in want:
            if key not in by_key:
                problems.append({"type": "MISSING", "item": key})
        for key, recs in by_key.items():
            if len(recs) > max(files.get(key, 0), 1):
                problems.append({"type": "DUPLICATE", "item": key, "records": len(recs)})
        opened = await self._who_opens(records)
        for record in records:
            key = self.key_of(record)
            readers = want.get(key)
            for email in self.people:
                status = opened[(email, record["id"])]
                if status != 200 and status not in NO_ACCESS_STATUSES:
                    problems.append({"type": "UNSETTLED", "user": email, "item": key, "status": status})
                    continue
                can_open = status == 200
                if readers is None:
                    if can_open:
                        problems.append({"type": "STALE", "user": email, "item": key})
                elif can_open and email not in readers:
                    problems.append({"type": "LEAK", "user": email, "item": key})
                elif email in readers and not can_open:
                    problems.append({"type": "DENIED", "user": email, "item": key, "status": status})
        problems += [{"type": "SIM_GAP", **g} for g in self.sim.call("GET", "/_sim/servicenow/gaps") or []]
        logger.info("Compared %d live records with %d expected (articles and files) as %d people: %d opened, "
                    "%d problems", len(records), len(want), len(self.people),
                    sum(s == 200 for s in opened.values()), len(problems))
        return problems

    async def assert_matches_source(self, label: str, **scope: Any) -> None:
        """Every probed person opens exactly the records ServiceNow lets them read, and nothing is missing,
        stale or stored twice."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            problems = await self.differences(**scope)
            if not problems or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        assert not problems, (
            f"{label}: PipesHub differs from ServiceNow in {len(problems)} way(s) "
            f"({dict(Counter(p['type'] for p in problems))}): {problems[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


def probe_set(expected: dict[str, Any], per_side: int = 3) -> list[str]:
    """Emails of a few active users such that every article has ``per_side`` probed readers and as many probed
    non-readers, where it has them: each access pattern is checked both ways without opening every record as every
    user (16 of the generated instance's 54 active users)."""
    active = {u["sys_id"]: u["email"].lower() for u in expected["users"] if u["active"] and u["email"]}
    everyone = set(active.values())
    chosen: set[str] = set()
    for article in sorted(expected["articles"], key=lambda a: a["number"]):
        readers = {active[u] for u in article["readers"] if u in active}
        for side in (readers, everyone - readers):
            for email in sorted(side - chosen)[:max(0, per_side - len(side & chosen))]:
                chosen.add(email)
    return sorted(chosen)


@pytest.fixture(scope="session")
def servicenow_sim() -> ServiceNowSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The ServiceNow simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = ServiceNowSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


@pytest.fixture(scope="session")
def servicenow_sim_people(servicenow_sim: ServiceNowSim, pipeshub_client: PipeshubClient) -> dict[str, SecondUser]:
    """A PipesHub account for each probed ServiceNow user, made before any connector so the first user sync
    links them. Kept after the session: a deleted email cannot join the org again."""
    pipeshub_client._ensure_access_token()
    servicenow_sim.reset()
    return {email: source_user_account(pipeshub_client, email, f"ServiceNow sim {email.split('@')[0]}")
            for email in probe_set(servicenow_sim.oracle())}


@pytest_asyncio.fixture(loop_scope="session")
async def servicenow_world(
    servicenow_sim: ServiceNowSim,
    servicenow_sim_people: dict[str, SecondUser],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ServiceNowWorld, None]:
    world = ServiceNowWorld(servicenow_sim, pipeshub_client, graph_provider, servicenow_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
