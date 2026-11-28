# pyright: ignore-file

"""Drupal Wiki against the connectors-simulator: a wiki the test owns, and who may read what.

The simulator answers Drupal Wiki's REST API and the two operations of its internal GraphQL API with the vendor's
paging, personal access token, spaces of every type and access status, groups, pages, shares, attachments and the
knobs for what the documentation leaves open. The PipesHub container reaches it by its compose alias
(``DRUPAL_WIKI_SIM_CONNECTOR_URL``, ``http://drupal-wiki-sim.internal``): a private address, which PipesHub allows for a
self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on. This process drives the simulator through its control
API on the published port (``DRUPAL_WIKI_SIM_URL``).

Each test puts the seed wiki back, creates a connector for it, syncs, changes the wiki, syncs again. After a sync the
simulator's oracle (``/_sim/drupalwiki/expected``: Drupal Wiki's own read rules, computed from its tables) says which
spaces, pages and attachments a correct sync holds and who may read each. Every person of the wiki has a PipesHub
account with the same email, and PipesHub's own permission query (``filter_accessible_record_ids``, what search relies
on) is asked, for each of them, which records of the connector they may read. The differences:

  MISSING        a page or attachment in the sync's scope has no live record
  STALE          a record, still linked to its space, of something gone, or outside the sync's scope (a deleted page, a trashed one, a
                 personal workspace, a space the filter leaves out); LEAK:stale-record when anyone can still read it
  DUPLICATE      two live records for one thing
  PARENT         a record not under the space, page or attachment's page Drupal Wiki files it under
  MISSING-GROUP / STALE-GROUP   a space that should (not) be a record group
  LEAK:<why>     a person may read a record Drupal Wiki does not let them read
  DENIED:<why>   a person may read it in Drupal Wiki but PipesHub refuses them
  SIM_UNMODELED  the connector sent a request the simulator does not model: the comparison would prove nothing

``<why>`` is the Drupal Wiki rule that explains the difference (``_reason``). A difference whose type is in
``KNOWN_GAPS`` is logged and tolerated; any other fails the test. So does an injected fault that never fired.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests
from connectors.drupal_wiki.sim_instances import PEOPLE, TOKEN
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

logger = logging.getLogger("drupal-wiki-sim")

CONNECTOR_TYPE = "Drupal Wiki"
SIM_URL_ENV = "DRUPAL_WIKI_SIM_URL"
CONNECTOR_URL = os.getenv("DRUPAL_WIKI_SIM_CONNECTOR_URL", "http://drupal-wiki-sim.internal")
SYNC_TIMEOUT_SEC = int(os.getenv("DRUPAL_WIKI_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("DRUPAL_WIKI_SIM_SETTLE_TIMEOUT_SEC", "30"))
SETTLE_POLL_SEC = 5
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
PAGE_PREFIX, ATTACHMENT_PREFIX = "page:", "attachment:"

@dataclass(frozen=True)
class Gap:
    """A difference from Drupal Wiki's rules the connector still shows, narrowed to the exact case it stands for:
    the open pull request that fixes it, and the records, people, spaces (record groups) or names it covers. A
    difference of the same type outside those is a new defect and fails. A constraint left out does not narrow."""

    pr: str
    records: frozenset[str] | None = None
    users: frozenset[str] | None = None
    spaces: frozenset[str] | None = None
    names: frozenset[str] | None = None

    def __post_init__(self) -> None:
        assert re.fullmatch(r"#\d+", self.pr), f"a known gap names its pull request: {self.pr}"
        assert any(v is not None for v in (self.records, self.users, self.spaces, self.names)), \
            "a known gap names the records, people, spaces or names it stands for"

    def covers(self, problem: dict[str, Any]) -> bool:
        for field_name, key in (("records", "record"), ("users", "user"), ("spaces", "space"), ("names", "name")):
            allowed = getattr(self, field_name)
            if allowed is not None and problem.get(key) not in allowed:
                return False
        return True


def gap(pr: str, *, records: Iterable[str] | None = None, users: Iterable[str] | None = None,
        spaces: Iterable[object] | None = None, names: Iterable[str] | None = None) -> Gap:
    def fs(values: Iterable[object] | None) -> frozenset[str] | None:
        return None if values is None else frozenset(str(v) for v in values)

    return Gap(pr, fs(records), fs(users), fs(spaces), fs(names))


def apply_known(problems: list[dict[str, Any]], known: dict[str, Gap]) -> tuple[list[dict[str, Any]], list[str]]:
    """(the differences no known gap covers, the known gaps that covered nothing). A gap that covers nothing is
    fixed, and its entry has to go."""
    failing = [p for p in problems if not (p["type"] in known and known[p["type"]].covers(p))]
    unused = sorted(t for t, g in known.items() if not any(p["type"] == t and g.covers(p) for p in problems))
    return failing, unused


# The differences present in every comparison, each with the open pull request that fixes it. A test names the others
# for itself (``assert_matches_source(known=...)``); either way the call fails once the gap no longer shows.
SEED_SPECIAL_SPACES = frozenset({"Alice's workspace", "Trash", "System", "News drafts", "Desktop"})
KNOWN_GAPS: dict[str, Gap] = {
    # Every space type is synced, the trash and personal workspaces included; the wiki reads them to nobody else.
    "STALE-GROUP": gap("#5799", names=SEED_SPECIAL_SPACES),
}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Drupal Wiki's ProblemResponse shape.
    ``trigger`` takes the core's fields (``after_n``, ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "status", "code": code}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def space_filter(operator: str, space_ids: Iterable[int]) -> dict[str, Any]:
    """The connector's space filter, saved as the app saves it."""
    return {"sync": {"values": {"space_ids": {"operator": operator, "type": "list",
                                              "value": [str(s) for s in space_ids]}}}}


def extension_filter(operator: str, extensions: Iterable[str]) -> dict[str, Any]:
    """The attachment type filter (``in``: only these; ``not_in``: all but these)."""
    return {"sync": {"values": {"file_extensions": {"operator": operator, "type": "list",
                                                    "value": list(extensions)}}}}


def indexing_filter(**enabled: bool) -> dict[str, Any]:
    """The indexing filters (``pages``, ``attachments``): False leaves the records stored but not indexed."""
    return {"indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v} for k, v in enabled.items()}}}


def merge_filters(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        for section, body in part.items():
            out.setdefault(section, {"values": {}})["values"].update(body["values"])
    return out


@dataclass
class Scope:
    """What the connector's filters keep: spaces (as the simulator's ``include`` / ``exclude``) and attachment types."""

    include: list[int] = field(default_factory=list)
    exclude: list[int] = field(default_factory=list)
    extensions_in: set[str] | None = None
    extensions_out: set[str] | None = None
    # False when the page tree cannot be read (GraphQL refused): the parent of a page is then whatever was stored.
    parents: bool = True

    def allows_extension(self, file_name: str | None) -> bool:
        ext = file_name.rsplit(".", 1)[-1].lower() if file_name and "." in file_name else None
        if self.extensions_in is not None:
            return ext in self.extensions_in
        return not (self.extensions_out is not None and ext in self.extensions_out)


def scope_of(filters: dict[str, Any] | None, **overrides: Any) -> Scope:
    """The scope a saved filter gives."""
    values = (((filters or {}).get("sync") or {}).get("values")) or {}
    scope = Scope(**overrides)
    spaces = values.get("space_ids") or {}
    if spaces.get("value"):
        ids = [int(v) for v in spaces["value"]]
        if spaces.get("operator", "in") == "in":
            scope.include = ids
        else:
            scope.exclude = ids
    ext = values.get("file_extensions") or {}
    if ext.get("value"):
        names = {str(v).lower().lstrip(".") for v in ext["value"]}
        if ext.get("operator", "in") == "in":
            scope.extensions_in = names
        else:
            scope.extensions_out = names
    return scope


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Want:
    kind: str                    # page, attachment
    group: str                   # the record group (space) it must sit under
    parent: str | None           # external id of the page it must hang from
    readers: set[int]            # wiki user ids who may read it
    page: int                    # the page (an attachment's own page)
    shared_into: list[int] = field(default_factory=list)


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, Want]:
    """External record id -> what a complete and correct sync holds for it."""
    wanted: dict[str, Want] = {}
    for pid, page in expected["pages"].items():
        parent = f"{PAGE_PREFIX}{page['parent']}" if page["parent"] is not None else None
        wanted[f"{PAGE_PREFIX}{pid}"] = Want("page", str(page["space"]), parent, set(page["readers"]), int(pid),
                                             list(page["shared_into"]))
    for aid, att in expected["attachments"].items():
        page = expected["pages"].get(str(att["page"]))
        if page is None or not scope.allows_extension(att["fileName"]):
            continue
        wanted[f"{ATTACHMENT_PREFIX}{aid}"] = Want("attachment", str(page["space"]), f"{PAGE_PREFIX}{att['page']}",
                                                   set(page["readers"]), int(att["page"]), list(page["shared_into"]))
    return wanted


def _reason(want: Want, user: dict[str, Any] | None, finds: bool, expected: dict[str, Any]) -> str:
    """The Drupal Wiki rule that explains a difference between PipesHub and the wiki for one person and one record."""
    side = "LEAK" if finds else "DENIED"
    if user is None:
        return f"{side}:no-wiki-account"
    if finds:
        if user["status"] != "ENABLED":
            return "LEAK:disabled-user"
        return "LEAK:other"
    if user["status"] != "ENABLED":
        return "DENIED:disabled-user"
    space = expected["spaces"].get(want.group, {})
    if want.shared_into and any(user["id"] in expected["spaces"].get(str(s), {}).get("readers", ())
                                for s in want.shared_into):
        return "DENIED:shared-page"
    return "DENIED:private-space" if space.get("accessStatus") == "PRIVATE" else "DENIED:open-space"


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]], readable: dict[str, set[str]],
            group_names: list[str] | None = None, unlinked_groups: int = 0) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, each with its parent's external id, the
    record ids each probed email may read, and the names of the record groups) and the wiki's rules (``expected``)."""
    wanted = wanted_records(expected, scope)
    by_email = {u["email"].lower(): u for u in expected["users"] if u["email"]}
    by_ext: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_ext[str(record["externalRecordId"])].append(record)
    problems: list[dict[str, Any]] = []
    for ext, want in wanted.items():
        found = by_ext.get(ext, [])
        if not found:
            problems.append({"type": "MISSING", "kind": want.kind, "record": ext, "space": want.group})
            continue
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(found), "space": want.group})
        if found[0].get("externalGroupId") != want.group:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": want.group, "space": want.group})
        elif (scope.parents or want.kind == "attachment") and found[0].get("parent") != want.parent:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("parent"), "should": want.parent,
                             "space": want.group})
        ids = {r["id"] for r in found}
        for email, seen in readable.items():
            user = by_email.get(email.lower())
            may = user is not None and user["id"] in want.readers
            finds = bool(ids & seen)
            if finds != may:
                problems.append({"type": _reason(want, user, finds, expected), "record": ext,
                                 "name": found[0].get("recordName"), "user": email, "space": want.group})
    for ext, found in by_ext.items():
        if ext in wanted:
            continue
        found = [r for r in found if r.get("linked") is not False]
        if not found:
            continue
        problems += [{"type": "STALE", "record": ext, "name": r.get("recordName"),
                      "space": r.get("externalGroupId")} for r in found]
        # Something the wiki no longer has, or outside the sync's scope, is read by nobody.
        ids = {r["id"] for r in found}
        problems += [{"type": "LEAK:stale-record", "record": ext, "name": found[0].get("recordName"), "user": email,
                      "space": found[0].get("externalGroupId")}
                     for email, seen in readable.items() if ids & seen]
    if group_names is not None:
        have, want_names = Counter(group_names), Counter(s["name"] for s in expected["spaces"].values())
        problems += [{"type": "MISSING-GROUP", "name": n} for n in (want_names - have)]
        # A full sync unlinks a space that is gone or left out (its node stays, with no edge to the app): that many
        # of the extra groups are removed, the ones that are not the seed's special spaces first.
        extra = sorted((have - want_names).elements(), key=lambda n: (n in SEED_SPECIAL_SPACES, n))
        problems += [{"type": "STALE-GROUP", "name": n} for n in extra[unlinked_groups:]]
    return problems


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class DrupalSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        # The simulator closes an idle connection after a few seconds; a reused one fails with RemoteDisconnected.
        self.http.headers["Connection"] = "close"

    def call(self, method: str, path: str, body: object = None) -> Any:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.content else None

    def load(self, knobs: dict[str, Any] | None = None) -> None:
        """The seed wiki again, with its default knobs, no faults, no triggers and no recorded requests."""
        self.call("DELETE", "/_sim/faults")
        self.call("POST", "/_sim/reset")
        self.call("DELETE", "/_sim/drupalwiki/triggers")
        self.call("DELETE", "/_sim/drupalwiki/gaps")
        if knobs:
            self.call("PUT", "/_sim/drupalwiki/knobs", knobs)
        self.call("POST", "/_sim/calls/reset")

    def expected(self, scope: Scope | None = None) -> dict[str, Any]:
        query = []
        if scope and scope.include:
            query.append("include=" + ",".join(str(i) for i in scope.include))
        if scope and scope.exclude:
            query.append("exclude=" + ",".join(str(i) for i in scope.exclude))
        return self.call("GET", "/_sim/drupalwiki/expected" + (f"?{'&'.join(query)}" if query else ""))

    def calls(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/calls")


class DrupalWorld:
    """One test's wiki, its connector, and the comparison between them."""

    def __init__(self, sim: DrupalSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.scope = Scope()
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    async def start(self, *, knobs: dict[str, Any] | None = None, token: str = TOKEN,
                    filters: dict[str, Any] | None = None, enable: bool = True, base_url: str | None = None,
                    scope: Scope | None = None, reset: bool = True) -> str | None:
        """Put the seed wiki back (with ``knobs``; not when ``reset`` is False, for a second connector on the same
        wiki), create a connector that signs in with ``token``, and (unless ``enable`` is False) turn it on; return the
        outcome of the run that starts."""
        if reset:
            self.sim.load(knobs)
        self.scope = scope or scope_of(filters)
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"drupal-wiki-sim-{uuid.uuid4().hex[:8]}",
            scope="team", config={"auth": {"baseUrl": base_url or CONNECTOR_URL, "apiToken": token}},
            auth_type="API_TOKEN",
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
        if outcome != REFUSED:
            assert self.sim.calls()["total"] > 0, (
                f"the connector never called the simulator at {CONNECTOR_URL} (outcome {outcome})")
        return outcome

    async def restart(self, filters: dict[str, Any] | None = None, scope: Scope | None = None) -> str | None:
        """Turn the connector off and on, with new filters saved the way the app saves them (on a disabled
        connector); return the outcome of the run enabling starts."""
        self.client.toggle_sync(self.connector_id, enable=False)
        await self._wait_idle()
        if filters is not None:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
            self.scope = scope or scope_of(filters, parents=self.scope.parents)
        return await self.enable()

    def set_token(self, token: str) -> None:
        """Save a new token on the connector (it must be disabled)."""
        self.client.update_connector_config(self.connector_id, {"auth": {"baseUrl": CONNECTOR_URL, "apiToken": token}})

    def remove_connector(self) -> None:
        if os.getenv("DRUPAL_WIKI_SIM_KEEP_CONNECTOR") == "1":
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

    def mutate(self, *ops: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Changes to the wiki, in order: ``("page_move", {"id": 7, "space": 3})`` (``/_sim/drupalwiki/mutate``)."""
        body = [{"op": op, "args": args} for op, args in ops]
        return self.sim.call("POST", "/_sim/drupalwiki/mutate", body)

    def knobs(self, **values: Any) -> None:
        self.sim.call("PUT", "/_sim/drupalwiki/knobs", values)

    def triggers(self, *triggers: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/drupalwiki/triggers", list(triggers))

    def advance_clock(self, seconds: int) -> None:
        self.sim.call("POST", "/_sim/drupalwiki/clock", {"advance_s": seconds})

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> None:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never reached
        proves nothing."""
        fired = self.sim.call("GET", "/_sim/faults/log") or []
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

    async def records(self, *, parents: bool = True) -> list[dict[str, Any]]:
        """The connector's live records, each with the external id of the record it hangs from (a graph query each,
        so only when asked for)."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        linked = ({str(r.get("id") or r.get("_key")) for r in
                   await self.graph.fetch_records_by_type(self.connector_id, "", scoped=True)} if parents else None)
        out = []
        for r in rows or []:
            if r.get("isDeleted"):
                continue
            ext = r.get("externalRecordId")
            out.append({"id": r.get("id") or r.get("_key"), "externalRecordId": ext,
                        "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                        "version": r.get("version"), "indexingStatus": r.get("indexingStatus"),
                        "externalRevisionId": r.get("externalRevisionId"), "webUrl": r.get("webUrl"),
                        "parent": (await self.graph.get_record_parent_external_id(self.connector_id, ext)) if parents
                        else None,
                        # A full sync unlinks what the wiki no longer has (the node stays, with no edge to its space):
                        # that is how the product removes it, and nobody reads it. None: not asked.
                        "linked": (str(r.get("id") or r.get("_key")) in linked) if linked is not None else None})
        return out

    async def record_for(self, external_id: str) -> dict[str, Any] | None:
        return next((r for r in await self.records(parents=False) if r["externalRecordId"] == external_id), None)

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
        records = await self.records(parents=False)
        ext = {r["id"]: str(r["externalRecordId"]) for r in records}
        readable = await self.readable(records)
        return {"records": set(ext.values()), **{email: {ext[i] for i in seen} for email, seen in readable.items()}}

    def oracle_holdings(self) -> dict[str, set[str]]:
        """What the wiki lets each probed person read now, as external ids."""
        expected = self.sim.expected(self.scope)
        by_id = {u["id"]: u["email"].lower() for u in expected["users"] if u["email"]}
        out: dict[str, set[str]] = {email: set() for email in self.people}
        for ext, want in wanted_records(expected, self.scope).items():
            for uid in want.readers:
                if by_id.get(uid) in {e.lower() for e in out}:
                    out[next(e for e in out if e.lower() == by_id[uid])].add(ext)
        return out

    async def assert_kept_not_overshared(self, before: dict[str, set[str]], label: str) -> dict[str, set[str]]:
        """After a run that could not read everything: nothing held before was removed, and no one gained a read the
        wiki does not give them now (a part of a change the run did read is not a gain). Returns what is held now."""
        after = await self.holdings()
        allowed = self.oracle_holdings()
        lost = {who: sorted(before[who] - after.get(who, set())) for who in before if before[who] - after.get(who, set())}
        over = {who: sorted(after[who] - before[who] - allowed.get(who, set())) for who in before
                if who != "records" and after.get(who, set()) - before[who] - allowed.get(who, set())}
        assert not lost and not over, f"{label}: PipesHub lost {lost} and over-shared {over}"
        return after

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
        expected = self.sim.expected(self.scope)
        records = await self.records()
        readable = await self.readable(records)
        names = list(await self.graph.fetch_record_group_names(self.connector_id))
        unlinked = (await self.graph.count_record_groups(self.connector_id)
                    - await self.graph.count_record_groups(self.connector_id, scoped=True))
        problems = compare(expected, self.scope, records, readable, names, unlinked_groups=unlinked)
        problems += [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/drupalwiki/gaps") or []]
        return problems

    async def assert_matches_source(self, label: str, *, known: dict[str, Gap] | None = None) -> None:
        """Every probed person reads exactly the records the wiki lets them read, but for KNOWN_GAPS and the gaps
        this call names (type -> Gap: the open pull request, and the exact records, people or spaces), and nothing is
        missing, stale, stored twice or filed under the wrong space or page. A gap that covers nothing fails the
        call: it is fixed."""
        gaps = {**KNOWN_GAPS, **(known or {})}
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            problems = await self.differences()
            failing, unused = apply_known(problems, gaps)
            if not failing or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        tolerated = Counter(p["type"] for p in problems if p not in failing)
        if tolerated:
            logger.info("%s: known gaps tolerated: %s", label, dict(tolerated))
        assert not failing, (
            f"{label}: PipesHub differs from Drupal Wiki in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}"
        )
        mine = [t for t in unused if t in (known or {})]
        assert not mine, f"{label}: the known gap(s) {mine} no longer show ({[gaps[t].pr for t in mine]}): remove them"


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drupal_wiki_sim() -> DrupalSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Drupal Wiki simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = DrupalSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the wiki user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def drupal_wiki_sim_people(
    drupal_wiki_sim: DrupalSim, pipeshub_client: PipeshubClient, users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the wiki, made before any connector so its first
    sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the org
    again."""
    pipeshub_client._ensure_access_token()
    drupal_wiki_sim.load()
    known = {u["email"].lower() for u in drupal_wiki_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the wiki without an account: {known}"
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def drupal_world(
    drupal_wiki_sim: DrupalSim,
    drupal_wiki_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[DrupalWorld, None]:
    world = DrupalWorld(drupal_wiki_sim, pipeshub_client, graph_provider, drupal_wiki_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
