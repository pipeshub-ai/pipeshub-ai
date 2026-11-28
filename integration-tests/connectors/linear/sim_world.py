# pyright: ignore-file

"""Linear against the connectors-simulator: a workspace the test owns, and who may read what.

The simulator answers Linear's GraphQL API, its OAuth sign-in and its file host for one workspace (``acme``), validated
against Linear's published schema, with Linear's rules for who may read an issue or a project: private, restricted and
public teams, sub-teams, guests, suspended people. The PipesHub container reaches it as ``https://api.linear.app``,
``https://linear.app`` and ``https://uploads.linear.app``, the names the connector calls, through the overlay's TLS front
(``docker-compose.integration.linear-sim.yml``). This process drives the simulator's control API on the published port
(``LINEAR_SIM_URL``).

Each test loads a workspace, creates a connector for it, signs it in, syncs, changes the workspace and syncs again. After
a sync the simulator's oracle (``/_sim/linear/expected``) says who may read every issue, project, link, document and file.
Every person of the workspace has a PipesHub account with the same email, and PipesHub's own permission query (the one
search uses, ``filter_accessible_record_ids``) is asked, for each of them, which records of the connector they may read.
The differences:

  MISSING        something the sync's account sees and the sync holds has no live record
  STALE          a person reads a record of something gone, trashed or outside the sync's scope
  DUPLICATE      two live records for one thing
  TEAM           the stored teams differ from the teams the account sees (and the filter keeps)
  LEAK           a person may read a record Linear does not let them read
  DENIED         a person may read it in Linear but PipesHub refuses them
  SIM_UNMODELED / SIM_SPEC / SIM_WRONG_HOST   the connector sent a request the simulator does not model, a request broke
                 Linear's published schema, or a request named a Linear host the simulator does not serve under that
                 name: the comparison would prove nothing

A difference whose type is in ``KNOWN_GAPS`` is logged and tolerated; any other fails the test. So does an injected fault
that never fired.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
import requests
from connectors.linear.sim_instances import (
    API_KEY,
    CLIENT,
    OUTSIDER,
    PEOPLE,
    workspace,
)
from helper.graph_provider_utils import async_poll_until
from helper.oauth_token_helper import inject_connector_credentials
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.source_credentials import source_unavailable
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.constants import ConnectorStateKeys

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("linear-sim")

CONNECTOR_TYPE = "Linear"
SIM_URL_ENV = "LINEAR_SIM_URL"
APP_CONTAINER_ENV = "LINEAR_SIM_APP_CONTAINER"
SYNC_TIMEOUT_SEC = int(os.getenv("LINEAR_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("LINEAR_SIM_SETTLE_TIMEOUT_SEC", "60"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
FILE_PARENT_MARK = "#parent="
LINEAR_HOSTS = ("api.linear.app", "linear.app", "uploads.linear.app")

# The differences from Linear's rules the connector still shows, each strict: a test fails when the difference is gone, so
# the entry is removed with the fix. Maps a difference type to the open pull request that fixes it (or the reason).
KNOWN_GAPS: dict[str, str] = {}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def op_fault(operation: str, status: int, **trigger: int) -> dict[str, Any]:
    """A simulator fault for the GraphQL operation(s) whose name matches the regular expression ``operation``: the answer
    has ``status`` and Linear's error code for it. ``trigger`` takes ``first_n``, ``after_n``, ``max_times``."""
    return {"operation": operation, "status": status, **trigger}


def team_filter(operator: str, team_ids: list[str]) -> dict[str, Any]:
    """The connector's team filter, saved as the app saves it."""
    return {"sync": {"values": {"team_ids": {"operator": operator, "type": "list", "value": list(team_ids)}}}}


def date_filter(name: str, start_ms: int | None, end_ms: int | None) -> dict[str, Any]:
    """A created / modified date filter (``name``), saved as the app saves it."""
    operator = "is_after" if end_ms is None else "is_before" if start_ms is None else "is_between"
    return {name: {"operator": operator, "type": "datetime", "value": {"start": start_ms, "end": end_ms}}}


def file_record_id(url: str, parent_external_id: str) -> str:
    return f"{url}{FILE_PARENT_MARK}{parent_external_id}"


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Scope:
    """What the sync is told to read: a team filter (operator, team keys) and a created-after cut."""

    teams: tuple[str, list[str]] | None = None
    created_after: int | None = None
    notes: list[str] = field(default_factory=list)


def synced_team_keys(expected: dict[str, Any], scope: Scope) -> set[str]:
    keys = {t["key"] for t in expected["teams"] if t["visible_to_viewer"] and not t["archived"]}
    if scope.teams:
        operator, chosen = scope.teams
        keys = keys - set(chosen) if operator == "not_in" else keys & set(chosen)
    return keys


def project_readers(expected: dict[str, Any], project: dict[str, Any], synced: set[str]) -> set[str] | None:
    """Who Linear shows a project to, among the synced teams: the union of those teams' readers
    (https://linear.app/docs/private-teams). None when no team of the project is synced."""
    teams = {t["key"]: t for t in expected["teams"]}
    mine = [teams[k] for k in project["teams"] if k in synced]
    return {e.lower() for t in mine for e in t["readers"]} if mine else None


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, dict[str, Any]]:
    """External record id -> what the source says about it: whether it is in scope, whether a sync must hold it, who may
    read it."""
    synced = synced_team_keys(expected, scope)
    issues = {i["key"]: i for i in expected["issues"]}
    projects = {p["key"]: p for p in expected["projects"]}
    out: dict[str, dict[str, Any]] = {}

    def lower(emails: list[str]) -> set[str]:
        return {e.lower() for e in emails}

    def in_time(created: int) -> bool:
        return not scope.created_after or created >= scope.created_after

    # An archived issue (not trashed) is Linear's still, and shown to the same readers; the first sync does not list it
    # (archived issues need includeArchived) and a later sync does not remove it: its record is tolerated, not required;
    # so are the records hanging on it. An archived project is required: the connector lists projects with includeArchived.
    live_issue = {k: i for k, i in issues.items() if i["visible_to_viewer"] and not i["trashed"]
                  and i["team"] in synced and in_time(i["created"])}
    for key, issue in issues.items():
        out[issue["id"]] = {"kind": "TICKET", "name": issue["identifier"], "in_scope": key in live_issue,
                            "readers": lower(issue["readers"]), "required": not issue["archived"]}
    for kind in ("attachments", "documents"):
        for row in expected[kind]:
            if row["owner_kind"] != "issue":
                continue
            owner = issues[row["owner"]]
            out[row["id"]] = {"kind": "LINK" if kind == "attachments" else "WEBPAGE", "name": row["key"],
                              "in_scope": row["owner"] in live_issue and not row["trashed"],
                              "readers": lower(row["readers"]), "required": not owner["archived"], "owner": owner["id"]}
    for key, project in projects.items():
        readers = project_readers(expected, project, synced)
        live = project["visible_to_viewer"] and not project["trashed"] and readers is not None
        readers = readers or set()
        out[project["id"]] = {"kind": "PROJECT", "name": project["name"], "in_scope": live, "readers": readers,
                              "required": True}
        for row in expected["documents"]:
            if row["owner_kind"] == "project" and row["owner"] == key:
                out[row["id"]] = {"kind": "WEBPAGE", "name": row["key"], "in_scope": live and not row["trashed"],
                                  "readers": readers, "required": True, "owner": project["id"]}
        for row in expected["links"]:
            if row["owner"] == key:
                out[row["id"]] = {"kind": "LINK", "name": row["key"], "in_scope": live, "readers": readers,
                                  "required": True, "owner": project["id"]}
    for upload in expected["uploads"]:
        for ref in upload["linked_from"]:
            if ref["image"]:
                continue  # images are inlined into the indexed content, not records
            owner = issues[ref["key"]] if ref["kind"] == "issue" else projects[ref["key"]]
            if ref["kind"] == "issue":
                in_scope, readers = ref["key"] in live_issue, lower(owner["readers"])
            else:
                in_scope, readers = out[owner["id"]]["in_scope"], out[owner["id"]]["readers"]
            out[file_record_id(upload["url"], owner["id"])] = {
                "kind": "FILE", "name": upload["key"], "in_scope": in_scope, "readers": readers,
                "required": ref["where"] != "document" and (ref["kind"] != "issue" or not owner["archived"]),
                "owner": owner["id"]}
    return out


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]], groups: set[str],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, the team groups it stores, and the record
    ids each probed email may read) and Linear's rules (``expected``, the oracle)."""
    wanted = wanted_records(expected, scope)
    problems: list[dict[str, Any]] = []
    by_ext: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_ext.setdefault(str(record["externalRecordId"]), []).append(record)
    for ext, item in wanted.items():
        if item["in_scope"] and item["required"] and ext not in by_ext:
            problems.append({"type": "MISSING", "kind": item["kind"], "item": item["name"], "record": ext})
    for ext, found in by_ext.items():
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "item": found[0].get("recordName"), "record": ext})
    keep = synced_team_keys(expected, scope)
    want_teams = {t["id"] for t in expected["teams"] if t["key"] in keep}
    # A team a sync filter leaves out keeps its (unreadable) group until a full sync of a different scope; a team Linear no
    # longer has or shows must go.
    left_out = {t["id"] for t in expected["teams"] if t["visible_to_viewer"] and not t["archived"]} - want_teams
    if want_teams - groups or groups - want_teams - left_out:
        problems.append({"type": "TEAM", "missing": sorted(want_teams - groups),
                         "extra": sorted(groups - want_teams - left_out)})
    for email, seen in readable.items():
        for record in records:
            item = wanted.get(str(record["externalRecordId"]))
            reachable = record["id"] in seen
            if item is None or not item["in_scope"]:
                if reachable:
                    problems.append({"type": "STALE", "user": email, "item": record.get("recordName"),
                                     "record": record["externalRecordId"],
                                     "detail": "gone from the source" if item is None else "trashed, archived or out of scope"})
                continue
            may = email.lower() in item["readers"]
            if reachable and not may:
                problems.append({"type": "LEAK", "user": email, "item": item["name"], "kind": item["kind"]})
            elif may and not reachable:
                problems.append({"type": "DENIED", "user": email, "item": item["name"], "kind": item["kind"]})
    return problems


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class LinearSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def call(self, method: str, path: str, body: object = None) -> Any:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.content else None

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.clear_faults()
        self.call("DELETE", "/_sim/linear/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/linear/behaviours", behaviours)
        self.call("PUT", "/_sim/linear/scenario", scenario)
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/linear/stats/reset")

    def clear_faults(self) -> None:
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/linear/op-faults")

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/linear/expected")

    def stats(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/linear/stats")

    def operations(self) -> dict[str, int]:
        """GraphQL operation name -> how many times the connector sent it, as the simulator names them."""
        return self.stats()["operations"]


class LinearWorld:
    """One test's workspace, its connector, and the comparison between them."""

    def __init__(self, sim: LinearSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.scenario: dict[str, Any] = {}
        self.scope = Scope()
        self.auth = "token"
        self.key = API_KEY
        self._faulted = False
        self._expect_fired = False

    # ---- set-up ---------------------------------------------------------------------------------------

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.scenario = scenario
        self.sim.load(scenario, behaviours)

    def create_connector(self, *, auth: str = "token", key: str | None = None, filters: dict[str, Any] | None = None,
                         client_id: str | None = None, client_secret: str | None = None) -> str:
        """A connector signed in with an API key (``auth="token"``) or set up for OAuth (the credentials come from
        ``sign_in`` or ``authorization_code``)."""
        self.auth = auth
        self.key = key or API_KEY
        config: dict[str, Any] = {}
        if auth == "token":
            config["auth"] = {"authType": "API_TOKEN", "apiToken": self.key}
            auth_type = "API_TOKEN"
        else:
            oauth = self.scenario.get("auth", {}).get("oauth") or CLIENT
            config["auth"] = {"authType": "OAUTH", "clientId": client_id or oauth["client_id"],
                              "clientSecret": client_secret or oauth["client_secret"]}
            auth_type = "OAUTH"
        if filters:
            config["filters"] = filters
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"linear-sim-{uuid.uuid4().hex[:8]}", scope="team",
            config=config, auth_type=auth_type)
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        return self.connector_id

    def token_pair(self) -> tuple[str, str]:
        """An access and a refresh token the simulator issues for the scenario's OAuth client (the authorization-code
        exchange, run by the test process through the front's host names on the published port)."""
        oauth = self.scenario["auth"]["oauth"]
        landed = requests.get(f"{self.sim.base_url}/oauth/authorize", allow_redirects=False, timeout=30,
                              headers={"Host": "linear.app"},
                              params={"client_id": oauth["client_id"], "redirect_uri": oauth["redirect_uris"][0],
                                      "response_type": "code", "scope": "read", "state": "s"})
        assert landed.status_code in (302, 303), landed.text[:200]
        code = parse_qs(urlparse(landed.headers["Location"]).query)["code"][0]
        resp = requests.post(f"{self.sim.base_url}/oauth/token", timeout=30, headers={"Host": "api.linear.app"}, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": oauth["redirect_uris"][0],
            "client_id": oauth["client_id"], "client_secret": oauth["client_secret"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body["access_token"], body["refresh_token"]

    async def sign_in(self, access_token: str | None = None, refresh_token: str | None = None) -> tuple[str, str]:
        """Give the connector a token pair stored as the sign-in would (the Salesforce and Zendesk suites do the same)."""
        if access_token is None:
            access_token, refresh_token = self.token_pair()
        await inject_connector_credentials(self.connector_id, access_token, refresh_token)
        return access_token, refresh_token

    async def start(self, scenario: dict[str, Any], *, behaviours: dict[str, Any] | None = None,
                    filters: dict[str, Any] | None = None, scope: Scope | None = None, auth: str = "token",
                    enable: bool = True) -> str | None:
        """Load ``scenario``, create a connector signed in with the viewer's API key (or OAuth), and (unless ``enable``
        is False) turn it on; return the outcome of the run that starts."""
        self.load(scenario, behaviours)
        self.scope = scope or Scope()
        self.create_connector(auth=auth, filters=filters)
        if auth == "oauth":
            await self.sign_in()
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        """Turn the connector on; the outcome of the run that starts, or REFUSED when PipesHub would not turn it on."""
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True), refusable=True)
        stats = self.sim.stats()
        assert stats["total_operations"] > 0 or outcome == REFUSED or stats["tokens_refused"] or self._faulted \
            or stats["calls"], f"the connector never called the simulator (outcome {outcome}, stats {stats})"
        return outcome

    async def restart(self, filters: dict[str, Any] | None = None, scope: Scope | None = None) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled connector);
        return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if scope is not None:
            self.scope = scope
        return await self.enable()

    def remove_connector(self) -> None:
        if os.getenv("LINEAR_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.clear_faults()
            self.sim.call("DELETE", "/_sim/linear/behaviours")
        except (requests.RequestException, RuntimeError):
            logger.exception("Could not clear the simulator's faults and behaviours")
        if not self.connector_id:
            return
        for action in (lambda: self.client.toggle_sync(self.connector_id, enable=False),
                       lambda: self.client.delete_connector(self.connector_id)):
            try:
                action()
            except (PipeshubClientError, requests.RequestException):
                logger.exception("Teardown of connector %s failed", self.connector_id)

    def ident(self, kind: str, name: str) -> str:
        """The id the simulator gave an entity (``kind``: issues, projects, teams, ...; ``name``: its key)."""
        return next(r["id"] for r in self.sim.expected()[kind] if r["key"] == name)

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the workspace, in order (``/_sim/linear/ops``)."""
        return self.sim.call("POST", "/_sim/linear/ops", list(ops))["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/linear/behaviours", values)

    def defer(self, operation: str, ops: list[dict[str, Any]], *, after: int = 1, contains: str | None = None) -> None:
        """Apply ``ops`` just before the ``after``-th request of the GraphQL operation ``operation``: a change landing
        between two pages of a listing."""
        self.sim.call("POST", "/_sim/linear/deferred",
                      {"operation": operation, "after": after, "contains": contains, "ops": ops})

    def deferred_applied(self) -> list[dict[str, Any]]:
        return self.sim.call("GET", "/_sim/linear/deferred")["applied"]

    def faults(self, *rules: dict[str, Any], core: tuple[dict[str, Any], ...] = ()) -> None:
        """Faults for named GraphQL operations (``op_fault``) and, as ``core``, the simulator's own rules (by path)."""
        self.sim.clear_faults()
        if rules:
            self.sim.call("PUT", "/_sim/linear/op-faults", list(rules))
        if core:
            self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(core)})
        self._faulted = True
        self._expect_fired = bool(rules or core)

    def fired(self) -> int:
        log = self.sim.call("GET", "/_sim/linear/op-faults")["log"]
        return len(log) + len(self.sim.call("GET", "/_sim/faults/log") or [])

    def clear_faults(self) -> int:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached proves
        nothing. Returns how many fired."""
        fired = self.fired()
        expect, self._expect_fired = self._expect_fired, False
        self.sim.clear_faults()
        assert not expect or fired, "no injected fault fired, so the test proves nothing"
        return fired

    # ---- PipesHub -------------------------------------------------------------------------------------

    def connector(self) -> dict[str, Any]:
        """The connector's document (its status, last outcome, whether another full sync is owed)."""
        return self.client.get_connector(self.connector_id)

    async def pending_full_sync(self) -> bool:
        """Whether the platform owes this connector another full sync (the last one left part of the source unread)."""
        app = await self.graph.get_document(self.connector_id, CollectionNames.APPS.value)
        assert app is not None, f"the app node of connector {self.connector_id} is missing"
        return bool(app.get(ConnectorStateKeys.PENDING_FULL_SYNC))

    async def _wait_idle(self) -> None:
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self.connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Callable[[], Any], *, refusable: bool = False) -> str | None:
        """Trigger a run and wait for the product to record its end; return the outcome it recorded."""
        await self._wait_idle()
        before = self.connector().get("lastSyncFinishedAtTimestamp") or 0
        try:
            trigger()
        except PipeshubClientError as exc:
            if not refusable:
                raise
            logger.info("PipesHub refused to turn connector %s on: %s", self.connector_id, exc)
            return REFUSED
        return await self._await_end(before)

    async def _await_end(self, before: int) -> str | None:
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while True:
            doc = self.connector()
            if (doc.get("lastSyncFinishedAtTimestamp") or 0) > before and doc.get("status") == AppStatus.IDLE.value:
                logger.info("Sync of %s ended: %s", self.connector_id, doc.get("lastSyncOutcome"))
                return doc.get("lastSyncOutcome")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no sync of connector {self.connector_id} ended within {SYNC_TIMEOUT_SEC}s")
            await asyncio.sleep(SYNC_POLL_SEC)

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self.client.resync_connector(
            self.connector_id, full_sync=full, connector_name=CONNECTOR_TYPE))

    async def records(self, *, placeholders: bool = False) -> list[dict[str, Any]]:
        """The connector's live records (placeholders only when asked)."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "recordType": r.get("recordType"), "version": r.get("version"), "webUrl": r.get("webUrl"),
                 "indexingStatus": r.get("indexingStatus")}
                for r in rows or [] if not r.get("isDeleted") and (placeholders or not r.get("isPlaceholder"))]

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records() if r["externalRecordId"] == external_id), None)

    async def team_groups(self, expected: dict[str, Any]) -> set[str]:
        """The ids of the workspace's teams PipesHub stores as record groups of this connector."""
        found = set()
        for team in expected["teams"]:
            group = await self.graph.get_record_group_by_external_id(self.connector_id, team["id"])
            if group is not None and not getattr(group, "is_deleted_at_source", False):
                found.add(team["id"])
        return found

    async def readable(self, records: list[dict[str, Any]]) -> dict[str, set[str]]:
        """For each probed email, the ids of ``records`` PipesHub's permission query lets them read."""
        ids = [r["id"] for r in records if r["id"]]
        out: dict[str, set[str]] = {}
        for email, user_id in self.people.items():
            out[email] = set(await self.graph.filter_accessible_record_ids(ids, user_id, self.client.org_id)) if ids \
                else set()
        return out

    async def holdings(self) -> dict[str, set[str]]:
        """What PipesHub holds and grants: the live external ids, and for each email the external ids they read."""
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

    async def assert_nothing_live_lost(self, before: list[dict[str, Any]], label: str) -> None:
        """No record the source still has (in the sync's scope) is gone from PipesHub, and nobody reads anything Linear does
        not show them: what a run that could not read part of the source must leave. Records of what the source no longer
        has may stay (the run could not see them go)."""
        wanted = wanted_records(self.sim.expected(), self.scope)
        now = {r["externalRecordId"] for r in await self.records()}
        lost = sorted(r["recordName"] or r["externalRecordId"] for r in before
                      if r["externalRecordId"] not in now and wanted.get(r["externalRecordId"], {}).get("in_scope"))
        assert not lost, f"{label}: records the source still has were removed: {lost[:10]}"
        over = [p for p in await self.differences() if p["type"] in ("LEAK", "DUPLICATE", "SIM_UNMODELED", "SIM_SPEC")
                or (p["type"] == "TEAM" and p["extra"])]
        assert not over, f"{label}: PipesHub shows more than Linear does: {over[:10]}"

    async def stream(self, external_id: str) -> bytes:
        """What PipesHub streams for a record: the bytes of the HTTP answer; raises on an HTTP error."""
        record = await self.record_for(external_id)
        assert record, f"no live record of {external_id}"
        resp = self.client.stream_record(record["id"], timeout=120)
        return b"".join(resp.iter_content(65536))

    def upload_bytes(self, key: str) -> bytes:
        resp = self.sim.http.get(f"{self.sim.base_url}/_sim/linear/upload/{key}", timeout=60)
        resp.raise_for_status()
        return resp.content

    # ---- the comparison --------------------------------------------------------------------------------

    def source_problems(self) -> list[dict[str, Any]]:
        """What the simulator saw wrong in the connector's requests: nothing modelled is missing, nothing it sent broke
        Linear's published schema, and every request named a Linear host the simulator serves under that name."""
        problems = [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/linear/unmodeled") or []
                    if str(e.get("host", "")).split(":")[0] in (*LINEAR_HOSTS, "")]
        problems += [{"type": "SIM_SPEC", **e} for e in self.sim.call("GET", "/_sim/linear/spec-problems") or []]
        return problems

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        groups = await self.team_groups(expected)
        return compare(expected, self.scope, records, groups, readable) + self.source_problems()

    async def assert_matches_source(self, label: str, *, allow_empty: bool = False) -> None:
        """Every probed person reads exactly the records Linear lets them read, but for KNOWN_GAPS, and nothing is
        missing, stale, stored twice or filed under a wrong team."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            problems = await self.differences()
            failing = [p for p in problems if p["type"] not in KNOWN_GAPS]
            if not failing or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        tolerated = Counter(p["type"] for p in problems if p["type"] in KNOWN_GAPS)
        if tolerated:
            logger.info("%s: known gaps tolerated: %s", label, dict(tolerated))
        assert not failing, (
            f"{label}: PipesHub differs from Linear in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}")
        if not allow_empty:
            assert any(w["in_scope"] for w in wanted_records(self.sim.expected(), self.scope).values()), (
                f"{label}: the workspace holds nothing to compare")

    def assert_gap(self, label: str, problems: list[dict[str, Any]], gap: str) -> None:
        """A strict known gap: the difference ``gap`` is present (its fix is not merged); the test fails when it is gone."""
        assert any(p["type"] == gap for p in problems), f"{label}: the known gap {gap} is gone; remove it ({KNOWN_GAPS})"


# ---- fixtures --------------------------------------------------------------------------------------------


def _app_container() -> str:
    """The PipesHub container the suite runs against: ``LINEAR_SIM_APP_CONTAINER``, else the one container the compose
    service ``pipeshub-ai`` has on this host (when several stacks run, name it)."""
    named = os.getenv(APP_CONTAINER_ENV, "").strip()
    if named:
        return named
    out = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.service=pipeshub-ai", "-q"],
                         capture_output=True, text=True, timeout=30, check=False).stdout.split()
    assert len(out) == 1, f"{len(out)} pipeshub-ai containers are running; set {APP_CONTAINER_ENV} to the one under test"
    return out[0]


def resolved_in_container(container: str, host: str) -> str:
    """The address ``host`` resolves to inside ``container``, as its own resolver says."""
    out = subprocess.run(["docker", "exec", container, "python3", "-c",
                          "import socket,sys;print(socket.gethostbyname(sys.argv[1]))", host],
                         capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, f"{host} does not resolve in {container}: {out.stderr.strip()}"
    return out.stdout.strip()


_TRUST_PROBE = """
import asyncio, ssl, sys, aiohttp, httpx
async def main():
    async with aiohttp.ClientSession() as s, s.get("https://api.linear.app/_sim/health") as r:
        print("aiohttp", r.status)
    async with httpx.AsyncClient() as c:
        print("httpx", (await c.get("https://uploads.linear.app/_sim/health")).status_code)
asyncio.run(main())
"""


def trusted_in_container(container: str) -> dict[str, str]:
    """What aiohttp (the GraphQL client) and httpx (the file download) answer for the Linear names inside ``container``:
    each must verify the front's certificate with the container's own trust settings (``SSL_CERT_FILE``)."""
    out = subprocess.run(["docker", "exec", container, "python3", "-c", _TRUST_PROBE],
                         capture_output=True, text=True, timeout=90, check=False)
    assert out.returncode == 0, f"a client in {container} does not trust the front: {out.stderr.strip()[-400:]}"
    return dict(line.split() for line in out.stdout.strip().splitlines())


@pytest.fixture(scope="session")
def linear_front() -> dict[str, str]:
    """The three Linear names resolve, inside the PipesHub container, to the simulator's TLS front and to nothing else,
    and both HTTP clients the connector uses trust it: checked before any connector exists, so nothing of this suite can
    reach the real Linear. Fails, never skips: a stack without the front must not run the suite."""
    if shutil.which("docker") is None:
        pytest.fail("the docker CLI is needed to check where the Linear names resolve in the PipesHub container")
    container = _app_container()
    front = {"linear-tls": resolved_in_container(container, "linear-tls")}
    for host in LINEAR_HOSTS:
        front[host] = resolved_in_container(container, host)
        assert front[host] == front["linear-tls"], (
            f"{host} resolves to {front[host]} in {container}, not to the simulator's front {front['linear-tls']}: "
            "a sync would reach the real Linear")
    trust = trusted_in_container(container)
    assert set(trust) == {"aiohttp", "httpx"}, trust
    return {**front, **trust}


@pytest.fixture(scope="session")
def linear_sim(linear_front: dict[str, str]) -> LinearSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Linear simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = LinearSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the Linear user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def linear_sim_people(
    linear_sim: LinearSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the workspaces, made before any connector so its first
    sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the org again."""
    pipeshub_client._ensure_access_token()
    linear_sim.load(workspace())
    known = {u["email"].lower() for u in linear_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the workspace without a PipesHub account: {known}"
    assert OUTSIDER in PEOPLE
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def linear_world(
    linear_sim: LinearSim,
    linear_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[LinearWorld, None]:
    world = LinearWorld(linear_sim, pipeshub_client, graph_provider, linear_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()


def authorization_code(world: LinearWorld) -> dict[str, Any]:
    """What a person's browser does with the app's authorize route: it gives Linear's URL, the (simulated) Linear sends the
    browser back with a code, and the app's callback route exchanges it. Returns the callback's answer."""
    started = world.client._request_json("GET", f"/api/v1/connectors/{world.connector_id}/oauth/authorize")
    url = urlparse(started["authorizationUrl"])
    assert url.hostname == "linear.app" and url.path == "/oauth/authorize", started["authorizationUrl"]
    # The browser reaches the simulator on its published port; the app's token request goes through the front.
    landed = requests.get(f"{world.sim.base_url}{url.path}?{url.query}", allow_redirects=False, timeout=30,
                          headers={"Host": "linear.app"})
    assert landed.status_code in (302, 303, 307), landed.text[:200]
    back = parse_qs(urlparse(landed.headers["Location"]).query)
    return world.client._request_json("GET", "/api/v1/connectors/oauth/callback",
                                      params={"code": back["code"][0], "state": back["state"][0]})
