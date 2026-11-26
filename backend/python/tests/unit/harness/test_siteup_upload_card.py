"""SITEUP-01..SITEUP-07 (WP-63, the upload card): a read-write website held to read-only after the run read private
data asks the person once per run and site; the turn does not wait. Until they allow it each write is refused
``site_upload_held``; once they do, the worker releases the hold at the egress proxy for that run's sandbox and host,
and later turns carry the site read-write. A denial keeps it read-only. A grant from another run, for another
sandbox, after the binding moved on, or for a site the sandbox does not hold, releases nothing.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.site_uploads import (
    SITE_UPLOAD,
    SITE_UPLOAD_RULE,
    SITE_UPLOAD_TOOL_ID,
    SiteUploads,
    site_upload_interaction,
)
from app.harness.websites import FlowFacts, RunSites, narrowed_for_flow
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.journal.types import JournalEvent, NewEvent
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.site_grants import SiteGrantInvalid
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    SiteAccess,
    SiteMode,
)
from harness_core.security.classification import UNCONFIGURED
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import Sequence

R = EgressRefusalReason
HOST = "upload.example.org"
PUBLIC = "93.184.216.34"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
HELD = SiteAccess(SiteMode.READ_WRITE, held=True)
POLICY = EgressPolicy(destinations=(EgressDestination(HOST, (443,), site=HELD), EgressDestination("docs.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE))))
BOUND = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, LABELS)
OTHER = EgressBinding("sbx-b", "10.213.0.6", "10.213.0.7", POLICY, {**LABELS, "runId": "r2"})


# --- SITEUP-01: the flow rule holds a read-write site, never drops it ---------------------------------------------


def test_siteup01_after_a_private_read_a_read_write_site_is_held_and_an_allowed_one_is_not() -> None:
    rw = EgressDestination(HOST, (443,), site=SiteAccess(SiteMode.READ_WRITE, paths=("/up/",)))
    read = EgressDestination("docs.example.org", (443,), site=SiteAccess(SiteMode.READ))
    tunnel = EgressDestination("pinned.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE, opaque=True))
    assert narrowed_for_flow((rw, read, tunnel), FlowFacts()) == (rw, read, tunnel)
    held = narrowed_for_flow((rw, read, tunnel), FlowFacts(private_read=True))
    assert [d.host for d in held] == [HOST, "docs.example.org"], "a host-level tunnel cannot be held"
    assert held[0].site == SiteAccess(SiteMode.READ_WRITE, paths=("/up/",), held=True)
    assert held[1].site == SiteAccess(SiteMode.READ, held=True), "SEC-A-01: a read site is held too"
    released = narrowed_for_flow((rw, read), FlowFacts(private_read=True), frozenset({HOST, "docs.example.org"}))
    assert released == (rw, read)
    assert narrowed_for_flow((rw,), FlowFacts(private_read=True, egress_forbidden=True)) == ()
    with pytest.raises(ValueError):
        _ = SiteAccess(SiteMode.READ_WRITE, opaque=True, held=True)


# --- SITEUP-02: the proxy refuses a held site's writes until it holds a grant --------------------------------------


async def _proxy() -> EgressProxy:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(BOUND)
    await bindings.register(OTHER)
    return EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"siteup", clock, ttl_s=600),
        resolver=StaticResolver({HOST: [PUBLIC], "docs.example.org": [PUBLIC]}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True),
    )


def _write(binding: EgressBinding = BOUND, method: str = "POST") -> tuple[ProxyRequest, EgressBinding]:
    return ProxyRequest(method, "/up/file", (("Host", HOST), ("Authorization", "token x")), b"data"), binding


async def _through(proxy: EgressProxy, request: ProxyRequest, binding: EgressBinding = BOUND) -> Any:  # noqa: ANN401
    return await proxy.authorize_intercepted(
        request, host=HOST, port=443, local=binding.proxy_address, peer=binding.sandbox_address
    )


async def test_siteup02_held_then_released_for_that_sandbox_and_run_only() -> None:
    proxy = await _proxy()
    get = ProxyRequest("GET", "/up/file", (("Host", HOST), ("Authorization", "token x")))
    with pytest.raises(EgressRefused) as read_held:
        _ = await _through(proxy, get)
    assert read_held.value.reason is R.SITE_REQUEST_HELD, "SEC-A-01: a held site's reads wait as its writes do"
    with pytest.raises(EgressRefused) as held:
        _ = await _through(proxy, _write()[0])
    assert held.value.reason is R.SITE_UPLOAD_HELD and held.value.host == HOST
    # Another run's grant, a host the sandbox holds no held site for, or no grant at all release nothing.
    for wrong in ({"host": HOST, "run_id": "r2"}, {"host": "docs.example.org", "run_id": "r1"}, {"host": "evil.example", "run_id": "r1"}):
        with pytest.raises(SiteGrantInvalid):
            proxy.site_grants.issue(BOUND, **wrong)  # type: ignore[arg-type]
    proxy.site_grants.issue(OTHER, host=HOST, run_id="r2")
    with pytest.raises(EgressRefused):
        _ = await _through(proxy, _write()[0])
    # Allowed for this sandbox and run: the write passes, with the sandbox's own credentials (a read-write site).
    proxy.site_grants.issue(BOUND, host=HOST, run_id="r1")
    forward = await _through(proxy, _write()[0])
    assert forward.method == "POST" and any(k.lower() == "authorization" for k, _ in forward.headers)
    assert (await _through(proxy, get)).method == "GET"
    # The binding moves on: a replay of the grant releases nothing for the next run.
    await proxy.bindings.unregister(BOUND.sandbox_id)
    await proxy.bindings.register(BOUND)
    with pytest.raises(EgressRefused) as again:
        _ = await _through(proxy, _write()[0])
    assert again.value.reason is R.SITE_UPLOAD_HELD


async def test_siteup02_the_control_route_releases_and_refuses_with_the_worker_credential(tmp_path: Any) -> None:  # noqa: ANN401
    proxy = await _proxy()
    server = EgressServer(proxy, StaticBearerAuth("ctl"), instance="siteup")
    _, control_port = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    client = RemoteEgressBindings(f"http://127.0.0.1:{control_port}", StaticBearerAuth("ctl"))
    try:
        for wrong in (
            {"sandbox_id": "sbx-a", "host": HOST, "run_id": "r2"},
            {"sandbox_id": "sbx-gone", "host": HOST, "run_id": "r1"},
            {"sandbox_id": "sbx-a", "host": "docs.example.org", "run_id": "r1"},
        ):
            with pytest.raises(Exception):  # noqa: B017 - the broker API's typed refusal
                await client.grant_site(**wrong)
        with pytest.raises(EgressRefused):
            _ = await _through(proxy, _write()[0])
        await client.grant_site(sandbox_id="sbx-a", host=HOST, run_id="r1")
        assert (await _through(proxy, _write()[0])).method == "POST"
        stranger = RemoteEgressBindings(f"http://127.0.0.1:{control_port}", StaticBearerAuth("not-it"))
        with pytest.raises(Exception):  # noqa: B017
            await stranger.grant_site(sandbox_id="sbx-b", host=HOST, run_id="r2")
        await stranger.aclose()
    finally:
        await client.aclose()
        await server.stop()


# --- SITEUP-03..05: the card ---------------------------------------------------------------------------------------


class _Ledger:
    def apply(self, events: Any) -> None:  # noqa: ANN401
        del events

    def source_records(self, run_id: str) -> frozenset[str]:
        return frozenset({"rec-1"})


class _Presenter:
    def approval(self, stamp: ProgressStamp, interaction_id: str, tool: str, arguments: Any) -> list[NewEvent]:  # noqa: ANN401
        return [NewEvent("x.pipeshub.engine.progress", {"kind": "approval", "data": {"interactionId": interaction_id, "toolName": tool, "argsJson": json.dumps(arguments)}}, critical=False)]


class _Journal:
    def __init__(self) -> None:
        self.events: list[JournalEvent] = []

    async def append(self, events: Sequence[NewEvent]) -> None:
        for event in events:
            self.events.append(JournalEvent.from_envelope({
                "orgId": "o1", "sessionId": "s1", "streamId": "s1", "seq": len(self.events) + 1, "runId": "r1",
                "schemaVersion": 1, "critical": event.critical, "ts": "2026-11-02T12:00:00.000Z", "visibility": "owner",
                "writer": "worker", "type": event.type, "data": event.data,
            }))

    def resolve(self, interaction_id: str, decision: str) -> None:
        self.events.append(JournalEvent.from_envelope({
            "orgId": "o1", "sessionId": "s1", "streamId": "s1", "seq": len(self.events) + 1, "runId": "r1",
            "schemaVersion": 1, "critical": True, "ts": "2026-11-02T12:00:01.000Z", "visibility": "owner",
            "writer": "node", "type": "interaction.resolved", "data": {"interactionId": interaction_id, "decision": decision},
        }))

    async def read(self) -> list[JournalEvent]:
        return list(self.events)


class _Control:
    def __init__(self) -> None:
        self.granted: list[tuple[str, str, str]] = []

    async def grant_site(self, *, sandbox_id: str, host: str, run_id: str) -> None:
        self.granted.append((sandbox_id, host, run_id))


class _Sandbox:
    def __init__(self, sandbox_id: str | None = "sbx-a") -> None:
        self.id = sandbox_id

    def sandbox_id(self) -> str | None:
        return self.id


def _uploads(journal: _Journal, control: _Control, *, asks: bool = True, sandbox: str | None = "sbx-a") -> SiteUploads:
    async def no_sleep(_: float) -> None:
        await asyncio.sleep(0)

    return SiteUploads(
        run_id="r1", append=journal.append, run_events=journal.read, stamp=lambda: ProgressStamp("t", "turn-1", iter(range(1000)).__next__),
        presenter=_Presenter(), ledger=_Ledger(), control=control, sandbox=_Sandbox(sandbox), journal_asks=asks,
        poll_s=0, sleep=no_sleep,
    )


async def test_siteup03_one_card_per_run_and_site_with_the_flow_rules_sources_and_a_conversation_grant() -> None:
    journal, control = _Journal(), _Control()
    uploads = _uploads(journal, control)
    assert await uploads.ask([HOST]) == 1
    assert await uploads.ask([HOST]) == 0
    frame, explained, requested = journal.events
    ix = site_upload_interaction("r1", HOST)
    assert frame.data["data"]["toolName"] == SITE_UPLOAD and frame.data["data"]["interactionId"] == ix  # type: ignore[index]
    assert explained.type == "interaction.explained" and explained.data["interactionId"] == ix
    # SEC-A-01: the same "allow for this conversation" a fetch_url card offers.
    assert (explained.data["rule"], explained.data["toolId"], explained.data["conversationGrant"]) == (SITE_UPLOAD_RULE, SITE_UPLOAD_TOOL_ID, True)
    assert explained.data["sensitivity"] == "private" and explained.data["destination"]["label"] == HOST  # type: ignore[index]
    assert (requested.type, requested.data["interactionId"], requested.data["kind"]) == ("interaction.requested", ix, "approval")
    # A later attempt reads the ask back and does not ask again.
    again = _uploads(journal, control)
    await again.refresh()
    assert await again.ask([HOST]) == 0
    await uploads.close()
    # A control plane that does not take the card: nothing is journaled, the site just stays held.
    quiet = _uploads(_Journal(), control, asks=False)
    assert await quiet.ask([HOST]) == 0


async def test_siteup04_allowed_mid_turn_releases_the_current_sandbox_and_the_next_turn_carries_it_read_write() -> None:
    journal, control = _Journal(), _Control()
    uploads = _uploads(journal, control)
    _ = await uploads.ask([HOST])
    journal.resolve(site_upload_interaction("r1", HOST), "approved")
    for _ in range(20):
        await asyncio.sleep(0)
        if control.granted:
            break
    assert control.granted == [("sbx-a", HOST, "r1")]
    assert uploads.released() == frozenset({HOST})

    class _Policy:
        async def allowed_sites(self) -> list[dict[str, Any]]:
            return [{"host": HOST, "access": "read-write", "readWriteConfirmed": True}]

    from harness_core.effects.flow import FlowRule

    sites = RunSites(_Policy(), UNCONFIGURED, _Ledger(), "r1", (FlowRule("ask"),), uploads=uploads)
    [destination] = await sites()
    assert destination.site is not None and not destination.site.held, "the next turn's sandbox writes from the start"
    await uploads.close()


async def test_siteup05_denied_stays_read_only_for_the_run_and_is_not_asked_again() -> None:
    journal, control = _Journal(), _Control()
    uploads = _uploads(journal, control)

    class _Policy:
        async def allowed_sites(self) -> list[dict[str, Any]]:
            return [{"host": HOST, "access": "read-write", "readWriteConfirmed": True}]

    from harness_core.effects.flow import FlowRule

    sites = RunSites(_Policy(), UNCONFIGURED, _Ledger(), "r1", (FlowRule("ask"),), uploads=uploads)
    [first] = await sites()
    assert first.site is not None and first.site.held
    journal.resolve(site_upload_interaction("r1", HOST), "denied")
    for _ in range(5):
        await asyncio.sleep(0)
    asked = len(journal.events)
    [later] = await sites()
    assert later.site is not None and later.site.held and len(journal.events) == asked
    assert control.granted == [] and uploads.denied() == frozenset({HOST})
    await uploads.close()


# --- SEC-A-01: a held read site is asked about when the sandbox first needs it, and a conversation grant answers it --


def _refused(journal: _Journal, host: str, reason: str = "site_request_held") -> None:
    journal.events.append(JournalEvent.from_envelope({
        "orgId": "o1", "sessionId": "s1", "streamId": "s1", "seq": len(journal.events) + 1, "runId": "r1",
        "schemaVersion": 1, "critical": False, "ts": "2026-11-02T12:00:02.000Z", "visibility": "owner",
        "writer": "node", "type": "gateway.refused",
        "data": {"gateway": "egress", "reason": reason, "status": 403, "host": host},
    }))


async def test_sec_a_01_a_held_read_site_is_asked_about_on_its_first_held_request_and_released() -> None:
    journal, control = _Journal(), _Control()
    uploads = _uploads(journal, control)
    uploads.watch_held()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not [e for e in journal.events if e.type == "interaction.requested"], "no card before the sandbox needs it"
    _refused(journal, "docs.example.org")
    _refused(journal, "elsewhere.example", reason="destination_not_allowed")
    for _ in range(50):
        await asyncio.sleep(0)
        if any(e.type == "interaction.requested" for e in journal.events):
            break
    asked = [e.data["interactionId"] for e in journal.events if e.type == "interaction.requested"]
    assert asked == [site_upload_interaction("r1", "docs.example.org")], "one card, only for the held host"
    journal.resolve(asked[0], "approved")  # type: ignore[arg-type]
    for _ in range(50):
        await asyncio.sleep(0)
        if control.granted:
            break
    assert control.granted == [("sbx-a", "docs.example.org", "r1")]
    await uploads.close()


async def test_sec_a_01_a_site_allowed_for_the_conversation_is_released_without_a_card() -> None:
    from harness_core.effects.flow import (
        ConversationGrants,
        GrantingLedger,
        destination_key,
    )

    grants = ConversationGrants([(SITE_UPLOAD_TOOL_ID, destination_key(SITE_UPLOAD_TOOL_ID, HOST), "private")])
    journal, control = _Journal(), _Control()
    uploads = _uploads(journal, control)
    uploads.ledger = GrantingLedger(_Ledger(), grants)  # type: ignore[arg-type]
    assert await uploads.ask([HOST]) == 0
    assert uploads.released() == frozenset({HOST})
    for _ in range(50):
        await asyncio.sleep(0)
        if control.granted:
            break
    assert control.granted == [("sbx-a", HOST, "r1")]
    await uploads.close()


async def test_sec_a_01_a_turn_binds_a_site_allowed_for_the_conversation_unheld() -> None:
    from harness_core.effects.flow import (
        ConversationGrants,
        FlowRule,
        GrantingLedger,
        destination_key,
    )
    from harness_core.security.classification import UNCONFIGURED

    class _Policy:
        async def allowed_sites(self) -> list[dict[str, Any]]:
            return [{"host": "docs.example.org", "access": "read"}, {"host": "other.example.org", "access": "read"}]

    grants = ConversationGrants([(SITE_UPLOAD_TOOL_ID, destination_key(SITE_UPLOAD_TOOL_ID, "docs.example.org"), "private")])
    ledger = GrantingLedger(_Ledger(), grants)  # type: ignore[arg-type]
    uploads = _uploads(_Journal(), _Control())
    uploads.ledger = ledger
    bound = await RunSites(_Policy(), UNCONFIGURED, ledger, "r1", (FlowRule("ask"),), uploads=uploads)()
    assert {d.host: d.site.held for d in bound if d.site is not None} == {"docs.example.org": False, "other.example.org": True}
    await uploads.close()
