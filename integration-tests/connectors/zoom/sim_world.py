# pyright: ignore-file

"""Zoom against the connectors-simulator: an account the test owns, and who may read which meeting.

The simulator answers Zoom's REST API (api.zoom.us/v2), its OAuth sign-in (zoom.us/oauth) and its transcript downloads
(zoom.us/rec). The connector's clients call those hosts over HTTPS and nothing in the product moves them, so the
overlay (deployment/docker-compose/docker-compose.integration.zoom-sim.yml) gives a TLS front the two names as network
aliases. ``zoom_front`` (below) checks, inside the PipesHub container and before any sync, that both names resolve to
that front, so no test can reach the real Zoom. This process drives the simulator's control API on the published port
(``ZOOM_SIM_URL``).

A test loads an account, signs a connector in, syncs, changes the account, syncs again. After a sync the simulator's
oracle (``/_sim/zoom/expected``, computed from the account alone) says who may read every meeting instance, and
PipesHub's own permission query (``filter_accessible_record_ids``, the one search uses) is asked, for every person of
the account, which of the connector's records they may read. The differences:

  MISSING        a meeting in the sync's scope has no live record
  STALE          a live record of a meeting outside the scope (its host is no longer active, it is too old)
  DUPLICATE      two live records for one meeting
  PARENT         a record not in its host's record group
  LEAK:<role>    a person reads a meeting Zoom's rule (the oracle) does not give them
  DENIED:<role>  a person the oracle gives a meeting cannot read it in PipesHub; ``<role>`` is how they are named
                 there: host, alt-host, participant or invitee
  SIM_UNMODELED / SIM_SPEC / SIM_BLOCKED  the connector sent a request the simulator does not model, a response broke
                 Zoom's reference, or a request left for a host that is not Zoom's: the comparison would prove nothing

The rule being checked: the host owns the record; alternative hosts, participants who have an e-mail address and
invitees may read it; a person outside the account's active users passes no connector gate, so reads nothing, however
the meeting names them (docs/connectors/06-permissions.md, "The connector gate"). The scope of a sync is the meetings
Zoom's report lists for an active host within the last 180 days and since the host's account was made.

Any difference fails a test but those named in ``KNOWN_GAPS``, each with the pull request that fixes it.
"""

from __future__ import annotations

import asyncio
import datetime
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
from urllib.parse import quote

import pytest
import pytest_asyncio
import requests
from app.config.constants.arangodb import AppStatus
from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubClient,
    PipeshubClientError,
)

from connectors.zoom.sim_instances import APP, DOMAIN, PEOPLE, REFRESH_TOKEN, base
from helper.graph_provider_utils import async_poll_until
from helper.oauth_token_helper import (
    exchange_refresh_token,
    inject_connector_credentials,
)
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.source_credentials import source_unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("zoom-sim")

CONNECTOR_TYPE = "Zoom"
SIM_URL_ENV = "ZOOM_SIM_URL"
APP_CONTAINER_ENV = "ZOOM_SIM_APP_CONTAINER"
SYNC_TIMEOUT_SEC = int(os.getenv("ZOOM_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
SETTLE_TIMEOUT_SEC = int(os.getenv("ZOOM_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
USER_GRAPH_TIMEOUT_SEC = 120
WINDOW_DAYS = 180  # the connector's first sync reaches back this far (Zoom's report: six months)
# What ``enable`` returns when PipesHub refuses to turn the connector on (its connection test failed): no sync ran.
REFUSED = "refused"
# More failures than any request of a run makes, so the source never stops refusing.
KEEPS_FAILING = 500

# What the connector calls, as the simulator names it in /_sim/calls.
CALLED = {
    "GET /v2/users", "GET /v2/users/{user_id}", "GET /v2/report/users/{user_id}/meetings",
    "GET /v2/report/meetings/{meeting_ref}/participants", "GET /v2/meetings/{meeting_id}",
    "GET /v2/meetings/{meeting_ref}/recordings", "GET /v2/meetings/{meeting_ref}/transcript",
    "GET /rec/meeting/transcript/download/{token}",
}

# The differences from the rule above the connector still shows, each strict: a test fails when the difference is gone,
# so the entry is removed with the fix. Empty: every defect the suite found is fixed in the same change.
KNOWN_GAPS: dict[str, str] = {}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = None, message: str | None = None,
          **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Zoom's error shape (``message`` of
    the form ``code=3322; text`` picks Zoom's own code). ``trigger`` takes the core's fields (``after_n``,
    ``max_times``, ``every_nth``, ``first_n``, ``probability``)."""
    effect: dict[str, Any] = {"type": "status", "code": code}
    if message:
        effect["message"] = message
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": effect}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def encode_uuid(meeting_uuid: str) -> str:
    """A UUID as Zoom wants it in a path: encoded twice when it begins with '/' or holds '//'."""
    once = quote(meeting_uuid, safe="")
    return quote(once, safe="") if meeting_uuid.startswith("/") or "//" in meeting_uuid else once


# ---- the comparison (pure: the oracle and PipesHub's answer in, the differences out) ----------------------------


@dataclass
class Scope:
    """What a complete sync of the account holds."""

    window_days: int = WINDOW_DAYS
    # host e-mail -> the date their account was made (a first sync starts there, never earlier than the window)
    created: dict[str, datetime.date] = field(default_factory=dict)
    # meeting keys the sync does not reach for a reason the oracle cannot know (a filter, a user not yet listed)
    excluded: set[str] = field(default_factory=set)
    # UUIDs of meetings stored before their host left Zoom's active users. The connector withdraws the host's own
    # access and keeps what it holds until a full sync removes what is out of scope, so each must still be there, read
    # by the people Zoom names who are still active and not by the host. Nothing else out of scope may stay.
    kept: set[str] = field(default_factory=set)


def _date(stamp: str) -> datetime.date:
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()


def wanted_records(expected: dict[str, Any], scope: Scope) -> dict[str, dict[str, Any]]:
    """Meeting UUID -> the oracle's entry, for the meetings a complete sync holds."""
    today = _date(expected["now"])
    floor = today - datetime.timedelta(days=scope.window_days)
    wanted: dict[str, dict[str, Any]] = {}
    for m in expected["meetings"]:
        if not m["synced"] or m["key"] in scope.excluded:
            continue
        start = _date(m["start_time"])
        if start < max(floor, scope.created.get(m["host"], floor)):
            continue
        wanted[m["uuid"]] = m
    return wanted


def role_of(entry: dict[str, Any], email: str) -> str:
    """How the meeting names ``email``: the host, an alternative host, a participant or an invitee."""
    email = email.lower()
    if email == entry["host"]:
        return "host"
    if email in {e.lower() for e in entry["alt_hosts"]} and entry["access"].get(email):
        return "alt-host"
    if email in {p["email"].lower() for p in entry["participants"] if p["email"]}:
        return "participant"
    if email in {e.lower() for e in entry["invitees"]}:
        return "invitee"
    return "none"


def compare(expected: dict[str, Any], scope: Scope, records: list[dict[str, Any]],
            readable: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every difference between PipesHub (live ``records`` of the connector, and the record ids each probed e-mail
    may read) and Zoom's rules (``expected``, the oracle)."""
    wanted = wanted_records(expected, scope)
    gate = {e.lower() for e in expected["app_users"]}
    for m in expected["meetings"]:
        if m["uuid"] in scope.kept and m["uuid"] not in wanted:
            wanted[m["uuid"]] = m
    by_ext: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_ext[str(record["externalRecordId"])].append(record)
    problems: list[dict[str, Any]] = []
    for ext, entry in wanted.items():
        found = by_ext.get(ext, [])
        if not found:
            problems.append({"type": "MISSING", "record": ext, "meeting": entry["key"]})
            continue
        if len(found) > 1:
            problems.append({"type": "DUPLICATE", "record": ext, "records": len(found)})
        if found[0].get("externalGroupId") != entry["host_id"]:
            problems.append({"type": "PARENT", "record": ext, "is": found[0].get("externalGroupId"),
                             "should": entry["host_id"], "meeting": entry["key"]})
        ids = {r["id"] for r in found}
        for email, seen in readable.items():
            key = email.lower()
            may = key in {e.lower() for e in entry["access"]} and key in gate
            finds = bool(ids & seen)
            if finds != may:
                role = role_of(entry, email) if may else ("outside-gate" if key in {e.lower() for e in entry["access"]}
                                                          else "not-named")
                problems.append({"type": f"{'LEAK' if finds else 'DENIED'}:{role}", "record": ext,
                                 "meeting": entry["key"], "user": email})
    for ext, found in by_ext.items():
        if ext not in wanted:
            problems += [{"type": "STALE", "record": ext, "name": r.get("recordName")} for r in found]
    return problems


# ---- the simulator and one test's world ----------------------------------------------------------------------------


class ZoomSim:
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
        self.call("DELETE", "/_sim/zoom/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/zoom/behaviours", behaviours)
        self.call("PUT", "/_sim/zoom/scenario", scenario)
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/zoom/stats/reset")

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/zoom/expected")

    def stats(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/zoom/stats")

    def calls(self) -> dict[str, dict[str, Any]]:
        """Endpoint ("GET /v2/users") -> {count, by_status}, as the simulator counts the connector's requests."""
        return self.call("GET", "/_sim/calls")["endpoints"]


class ZoomWorld:
    """One test's account, its connector, and the comparison between them."""

    def __init__(self, sim: ZoomSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, str]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people            # email -> PipesHub user id
        self.connector_id: str | None = None
        self.scenario: dict[str, Any] = {}
        self.scope = Scope()
        self._fault_rules: list[dict[str, Any]] = []
        self.refresh_token = REFRESH_TOKEN

    # ---- set-up ---------------------------------------------------------------------------------------

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.scenario = scenario
        self.sim.load(scenario, behaviours)
        now = datetime.datetime.fromisoformat(scenario["now"].replace("Z", "+00:00")).date()
        self.scope = Scope(created={
            f"{u['key']}@{scenario.get('domain', DOMAIN)}".lower() if "email" not in u else u["email"].lower():
                now - datetime.timedelta(days=int(float(u.get("created_days_ago", 200))))
            for u in scenario.get("users", [])})

    def create_connector(self, *, client_id: str | None = None, client_secret: str | None = None) -> str:
        app = self.scenario.get("app") or APP
        created = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"zoom-sim-{uuid.uuid4().hex[:8]}", scope="team",
            auth_type="OAUTH",
            config={"auth": {"clientId": client_id or app["client_id"],
                             "clientSecret": client_secret or app["client_secret"]}},
        )
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        return self.connector_id

    async def sign_in(self, *, refresh_token: str | None = None, client_id: str | None = None,
                      client_secret: str | None = None) -> tuple[str, str]:
        """Give the connector its tokens the way the Salesforce suite does: exchange a refresh token at the
        simulator's token endpoint, and store the pair as the sign-in would."""
        app = self.scenario.get("app") or APP
        access, refresh = exchange_refresh_token(
            f"{self.sim.base_url}/oauth/token", client_id or app["client_id"], client_secret or app["client_secret"],
            refresh_token or self.refresh_token, basic_auth=True)
        await inject_connector_credentials(self.connector_id, access, refresh)
        self.refresh_token = refresh
        return access, refresh

    async def start(self, scenario: dict[str, Any], *, behaviours: dict[str, Any] | None = None,
                    filters: dict[str, Any] | None = None, enable: bool = True) -> str | None:
        """Load ``scenario``, create a connector signed in as its OAuth user, and (unless ``enable`` is False) turn
        it on; return the outcome of the run that starts."""
        self.load(scenario, behaviours)
        self.refresh_token = (scenario.get("oauth") or {}).get("refresh_token", REFRESH_TOKEN)
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
        return await self.enable()

    def remove_connector(self) -> None:
        if os.getenv("ZOOM_SIM_KEEP_CONNECTOR") == "1":
            logger.warning("Keeping connector %s for inspection", self.connector_id)
            return
        try:
            self.sim.call("DELETE", "/_sim/faults")
            self.sim.call("DELETE", "/_sim/zoom/behaviours")
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

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        """Changes to the account, in order (``/_sim/zoom/ops``; ids may be the account's keys)."""
        return self.sim.call("POST", "/_sim/zoom/ops", list(ops))["results"]

    def behaviours(self, values: dict[str, Any]) -> None:
        self.sim.call("PUT", "/_sim/zoom/behaviours", values)

    def defer(self, contains: str, ops: list[dict[str, Any]], *, after: int = 1, query: str | None = None) -> None:
        """Apply ``ops`` just before the ``after``-th request whose path (and query) holds ``contains``."""
        self.sim.call("POST", "/_sim/zoom/deferred", {"contains": contains, "after": after, "ops": ops, "query": query})

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

    def api(self, method: str, path: str, body: Any = None) -> Any:
        """A call to Zoom's API as the account's owner (an account-credentials token), for the changes a user makes
        through the API (rename a meeting, change its invitees, delete it). Reaches the published port, not the TLS
        front: only the connector's own traffic is meant to take that route."""
        app = self.scenario.get("app") or APP
        account = self.sim.expected()["account"]["id"]
        token = requests.post(f"{self.sim.base_url}/oauth/token", auth=(app["client_id"], app["client_secret"]),
                              data={"grant_type": "account_credentials", "account_id": account}, timeout=30)
        token.raise_for_status()
        resp = requests.request(method, f"{self.sim.base_url}/v2{path}", json=body, timeout=30,
                                headers={"Authorization": f"Bearer {token.json()['access_token']}"})
        assert resp.status_code < 400, f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}"
        return resp.json() if resp.content else None

    async def set_checkpoint(self, zoom_user_key: str, day: datetime.date) -> None:
        """Write a host's checkpoint through the sync point store, as a run that last read up to ``day`` would."""
        user_id = self.sim.expected()["keys"][zoom_user_key]
        org_id = self.client.org_id
        key = f"{org_id}/{self.connector_id}/records/zoom_report_last_sync/{user_id}"
        ok = await self.graph.upsert_sync_point(
            key, {"orgId": org_id, "connectorId": self.connector_id, "syncPointKey": key,
                  "syncDataPointType": "records", "last_sync_date": day.isoformat()}, collection="syncPoints")
        assert ok, f"could not write the checkpoint {key}"

    async def keep_after_leaving(self, *host_keys: str) -> None:
        """Before ``host_keys`` leave Zoom's active users: note the meetings PipesHub holds of theirs, which the
        comparison then expects to stay (``Scope.kept``) until a full sync."""
        expected = self.sim.expected()
        hosts = {next(u["email"] for u in expected["users"] if u["key"] == k).lower() for k in host_keys}
        stored = {r["externalRecordId"] for r in await self.records()}
        self.scope.kept |= {m["uuid"] for m in expected["meetings"] if m["host"].lower() in hosts and m["uuid"] in stored}

    def instance(self, key: str) -> dict[str, Any]:
        return next(m for m in self.sim.expected()["meetings"] if m["key"] == key)

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
        return await self._await_end(before)

    async def _await_end(self, before: int) -> str | None:
        """Wait for a run that started after ``before`` (the last finish time) to end; its recorded outcome."""
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
        """The connector's live records: those still linked to a record group."""
        # scoped: a record a full sync unlinked (no BELONGS_TO to a record group) is out of the connector, whatever
        # node it leaves behind, and nobody reads it.
        rows = await self.graph.fetch_records_by_type(self.connector_id, "", scoped=True)
        return [{"id": r.get("id") or r.get("_key"), "externalRecordId": r.get("externalRecordId"),
                 "externalGroupId": r.get("externalGroupId"), "recordName": r.get("recordName"),
                 "version": r.get("version"), "revision": r.get("externalRevisionId"),
                 "indexingStatus": r.get("indexingStatus"), "webUrl": r.get("webUrl")}
                for r in rows or [] if not r.get("isDeleted")]

    async def recording_url(self, external_id: str) -> str | None:
        """The recording link stored on a meeting's record, as the graph provider reads the typed record."""
        record = await self.graph.get_typed_record_by_external_id(self.connector_id, external_id)
        return getattr(record, "recording_url", None)

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

    async def stream(self, external_id: str) -> dict[str, str]:
        """What PipesHub streams for a meeting: {"Transcript": ..., "Participants": ...}; raises on an HTTP error."""
        record = await self.record_for(external_id)
        assert record, f"no live record of {external_id}"
        resp = self.client.stream_record(record["id"])
        body = json.loads(b"".join(resp.iter_content(65536)))
        return {g["name"]: g.get("data") or "" for g in body["block_groups"]}

    # ---- the comparison --------------------------------------------------------------------------------

    def source_problems(self) -> list[dict[str, Any]]:
        """What the simulator saw wrong in the connector's requests: nothing modelled is missing, nothing it sent
        broke Zoom's reference."""
        problems = [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/zoom/unmodeled") or []]
        problems += [{"type": "SIM_SPEC", **e} for e in self.sim.call("GET", "/_sim/zoom/spec-problems") or []]
        return problems

    async def differences(self) -> list[dict[str, Any]]:
        expected = self.sim.expected()
        records = await self.records()
        readable = await self.readable(records)
        return compare(expected, self.scope, records, readable) + self.source_problems()

    async def assert_matches_source(self, label: str, *, allow_empty: bool = False) -> None:
        """Every probed person reads exactly the meetings Zoom lets them read, and nothing is missing, stale, stored
        twice or filed under the wrong host."""
        expected = self.sim.expected()
        assert allow_empty or wanted_records(expected, self.scope), (
            f"{label}: the oracle expects no meeting in the sync, so the comparison proves nothing")
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
        missing = [t for t in KNOWN_GAPS if not any(p["type"] == t for p in problems)]
        assert not missing, f"{label}: known gap(s) {missing} no longer occur; remove them from KNOWN_GAPS"
        assert not failing, (
            f"{label}: PipesHub differs from Zoom in {len(failing)} way(s) "
            f"({dict(Counter(p['type'] for p in failing))}): {failing[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


def _app_container() -> str:
    """The PipesHub container the suite runs against: ``ZOOM_SIM_APP_CONTAINER``, else the one container the
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
def zoom_front() -> dict[str, str]:
    """Both Zoom host names resolve, inside the PipesHub container, to the simulator's TLS front and to nothing else:
    checked before any connector exists, so nothing of this suite can reach the real Zoom. Fails, never skips: a
    stack without the front must not run the suite."""
    if shutil.which("docker") is None:
        pytest.fail("the docker CLI is needed to check where api.zoom.us and zoom.us resolve in the PipesHub container")
    container = _app_container()
    front = {"zoom-tls": resolved_in_container(container, "zoom-tls")}
    for host in ("api.zoom.us", "zoom.us", "ssrweb.zoom.us"):
        front[host] = resolved_in_container(container, host)
        assert front[host] == front["zoom-tls"], (
            f"{host} resolves to {front[host]} in {container}, not to the simulator's front {front['zoom-tls']}: "
            "a sync would reach the real Zoom")
    return front


@pytest.fixture(scope="session")
def zoom_sim(zoom_front: dict[str, str]) -> ZoomSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The Zoom simulator this suite syncs from is not configured.", secrets=[SIM_URL_ENV])
    sim = ZoomSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


async def _active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once the graph has it active: a sync started before
    then would map the Zoom user to no one."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")
    return user_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def zoom_sim_people(
    zoom_sim: ZoomSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: GraphProviderProtocol,
) -> dict[str, str]:
    """A PipesHub account (email -> user id) for every person of the accounts, made before any connector so its
    first sync maps them. Kept after the session, as the other simulator suites do: a deleted email cannot join the
    org again."""
    pipeshub_client._ensure_access_token()
    zoom_sim.load(base())
    known = {u["email"].lower() for u in zoom_sim.expected()["users"] if u["email"]}
    assert known <= {p.lower() for p in PEOPLE}, f"people of the account without a PipesHub account: {known}"
    return {email: await _active_user(users_client, graph_provider, email) for email in PEOPLE}


@pytest_asyncio.fixture(loop_scope="session")
async def zoom_world(
    zoom_sim: ZoomSim,
    zoom_sim_people: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ZoomWorld, None]:
    world = ZoomWorld(zoom_sim, pipeshub_client, graph_provider, zoom_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()
