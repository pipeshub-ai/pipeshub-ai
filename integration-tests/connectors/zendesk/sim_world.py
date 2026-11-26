# pyright: ignore-file

"""Zendesk against the connectors-simulator: an account the test owns, and who may read what.

The simulator answers Zendesk's Support and Help Center APIs, its OAuth sign-in and its attachment downloads for one
account (``pipeshubsim``), with Zendesk's own rules for who may read a ticket or an article: roles, custom roles and
their ticket access, groups and memberships, organizations that share tickets, suspended and deleted people. The PipesHub
container reaches it as ``https://pipeshubsim.zendesk.com`` and ``https://p1.zdusercontent.com``, the names the connector
calls, through the overlay's TLS front (``docker-compose.integration.zendesk-sim.yml``). This process drives the
simulator's control API on the published port (``ZENDESK_SIM_URL``).

Each test loads an account, creates a connector for it, signs it in, syncs, changes the account and syncs again. After a
sync the simulator's oracle (``/_sim/zendesk/expected``) says who may read every ticket, file and article. Every person of
the account has a PipesHub account with the same email, and PipesHub's own permission query (the one search uses,
``filter_accessible_record_ids``) is asked, for each of them, which records of the connector they may read. The
differences:

  MISSING        a ticket, file or article in the sync's scope has no live record
  STALE          a live record of something gone, or outside the sync's scope (a deleted ticket, a draft)
  DUPLICATE      two live records for one thing
  PARENT         a ticket not under the group, or an article not under the section, Zendesk files it in
  LEAK:<why>     a person may read a record Zendesk does not let them read
  DENIED:<why>   a person may read it in Zendesk but PipesHub refuses them
  SIM_UNMODELED / SIM_SPEC / SIM_CDN_LEAK / SIM_WRONG_HOST  the connector sent a request the simulator does not model, a
                 response broke Zendesk's reference, a bearer token reached the attachment CDN, or a request named
                 another account: the comparison would prove nothing

A difference whose type is in ``KNOWN_GAPS`` is logged and tolerated; any other fails the test. So does an injected fault
that never fired.

What a sync holds (the connector's scope): tickets that are not deleted, files on public comments that are not embedded
images, published articles with no user segment, and their files.
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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
import requests
from connectors.zendesk.sim_instances import (
    CDN_HOST,
    OAUTH,
    OUTSIDER,
    PEOPLE,
    SUBDOMAIN,
    TENANT_HOST,
    desk,
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
    from collections.abc import AsyncGenerator, Callable, Iterable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("zendesk-sim")

CONNECTOR_TYPE = "Zendesk"
SIM_URL_ENV = "ZENDESK_SIM_URL"
APP_CONTAINER_ENV = "ZENDESK_SIM_APP_CONTAINER"
SYNC_TIMEOUT_SEC = int(os.getenv("ZENDESK_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("ZENDESK_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
# More failures than any request of a run makes, so the source never stops refusing.
KEEPS_FAILING = 500
UNASSIGNED = "unassigned_tickets"

# What the connector calls, as the simulator names it in /_sim/calls ("METHOD /route-template").
CALLED: set[str] = set()

# The differences from Zendesk's rules the connector still shows, each strict: a test fails when the difference is gone,
# so the entry is removed with the fix. Maps a difference type to the open pull request that fixes it.
KNOWN_GAPS: dict[str, str] = {
    # A workspace member the connector does not register (no Zendesk account, or an end user none of whose tickets is
    # in the sync) cannot find a public article: the connector grants the whole org, but never links the org's All team
    # to the app (the other team connectors call ensure_team_app_edge). No open pull
    # request fixes it; who may read (everyone, or only the people the connector registers) is an open product
    # question. Pinned strictly by test_tc_zdsim_acl_001.
    "DENIED:article-unregistered": "open question: who may read a public article",
}

# Requests the connector makes that the vendor's document does not declare (Zendesk ignores them): operation ->
# parameter texts. Listed in ZENDESK_SIM_ITS.md; every other spec problem fails the test.
KNOWN_REQUEST_FINDINGS: dict[str, tuple[str, ...]] = {
    "GET /api/v2/help_center/articles/{article_id}/comments": ("'sort_order' is not declared",),
}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, message: str | None = None,
          **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Zendesk's error shape. ``trigger`` takes
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


def throttle(path_regex: str, **trigger: float) -> dict[str, Any]:
    """A 429 with Retry-After: one request, then one more every second."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex},
                            "effect": {"type": "rate_limit", "capacity": 1, "refill_per_s": 1}}
    if trigger:
        rule["trigger"] = trigger
    return rule


def group_filter(operator: str, group_ids: Iterable[int | str]) -> dict[str, Any]:
    """The connector's group filter, saved as the app saves it."""
    return {"sync": {"values": {"group_ids": {"operator": operator, "type": "list",
                                              "value": [str(g) for g in group_ids]}}}}


def date_filter(name: str, start_ms: int | None, end_ms: int | None) -> dict[str, Any]:
    """A created / modified date filter (``name``), saved as the app saves it."""
    operator = "is_after" if end_ms is None else "is_before" if start_ms is None else "is_between"
    return {"sync": {"values": {name: {"operator": operator, "type": "datetime",
                                       "value": {"start": start_ms, "end": end_ms}}}}}


def _group_scope(filters: dict[str, Any] | None) -> tuple[set[str] | None, set[str] | None]:
    """(groups the filter keeps, groups it excludes) from a saved filter; (None, None) when it has none."""
    spec = (((filters or {}).get("sync") or {}).get("values") or {}).get("group_ids") or {}
    if not spec.get("value"):
        return None, None
    ids = {str(v) for v in spec["value"]}
    return (ids, None) if spec.get("operator", "in") == "in" else (None, ids)


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Want:
    kind: str                    # ticket, ticket-file, article, article-file
    group: str | None            # the record group it must sit under (None: not checked)
    readers: set[int] | None     # Zendesk user ids who may read it; None: everyone, Zendesk account or not
    reader_pool: set[int] = field(default_factory=set)


@dataclass
class Scope:
    groups_in: set[str] | None = None        # group filter, "in": ids as strings, UNASSIGNED for groupless tickets
    groups_out: set[str] | None = None       # group filter, "not_in"
    left_out: set[str] = field(default_factory=set)   # external ids a date filter keeps out (named by the test)
    notes: list[str] = field(default_factory=list)


def is_embedded_image(attachment: dict[str, Any]) -> bool:
    return bool(attachment.get("inline")) and str(attachment.get("content_type", "")).startswith("image/")


def _ticket_group_ok(ticket: dict[str, Any], scope: Scope) -> bool:
    gid = str(ticket["group_id"]) if ticket["group_id"] else UNASSIGNED
    if scope.groups_in is not None and gid not in scope.groups_in:
        return False
    return not (scope.groups_out is not None and gid in scope.groups_out)


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, Want]:
    """External record id -> what a complete and correct sync holds for it."""
    live_groups = {g["id"] for g in expected["groups"] if not g["deleted"]}
    wanted: dict[str, Want] = {}
    for t in expected["tickets"]:
        if t["deleted"] or not _ticket_group_ok(t, scope) or str(t["id"]) in scope.left_out:
            continue
        group = f"group_{t['group_id']}" if t["group_id"] in live_groups else None
        readers = set(t["readers"])
        wanted[str(t["id"])] = Want("ticket", group, readers)
        for c in t["comments"]:
            if not c["public"]:
                continue
            for a in c["attachments"]:
                if a["deleted"] or is_embedded_image(a):
                    continue
                wanted[f"ticket_{t['id']}_comment_{c['id']}_attachment_{a['id']}"] = Want("ticket-file", group, readers)
    for a in expected["articles"]:
        if a["draft"] or a["segmented"] or f"article_{a['id']}" in scope.left_out:
            continue
        group = f"section_{a['section_id']}"
        readers = None if a["public"] else set(a["readers"])
        wanted[f"article_{a['id']}"] = Want("article", group, readers, set(a["readers"]))
        for f in a["attachments"]:
            if not f["inline"]:
                wanted[f"article_{a['id']}_attachment_{f['id']}"] = Want("article-file", group, readers,
                                                                          set(a["readers"]))
    return wanted


def _reason(want: Want, user: dict[str, Any] | None, finds: bool, registered: bool = True) -> str:
    """The Zendesk rule that explains a difference between PipesHub and Zendesk for one person and one record."""
    side = "LEAK" if finds else "DENIED"
    if want.kind.startswith("article"):
        return f"{side}:article" if registered or finds else "DENIED:article-unregistered"
    if user is None:
        return f"{side}:not-a-zendesk-user"
    if finds and (not user["active"] or user["suspended"]):
        return "LEAK:inactive-user"
    if finds and user["role"] in ("agent", "admin"):
        return "LEAK:agent"
    return f"{side}:{'agent' if user['role'] in ('agent', 'admin') else 'end-user'}"


def _by_email(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Email -> the Zendesk user holding it now (an address that passed to a new account is the new one's)."""
    out: dict[str, dict[str, Any]] = {}
    for u in sorted(expected["users"], key=lambda x: (x["active"], x["id"])):
        if u["email"]:
            out[u["email"].lower()] = u
    return out


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed email may
    read) and Zendesk's rules (``expected``, the oracle)."""
    wanted = wanted_records(expected, scope)
    by_email = _by_email(expected)
    # The people the connector registers: active staff, and end users who may read a ticket of the sync.
    ticket_readers = {uid for w in wanted.values() if w.kind == "ticket" for uid in (w.readers or ())}
    by_ext: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_ext[str(record["externalRecordId"])].append(record)
    problems: list[dict[str, Any]] = []
    for ext, want in wanted.items():
        found = by_ext.get(ext, [])
        if not found:
            problems.append({"type": "MISSING", "kind": want.kind, "record": ext})
            continue
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(found)})
        if want.group is not None and found[0].get("externalGroupId") != want.group:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": want.group})
        ids = {r["id"] for r in found}
        for email, seen in readable.items():
            user = by_email.get(email.lower())
            if want.readers is None:
                # A public article: every signed-in Zendesk person it lists and every workspace member without one.
                may = user is None or user["id"] in want.reader_pool
            else:
                may = user is not None and user["id"] in want.readers
            finds = bool(ids & seen)
            if finds != may:
                registered = user is not None and (
                    user["id"] in ticket_readers or (user["role"] in ("agent", "admin") and user["active"]
                                                     and not user["suspended"]))
                problems.append({"type": _reason(want, user, finds, registered), "record": ext,
                                 "name": found[0].get("recordName"), "user": email})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName")} for r in found]
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


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class ZendeskSim:
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
        self.call("DELETE", "/_sim/zendesk/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/zendesk/behaviours", behaviours)
        self.call("PUT", "/_sim/zendesk/scenario", scenario)
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/zendesk/stats/reset")

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/zendesk/expected")

    def stats(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/zendesk/stats")

    def calls(self) -> dict[str, int]:
        """Route ("GET /api/v2/users") -> how many times the connector called it, as the simulator names its routes."""
        return self.stats()["calls"]


class ZendeskWorld:
    """One test's account, its connector, and the comparison between them."""

    def __init__(self, sim: ZendeskSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.scenario: dict[str, Any] = {}
        self.scope = Scope()
        self.refresh_token = OAUTH["refresh_token"]
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.scenario = scenario
        self.sim.load(scenario, behaviours)

    def create_connector(self, *, client_id: str | None = None, client_secret: str | None = None,
                         subdomain: str = SUBDOMAIN) -> str:
        oauth = self.scenario.get("oauth") or OAUTH
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"zendesk-sim-{uuid.uuid4().hex[:8]}", scope="team",
            auth_type="OAUTH",
            config={"auth": {"subdomain": subdomain,
                             "authorizeUrl": f"https://{subdomain}.zendesk.com/oauth/authorizations/new",
                             "tokenUrl": f"https://{subdomain}.zendesk.com/oauth/tokens",
                             "clientId": client_id or oauth["client_id"],
                             "clientSecret": client_secret or oauth["client_secret"]}},
        )
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        return self.connector_id

    async def sign_in(self, *, access_token: str | None = None, refresh_token: str | None = None) -> tuple[str, str]:
        """Give the connector the account's own token pair, stored as the sign-in would (the Salesforce suite does the
        same). The simulator issued the pair when it loaded the account."""
        oauth = self.scenario.get("oauth") or OAUTH
        access = access_token or oauth["access_token"]
        refresh = refresh_token or oauth["refresh_token"]
        await inject_connector_credentials(self.connector_id, access, refresh)
        self.refresh_token = refresh
        return access, refresh

    async def start(self, scenario: dict[str, Any], *, behaviours: dict[str, Any] | None = None,
                    filters: dict[str, Any] | None = None, enable: bool = True) -> str | None:
        """Load ``scenario``, create a connector signed in as the account's OAuth user, and (unless ``enable`` is False)
        turn it on; return the outcome of the run that starts."""
        self.load(scenario, behaviours)
        self.scope = Scope()
        self.scope.groups_in, self.scope.groups_out = _group_scope(filters)
        self.create_connector()
        await self.sign_in()
        if filters:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        """Turn the connector on; the outcome of the run that starts, or REFUSED when PipesHub would not turn it on."""
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True), refusable=True)
        stats = self.sim.stats()
        assert stats["total_calls"] > 0, (
            f"the connector never called the simulator (outcome {outcome}, stats {stats}): it reached another host")
        return outcome

    async def restart(self, filters: dict[str, Any] | None = None) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled
        connector); return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
            self.scope.groups_in, self.scope.groups_out = _group_scope(filters)
        return await self.enable()

    def remove_connector(self) -> None:
        if os.getenv("ZENDESK_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.call("DELETE", "/_sim/faults")
            self.sim.call("DELETE", "/_sim/zendesk/behaviours")
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

    def key(self, kind: str, name: str) -> int:
        return int(self.sim.expected()["keys"][kind][name])

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the account, in order (``/_sim/zendesk/ops``; ids may be the account's keys)."""
        return self.sim.call("POST", "/_sim/zendesk/ops", list(ops))["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/zendesk/behaviours", values)

    def defer(self, contains: str, ops: list[dict[str, Any]], *, after: int = 1) -> None:
        """Apply ``ops`` just before the ``after``-th request whose path contains ``contains``: a change landing between
        two pages of a listing."""
        self.sim.call("POST", "/_sim/zendesk/deferred", {"contains": contains, "after": after, "ops": ops})

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> int:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing. Returns how many fired."""
        fired = self.sim.call("GET", "/_sim/faults/log") or []
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"
        return len(fired)

    def token_for(self, ticket_key: str | None = None) -> str:
        return (self.scenario.get("oauth") or OAUTH)["access_token"]

    # ---- PipesHub -------------------------------------------------------------------------------------

    def _connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    def connector(self) -> dict[str, Any]:
        """The connector's document (its status, last outcome, whether another full sync is owed)."""
        return self._connector()

    async def pending_full_sync(self) -> bool:
        """Whether the platform owes this connector another full sync (the last one left part of the source unread)."""
        app = await self.graph.get_document(self.connector_id, CollectionNames.APPS.value)
        assert app is not None, f"the app node of connector {self.connector_id} is missing"
        return bool(app.get(ConnectorStateKeys.PENDING_FULL_SYNC))

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
        return await self._await_end(before)

    async def _await_end(self, before: int) -> str | None:
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
            self.connector_id, full_sync=full, connector_name=CONNECTOR_TYPE))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "version": r.get("version"), "webUrl": r.get("webUrl")}
                for r in rows or [] if not r.get("isDeleted")]

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records() if r["externalRecordId"] == external_id), None)

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

    async def stream(self, external_id: str) -> bytes:
        """What PipesHub streams for a record: the bytes of the HTTP answer; raises on an HTTP error."""
        record = await self.record_for(external_id)
        assert record, f"no live record of {external_id}"
        resp = self.client.stream_record(record["id"], timeout=120)
        return b"".join(resp.iter_content(65536))

    async def stream_groups(self, external_id: str) -> list[dict[str, Any]]:
        return json.loads(await self.stream(external_id))["block_groups"]

    # ---- the comparison --------------------------------------------------------------------------------

    def source_problems(self) -> list[dict[str, Any]]:
        """What the simulator saw wrong in the connector's requests: nothing modelled is missing, nothing it sent
        broke Zendesk's reference, no bearer token reached the CDN, no request named another account."""
        problems = [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/zendesk/unmodeled") or []]
        specs = self.sim.call("GET", "/_sim/zendesk/spec-problems") or []
        problems += [{"type": "SIM_SPEC", **e} for e in spec_findings_only(specs)]
        stats = self.sim.stats()
        problems += [{"type": "SIM_CDN_LEAK", "path": p} for p in stats["cdn_auth_leaks"]]
        problems += [{"type": "SIM_WRONG_HOST", "host": h} for h in stats["wrong_hosts"]]
        return problems

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        return compare(expected, self.scope, records, readable) + self.source_problems()

    async def assert_matches_source(self, label: str, *, allow_empty: bool = False) -> None:
        """Every probed person reads exactly the records Zendesk lets them read, but for KNOWN_GAPS, and nothing is
        missing, stale, stored twice or filed under the wrong parent."""
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
            f"{label}: PipesHub differs from Zendesk in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}")
        if not allow_empty:
            assert wanted_records(self.sim.expected(), self.scope), f"{label}: the account holds nothing to compare"

    def assert_gap(self, label: str, problems: list[dict[str, Any]], gap: str) -> None:
        """A strict known gap: the difference ``gap`` is present (its fix is not merged); the test fails when it is gone."""
        assert any(p["type"] == gap for p in problems), f"{label}: the known gap {gap} is gone; remove it ({KNOWN_GAPS})"


# ---- fixtures --------------------------------------------------------------------------------------------


def _app_container() -> str:
    """The PipesHub container the suite runs against: ``ZENDESK_SIM_APP_CONTAINER``, else the one container the
    compose service ``pipeshub-ai`` has on this host (when several stacks run, name it)."""
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
def zendesk_front() -> dict[str, str]:
    """The tenant host and both CDN hosts resolve, inside the PipesHub container, to the simulator's TLS front and to
    nothing else: checked before any connector exists, so nothing of this suite can reach the real Zendesk. Fails,
    never skips: a stack without the front must not run the suite."""
    if shutil.which("docker") is None:
        pytest.fail("the docker CLI is needed to check where the Zendesk names resolve in the PipesHub container")
    container = _app_container()
    front = {"zendesk-tls": resolved_in_container(container, "zendesk-tls")}
    for host in (TENANT_HOST, CDN_HOST, f"{SUBDOMAIN}.zdusercontent.com"):
        front[host] = resolved_in_container(container, host)
        assert front[host] == front["zendesk-tls"], (
            f"{host} resolves to {front[host]} in {container}, not to the simulator's front {front['zendesk-tls']}: "
            "a sync would reach the real Zendesk")
    return front


@pytest.fixture(scope="session")
def zendesk_sim(zendesk_front: dict[str, str]) -> ZendeskSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Zendesk simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = ZendeskSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the Zendesk user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def zendesk_sim_people(
    zendesk_sim: ZendeskSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the accounts, made before any connector so its
    first sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the
    org again."""
    pipeshub_client._ensure_access_token()
    zendesk_sim.load(desk(custom_roles=True))
    known = {u["email"].lower() for u in zendesk_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the account without a PipesHub account: {known}"
    assert OUTSIDER in PEOPLE
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def zendesk_world(
    zendesk_sim: ZendeskSim,
    zendesk_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ZendeskWorld, None]:
    world = ZendeskWorld(zendesk_sim, pipeshub_client, graph_provider, zendesk_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()


def authorization_code(world: ZendeskWorld) -> dict[str, str]:
    """What a person's browser does with the app's authorize route: it gives Zendesk's URL, the (simulated) Zendesk sends
    the browser back with a code, and the app's callback route exchanges it. Returns the callback's answer."""
    started = world.client._request_json("GET", f"/api/v1/connectors/{world.connector_id}/oauth/authorize")
    url = urlparse(started["authorizationUrl"])
    assert url.hostname == TENANT_HOST and url.path == "/oauth/authorizations/new", started["authorizationUrl"]
    # The browser reaches the simulator on its published port; the app's token request goes through the front.
    landed = requests.get(f"{world.sim.base_url}{url.path}?{url.query}", allow_redirects=False, timeout=30,
                          headers={"Host": TENANT_HOST})
    assert landed.status_code in (302, 303, 307), landed.text[:200]
    back = parse_qs(urlparse(landed.headers["Location"]).query)
    return world.client._request_json("GET", "/api/v1/connectors/oauth/callback",
                                      params={"code": back["code"][0], "state": back["state"][0]})
