# pyright: ignore-file

"""GitLab against the connectors-simulator: an instance the test owns, and who may read what.

The simulator answers GitLab's REST API v4, the GraphQL repository tree and the OAuth provider, with users, nested
groups, projects of three visibilities, members at every access level, issues (confidential ones too), merge
requests, notes (internal ones too), a small git repository per project and uploads. The PipesHub container reaches it
by its compose service name (``GITLAB_SIM_CONNECTOR_URL``, ``http://gitlab-sim``): a private address, which PipesHub
allows for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on, and the app container opts into plain
http for a trusted network (``PIPESHUB_GITLAB_ALLOW_INSECURE_HTTP``, set by the compose overlay). This process drives
the simulator through its control API on the published port (``GITLAB_SIM_URL``).

Each test loads an instance, creates a connector for one project of it (the connector syncs exactly one repository),
syncs, changes the instance and syncs again. After a sync the simulator's oracle (``/_sim/gitlab/expected``: GitLab's
own visibility, membership, role, confidential-issue and internal-note rules) says who may read every issue, merge
request, file, folder and upload. Every person of the instance has a PipesHub account with the same email, and
PipesHub's own permission query (the one search uses, ``filter_accessible_record_ids``) is asked, for each of them,
which records of the connector they may read. The differences:

  MISSING        something the sync account reads in the project has no live record
  STALE          a live record of something gone, or outside the sync's scope
  DUPLICATE      two live records for one thing
  PARENT         a record not under the record group GitLab's rules file it in
  LEAK:<why>     a person may read a record GitLab does not let them read
  DENIED:<why>   a person may read it on GitLab but PipesHub refuses them
  SIM_UNMODELED / SIM_SPEC  the connector sent a request the simulator does not model, or a response broke GitLab's
                 reference: the comparison would prove nothing

A difference whose type is in ``KNOWN_GAPS`` is tolerated (and counted): the project's visibility is not granted,
which GitLab does (``DENIED:project-visibility``). TC-GLSIM-ACL-009 pins exactly that set, strictly.

The scope of a sync: the one project the connector is configured for, if the sync account reads it; for each record
the sync account reads, a record; files the product's code path policy does not keep (dotfiles, binary and generated
files) and an upload linked only from internal notes have none (the connector makes no record of those).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests
from app.config.constants.arangodb import AppStatus
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from connectors.gitlab.sim_instances import PEOPLE, base_instance
from helper.graph_provider_utils import async_poll_until
from helper.oauth_token_helper import inject_access_token
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.source_credentials import source_unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("gitlab-sim")

CONNECTOR_TYPE = "GitLab"
SIM_URL_ENV = "GITLAB_SIM_URL"
CONNECTOR_URL = os.getenv("GITLAB_SIM_CONNECTOR_URL", "http://gitlab-sim")
SYNC_TIMEOUT_SEC = int(os.getenv("GITLAB_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 1
SETTLE_TIMEOUT_SEC = int(os.getenv("GITLAB_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
# GitLab's clock moves between two syncs, as a real one does: the connector compares record times with its checkpoint.
CLOCK_STEP_SEC = 180
DEFAULT_PROJECT = "acme/widgets"

# The differences from GitLab's rules the connector is still allowed to show: GitLab lets anyone read a public
# project and any signed-in user (not external) an internal one; PipesHub grants a project's members only
# (backend/python/app/connectors/sources/gitlab/projects.py, the project record groups' permissions). Under-sharing
# only. Remove the entry once the connector grants it, so a regression fails again.
KNOWN_GAPS = frozenset({"DENIED:project-visibility"})

_UPLOAD = re.compile(r"/api/v4/projects/(\d+)/uploads/([^/]+)/(.+)$")


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in GitLab's error shape. ``trigger`` takes
    the core's fields (``after_n``, ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "status", "code": code}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def reset_fault(path_regex: str, **trigger: float) -> dict[str, Any]:
    """A dropped connection."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "reset"}}
    if trigger:
        rule["trigger"] = trigger
    return rule


def list_filter(operator: str, values: list[str]) -> dict[str, Any]:
    return {"operator": operator, "type": "list", "value": values}


def sync_filters(project: str | None = DEFAULT_PROJECT, **values: Any) -> dict[str, Any]:
    """``config.filters`` as the app saves it: the one repository, and any other sync filter."""
    if project:
        values = {"project_ids": list_filter("in", [project]), **values}
    return {"sync": {"values": values}}


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Want:
    kind: str                    # issue, merge_request, file, folder, upload
    group: str                   # the record group it must sit under
    readers: set[str]            # usernames who may read it on GitLab
    anonymous: bool = False
    project: int = 0
    title: str = ""


@dataclass
class Scope:
    projects: list[str] = field(default_factory=lambda: [DEFAULT_PROJECT])
    sync_user: str = "admin"
    kinds: set[str] | None = None        # record kinds in scope (None: every kind)
    exclude: set[str] = field(default_factory=set)   # external ids a sync filter keeps out (dates, paths)
    notes: list[str] = field(default_factory=list)


def norm(external_id: str) -> str:
    """An upload's external id is its API path whatever host the connector put in front of it."""
    m = _UPLOAD.search(external_id)
    return m.group(0) if m else external_id


def _keeps_file(path: str) -> bool:
    from app.modules.parsers.code_parser.path_policy import PathPolicy

    name = path.rsplit("/", 1)[-1]
    return not name.startswith(".") and PathPolicy().keeps_file(path, name)


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, Want]:
    """External record id -> what a complete and correct sync holds for it."""
    projects = {p["id"]: p for p in expected["projects"] if not p["deleted"] and p["path_with_namespace"] in scope.projects}
    wanted: dict[str, Want] = {}
    for r in expected["records"]:
        if r["project"] not in projects or r.get("internal_only"):
            continue
        if scope.sync_user not in r["readers"] and not r.get("anonymous"):
            continue            # what the sync account cannot read, the connector cannot store
        if scope.kinds is not None and r["kind"] not in scope.kinds:
            continue
        if norm(r["id"]) in scope.exclude:
            continue
        if r["kind"] == "file" and not _keeps_file(r["path"]):
            continue
        if r["kind"] == "folder" and r["path"].rsplit("/", 1)[-1].startswith("."):
            continue
        wanted[norm(r["id"])] = Want(r["kind"], r["group"], set(r["readers"]), bool(r.get("anonymous")), r["project"],
                                     r.get("title") or r.get("path") or r.get("filename") or "")
    return wanted


def _reason(want: Want, user: dict[str, Any] | None, finds: bool, project: dict[str, Any] | None) -> str:
    """The GitLab rule that explains a difference between PipesHub and GitLab for one person and one record."""
    side = "LEAK" if finds else "DENIED"
    if user is None:
        return f"{side}:not-a-gitlab-user"
    if finds:
        if user["state"] != "active":
            return "LEAK:inactive-user"
        if project and user["username"] not in project["members"]:
            return "LEAK:not-a-member"
        if want.kind in ("file", "folder", "merge_request"):
            return f"LEAK:{want.kind}-below-reporter"
        if "confidential" in want.group:
            return "LEAK:confidential"
        return "LEAK:other"
    if project and project["visibility"] != "private" and user["username"] not in project["members"]:
        return "DENIED:project-visibility"
    if project and user["username"] in project.get("inactive_members", []):
        return "DENIED:inactive-member"
    return f"DENIED:{want.kind}"


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed email may
    read) and GitLab's rules (``expected``, the oracle)."""
    wanted = wanted_records(expected, scope)
    by_email = {u["email"].lower(): u for u in expected["users"] if u.get("email")}
    projects = {p["id"]: p for p in expected["projects"]}
    by_ext: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_ext.setdefault(norm(str(record["externalRecordId"])), []).append(record)
    problems: list[dict[str, Any]] = []
    for ext, want in wanted.items():
        found = by_ext.get(ext, [])
        if not found:
            problems.append({"type": "MISSING", "kind": want.kind, "record": ext, "name": want.title})
            continue
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(found)})
        group = found[0].get("externalGroupId")
        # An upload is filed under the record group of the issue or merge request that links it; the oracle names the
        # work items' for all of them and its readers (the owner's rules) are what is compared.
        if want.kind == "upload":
            parent_ok = str(group).startswith(f"{want.group.split('-')[0]}-") and not str(group).endswith("code-repository")
        else:
            parent_ok = group == want.group
        if not parent_ok:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": want.group})
        ids = {r["id"] for r in found}
        for email, seen in readable.items():
            user = by_email.get(email.lower())
            may = user is not None and user["username"] in want.readers
            finds = bool(ids & seen)
            if want.anonymous and not may and user is None:
                # A stranger to GitLab reads an anonymous record there; a PipesHub team connector shows its records only
                # to the source users it registers (the connector gate), by design of the product, not a defect.
                continue
            if finds != may:
                problems.append({"type": _reason(want, user, finds, projects.get(want.project)), "record": ext,
                                 "name": found[0].get("recordName"), "user": email})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName")} for r in found]
    return problems


@dataclass
class Snapshot:
    """What a test that only reads compares: the oracle and PipesHub's holdings as they were."""

    expected: dict[str, Any]
    records: list[dict[str, Any]]
    readable: dict[str, set[str]]
    scope: Scope

    def differences(self) -> list[dict[str, Any]]:
        return compare(self.expected, self.scope, self.records, self.readable)

    def failing(self, tolerate: frozenset[str] = KNOWN_GAPS) -> list[dict[str, Any]]:
        return [p for p in self.differences() if p["type"] not in tolerate]


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class GitLabSim:
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

    def g(self, method: str, path: str, body: object = None) -> Any:
        return self.call(method, f"/_sim/gitlab{path}", body)

    def load(self, instance: dict[str, Any], behaviours: dict[str, Any] | None = None) -> dict[str, Any]:
        self.call("DELETE", "/_sim/faults")
        self.g("DELETE", "/behaviours")
        if behaviours:
            self.g("PUT", "/behaviours", behaviours)
        loaded = self.g("PUT", "/scenario", instance)
        self.g("POST", "/calls/reset")
        return loaded

    def expected(self) -> dict[str, Any]:
        return self.g("GET", "/expected")

    def calls(self) -> list[dict[str, Any]]:
        return self.g("GET", "/calls") or []

    def paths(self) -> set[str]:
        """The distinct endpoints (path with numbers and names folded) the connector has called since the last reset."""
        return {f"{c['method']} {re.sub(r'/[0-9]+', '/:id', c['path'])}" for c in self.calls()}


class GitLabWorld:
    """One test's instance, its connector, and the comparison between them."""

    def __init__(self, sim: GitLabSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.loaded: dict[str, Any] = {}
        self.keys: dict[str, int] = {}
        self.tokens: dict[str, str] = {}
        self.scope = Scope()
        self._fault_rules: list[dict[str, Any]] = []
        self._extra: list[GitLabWorld] = []
        self.silent: dict[tuple[str, str], bool] = {}

    # ---- set-up ---------------------------------------------------------------------------------------

    def load(self, instance: dict[str, Any], behaviours: dict[str, Any] | None = None) -> dict[str, Any]:
        self.loaded = self.sim.load(instance, behaviours)
        self.keys, self.tokens = self.loaded["keys"], self.loaded["tokens"]
        return self.loaded

    async def start(self, instance: dict[str, Any] | None = None, *, behaviours: dict[str, Any] | None = None,
                    project: str | None = DEFAULT_PROJECT, token: str = "sync_account", auth: str = "oauth",
                    filters: dict[str, Any] | None = None, enable: bool = True, load: bool = True,
                    kinds: set[str] | None = None, instance_url: str | None = None) -> str | None:
        """Load ``instance`` (unless ``load`` is False), create a connector for ``project`` that signs in with the
        simulator's token ``token`` (``auth``: ``oauth``, the access token the app stores after the sign-in, or
        ``api_token``), and (unless ``enable`` is False) turn it on; return the outcome of the run that starts."""
        if load:
            self.load(instance if instance is not None else base_instance(), behaviours)
        self.scope = Scope(projects=[project] if project else [], kinds=kinds)
        self.create_connector(project=project, token=self.tokens.get(token, token), auth=auth, filters=filters,
                              instance_url=instance_url)
        if not enable:
            return None
        return await self.enable()

    def create_connector(self, *, project: str | None, token: str, auth: str = "oauth",
                         filters: dict[str, Any] | None = None, scope: str = "team",
                         instance_url: str | None = None) -> str:
        config: dict[str, Any] = {"auth": {"instanceUrl": instance_url or CONNECTOR_URL}}
        flt = filters if filters is not None else sync_filters(project)
        if flt:
            config["filters"] = flt
        if auth == "api_token":
            config["auth"]["token"] = token
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"gitlab-sim-{uuid.uuid4().hex[:8]}", scope=scope,
            config=config, auth_type="API_TOKEN" if auth == "api_token" else "OAUTH")
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        if auth != "api_token":
            inject_access_token(self.connector_id, token)
        return self.connector_id

    async def enable(self) -> str | None:
        """Turn the connector on; the outcome of the run that starts, or REFUSED when PipesHub would not turn it on."""
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True), refusable=True)
        assert outcome == REFUSED or self.sim.g("GET", "/stats")["total"] > 0, (
            f"the connector never called the simulator at {CONNECTOR_URL} (outcome {outcome})")
        return outcome

    async def disable(self) -> None:
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()

    async def restart(self, filters: dict[str, Any] | None = None, project: str | None = None) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled
        connector); return the outcome of the run enabling starts."""
        await self.disable()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if project is not None:
            self.scope.projects = [project]
        return await self.enable()

    async def second(self, project: str, *, token: str = "sync_account", auth: str = "oauth",
                     filters: dict[str, Any] | None = None, kinds: set[str] | None = None) -> GitLabWorld:
        """Another connector, for another project of the same loaded instance (a second world on the same source)."""
        other = GitLabWorld(self.sim, self.client, self.graph, self.people)
        other.loaded, other.keys, other.tokens = self.loaded, self.keys, self.tokens
        self._extra.append(other)
        await other.start(None, project=project, token=token, auth=auth, filters=filters, load=False, kinds=kinds)
        return other

    def remove_connector(self) -> None:
        for other in self._extra:
            other.remove_connector()
        if os.getenv("GITLAB_SIM_KEEP_CONNECTOR") == "1":
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

    def key(self, name: str) -> int:
        return int(self.keys[name])

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the instance, in order (``/_sim/gitlab/ops``; ids may be the instance's keys). GitLab's clock moves
        on first: a change is made some time after the last run, never in the same second (the connector's revision of
        an item is its ``updated_at`` in seconds)."""
        if not all(op["op"] == "advance" for op in ops):
            self.sim.g("POST", "/ops", [{"op": "advance", "seconds": 5}])
        return self.sim.g("POST", "/ops", list(ops))["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.g("PUT", "/behaviours", values)

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def fired(self) -> list[Any]:
        return self.sim.call("GET", "/_sim/faults/log") or []

    def clear_faults(self, *, every_rule: bool = False) -> int:
        """Remove the faults, failing when none of them fired (or, with ``every_rule``, when one of them did not): a
        test whose fault the connector never reached proves nothing. Returns how many times faults fired."""
        fired = self.fired()
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"
        if every_rule:
            silent = [rules[i]["match"]["path_regex"] for i in range(len(rules)) if not any(e["rule"] == i for e in fired)]
            assert not silent, f"these injected faults never fired, so they prove nothing: {silent}"
        return len(fired)

    async def wait_fired(self, path_regex: str, timeout: float = 120.0) -> None:
        """Wait until the fault on ``path_regex`` has fired. Some reads run in a task the run starts as it ends (the code
        files' commit timestamps, ``ReposSync.schedule_timestamp_backfill``), so checking right after the run races them."""
        index = next(i for i, r in enumerate(self._fault_rules) if r["match"]["path_regex"] == path_regex)
        deadline = time.monotonic() + timeout
        while not any(e["rule"] == index for e in self.fired()):
            assert time.monotonic() < deadline, f"the fault on {path_regex} did not fire within {timeout:.0f}s of the run's end"
            await asyncio.sleep(2)

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

    async def sync(self, *, full: bool = False, step: bool = True) -> str | None:
        """A run. GitLab's clock moves on first (``step``): syncs are minutes apart on a real instance."""
        if step:
            self.ops({"op": "advance", "seconds": CLOCK_STEP_SEC})
        return await self._run(lambda: self.client.resync_connector(
            self.connector_id, full_sync=full, connector_name=CONNECTOR_TYPE))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "recordType": r.get("recordType"), "version": r.get("version"),
                 "indexingStatus": r.get("indexingStatus")}
                for r in rows or [] if not r.get("isDeleted") and not r.get("isPlaceholder")]

    async def snapshot(self) -> Snapshot:
        """The oracle, PipesHub's live records and who reads them, now: a test that only reads can compare against
        this long after the instance has been reloaded by another."""
        expected = self.sim.expected()
        records = await self.records()
        return Snapshot(expected, records, await self.readable(records), Scope(self.scope.projects[:], self.scope.sync_user,
                                                                                self.scope.kinds, set(self.scope.exclude)))

    def rest(self, token: str = "sync_account") -> Any:
        """A REST client of the suite's (``gitlab_test_utils.GitLabRestClient``) on the simulator."""
        from connectors.gitlab.gitlab_test_utils import GitLabRestClient

        return GitLabRestClient(self.tokens.get(token, token), self.sim.base_url)

    def stream(self, record_id: str) -> bytes:
        """The content PipesHub streams for a record (what the connector reads from GitLab now)."""
        original = self.client.timeout_seconds
        self.client.timeout_seconds = 180
        try:
            resp = self.client.stream_record(record_id)
        finally:
            self.client.timeout_seconds = original
        assert resp.status_code == 200, f"stream_record {record_id}: HTTP {resp.status_code} {resp.text[:200]}"
        return resp.content

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records() if norm(str(r["externalRecordId"])) == external_id), None)

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
        ext = {r["id"]: norm(str(r["externalRecordId"])) for r in records}
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

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        problems = compare(expected, self.scope, records, readable)
        problems += [{"type": "SIM_UNMODELED", "what": e} for e in self.sim.g("GET", "/unmodeled") or []]
        problems += [{"type": "SIM_SPEC", "what": e} for e in self.sim.g("GET", "/spec-problems") or []]
        return problems

    async def assert_matches_source(self, label: str, *, tolerate: frozenset[str] = KNOWN_GAPS,
                                    settle: bool = True) -> list[dict[str, Any]]:
        """Every probed person reads exactly the records GitLab lets them read, but for ``tolerate`` (KNOWN_GAPS), and
        nothing is missing, stale, stored twice or filed under the wrong record group. Returns the tolerated ones."""
        deadline = time.monotonic() + (SETTLE_TIMEOUT_SEC if settle else 0)
        while True:
            problems = await self.differences()
            failing = [p for p in problems if p["type"] not in tolerate]
            if not failing or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        tolerated = [p for p in problems if p["type"] in tolerate]
        if tolerated:
            logger.info("%s: known gaps tolerated: %s", label, dict(Counter(p["type"] for p in tolerated)))
        assert not failing, (
            f"{label}: PipesHub differs from GitLab in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}"
        )
        return tolerated


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gitlab_sim() -> GitLabSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The GitLab simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = GitLabSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the GitLab user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gitlab_sim_people(
    gitlab_sim: GitLabSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the instances, made before any connector so its
    first sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the
    org again."""
    pipeshub_client._ensure_access_token()
    gitlab_sim.load(base_instance())
    known = {u["email"].lower() for u in gitlab_sim.expected()["users"] if u.get("email")}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the instance without an account: {known}"
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def gitlab_synced(
    gitlab_sim: GitLabSim,
    gitlab_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[tuple[GitLabWorld, Snapshot, dict[str, Any]], None]:
    """One connector that has synced the base instance once, then been turned off, for the tests that only read: the
    world, the snapshot (oracle and PipesHub's holdings) and the source's own JSON (issues, merge requests, tree,
    members) as the instance served it. Nothing here reads the simulator again, so another test may reload it."""
    world = GitLabWorld(gitlab_sim, pipeshub_client, graph_provider, gitlab_sim_people)
    try:
        assert succeeded(await world.start(base_instance())), "the first sync of the base instance did not succeed"
        await world.disable()
        pid = world.key("widgets")
        rest = world.rest()
        try:
            from connectors.gitlab.gitlab_test_utils import (
                get_merge_request,
                get_project,
                list_issues,
                list_merge_requests,
                list_project_members,
            )

            mrs = [await get_merge_request(rest, DEFAULT_PROJECT, m["iid"])
                   for m in await list_merge_requests(rest, DEFAULT_PROJECT)]
            source = {"project": await get_project(rest, DEFAULT_PROJECT), "issues": await list_issues(rest, DEFAULT_PROJECT),
                      "mrs": mrs,
                      "members": await list_project_members(rest, DEFAULT_PROJECT), "pid": pid}
        finally:
            await rest.aclose()
        yield world, await world.snapshot(), source
    finally:
        world.remove_connector()


@pytest_asyncio.fixture(loop_scope="session")
async def gitlab_world(
    gitlab_sim: GitLabSim,
    gitlab_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GitLabWorld, None]:
    world = GitLabWorld(gitlab_sim, pipeshub_client, graph_provider, gitlab_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
