# pyright: ignore-file

"""Zammad against the connectors-simulator: an installation the test owns, and who may read what.

The simulator answers Zammad's REST API (6.4.1 and 6.5) with its API tokens, groups and their access levels, roles,
organisations, tickets, articles, attachments, links and the knowledge base. The PipesHub container reaches it by its
compose service name (``ZAMMAD_SIM_CONNECTOR_URL``, ``http://zammad-sim``): a private address, which PipesHub allows
for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on. This process drives the simulator through its
control API on the published port (``ZAMMAD_SIM_URL``).

Each test loads an installation, creates a connector for it, syncs, changes the installation and syncs again. After a
sync the simulator's oracle (``/_sim/zammad/expected``: Zammad's own ticket, article, group, role and knowledge-base
rules, ported from its source) says who may read every ticket, attachment and answer. Every person of the
installation has a PipesHub account with the same email, and PipesHub's own permission query (the one search uses,
``filter_accessible_record_ids``) is asked, for each of them, which records of the connector they may read. The
differences:

  MISSING        a ticket, attachment or answer in the sync's scope has no live record
  STALE          a live record of something gone, or outside the sync's scope (a deactivated or excluded group)
  DUPLICATE      two live records for one thing
  PARENT         a record not under the group or category Zammad files it in
  LEAK:<why>     a person may read a record Zammad does not let them read
  DENIED:<why>   a person may read it in Zammad but PipesHub refuses them
  SIM_UNMODELED / SIM_SPEC  the connector sent a request the simulator does not model, or a response broke Zammad's
                 reference: the comparison would prove nothing

``<why>`` is the Zammad rule that explains the difference (``_reason``). A difference whose type is in
``KNOWN_GAPS`` is logged and tolerated; any other fails the test. So does an injected fault that never fired.

The scope of a sync: tickets the token's search lists (``tokens[<name>].search_ticket_ids``) in groups that are
active, named, listed to the token (6.4.1 lists groups to ``admin.group`` only) and allowed by the connector's group
filter; their attachments, except a System article's (the connector skips those); the answers the token's
``knowledge_bases/init`` lists, and their attachments.
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
from connectors.zammad.sim_instances import PEOPLE, TOKEN, TOKENS, base_instance
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

logger = logging.getLogger("zammad-sim")

CONNECTOR_TYPE = "Zammad"
SIM_URL_ENV = "ZAMMAD_SIM_URL"
CONNECTOR_URL = os.getenv("ZAMMAD_SIM_CONNECTOR_URL", "http://zammad-sim")
SYNC_TIMEOUT_SEC = int(os.getenv("ZAMMAD_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("ZAMMAD_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
# Oracle sender ids (db/seeds/ticket_article_senders.rb): 1 Agent, 2 Customer, 3 System.
SYSTEM_SENDER = 3

# The differences from Zammad's rules the connector is still allowed to show. Zammad lets a customer read their own
# tickets and, when their primary organisation is shared, its tickets; PipesHub grants customers nothing yet. Remove
# an entry once the connector grants it, so a regression fails again.
KNOWN_GAPS = frozenset({"DENIED:customer-own-ticket", "DENIED:customer-shared-org"})


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Zammad's error shape. ``trigger`` takes
    the core's fields (``after_n``, ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "status", "code": code}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def group_filter(operator: str, group_ids: Iterable[int]) -> dict[str, Any]:
    """The connector's group filter, saved as the app saves it."""
    return {"sync": {"values": {"group_ids": {"operator": operator, "type": "list",
                                              "value": [str(g) for g in group_ids]}}}}


def _group_scope(filters: dict[str, Any] | None) -> tuple[set[int] | None, set[int] | None]:
    """(groups the filter keeps, groups it excludes) from a saved filter; (None, None) when it has none."""
    spec = (((filters or {}).get("sync") or {}).get("values") or {}).get("group_ids") or {}
    if not spec.get("value"):
        return None, None
    ids = {int(v) for v in spec["value"]}
    return (ids, None) if spec.get("operator", "in") == "in" else (None, ids)


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Want:
    kind: str                    # ticket, ticket-file, answer, answer-file
    group: str                   # the record group it must sit under
    readers: set[int]            # Zammad user ids who may read it
    ticket: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    internal: bool = False       # an attachment of an internal article


@dataclass
class Scope:
    token: str = "pipeshub"
    groups_in: set[int] | None = None
    groups_out: set[int] | None = None
    notes: list[str] = field(default_factory=list)


def group_in_scope(group: dict[str, Any], token: dict[str, Any], scope: Scope) -> bool:
    """The connector syncs the groups the token can list that are active and named, and the filter allows."""
    if not group["active"] or not group["name"] or group["id"] not in token["groups_listed"]:
        return False
    if scope.groups_in is not None and group["id"] not in scope.groups_in:
        return False
    return not (scope.groups_out is not None and group["id"] in scope.groups_out)


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, Want]:
    """External record id -> what a complete and correct sync holds for it."""
    token = expected["tokens"][scope.token]
    groups = {g["id"]: g for g in expected["groups"]}
    visible = set(token["search_ticket_ids"])
    wanted: dict[str, Want] = {}
    for t in expected["tickets"]:
        if t["id"] not in visible or not group_in_scope(groups[t["group_id"]], token, scope):
            continue
        group = f"group_{t['group_id']}"
        wanted[str(t["id"])] = Want("ticket", group, set(t["readers"]), ticket=t)
        for art in t["articles"]:
            if art["sender"] == SYSTEM_SENDER:
                continue
            # An attachment is read with its article: an internal one needs agent read access to the group.
            for sid in art["attachments"]:
                wanted[f"{t['id']}_{art['id']}_{sid}"] = Want("ticket-file", group, set(art["readers"]), ticket=t,
                                                              internal=bool(art["internal"]))
    answers = {a["id"]: a for a in expected["kb"]["answers"]}
    for aid in token["kb"]["answers"]:
        a = answers[aid]
        group = f"cat_{a['category_id']}"
        wanted[f"kb_answer_{aid}"] = Want("answer", group, set(a["readers"]), answer=a)
        for sid in a["attachments"]:
            wanted[f"kb_answer_{aid}_attachment_{sid}"] = Want("answer-file", group, set(a["readers"]), answer=a)
    return wanted


def _reason(want: Want, user: dict[str, Any] | None, finds: bool, groups: dict[int, dict[str, Any]]) -> str:
    """The Zammad rule that explains a difference between PipesHub and Zammad for one person and one record."""
    side = "LEAK" if finds else "DENIED"
    if want.answer is not None:
        return f"{side}:kb-{want.answer['state']}"
    if user is None:
        return f"{side}:not-a-zammad-user"
    t = want.ticket
    group = groups[t["group_id"]]
    uid = user["id"]
    if finds:
        if not user["active"]:
            return "LEAK:inactive-user"
        if not group["active"]:
            return "LEAK:inactive-group"
        if uid in group["user_ids"] and uid not in group["read_user_ids"]:
            # A row of change, create or overview only: Zammad does not let them read the group.
            return "LEAK:group-access-level"
        if want.internal and uid in t["readers"]:
            return "LEAK:internal-article"
        return "LEAK:other"
    if uid in group["read_user_ids"]:
        direct = set((user.get("groups") or {}).get(str(group["id"]), []))
        return "DENIED:group-access" if direct & {"read", "full"} else "DENIED:role-group-access"
    if uid == t["customer_id"]:
        return "DENIED:customer-own-ticket"
    if t["organization_id"] and t["organization_id"] == user.get("organization_id"):
        return "DENIED:customer-shared-org"
    return "DENIED:other"


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed email may
    read) and Zammad's rules (``expected``, the oracle)."""
    wanted = wanted_records(expected, scope)
    groups = {g["id"]: g for g in expected["groups"]}
    by_email = {u["email"].lower(): u for u in expected["users"] if u["email"]}
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
        if found[0].get("externalGroupId") != want.group:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": want.group})
        ids = {r["id"] for r in found}
        # A published answer is everyone's, signed in to Zammad or not.
        public = bool(want.answer and want.answer.get("anonymous"))
        for email, seen in readable.items():
            user = by_email.get(email.lower())
            may = public or (user is not None and user["id"] in want.readers)
            finds = bool(ids & seen)
            if public and may and not finds and not (user and user["active"]):
                # A PipesHub team connector shows its records only to the source users it registers (the connector
                # gate, docs/connectors/06-permissions.md): Zammad's inactive users and people with no Zammad account
                # are outside it, so they do not find a published answer. By design of the product, not a defect.
                continue
            if finds != may:
                problems.append({"type": _reason(want, user, finds, groups), "record": ext,
                                 "name": found[0].get("recordName"), "user": email})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName")} for r in found]
    return problems


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class ZammadSim:
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

    def load(self, instance: dict[str, Any], behaviours: dict[str, Any] | None = None) -> dict[str, dict[str, int]]:
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/zammad/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/zammad/behaviours", behaviours)
        keys = self.call("PUT", "/_sim/zammad/scenario", instance)["keys"]
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/zammad/stats/reset")
        return keys

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/zammad/expected")


class ZammadWorld:
    """One test's installation, its connector, and the comparison between them."""

    def __init__(self, sim: ZammadSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.keys: dict[str, dict[str, int]] = {}
        self.scope = Scope()
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    async def start(self, instance: dict[str, Any], *, behaviours: dict[str, Any] | None = None, token: str = TOKEN,
                    filters: dict[str, Any] | None = None, enable: bool = True) -> str | None:
        """Load ``instance``, create a connector that signs in with ``token``, and (unless ``enable`` is False) turn
        it on; return the outcome of the run that starts."""
        self.keys = self.sim.load(instance, behaviours)
        tokens = instance.get("tokens") or TOKENS[:1]
        name = next((t.get("name") for t in tokens if t["token"] == token), None)
        assert name, f"token {token[:12]}... is not one of the installation's"
        self.scope = Scope(token=name)
        self.scope.groups_in, self.scope.groups_out = _group_scope(filters)
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"zammad-sim-{uuid.uuid4().hex[:8]}",
            scope="team", config={"auth": {"baseUrl": CONNECTOR_URL, "token": token}}, auth_type="API_TOKEN",
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
        stats = self.sim.call("GET", "/_sim/zammad/stats")
        assert stats["total"] > 0, (
            f"the connector never called the simulator at {CONNECTOR_URL} (outcome {outcome}, stats {stats})")
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
        if os.getenv("ZAMMAD_SIM_KEEP_CONNECTOR") == "1":
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

    def key(self, kind: str, name: str) -> int:
        return int(self.keys[kind][name])

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the installation, in order (``/_sim/zammad/ops``; ids may be the instance's keys)."""
        result = self.sim.call("POST", "/_sim/zammad/ops", list(ops))
        self.keys = result["keys"]
        return result["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/zammad/behaviours", values)

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
            self.connector_id, full_sync=full, connector_name=CONNECTOR_TYPE))

    async def records(self) -> list[dict[str, Any]]:
        """The connector's live records."""
        rows = await self.graph.fetch_records_by_type(self.connector_id, "")
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "version": r.get("version")}
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

    # ---- the comparison --------------------------------------------------------------------------------

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        problems = compare(expected, self.scope, records, readable)
        problems += [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/zammad/unmodeled") or []]
        problems += [{"type": "SIM_SPEC", **e} for e in self.sim.call("GET", "/_sim/zammad/spec-problems") or []]
        return problems

    async def assert_matches_source(self, label: str) -> None:
        """Every probed person reads exactly the records Zammad lets them read, but for KNOWN_GAPS, and nothing is
        missing, stale, stored twice or filed under the wrong group."""
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
            f"{label}: PipesHub differs from Zammad in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def zammad_sim() -> ZammadSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Zammad simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = ZammadSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the Zammad user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def zammad_sim_people(
    zammad_sim: ZammadSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the installations, made before any connector so its
    first sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the
    org again."""
    pipeshub_client._ensure_access_token()
    zammad_sim.load(base_instance())
    known = {u["email"].lower() for u in zammad_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the installation without an account: {known}"
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def zammad_world(
    zammad_sim: ZammadSim,
    zammad_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ZammadWorld, None]:
    world = ZammadWorld(zammad_sim, pipeshub_client, graph_provider, zammad_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
