# pyright: ignore-file

"""Jira Data Center against the connectors-simulator: a world the test changes, and who may read what.

The simulator (``connsim serve jiradc``) answers Jira Data Center's REST API v2 for a small generated site (20 people,
12 projects, about 60 issues with attachments, every permission pattern, issue security schemes). The PipesHub container
reaches it by its network alias (``JIRA_DC_SIM_CONNECTOR_URL``, ``http://jira-dc-sim.internal``): a private address,
which PipesHub allows for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on. This process drives the
simulator through its control API on the published port (``JIRA_DC_SIM_URL``).

Each test resets the site, creates a connector, syncs, changes the site (``POST /_sim/jiradc/mutate`` for a named
change, ``POST /_sim/jiradc/clock/advance`` for seeded ones) and syncs again. After a sync the simulator's oracle says
which tickets and attachments a complete sync holds (``/_sim/jiradc/expected?ids=true``) and which issues Jira lets each
person read (``/_sim/jiradc/access?email=..&ids=true``, computed from the rows and never through the routes). Every
person of the site has a PipesHub account with the same e-mail, and PipesHub's own permission query (the one search
uses, ``filter_accessible_record_ids``) is asked, for each of them, which records of the connector they may read.
The differences:

  MISSING        a ticket or attachment in the sync's scope has no live record
  STALE          a live record of something gone, or outside the sync's scope
  DUPLICATE      two live records for one thing
  PARENT         a record not under the project Jira files it in
  LEAK:<why>     a person may read a record Jira does not let them read
  DENIED:<why>   a person may read it in Jira but PipesHub refuses them
  SIM_UNMODELED  the connector sent a request the simulator does not model: the comparison would prove nothing

``<why>`` is the Jira rule that explains it. A difference whose type is in ``KNOWN_GAPS`` is logged and tolerated (each
names the open pull request that fixes it); any other fails the test. So does an injected fault that never fired.

A person reads a ticket in PipesHub when Jira shows it to them AND they are a user the connector links: active, with
Jira product access (a deactivated or unlicensed account is not a source user of the connector). An attachment is read
by those who read its ticket.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests
from helper.graph_provider_utils import async_poll_until
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.source_credentials import source_unavailable
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from app.config.constants.arangodb import AppStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("jira-dc-sim")

CONNECTOR_TYPE = "Jira Data Center"
PERSONAL_TYPE = "Jira Data Center Personal"
SIM_URL_ENV = "JIRA_DC_SIM_URL"
CONNECTOR_URL = os.getenv("JIRA_DC_SIM_CONNECTOR_URL", "http://jira-dc-sim.internal")
SYNC_TIMEOUT_SEC = int(os.getenv("JIRA_DC_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 1
SETTLE_TIMEOUT_SEC = int(os.getenv("JIRA_DC_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"

SYNC_ACCOUNT = "svc-pipeshub-sync"
# The simulator's strict password for basic sign-in (jira-dc-sim compose overlay: CONNSIM_JIRADC_PASSWORD).
PASSWORD = "jira-dc-sim-password"
# The PipesHub test user creates every connector; the connector maps the source account it signs in as to the creator
# (``register_authenticated_source_user``), so this person reads what the sync account reads.
CREATOR_EMAIL = os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com")
# A PipesHub user who has no Jira account: reads nothing of any connector.
OUTSIDER_EMAIL = "outsider-no-jira-account@example.com"

# Differences from Jira's rules the connector shows today, each fixed by an open pull request: type -> the number. They are
# tolerated by ``assert_matches_source`` and nowhere else; ``jira_dc_sim_gaps_test.py`` holds one strict expected failure per
# type, so the day the pull request lands the test passes unexpectedly, fails, and the entry goes.
#   DENIED:user-holder-by-name  a person a permission scheme names with a ``user`` Browse holder does not read the project:
#                               Data Center answers the holder's username (the simulator's default, as a live 10.3 does),
#                               the connector reads the parameter only as a user key (#6114)
#   DENIED:leveled-issue        a person a security level admits does not read the leveled issue: no route lists a level's
#                               holders on Data Center, so the connector closes the issue to everyone (#6114 reads who
#                               browses it per issue)
KNOWN_GAPS: dict[str, int] = {"DENIED:user-holder-by-name": 6114, "DENIED:leveled-issue": 6114}
# Switches every test starts from: none, the simulator's own defaults. What differs from them is passed by the test that
# needs it (``start(switches=...)``).
BASE_SWITCHES: dict[str, Any] = {}

FILE_PREFIX = "attachment_"


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Jira's error shape. ``trigger`` takes
    the core's fields (``after_n``, ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    return _rule({"type": "status", "code": code}, path_regex, method, trigger)


def drop(path_regex: str, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """The connection is dropped without an answer."""
    return _rule({"type": "reset"}, path_regex, method, trigger)


def malformed(path_regex: str, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """The answer's body is cut short (not JSON any more)."""
    return _rule({"type": "truncate", "bytes": 12}, path_regex, method, trigger)


def throttle(path_regex: str, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """429 with Retry-After from a token bucket: one request every two seconds."""
    return _rule({"type": "rate_limit", "capacity": 1, "refill_per_s": 0.5}, path_regex, method, trigger)


def page_token(path_regex: str, mode: str, **trigger: float) -> dict[str, Any]:
    """Token paging goes wrong (``short``, ``drop``, ``expire``, ``repeat``): see the simulator's core faults."""
    return _rule({"type": "page_token", "mode": mode}, path_regex, None, trigger)


def _rule(effect: dict[str, Any], path_regex: str, method: str | None, trigger: dict[str, float]) -> dict[str, Any]:
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": effect}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def project_filter(operator: str, keys: Iterable[str]) -> dict[str, Any]:
    """The connector's project-key filter, saved as the app saves it."""
    return {"sync": {"values": {"project_keys": {"operator": operator, "type": "list", "value": list(keys)}}}}


def date_filter(name: str, operator: str, start: int | None = None, end: int | None = None) -> dict[str, Any]:
    """A created or modified date filter (``name`` is "created" or "modified"), saved as the app saves it; times are
    epoch milliseconds."""
    return {"sync": {"values": {name: {"operator": operator, "type": "datetime", "value": {"start": start, "end": end}}}}}


def merge_filters(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"sync": {"values": {}}}
    for part in parts:
        out["sync"]["values"].update(part["sync"]["values"])
    return out


def _date_skip(expected: dict[str, Any], filters: dict[str, Any] | None) -> set[str]:
    """Tickets the date filters leave out: a created or modified time outside [start, end]."""
    values = (((filters or {}).get("sync") or {}).get("values") or {})
    skip: set[str] = set()
    for name, field_ in (("created", "created_ms"), ("modified", "updated_ms")):
        spec = values.get(name)
        if not spec:
            continue
        start, end = (spec.get("value") or {}).get("start"), (spec.get("value") or {}).get("end")
        for tid, t in expected["ticket_index"].items():
            at = t[field_]
            if (start is not None and at < start) or (end is not None and at > end):
                skip.add(tid)
    return skip


def _project_scope(filters: dict[str, Any] | None) -> tuple[set[str] | None, set[str] | None]:
    """(projects the filter keeps, projects it excludes) from a saved filter; (None, None) when it has none."""
    spec = (((filters or {}).get("sync") or {}).get("values") or {}).get("project_keys") or {}
    if not spec.get("value"):
        return None, None
    keys = {str(v) for v in spec["value"]}
    return (keys, None) if spec.get("operator", "in") == "in" else (None, keys)


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Want:
    kind: str                    # ticket | file
    group: str                   # the record group it must sit under: the project id
    readers: set[str]            # e-mails of the people who may read it
    ticket: str = ""             # the ticket's id (a file's own ticket)
    key: str = ""
    leveled: bool = False        # the ticket is at an issue security level


@dataclass
class Scope:
    projects_in: set[str] | None = None
    projects_out: set[str] | None = None
    # Tickets the sync leaves out for another reason (a date filter): ids.
    skip: set[str] = field(default_factory=set)

    def allows(self, project_key: str) -> bool:
        if self.projects_in is not None and project_key not in self.projects_in:
            return False
        return not (self.projects_out is not None and project_key in self.projects_out)


def _project_key(ticket_key: str) -> str:
    return ticket_key.rsplit("-", 1)[0]


def reads(access: dict[str, Any], ticket_id: str) -> bool:
    """Whether this person is a source user the connector links and Jira shows them the ticket."""
    return bool(access.get("active")) and bool(access.get("licensed")) and \
        ticket_id in {str(i) for i in access.get("jira_ids", ())}


def wanted_records(expected: dict[str, Any], access: dict[str, dict[str, Any]], scope: Scope) -> dict[str, Want]:
    """External record id -> what a complete and correct sync holds for it, and who reads it."""
    wanted: dict[str, Want] = {}
    for tid, t in expected["ticket_index"].items():
        if not scope.allows(_project_key(t["key"])) or tid in scope.skip:
            continue
        readers = {email for email, acc in access.items() if reads(acc, tid)}
        group = str(t["project_id"])
        leveled = t.get("level") is not None
        wanted[tid] = Want("ticket", group, readers, ticket=tid, key=t["key"], leveled=leveled)
        for fid in t["files"]:
            wanted[f"{FILE_PREFIX}{fid}"] = Want("file", group, readers, ticket=tid, key=t["key"], leveled=leveled)
    return wanted


def _reason(want: Want, acc: dict[str, Any] | None, finds: bool, email: str = "",
            user_holders: dict[str, list[str]] | None = None) -> str:
    """The Jira rule that explains a difference between PipesHub and Jira for one person and one record."""
    side = "LEAK" if finds else "DENIED"
    if acc is None:
        return f"{side}:not-a-jira-user"
    if finds:
        if not acc.get("active"):
            return "LEAK:inactive-user"
        if not acc.get("licensed"):
            return "LEAK:no-product-access"
        return "LEAK:other"
    if want.leveled:
        return "DENIED:leveled-issue"
    if email.lower() in {e.lower() for e in (user_holders or {}).get(want.group, [])}:
        return "DENIED:user-holder-by-name"
    return "DENIED:other"


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]], readable: dict[str, set[str]],
            access: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed e-mail may
    read) and Jira's rules (``expected``, and ``access`` for each probed e-mail)."""
    wanted = wanted_records(expected, access, scope)
    by_ext: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_ext[str(record["externalRecordId"])].append(record)
    problems: list[dict[str, Any]] = []
    for ext, want in wanted.items():
        found = by_ext.get(ext, [])
        if not found:
            problems.append({"type": "MISSING", "kind": want.kind, "record": ext, "key": want.key})
            continue
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(found)})
        if str(found[0].get("externalGroupId")) != want.group:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": want.group})
        ids = {r["id"] for r in found}
        for email, seen in readable.items():
            may = email in want.readers
            finds = bool(ids & seen)
            if finds != may:
                problems.append({"type": _reason(want, access.get(email), finds, email, expected.get("browse_user_holders")),
                                 "record": ext, "key": want.key,
                                 "user": email})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName")} for r in found]
    return problems


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class JiraDcSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def call(self, method: str, path: str, body: object = None, params: dict[str, Any] | None = None) -> Any:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, params=params, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.content else None

    def reset(self) -> None:
        """The generated site as it was built, no faults, no switches, empty call log and unmodelled list."""
        self.call("DELETE", "/_sim/faults")
        self.call("POST", "/_sim/reset")
        self.call("DELETE", "/_sim/jiradc/switches")
        self.switches(**BASE_SWITCHES)
        self.call("DELETE", "/_sim/jiradc/unmodeled")
        self.call("POST", "/_sim/calls/reset")

    def mutate(self, *ops: dict[str, Any]) -> list[dict[str, Any]]:
        return self.call("POST", "/_sim/jiradc/mutate", list(ops))

    def advance(self, **counts: int) -> dict[str, Any]:
        return self.call("POST", "/_sim/jiradc/clock/advance", {"counts": counts})

    def switches(self, **values: Any) -> dict[str, Any]:
        return self.call("POST", "/_sim/jiradc/switches", values)

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/jiradc/expected", params={"ids": "true"})

    def users(self) -> list[dict[str, Any]]:
        return self.call("GET", "/_sim/jiradc/users")

    def access(self, email: str) -> dict[str, Any]:
        return self.call("GET", "/_sim/jiradc/access", params={"email": email, "ids": "true"})

    def vendor_get(self, path: str, user: str = SYNC_ACCOUNT, **params: Any) -> requests.Response:
        """A request to Jira's own API as ``user`` (a personal access token), for what a stream must equal."""
        resp = self.http.get(f"{self.base_url}{path}", params=params, headers={"Authorization": f"Bearer pat-{user}"},
                             timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def paths_called(self) -> dict[str, int]:
        """The vendor endpoints the connector requested since the call log was reset, as ``METHOD template`` -> count."""
        return {k: v["count"] for k, v in self.call("GET", "/_sim/calls")["endpoints"].items()}


class JiraDcWorld:
    """One test's site, its connector, and the comparison between them."""

    def __init__(self, sim: JiraDcSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str], users_client: Any = None) -> None:
        self.users_client = users_client
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # e-mail -> PipesHub user id: every probed person
        self.connector_id: str | None = None
        self.scope = Scope()
        self.filters: dict[str, Any] | None = None
        self.switches_on: dict[str, Any] = {}
        self.connector_type = CONNECTOR_TYPE
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    def credentials(self, auth: str = "token", user: str = SYNC_ACCOUNT, secret: str | None = None) -> dict[str, Any]:
        if auth == "token":
            return {"baseUrl": CONNECTOR_URL, "apiToken": secret if secret is not None else f"pat-{user}"}
        return {"baseUrl": CONNECTOR_URL, "username": user, "password": secret if secret is not None else PASSWORD}

    async def start(self, *, auth: str = "token", filters: dict[str, Any] | None = None, enable: bool = True,
                    prepare: list[dict[str, Any]] | None = None, switches: dict[str, Any] | None = None,
                    base_url: str | None = None, user: str = SYNC_ACCOUNT, secret: str | None = None,
                    reset: bool = True, personal: bool = False) -> str | None:
        """Reset the site, apply ``prepare`` (mutations) and ``switches``, create a connector that signs in with
        ``auth`` ("token" or "basic"), and (unless ``enable`` is False) turn it on; return the outcome of the run that
        starts."""
        if reset:
            self.sim.reset()
        if switches:
            self.sim.switches(**switches)
        if prepare:
            self.sim.mutate(*prepare)
        self.switches_on = dict(switches or {})
        self.scope = Scope()
        self.scope.projects_in, self.scope.projects_out = _project_scope(filters)
        self.filters = filters
        self.connector_type = PERSONAL_TYPE if personal else CONNECTOR_TYPE
        config = self.credentials(auth, user, secret)
        if base_url:
            config["baseUrl"] = base_url
        created = self.client.create_connector(
            connector_type=PERSONAL_TYPE if personal else CONNECTOR_TYPE,
            instance_name=f"jira-dc-sim-{uuid.uuid4().hex[:8]}", scope="personal" if personal else "team",
            config={"auth": config}, auth_type="API_TOKEN" if auth == "token" else "BASIC_AUTH",
        )
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        if filters:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        """Turn the connector on; the outcome of the run that starts, or REFUSED when PipesHub would not turn it on."""
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True), refusable=True)
        return outcome

    async def restart(self, filters: dict[str, Any] | None = None) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled
        connector); return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
            self.scope.projects_in, self.scope.projects_out = _project_scope(filters)
            self.filters = filters
        return await self.enable()

    def disable(self) -> None:
        self.client.toggle_sync(self.connector_id, enable=False)

    def remove_connector(self) -> None:
        if os.getenv("JIRA_DC_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.call("DELETE", "/_sim/faults")
            self.sim.call("DELETE", "/_sim/jiradc/switches")
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

    def mutate(self, *ops: dict[str, Any]) -> list[dict[str, Any]]:
        """Named changes to the site (``/_sim/jiradc/mutate``), applied together."""
        return self.sim.mutate(*ops)

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> int:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing."""
        fired = self.sim.call("GET", "/_sim/faults/log") or []
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"
        return len(fired)

    async def ensure_account(self, email: str) -> None:
        """A PipesHub account for a person the test added to Jira; probed from now on."""
        assert self.users_client is not None, "this world was made without a users client"
        self.people[email] = await _active_user(self.users_client, self.graph, email)

    def issue_keys(self, project: str) -> list[str]:
        """The keys of the issues of ``project`` the sync account sees, in id order."""
        index = self.sim.expected()["ticket_index"]
        return [t["key"] for _id, t in sorted(index.items(), key=lambda kv: int(kv[0]))
                if _project_key(t["key"]) == project]

    def usernames(self, *, active: bool = True) -> list[str]:
        """Usernames of the people other than the sync account, in the site's order."""
        return [u["username"] for u in self.sim.users() if u["username"] != SYNC_ACCOUNT and bool(u["active"]) == active]

    # ---- PipesHub -------------------------------------------------------------------------------------

    def _connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    async def _wait_idle(self) -> None:
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self._connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Callable[[], Any], *, refusable: bool = False) -> str | None:
        """Trigger a run and wait for the product to record its end; return the outcome it recorded."""
        await self._wait_idle()
        before = self._connector().get("lastSyncFinishedAtTimestamp") or 0
        try:
            trigger()
        except PipeshubClientError as exc:
            if not refusable:
                raise
            logger.info("PipesHub refused to turn connector %s on: %s", self.connector_id, exc)
            return REFUSED
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
        return await self._run(lambda: self.client.resync_connector(
            self.connector_id, full_sync=full, connector_name=self.connector_type))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records: not deleted and still filed under a record group. A full sync unlinks what it
        no longer sees (every edge goes, the node stays); that record is gone for every reader."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "", scoped=True)
        # A placeholder is the stub of an ancestor outside the sync's scope (its name, so a child shows where it sits):
        # not a synced issue, so it is not compared.
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "version": r.get("version")}
                for r in rows or [] if not r.get("isDeleted") and not r.get("isPlaceholder")]

    async def creator_records(self) -> list[dict[str, Any]]:
        """The live records the PipesHub test user may read: the ones a test can stream or reindex as that user."""
        records = await self.records()
        seen = await self.readable(records)
        mine = seen.get(CREATOR_EMAIL, set())
        return [r for r in records if r["id"] in mine]

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records() if r["externalRecordId"] == external_id), None)

    async def readable(self, records: list[dict[str, Any]]) -> dict[str, set[str]]:
        """For each probed e-mail, the ids of ``records`` PipesHub's permission query lets them read."""
        ids = [r["id"] for r in records if r["id"]]
        if not ids:
            return {email: set() for email in self.people}
        emails = list(self.people)
        seen = await asyncio.gather(*(self.graph.filter_accessible_record_ids(ids, self.people[e], self.client.org_id)
                                      for e in emails))
        return {email: set(found) for email, found in zip(emails, seen, strict=True)}

    async def holdings(self) -> dict[str, set[str]]:
        """What PipesHub holds and grants: the live external ids, and for each e-mail the external ids they read."""
        records = await self.records()
        ext = {r["id"]: str(r["externalRecordId"]) for r in records}
        readable = await self.readable(records)
        return {"records": set(ext.values()), **{email: {ext[i] for i in seen} for email, seen in readable.items()}}

    async def assert_holdings_kept(self, before: dict[str, set[str]], label: str, *, allow_gains: bool = False) -> None:
        """Nothing was removed and nobody lost access: what a sync that could not read must leave behind. Unless
        ``allow_gains`` (the run read part of a change), nothing was added and nobody gained access either."""
        after = await self.holdings()
        lost = {who: sorted(before[who] - after.get(who, set())) for who in before}
        gained = {} if allow_gains else {who: sorted(after.get(who, set()) - before[who]) for who in before}
        lost = {who: v for who, v in lost.items() if v}
        gained = {who: v for who, v in gained.items() if v}
        assert not lost and not gained, f"{label}: PipesHub changed what it holds; lost {lost}, gained {gained}"

    # ---- the comparison --------------------------------------------------------------------------------

    def probes(self) -> dict[str, dict[str, Any]]:
        """Jira's answer for each probed e-mail (the creator is the sync account: the connector maps it to them)."""
        users = {u["email"].lower(): u for u in self.sim.users()}
        sa_email = next(u["email"] for u in users.values() if u["username"] == SYNC_ACCOUNT)
        out: dict[str, dict[str, Any]] = {}
        for email in self.people:
            asked = sa_email if email.lower() == CREATOR_EMAIL.lower() else email
            out[email] = self.sim.access(asked) if asked.lower() in users else {"active": False, "licensed": False,
                                                                                 "jira_ids": []}
        return out

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        # The creator is the sync account to the connector: where a scheme names the account, it names the creator.
        sa_email = next((u["email"] for u in self.sim.users() if u["username"] == SYNC_ACCOUNT), None)
        expected["browse_user_holders"] = {
            pid: [*emails, CREATOR_EMAIL] if sa_email in emails else emails
            for pid, emails in (expected.get("browse_user_holders") or {}).items()}
        self.scope.skip = _date_skip(expected, self.filters)
        records = await self.records()
        readable = await self.readable(records)
        problems = compare(expected, self.scope, records, readable, self.probes())
        problems += [{"type": "SIM_UNMODELED", **e}
                     for e in (self.sim.call("GET", "/_sim/jiradc/unmodeled") or {}).get("unmodeled", [])]
        return problems

    def known_gaps(self) -> frozenset[str]:
        """The tolerated differences for the switches this connector runs with: a gap that needs the simulator to answer a
        way (the level-members route absent, a user holder named by username) is not tolerated where it answers the other."""
        gaps = set(KNOWN_GAPS)
        if self.switches_on.get("level_members"):
            gaps.discard("DENIED:leveled-issue")
        if self.switches_on.get("holder_user_parameter") == "key":
            gaps.discard("DENIED:user-holder-by-name")
        return frozenset(gaps)

    async def assert_matches_source(self, label: str, *, known_gaps: frozenset[str] | None = None,
                                    settle: int | None = None) -> None:
        """Every probed person reads exactly the records Jira lets them read, but for the known gaps (``known_gaps()``;
        none when passed empty), and nothing is missing, stale, stored twice or filed under the wrong project."""
        known_gaps = self.known_gaps() if known_gaps is None else known_gaps
        deadline = time.monotonic() + (SETTLE_TIMEOUT_SEC if settle is None else settle)
        while True:
            problems = await self.differences()
            failing = [p for p in problems if p["type"] not in known_gaps]
            if not failing or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        tolerated = Counter(p["type"] for p in problems if p["type"] in known_gaps)
        if tolerated:
            logger.info("%s: known gaps tolerated: %s", label, dict(tolerated))
        by_project = Counter((p["type"], str(p.get("key", "")).rsplit("-", 1)[0]) for p in failing)
        assert not failing, (
            f"{label}: PipesHub differs from Jira in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}; by project {dict(by_project)}): {failing[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def jira_dc_sim() -> JiraDcSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Jira Data Center simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = JiraDcSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the Jira user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def jira_dc_sim_people(
    jira_dc_sim: JiraDcSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (e-mail -> user id) for every person of the site, made before any connector so its first
    sync maps them, and the PipesHub test user (no Jira account of its own: the connector maps it to the sync
    account). Kept after the session, as the other simulator suites do: a deleted e-mail cannot join the org again."""
    pipeshub_client._ensure_access_token()
    jira_dc_sim.reset()
    emails = [u["email"] for u in jira_dc_sim.users()]
    return {email: await _active_user(users_client, graph_provider, email) for email in [*emails, CREATOR_EMAIL, OUTSIDER_EMAIL]}


@pytest_asyncio.fixture(loop_scope="session")
async def jira_dc_world(
    jira_dc_sim: JiraDcSim,
    jira_dc_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    users_client: Any,
) -> AsyncGenerator[JiraDcWorld, None]:
    world = JiraDcWorld(jira_dc_sim, pipeshub_client, graph_provider, dict(jira_dc_sim_people), users_client)
    try:
        yield world
    finally:
        world.remove_connector()
