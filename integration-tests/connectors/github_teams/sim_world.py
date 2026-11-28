# pyright: ignore-file

"""GitHub against the connectors-simulator: an organisation the test owns, and who may read what.

The simulator answers GitHub's REST and GraphQL APIs, its OAuth sign-in and its upload downloads for one world of
users, organisations, teams and repositories, with GitHub's own rules for who may read a repository (organisation
owner, base permission, teams and their children, direct collaborators, public / internal / private). The PipesHub
container reaches it as ``https://api.github.com`` and ``https://github.com``, the names the connector calls, through the
overlay's TLS front (``docker-compose.integration.github-teams-sim.yml``). This process drives the simulator's control API
on the published port (``GITHUB_SIM_URL``).

Each test loads a world, creates a connector for one repository, signs it in, syncs, changes the world and syncs again.
After a sync the simulator's oracle (``/_sim/github/expected``) says who may read every issue, pull request and file.
Every person of the world has a PipesHub account with the same email, and PipesHub's own permission query (the one
search uses, ``filter_accessible_record_ids``) is asked, for each of them, which records of the connector they may read.
The differences:

  MISSING        something in the sync's scope has no live record
  STALE          a live record of something gone from GitHub, or out of the sync's scope
  DUPLICATE      two live records for one thing
  LEAK           a person may read a record GitHub does not let them read
  DENIED         a person may read it on GitHub but PipesHub refuses them
  SIM_UNMODELED / SIM_SPEC / SIM_HOST / SIM_TOKEN_LEAK   the connector sent a request the simulator does not model, a
                 response broke GitHub's OpenAPI description, a call named a host the front does not own, or a token
                 reached the storage host: the comparison would prove nothing

A difference whose type is in ``KNOWN_GAPS`` is logged and tolerated; any other fails the test. So does an injected fault
that never fired.

A public repository's records count as readable by every person with a GitHub account and by nobody without one: PipesHub
shows people without an account in the source nothing, whatever signed-out visitors see on github.com. The PipesHub user
who connected the connector also reads what the connected GitHub account reads (the product's authenticated-account link).
"""

from __future__ import annotations

import asyncio
import json
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
from connectors.github_teams.sim_instances import (
    ADMIN_TOKEN,
    API_HOST,
    CLIENT_EMAIL,
    HOSTS,
    NOT_ALIASED,
    OAUTH_APP,
    PEOPLE,
    STORAGE_HOST,
    WEB_HOST,
    acme,
)
from helper.graph_provider_utils import async_poll_until
from helper.oauth_token_helper import inject_access_token
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

logger = logging.getLogger("github-teams-sim")

TEAMS_TYPE = "GitHub Teams"
PERSONAL_TYPE = "Github"
SIM_URL_ENV = "GITHUB_SIM_URL"
APP_CONTAINER_ENV = "GITHUB_SIM_APP_CONTAINER"
SYNC_TIMEOUT_SEC = int(os.getenv("GITHUB_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 2
SETTLE_TIMEOUT_SEC = int(os.getenv("GITHUB_SIM_SETTLE_TIMEOUT_SEC", "60"))
SETTLE_POLL_SEC = 5
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
# What a sync is advanced by before it runs: syncs are minutes apart on GitHub.
SYNC_GAP_SEC = 180

# What the connector calls, as the simulator names it ("METHOD /route-template"): filled by the endpoint test.
CALLED: set[str] = set()

# The differences from GitHub's rules the connector still shows, each strict: a test fails when the difference is gone,
# so the entry is removed with the fix. Maps a difference type to the open pull request that fixes it.
KNOWN_GAPS: dict[str, str] = {
    # A public repository's records are granted to the whole organisation, so a PipesHub user with no GitHub account reads
    # them; GitHub's rule (and the product's: "content open to anyone reaches only people with an account in the source")
    # needs an account. Fixed by the open pull request below; pinned strictly by test_tc_ghsim_acl_015.
    "LEAK:public-without-account": "#6209",
}

# Requests the connector makes that GitHub's description does not declare (GitHub ignores them): operation -> parameter
# texts. Listed in GITHUB_TEAMS_SIM_ITS.md; every other spec problem fails the test.
KNOWN_REQUEST_FINDINGS: dict[str, tuple[str, ...]] = {}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = "GET", message: str | None = None,
          **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in GitHub's error shape. ``trigger`` takes
    the core's fields (``after_n``, ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    effect: dict[str, Any] = {"type": "status", "code": code}
    if message:
        effect["message"] = message
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": effect}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def drop(path_regex: str, method: str | None = "GET", **trigger: float) -> dict[str, Any]:
    """The connection is dropped without an answer."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "reset"}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def throttle(path_regex: str, method: str | None = "GET", **trigger: float) -> dict[str, Any]:
    """A 429 with Retry-After: one request, then one more every second."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex},
                            "effect": {"type": "rate_limit", "capacity": 1, "refill_per_s": 1}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def repo_filter(*full_names: str, operator: str = "in") -> dict[str, Any]:
    """The connector's repository filter, saved as the app saves it."""
    return {"sync": {"values": {"repo_ids": {"operator": operator, "type": "select", "value": list(full_names)}}}}


def with_filters(base: dict[str, Any], *, indexing: dict[str, bool] | None = None, **sync: Any) -> dict[str, Any]:
    """``base`` (a ``repo_filter``) plus more sync values (``branch=...``) and indexing switches."""
    out: dict[str, Any] = {"sync": {"values": dict(base["sync"]["values"])}}
    for name, spec in sync.items():
        out["sync"]["values"][name] = spec
    if indexing:
        out["indexing"] = {"values": {k: {"operator": "is", "type": "boolean", "value": v} for k, v in indexing.items()}}
    return out


def branch_filter(branch: str) -> dict[str, Any]:
    return {"operator": "in", "type": "select", "value": [branch]}


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Scope:
    """What the connector is set to sync."""

    repos: set[str] = field(default_factory=set)        # full names the repository filter selects
    branch: str | None = None                            # the branch filter; None: each repository's default
    left_out: set[str] = field(default_factory=set)      # external ids a filter keeps out (named by the test)
    creator: str = CLIENT_EMAIL                          # the PipesHub user who connected it
    personal: bool = False                               # the personal connector: only the creator reads
    notes: list[str] = field(default_factory=list)


def _external_kind(external_id: str) -> str:
    return "upload" if external_id.startswith("https://") else "code" if external_id.startswith("/") else "item"


class Truth:
    """GitHub's answer for one oracle: which external ids exist in the sync's scope and who may read each."""

    def __init__(self, exp: dict[str, Any], scope: Scope) -> None:
        self.exp, self.scope = exp, scope
        self.repos_by_id = {r["id"]: r for r in exp["repos"]}
        self.in_scope = {r["id"] for r in exp["repos"] if r["full_name"] in scope.repos}
        self.items = {(i["repo_id"], i["kind"], i["number"]): i for i in exp["issues"]}
        self.uploads = {u["url"]: u for u in exp["uploads"]}
        self._files: dict[int, list[dict[str, Any]]] = {}

    def files(self, repo_id: int) -> list[dict[str, Any]]:
        """The files of the branch the connector is set to sync."""
        if repo_id not in self._files:
            self._files[repo_id] = [
                f for f in self.exp["files"] if f["repo_id"] == repo_id
                and ((f["branch"] == self.scope.branch) if self.scope.branch else f["default_branch"])]
        return self._files[repo_id]

    def wanted(self) -> dict[str, tuple[set[str], bool]]:
        """External id -> (logins who may read, public) of everything a complete sync holds."""
        out: dict[str, tuple[set[str], bool]] = {}
        for rid in self.in_scope:
            repo = self.repos_by_id[rid]
            if repo["deleted"]:
                continue
            for i in self.exp["issues"]:
                if i["repo_id"] == rid and i["live"]:
                    out[f"{rid}/{'pull' if i['kind'] == 'pull' else 'issues'}/{i['number']}"] = (
                        set(i["readers"]), i["public"])
            for f in self.files(rid):
                out[f"/{rid}/blob/{f['path']}"] = (set(f["readers"]), f["public"])
                parts = f["path"].split("/")
                for depth in range(1, len(parts)):
                    out.setdefault(f"/{rid}/tree/{'/'.join(parts[:depth])}", (set(repo["readers"]), repo["public"]))
        for url, up in self.uploads.items():
            for o in up["owners"]:
                if o["repo_id"] in self.in_scope and not self.repos_by_id[o["repo_id"]]["deleted"]:
                    parent = f"{o['repo_id']}/{'pull' if o['kind'] == 'pull' else 'issues'}/{o['number']}"
                    item = self.items.get((o["repo_id"], o["kind"], o["number"]))
                    if item and item["live"]:
                        out[f"{url}#{parent}"] = (set(item["readers"]), item["public"])
        return {k: v for k, v in out.items() if k not in self.scope.left_out}

    def truth(self, external_id: str) -> tuple[set[str], bool] | None:
        """(readers, public) for one external id; None: gone or out of scope. A tree entry counts when the connector
        keeps a folder record for it (``wanted`` lists them)."""
        return self.wanted().get(external_id)


def _login_of(exp: dict[str, Any], email: str) -> str | None:
    for u in exp["users"]:
        if email.lower() in ((u["email"] or "").lower(), (u["corporate_email"] or "").lower()):
            return u["login"]
    return None


def compare(exp: dict[str, Any], scope: Scope, records: list[dict[str, Any]],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed email may
    read) and GitHub's rules (``exp``, the oracle).

    A folder record GitHub does not model (the connector keeps one per directory, and ``wanted`` derives the same set)
    is compared like a file. A record with no oracle entry is STALE.
    """
    truth = Truth(exp, scope)
    wanted = truth.wanted()
    by_ext: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_ext.setdefault(str(r["externalRecordId"]), []).append(r)
    problems: list[dict[str, Any]] = []
    for ext in wanted:
        if ext.startswith("/") and "/tree/" in ext:
            continue  # a folder: not required to exist (a folder is kept only while it holds a synced file)
        if ext.startswith("https://"):
            continue  # an upload: images are embedded in the streamed body and get no record; the content tests check files
        if ext not in by_ext:
            problems.append({"type": "MISSING", "record": ext})
        elif len(by_ext[ext]) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(by_ext[ext])})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName"), "kind": r.get("recordType")}
                         for r in found]
    linked = exp["sync_account"]
    for email, seen in readable.items():
        login = _login_of(exp, email)
        for ext, found in by_ext.items():
            t = wanted.get(ext)
            ids = {r["id"] for r in found}
            if t is None:
                allowed = False
            elif scope.personal:
                allowed = email == scope.creator
            else:
                readers, public = t
                allowed = ((public and (login is not None or email == scope.creator))
                           or (login in readers if login else False)
                           # the user who connected it also reads what its GitHub account reads
                           or (email == scope.creator and linked in readers))
            finds = bool(ids & seen)
            if finds and not allowed and t is not None:
                kind = "LEAK:public-without-account" if (t[1] and login is None and not scope.personal) else "LEAK"
                problems.append({"type": kind, "record": ext, "user": email, "name": found[0].get("recordName")})
            elif allowed and not finds:
                problems.append({"type": "DENIED", "record": ext, "user": email, "name": found[0].get("recordName")})
    return problems


def spec_findings_only(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``SIM_SPEC`` entries that are not a request the guide lists as a known finding."""
    out = []
    for p in problems:
        known = KNOWN_REQUEST_FINDINGS.get(p.get("op", ""))
        if known and all(any(x in line for x in known) for line in p.get("problems", [])):
            continue
        out.append(p)
    return out


def call_problems(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the connector's calls show wrong: a host the front does not own, a token sent to the storage host or to a
    web host for anything but an upload's second attempt."""
    out = []
    for c in calls:
        if c["host"] not in HOSTS:
            out.append({"type": "SIM_HOST", "host": c["host"], "path": c["path"]})
        elif c["host"] == STORAGE_HOST and c.get("authorization"):
            out.append({"type": "SIM_TOKEN_LEAK", "host": c["host"], "path": c["path"]})
        elif c["host"] == WEB_HOST and c.get("authorization") and not c["path"].startswith("/user-attachments/"):
            out.append({"type": "SIM_TOKEN_LEAK", "host": c["host"], "path": c["path"]})
    return out


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class GithubSim:
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

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/github/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/github/behaviours", behaviours)
        self.call("PUT", "/_sim/github/scenario", scenario)
        self.call("POST", "/_sim/calls/reset")

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/github/expected")

    def calls(self, since: int = 0) -> list[dict[str, Any]]:
        return self.call("GET", f"/_sim/github/calls?since={since}")["calls"]

    def total_calls(self) -> int:
        return self.call("GET", "/_sim/github/calls?since=0")["total"]

    def routes(self, since: int = 0) -> set[str]:
        """"METHOD /path" of every API call since a position, ids replaced by their kind."""
        return {f"{c['method']} {c['path']}" for c in self.calls(since) if c["host"] == API_HOST}


class GithubWorld:
    """One test's world, its connector, and the comparison between them."""

    def __init__(self, sim: GithubSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.connector_type = TEAMS_TYPE
        self.scenario: dict[str, Any] = {}
        self.scope = Scope()
        self.token = ADMIN_TOKEN
        self._fault_rules: list[dict[str, Any]] = []
        self.extra_connectors: list[str] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.scenario = scenario
        self.sim.load(scenario, behaviours)

    def create_connector(self, *, auth: str = "pat", token: str | None = None, filters: dict[str, Any] | None = None,
                         personal: bool = False, client_id: str | None = None,
                         client_secret: str | None = None) -> str:
        """Register a connector. ``auth``: ``pat`` (API token), ``oauth`` (the token is stored as the sign-in would
        store it, as the live suite does) or ``oauth-app`` (an OAuth app, signed in through the app's own routes)."""
        self.token = token or self.token
        self.connector_type = PERSONAL_TYPE if personal else TEAMS_TYPE
        config: dict[str, Any] = {"auth": {}}
        if auth == "pat":
            config["auth"] = {"authType": "API_TOKEN", "token": self.token}
        elif auth == "oauth-app":
            config["auth"] = {"clientId": client_id or OAUTH_APP["client_id"],
                              "clientSecret": client_secret or OAUTH_APP["client_secret"]}
        if filters:
            config["filters"] = filters
        created = self.client.create_connector(
            connector_type=self.connector_type, instance_name=f"github-sim-{uuid.uuid4().hex[:8]}",
            scope="personal" if personal else "team", auth_type="API_TOKEN" if auth == "pat" else "OAUTH",
            config=config)
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        if auth == "oauth":
            inject_access_token(self.connector_id, self.token)
        return self.connector_id

    def sign_in(self, token: str) -> None:
        """Store a (new) access token as the sign-in would."""
        inject_access_token(self.connector_id, token)
        self.token = token

    async def start(self, scenario: dict[str, Any], repos: list[str] | tuple[str, ...] = ("acme/widgets",), *,
                    behaviours: dict[str, Any] | None = None, auth: str = "pat", filters: dict[str, Any] | None = None,
                    enable: bool = True, personal: bool = False, ops: list[dict[str, Any]] | None = None,
                    token: str | None = None, branch: str | None = None) -> str | None:
        """Load ``scenario`` (and ``ops`` on it), create a connector over ``repos`` signed in as the sync account, and
        (unless ``enable`` is False) turn it on; return the outcome of the run that starts."""
        self.load(scenario, behaviours)
        if ops:
            self.ops(*ops)
        flt = filters or repo_filter(*repos)
        self.scope = Scope(repos=set(repos), branch=branch, personal=personal)
        self.create_connector(auth=auth, filters=flt, personal=personal, token=token)
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        """Turn the connector on; the outcome of the run that starts, or REFUSED when PipesHub would not turn it on."""
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True), refusable=True)
        assert outcome == REFUSED or self.sim.total_calls() > 0, (
            f"the connector never called the simulator (outcome {outcome}): it reached another host")
        return outcome

    async def restart(self, filters: dict[str, Any] | None = None, *, repos: list[str] | None = None,
                      branch: str | None | bool = False) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled
        connector); return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
            values = (filters.get("sync") or {}).get("values") or {}
            if "repo_ids" in values:
                self.scope.repos = set(values["repo_ids"]["value"])
            self.scope.branch = values["branch"]["value"][0] if "branch" in values else None
        return await self.enable()

    def remove_connector(self) -> None:
        if os.getenv("GITHUB_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.call("DELETE", "/_sim/faults")
            self.sim.call("DELETE", "/_sim/github/behaviours")
        except (requests.RequestException, RuntimeError):
            logger.exception("Could not clear the simulator's faults and behaviours")
        for cid in [self.connector_id, *self.extra_connectors]:
            if not cid:
                continue
            for action in (lambda: self.client.toggle_sync(cid, enable=False),
                           lambda: self.client.delete_connector(cid)):
                try:
                    action()
                except (PipeshubClientError, requests.RequestException):
                    logger.exception("Teardown of connector %s failed", cid)

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the world, in order (``/_sim/github/ops``; keys name what the scenario or an earlier op made)."""
        return self.sim.call("POST", "/_sim/github/ops", list(ops))["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/github/behaviours", values)

    def defer(self, contains: str, ops: list[dict[str, Any]], *, after: int = 1) -> None:
        """Apply ``ops`` just before the ``after``-th request whose path contains ``contains``: a change landing between
        two pages of a listing."""
        self.sim.call("POST", "/_sim/github/deferred", {"contains": contains, "after": after, "ops": ops})

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self, *, must_fire: bool = True) -> int:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing. Returns how many fired."""
        fired = self.sim.call("GET", "/_sim/faults/log") or []
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not (rules and must_fire) or fired, f"no injected fault fired, so the test proves nothing: {rules}"
        return len(fired)

    def repo_id(self, key: str = "widgets") -> int:
        return next(r["id"] for r in self.sim.expected()["repos"] if key in (r["key"], r["full_name"]))

    # ---- PipesHub -------------------------------------------------------------------------------------

    def connector(self) -> dict[str, Any]:
        """The connector's document (its status, last outcome, whether another full sync is owed)."""
        return self.client.get_connector(self.connector_id)

    async def pending_full_sync(self) -> bool:
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

    async def sync(self, *, full: bool = False, advance: bool = True) -> str | None:
        if advance:
            self.ops({"op": "advance", "seconds": SYNC_GAP_SEC})
        return await self._run(lambda: self.client.resync_connector(
            self.connector_id, full_sync=full, connector_name=self.connector_type))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "recordType": r.get("recordType"), "version": r.get("version"), "webUrl": r.get("webUrl"),
                 "indexingStatus": r.get("indexingStatus")}
                for r in rows or [] if not r.get("isDeleted") and not r.get("isPlaceholder")]

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records() if r["externalRecordId"] == external_id), None)

    async def readable(self, records: list[dict[str, Any]]) -> dict[str, set[str]]:
        """For each probed email, the ids of ``records`` PipesHub's permission query lets them read."""
        ids = [r["id"] for r in records if r["id"]]
        if not ids:
            return {email: set() for email in self.people}
        answers = await asyncio.gather(*[self.graph.filter_accessible_record_ids(ids, user_id, self.client.org_id)
                                         for user_id in self.people.values()])
        return {email: set(seen) for email, seen in zip(self.people, answers)}

    async def holdings(self) -> dict[str, set[str]]:
        """What PipesHub holds and grants: the live external ids, and for each email the external ids they read."""
        records = await self.records()
        ext = {r["id"]: str(r["externalRecordId"]) for r in records}
        readable = await self.readable(records)
        return {"records": set(ext.values()), **{email: {ext[i] for i in seen} for email, seen in readable.items()}}

    async def assert_holdings_kept(self, before: dict[str, set[str]], label: str, *, allow_gains: bool = False,
                                   new_records_ok: bool = False) -> None:
        """Nothing was removed and nobody lost access: what a sync that could not read must leave behind. Unless
        ``allow_gains`` (the run read part of a change), nothing was added and nobody gained access either. With
        ``new_records_ok`` a record the run read before it failed may appear, but nobody gains access to a record that
        was already there."""
        after = await self.holdings()
        lost = {who: sorted(before[who] - after.get(who, set())) for who in before}
        if new_records_ok:
            gained = {who: sorted((after.get(who, set()) - before[who]) & before["records"]) for who in before}
        else:
            gained = {} if allow_gains else {who: sorted(after.get(who, set()) - before[who]) for who in before}
        lost = {who: v for who, v in lost.items() if v}
        gained = {who: v for who, v in gained.items() if v}
        assert not lost and not gained, f"{label}: PipesHub changed what it holds; lost {lost}, gained {gained}"

    async def stream(self, external_id: str) -> bytes:
        """What PipesHub streams for a record: the bytes of the HTTP answer; raises on an HTTP error."""
        record = await self.record_for(external_id)
        assert record, f"no live record of {external_id}"
        resp = self.client.stream_record(record["id"], timeout=120)
        return b"".join(resp.iter_content(65536))

    async def stream_text(self, external_id: str) -> str:
        """The text of a streamed issue or pull request (its blocks), or the body of a file."""
        body = await self.stream(external_id)
        try:
            container = json.loads(body)
        except ValueError:
            return body.decode(errors="replace")
        if not isinstance(container, dict):
            return body.decode(errors="replace")
        parts = [str(g.get("data") or "") for g in container.get("block_groups", [])]
        parts += [str(b.get("data") or "") for b in container.get("blocks", [])]
        return "\n".join(parts)

    # ---- the comparison --------------------------------------------------------------------------------

    def source_problems(self) -> list[dict[str, Any]]:
        """What the simulator saw wrong in the connector's requests: nothing modelled is missing, nothing it sent
        broke GitHub's description, no call named another host, no token reached the storage host."""
        problems = [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/github/unmodeled") or []]
        specs = self.sim.call("GET", "/_sim/github/spec-problems") or []
        problems += [{"type": "SIM_SPEC", **e} for e in spec_findings_only(specs)]
        return problems + call_problems(self.sim.calls())

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        return compare(expected, self.scope, records, readable) + self.source_problems()

    async def assert_matches_source(self, label: str, *, allow_empty: bool = False) -> None:
        """Every probed person reads exactly the records GitHub lets them read, but for KNOWN_GAPS, and nothing is
        missing, stale or stored twice."""
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
            f"{label}: PipesHub differs from GitHub in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}")
        if not allow_empty:
            assert Truth(self.sim.expected(), self.scope).wanted(), f"{label}: the world holds nothing to compare"

    def assert_gap(self, label: str, problems: list[dict[str, Any]], gap: str) -> None:
        """A strict known gap: the difference ``gap`` is present (its fix is not merged); the test fails when it is gone."""
        assert any(p["type"] == gap for p in problems), f"{label}: the known gap {gap} is gone; remove it ({KNOWN_GAPS})"

    async def reads(self, email: str) -> set[str]:
        """The external ids of this connector's records ``email`` may read."""
        return (await self.holdings())[email]


# ---- fixtures --------------------------------------------------------------------------------------------


def _app_container() -> str:
    """The PipesHub container the suite runs against: ``GITHUB_SIM_APP_CONTAINER``, else the one container the compose
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


@pytest.fixture(scope="session")
def github_front() -> dict[str, str]:
    """The three hosts the connector calls resolve, inside the PipesHub container, to the simulator's TLS front and to
    nothing else: checked before any connector exists, so nothing of this suite can reach the real GitHub. Fails, never
    skips: a stack without the front must not run the suite."""
    if shutil.which("docker") is None:
        pytest.fail("the docker CLI is needed to check where the GitHub names resolve in the PipesHub container")
    container = _app_container()
    front = {"github-tls": resolved_in_container(container, "github-tls")}
    for host in HOSTS:
        front[host] = resolved_in_container(container, host)
        assert front[host] == front["github-tls"], (
            f"{host} resolves to {front[host]} in {container}, not to the simulator's front {front['github-tls']}: "
            "a sync would reach the real GitHub")
    return front


@pytest.fixture(scope="session")
def github_sim(github_front: dict[str, str]) -> GithubSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The GitHub simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = GithubSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the GitHub account to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def github_sim_people(
    github_sim: GithubSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the worlds, made before any connector so its first
    sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the org
    again."""
    pipeshub_client._ensure_access_token()
    github_sim.load(acme())
    known = {u["email"].lower() for u in github_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the world without a PipesHub account: {known}"
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def github_world(
    github_sim: GithubSim,
    github_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GithubWorld, None]:
    world = GithubWorld(github_sim, pipeshub_client, graph_provider, github_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()


def authorization_code(world: GithubWorld) -> dict[str, str]:
    """What a person's browser does with the app's authorize route: it gives GitHub's URL, the (simulated) GitHub sends
    the browser back with a code, and the app's callback route exchanges it. Returns the callback's answer."""
    started = world.client._request_json("GET", f"/api/v1/connectors/{world.connector_id}/oauth/authorize")
    url = urlparse(started["authorizationUrl"])
    assert url.hostname == WEB_HOST and url.path == "/login/oauth/authorize", started["authorizationUrl"]
    # The browser reaches the simulator on its published port; the app's token request goes through the front.
    landed = requests.get(f"{world.sim.base_url}{url.path}?{url.query}", allow_redirects=False, timeout=30,
                          headers={"Host": WEB_HOST})
    assert landed.status_code in (302, 303, 307), landed.text[:200]
    back = parse_qs(urlparse(landed.headers["Location"]).query)
    return world.client._request_json("GET", "/api/v1/connectors/oauth/callback",
                                      params={"code": back["code"][0], "state": back["state"][0]})


async def indexed(world: GithubWorld, *, timeout: int = 180) -> list[dict[str, Any]]:
    """Wait until no record of the connector is still being indexed; return the records."""
    pending = {"NOT_STARTED", "QUEUED", "IN_PROGRESS", None}
    deadline = time.monotonic() + timeout
    while True:
        records = await world.records()
        busy = [r for r in records if r["indexingStatus"] in pending]
        if not busy or time.monotonic() >= deadline:
            return records
        await asyncio.sleep(3)


GROUPS = {"TICKET": "work-items", "PULL_REQUEST": "pull-requests", "CODE_FILE": "code-repository",
          "FILE": "code-repository"}
