# pyright: ignore-file

"""SharePoint Online against the connectors-simulator: a tenant the test owns, and who may read what.

The simulator answers Entra ID sign-in, Microsoft Graph and SharePoint REST. The PipesHub container reaches
it under Microsoft's own host names, through a TLS front its trust store accepts
(deployment/docker-compose/docker-compose.integration.sharepoint-sim.yml); this process drives it through
its control API on the published port (``SHAREPOINT_SIM_URL``).

Each test loads a tenant, creates a connector for it, syncs, changes the tenant and syncs again. After a
sync the simulator's oracle (``/_sim/sharepoint/expected``, computed from the tenant alone) says who may
read each item. Each of those people is a PipesHub account with the same email, logged in, and opens every
record of the connector (``GET /api/v1/knowledgeBase/record/:id``, the access check search and chat rely
on). The differences:

  LEAK       a person opens a record the source does not let them read
  DENIED     a person can read an item in the source but PipesHub refuses the record
  MISSING    an item that should be synced has no live record
  STALE      a record of an item that is gone, or outside the sync's scope, still opens for someone
  OUT_OF_SCOPE  a record of an item a filter excludes is still in the graph: a filter change must delete
             the records it leaves out, not only hide them
  EXTRA      a record of something that should never be a record opens for someone
  DUPLICATE  two live records for one item, or a page stored a second time as a file
  UNSETTLED  opening a record answered neither 200 nor a refusal (a 5xx, an expired login)
  SIM_UNMODELED / SIM_SPEC  the connector sent a request the simulator does not model, or a response
             broke Microsoft's OpenAPI document: the comparison would prove nothing

Folder records are left out, as in the harness these tests come from: only files and pages are compared.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests
from connectors.sharepoint.sim_tenants import base_tenant
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
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

    from app.models.entities import Record

logger = logging.getLogger("sharepoint-sim")

CONNECTOR_TYPE = "SharePoint Online"
SIM_URL_ENV = "SHAREPOINT_SIM_URL"
SYNC_TIMEOUT_SEC = int(os.getenv("SHAREPOINT_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 3
# Graph writes land a little after the run's end is recorded; the comparison polls this long for them.
SETTLE_TIMEOUT_SEC = int(os.getenv("SHAREPOINT_SIM_SETTLE_TIMEOUT_SEC", "90"))
SETTLE_POLL_SEC = 10
OPEN_CONCURRENCY = 8
FOLDER_MIME = "text/directory"

# Most tests do not care whether Graph lists a site's Site Pages library among its drives (the documents
# disagree); hiding it keeps them about one thing. The first-sync test lists it.
QUIET = {"drives_include_site_pages": False}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def fault(path_regex: str, code: int = 503, method: str | None = "GET", **trigger: float) -> dict[str, Any]:
    """A simulator fault rule: requests whose path matches answer ``code`` in Microsoft's error shape."""
    rule: dict[str, Any] = {"match": {"path_regex": path_regex}, "effect": {"type": "status", "code": code}}
    if method:
        rule["match"]["method"] = method
    if trigger:
        rule["trigger"] = trigger
    return rule


def empty_listing(path_regex: str) -> dict[str, Any]:
    """A listing that answers 200 with nothing in it, which is not the same as "everything is gone"."""
    return {"match": {"method": "GET", "path_regex": path_regex},
            "effect": {"type": "shape", "ops": [{"path": "value", "value": []}]}}


_CERTIFICATE: dict[str, tuple[str, str]] = {}


def client_certificate() -> tuple[str, str]:
    """The app's sign-in certificate and PKCS#8 key, made for this run; the simulator is given the
    certificate and checks the connector's assertions against it, as Entra ID does."""
    if "pair" not in _CERTIFICATE:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pipeshub-it-sharepoint-sim")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=7)).sign(key, hashes.SHA256()))
        _CERTIFICATE["pair"] = (
            cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())
    return _CERTIFICATE["pair"]


class SharePointSim:
    """The simulator's control API (``/_sim/...``), which is never faulted."""

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def call(self, method: str, path: str, body: object = None) -> dict[str, Any] | list[Any]:
        resp = self.http.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"simulator {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json()

    def load(self, tenant: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self.call("DELETE", "/_sim/faults")
        self.call("DELETE", "/_sim/sharepoint/behaviours")
        if behaviours:
            self.call("PUT", "/_sim/sharepoint/behaviours", behaviours)
        self.call("PUT", "/_sim/sharepoint/scenario", tenant)
        self.call("POST", "/_sim/calls/reset")
        self.call("POST", "/_sim/sharepoint/stats/reset")

    def expected(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/sharepoint/expected")

    def content(self, item_id: str) -> bytes:
        resp = self.http.get(f"{self.base_url}/_sim/sharepoint/content/{item_id}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content


def external_id(item: dict[str, Any]) -> str:
    """The externalRecordId the connector gives the item's record."""
    if item["kind"] == "page":
        return item["unique_id"]
    if item["root"]:
        return f"{item['drive_id']}:root:{item['id']}"
    return item["id"]


_RECORDS_CYPHER = (
    "MATCH (r:Record {connectorId: $c}) RETURN r.id AS id, r.externalRecordId AS externalRecordId, "
    "r.recordName AS recordName, r.recordType AS recordType, r.mimeType AS mimeType, "
    "r.isDeleted AS isDeleted, r.isPlaceholder AS isPlaceholder, r.externalParentId AS externalParentId, "
    "r.version AS version"
)
_RECORDS_AQL = (
    "FOR r IN records FILTER r.connectorId == @c RETURN {id: r._key, externalRecordId: r.externalRecordId, "
    "recordName: r.recordName, recordType: r.recordType, mimeType: r.mimeType, isDeleted: r.isDeleted, "
    "isPlaceholder: r.isPlaceholder, externalParentId: r.externalParentId, version: r.version}"
)


class SharePointWorld:
    """One test's tenant, its connector, and the comparison between them."""

    def __init__(self, sim: SharePointSim, client: PipeshubClient, graph_provider: GraphProviderProtocol,
                 people: dict[str, SecondUser]) -> None:
        self.sim = sim
        self.client = client
        self.graph = graph_provider
        self.people = people
        self.connector_id: str | None = None
        self.expected: dict[str, Any] = {}
        # The PipesHub accounts compared: fixed when the tenant is loaded, so a person disabled or deleted
        # in the source later is still asked, and must then be refused everything.
        self.probed: dict[str, SecondUser] = {}
        self.scope_sites: set[str] | None = None
        self.scope_libraries: set[str] | None = None
        self.scope_extensions: set[str] | None = None
        self._fault_rules: list[dict[str, Any]] = []

    # ---- set-up ---------------------------------------------------------------------------------------

    async def start(self, tenant: dict[str, Any], *, behaviours: dict[str, Any] | None = None,
                    filters: dict[str, Any] | None = None, guests: bool = False,
                    enable: bool = True) -> str | None:
        """Load the tenant, create a connector for it and, with ``enable``, run its first sync.
        Returns the first sync's outcome."""
        cert, key = client_certificate()
        tenant = {**tenant, "app": {**tenant.get("app", {}), "certificate_pem": cert}}
        self.sim.load(tenant, behaviours)
        self.expected = self.sim.expected()
        self.probed = {}
        for user in self.expected["users"]:
            if user["deleted"] or (user["type"] == "Guest" and not guests):
                continue
            email = (user["mail"] or user["upn"]).lower()
            assert email in self.people, f"no PipesHub account for tenant user {email}"
            self.probed[email] = self.people[email]

        info = self.expected["tenant"]
        config = {"auth": {"clientId": self.expected["app"]["client_id"], "tenantId": info["id"],
                           "sharepointDomain": f"https://{info['host']}", "hasAdminConsent": True,
                           "certificate": cert, "privateKey": key}}
        instance = self.client.create_connector(
            connector_type=CONNECTOR_TYPE, instance_name=f"sharepoint-sim-{uuid.uuid4().hex[:8]}",
            scope="team", config=config, auth_type="OAUTH_ADMIN_CONSENT",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        self.connector_id = instance.connector_id
        if filters:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if not enable:
            return None
        return await self.enable()

    async def enable(self) -> str | None:
        outcome = await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))
        # A name that resolved to the real Microsoft would fail sign-in too; this tells the two apart.
        stats = self.sim.call("GET", "/_sim/sharepoint/stats")
        assert stats["tokens_issued"] > 0, (
            "the connector never signed in at the simulator: Microsoft's host names do not lead to it "
            f"from the PipesHub container (sync outcome {outcome}, simulator stats {stats})"
        )
        return outcome

    def remove_connector(self) -> None:
        """Disable and delete; the graph clean-up runs on its own while the next test starts.
        SHAREPOINT_SIM_KEEP_CONNECTOR=1 leaves it in place, to inspect a failure."""
        if os.getenv("SHAREPOINT_SIM_KEEP_CONNECTOR") == "1":
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

    def key(self, key: str) -> str:
        return self.expected["keys"][key]

    def library(self, key: str) -> dict[str, Any]:
        return next(lib for lib in self.expected["libraries"] if lib["key"] == key)

    # ---- the source -----------------------------------------------------------------------------------

    def ops(self, *ops: dict[str, Any]) -> list[dict[str, Any]]:
        return self.sim.call("POST", "/_sim/sharepoint/ops", list(ops))["results"]

    def faults(self, *rules: dict[str, Any]) -> None:
        self.sim.call("DELETE", "/_sim/faults")
        self.sim.call("PUT", "/_sim/faults", {"enabled": True, "rules": list(rules)})
        self._fault_rules = list(rules)

    def clear_faults(self) -> None:
        """Remove the faults, failing when none of them fired: a test whose fault the connector never
        reached proves nothing. Rules given together are alternatives, so one firing is enough."""
        fired = {entry["rule"] for entry in self.sim.call("GET", "/_sim/faults/log")}
        rules, self._fault_rules = self._fault_rules, []
        self.sim.call("DELETE", "/_sim/faults")
        assert not rules or fired, f"no injected fault fired, so the test proves nothing: {rules}"

    def expire_delta_tokens(self) -> None:
        self.sim.call("POST", "/_sim/sharepoint/delta/expire")

    # ---- PipesHub -------------------------------------------------------------------------------------

    def _connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    async def _wait_idle(self) -> None:
        # A connector that never synced has no status yet, which is not a running sync.
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self._connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Callable[[], Any]) -> str | None:
        """Trigger a run and wait for the product to record its end; return the outcome it recorded.

        The end time and outcome are written in one update with IDLE, so a newer end time is this run's
        end, whatever the status read in between.
        """
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
                raise TimeoutError(
                    f"no sync of connector {self.connector_id} ended within {SYNC_TIMEOUT_SEC}s "
                    f"(status {doc.get('status')}, last outcome {doc.get('lastSyncOutcome')})"
                )
            await asyncio.sleep(SYNC_POLL_SEC)

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self.client.resync_connector(self.connector_id, full_sync=full))

    async def set_filters(self, filters: dict[str, Any]) -> str | None:
        """Save filters the way the UI does; the re-enable that follows runs the full sync they owe."""
        return await self._run(
            lambda: self.client.update_connector_filters_sync_safe(self.connector_id, filters=filters)
        )

    async def records(self) -> list[dict[str, Any]]:
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await self.graph.execute_query(_RECORDS_AQL, bind_vars=params)
        else:
            rows = await self.graph.client.execute_query(_RECORDS_CYPHER, params)
        return list(rows or [])

    async def record_of(self, key: str) -> Record | None:
        """The stored record of a tenant item, by its key."""
        item = next(i for i in self.sim.expected()["items"] if i["id"] == self.key(key))
        return await self.graph.get_record_by_external_id(self.connector_id, external_id(item))

    def reader_of(self, key: str) -> SecondUser:
        """A person the source lets read the item: the org admin is not one, and is refused."""
        expected = self.sim.expected()
        email_of = {u["id"]: (u["mail"] or u["upn"]).lower() for u in expected["users"]}
        item = next(i for i in expected["items"] if i["id"] == self.key(key))
        email = next((email_of[r] for r in item["readers"] if email_of.get(r) in self.probed), None)
        assert email, f"nobody probed may read {key}"
        return self.probed[email]

    def stream(self, record_id: str, user: SecondUser) -> tuple[int, bytes]:
        """Stream a record as preview does, as ``user``: (status, body)."""
        resp = requests.get(f"{user.base_url}{self.client._stream_record_path(record_id)}",
                            headers=user.headers, timeout=user.timeout)
        return resp.status_code, resp.content

    # ---- the comparison --------------------------------------------------------------------------------

    def _in_scope(self, item: dict[str, Any]) -> bool:
        if item["personal"]:
            return False
        if self.scope_sites is not None and item["site"] not in self.scope_sites:
            return False
        if self.scope_libraries is not None and item["kind"] != "page" and item["library"] not in self.scope_libraries:
            return False
        if self.scope_extensions is not None and item["kind"] == "file" and \
                item["name"].rsplit(".", 1)[-1].lower() not in self.scope_extensions:
            return False
        return True

    def _expected_synced(self, item: dict[str, Any]) -> bool:
        if not self._in_scope(item):
            return False
        if item["kind"] == "file":
            return item["library_template"] == 101 and "." in item["name"]
        if item["kind"] == "page":
            return not item["system"]
        return False

    async def _who_opens(self, records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
        """(email, record id) -> HTTP status of that person opening that record."""
        gate = asyncio.Semaphore(OPEN_CONCURRENCY)

        async def one(email: str, user: SecondUser, record_id: str) -> tuple[tuple[str, str], int]:
            async with gate:
                return (email, record_id), await asyncio.to_thread(record_access_status, user, record_id)

        pairs = [(email, user, r["id"]) for r in records for email, user in self.probed.items()]
        return dict(await asyncio.gather(*(one(*pair) for pair in pairs)))

    async def differences(self, *, allow: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        self.expected = self.sim.expected()
        email_of = {u["id"]: (u["mail"] or u["upn"]).lower() for u in self.expected["users"]}
        items = {external_id(i): i for i in self.expected["items"]}
        # A page is also a file in the Site Pages library; a record of that file is a second copy of the page.
        page_files = {i["id"]: i for i in self.expected["items"] if i["kind"] == "page"}
        records = [r for r in await self.records()
                   if not r.get("isDeleted") and not r.get("isPlaceholder") and r.get("mimeType") != FOLDER_MIME]
        problems: list[dict[str, Any]] = []

        by_external: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_external[record["externalRecordId"]].append(record)
        for ext, item in items.items():
            if self._expected_synced(item) and ext not in by_external:
                problems.append({"type": "MISSING", "item": item["name"], "kind": item["kind"], "id": ext})
        for ext, recs in by_external.items():
            if len(recs) > 1:
                problems.append({"type": "DUPLICATE", "item": recs[0]["recordName"], "id": ext,
                                 "records": [r["id"] for r in recs]})

        opened = await self._who_opens(records)
        for record in records:
            ext = record["externalRecordId"]
            item = items.get(ext)
            readers: set[str] = set()
            if item is not None and item["kind"] != "folder" and not item["personal"] and not self._in_scope(item):
                problems.append({"type": "OUT_OF_SCOPE", "item": item["name"], "record": record["id"],
                                 "detail": "a filter excludes it, but its record is still in the graph"})
            if item is None and ext in page_files:
                problems.append({"type": "DUPLICATE", "item": record["recordName"], "record": record["id"],
                                 "detail": "a page also synced as a FILE from the Site Pages library"})
                readers = {email_of[r] for r in page_files[ext]["readers"] if r in email_of}
            elif item is not None and item["kind"] == "folder":
                continue
            elif item is not None:
                readers = {email_of[r] for r in item["readers"] if r in email_of}
            for email in self.probed:
                status = opened[(email, record["id"])]
                if status != 200 and status not in NO_ACCESS_STATUSES:
                    problems.append({"type": "UNSETTLED", "user": email, "item": record["recordName"],
                                     "record": record["id"], "status": status})
                    continue
                can_open = status == 200
                if item is None and ext not in page_files:
                    if can_open:
                        problems.append({"type": "STALE", "user": email, "item": record["recordName"],
                                         "record": record["id"], "detail": "item no longer in the source"})
                elif item is not None and not self._in_scope(item):
                    if can_open:
                        problems.append({"type": "STALE", "user": email, "item": item["name"],
                                         "record": record["id"], "detail": "out of the sync's scope, still opens"})
                elif item is not None and not self._expected_synced(item):
                    if can_open:
                        problems.append({"type": "EXTRA", "user": email, "item": item["name"],
                                         "record": record["id"], "source_can_read": email in readers})
                elif can_open and email not in readers:
                    problems.append({"type": "LEAK", "user": email, "item": record["recordName"],
                                     "record": record["id"]})
                elif email in readers and not can_open and item is not None:
                    problems.append({"type": "DENIED", "user": email, "item": record["recordName"],
                                     "record": record["id"], "status": status})

        problems += [{"type": "SIM_UNMODELED", **e} for e in self.sim.call("GET", "/_sim/sharepoint/unmodeled")]
        problems += [{"type": "SIM_SPEC", **e} for e in self.sim.call("GET", "/_sim/sharepoint/spec-problems")]
        return [p for p in problems if p["type"] not in allow]

    async def assert_matches_source(self, label: str, *, allow: tuple[str, ...] = ()) -> None:
        """Every probed person opens exactly the records the source lets them read, and nothing is
        missing, stale or stored twice."""
        await self._settle(label, lambda: self.differences(allow=allow))

    async def hierarchy_problems(self) -> list[dict[str, Any]]:
        """Each file and folder record hangs under the record of its source parent, and no placeholder
        stands in for a parent that exists."""
        self.expected = self.sim.expected()
        items = {i["id"]: i for i in self.expected["items"]}
        problems: list[dict[str, Any]] = []
        for record in await self.records():
            if record.get("isDeleted"):
                continue
            if record.get("isPlaceholder"):
                problems.append({"type": "PLACEHOLDER", "record": record["id"],
                                 "external": record["externalRecordId"]})
                continue
            item = items.get((record["externalRecordId"] or "").split(":root:")[-1])
            if item is None or item["kind"] == "page" or item["root"]:
                continue
            parent = items.get(item["parent"])
            if parent is None:
                continue
            if record.get("externalParentId") != external_id(parent):
                problems.append({"type": "PARENT", "item": item["name"],
                                 "record_parent": record.get("externalParentId"),
                                 "source_parent": external_id(parent)})
        return problems

    async def assert_hierarchy_matches_source(self, label: str) -> None:
        await self._settle(label, self.hierarchy_problems)

    async def _settle(self, label: str, compare: Callable[[], Any]) -> None:
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            problems = await compare()
            if not problems or time.monotonic() >= deadline:
                break
            await asyncio.sleep(SETTLE_POLL_SEC)
        assert not problems, (
            f"{label}: PipesHub differs from SharePoint in {len(problems)} way(s) "
            f"({dict(Counter(p['type'] for p in problems))}): {problems[:25]}"
        )


# ---- fixtures --------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sharepoint_sim() -> SharePointSim:
    url = os.getenv(SIM_URL_ENV, "").strip()
    if not url:
        source_unavailable("The SharePoint simulator this suite syncs from is not configured.",
                           secrets=[SIM_URL_ENV])
    sim = SharePointSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{SIM_URL_ENV}={url} is set but the simulator does not answer: {exc}")
    return sim


def _account(client: PipeshubClient, email: str, name: str) -> SecondUser:
    """A logged-in PipesHub account with the tenant user's email: connector permissions are keyed by it."""
    resp = requests.post(
        f"{client.base_url}/api/v1/users",
        headers=client._headers(),
        json={"fullName": name, "email": email},
        timeout=client.timeout_seconds,
    )
    created = resp.json() if resp.status_code < 400 else {}
    if resp.status_code >= 400:
        logger.info("createUser %s answered %s; looking for an existing account", email, resp.status_code)
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
def sharepoint_sim_people(sharepoint_sim: SharePointSim, pipeshub_client: PipeshubClient) -> dict[str, SecondUser]:
    """A PipesHub account for every person in the tenants (``small_tenant``'s are a subset).

    Kept after the session: an email whose account was deleted cannot be added to the org again, so a
    deleting teardown would break the next session on the same stack. The next session reuses them.
    """
    pipeshub_client._ensure_access_token()
    sharepoint_sim.load(base_tenant())
    return {
        (user["mail"] or user["upn"]).lower(): _account(
            pipeshub_client, (user["mail"] or user["upn"]).lower(), f"SharePoint sim {user['key']}")
        for user in sharepoint_sim.expected()["users"]
    }


@pytest_asyncio.fixture(loop_scope="session")
async def sharepoint_world(
    sharepoint_sim: SharePointSim,
    sharepoint_sim_people: dict[str, SecondUser],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[SharePointWorld, None]:
    world = SharePointWorld(sharepoint_sim, pipeshub_client, graph_provider, sharepoint_sim_people)
    try:
        yield world
    finally:
        world.remove_connector()


def library_regex(world: SharePointWorld, site_key: str, library_key: str, target: str) -> str:
    """The path of one read of the connector, for a fault on that read alone."""
    collection = world.key(site_key).split(",")[1]
    drive = world.library(library_key)["drive_id"]
    return {"drives": re.escape(collection) + r"[^/]*/drives$",
            "rest": rf"^/sites/{site_key}/_api/web/lists",
            "delta": re.escape(drive) + r"/root/delta"}[target]
