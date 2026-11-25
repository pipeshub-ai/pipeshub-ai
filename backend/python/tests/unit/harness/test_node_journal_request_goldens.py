"""WP3-1..3: what each Node-calling client puts on the wire, what it answers and what it logs, pinned by a golden.

The golden was recorded before the six clients moved onto ``NodeServiceClient``; the move must leave every entry
unchanged. Clients are built the production way (no injected ``http_client``): ``httpx.AsyncClient`` is replaced by
a subclass that only swaps in a mock transport, so the base URL, timeout and redirect setting each client chose are
recorded as built. Re-record with ``PXH_RECORD_GOLDENS=1`` only for an intended change.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import BaseModel

from app.harness.access.budget import NodeBudgetLedger
from app.harness.access.grants import NodeRunModelGrants
from app.harness.access.refusals import NodeRefusalSink
from app.harness.audit.node_source import NodeRunEvidenceSource
from app.harness.gateway.tokens import NodeGatewayTokens, NodeGatewayTokenSource
from app.harness.journal_node import NodeClaim, NodeJournal
from harness_core.budget.types import (
    Amount,
    LedgerUsage,
    Reservation,
    ReservationState,
    RunAccountRef,
)
from harness_core.contracts import dump_contract
from harness_core.gateway.refusal_reports import (
    EgressAllowance,
    EgressVolumeAlert,
    RefusalReport,
)
from harness_core.gateway.types import GatewayClaims
from harness_core.journal.children import ChildRequest
from harness_core.journal.types import CommitRequest, NewEvent, Suspend
from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.types import TokenRoute
from harness_core.testing.contracts.model_access import run_binding
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

GOLDEN = Path(__file__).with_name("node_request_goldens.json")
RECORD = os.environ.get("PXH_RECORD_GOLDENS") == "1"

SECRETS = {
    "phw-SECRET-worker": "<worker>",
    "tok-SECRET-attempt": "<attempt>",
    "tok-SECRET-renewed": "<renewed>",
    "gw-SECRET-run": "<run-token>",
}
# Values that follow the installed httpx (and its optional codecs), not the client under test.
_VOLATILE_HEADERS = frozenset({"user-agent", "accept-encoding"})
_ERROR_ATTRS = ("reason", "detail", "http_status", "reason_code", "refusal")
_UNREACHABLE = httpx.ConnectError("connection refused")

Reply = httpx.Response | Exception


def _redact(text: str) -> str:
    for secret, placeholder in SECRETS.items():
        text = text.replace(secret, placeholder)
    return text


def _canon(value: object) -> Any:  # noqa: ANN401 - JSON of any shape
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _canon(getattr(value, f.name)) for f in dataclasses.fields(value) if f.repr}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return _canon(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in cast("dict[object, object]", value).items()}
    if isinstance(value, list | tuple):
        return [_canon(v) for v in cast("tuple[object, ...]", value)]
    if isinstance(value, set | frozenset):
        return sorted((_canon(v) for v in cast("frozenset[object]", value)), key=json.dumps)
    if isinstance(value, bytes):
        return {"bytes": value.decode("latin-1")}
    if isinstance(value, httpx.Response):
        return {"status": value.status_code}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return {"type": type(value).__qualname__}


def _claim_summary(claim: NodeClaim) -> dict[str, Any]:
    return {
        "runId": claim.run_id,
        "orgId": claim.org_id,
        "streamId": claim.stream_id,
        "attemptId": claim.attempt_id,
        "fence": claim.fence,
        "lastSeq": claim.last_seq,
        "accepts": sorted(claim.accepts or ()),
    }


def _error(exc: BaseException) -> dict[str, Any]:
    cls = type(exc)
    return {
        "type": f"{cls.__module__}.{cls.__qualname__}",
        "str": str(exc),
        "attrs": {name: _canon(getattr(exc, name)) for name in _ERROR_ATTRS if hasattr(exc, name)},
        "cause": type(exc.__cause__).__qualname__ if exc.__cause__ is not None else None,
    }


class _Wire:
    """A fake Node answering scripted replies, recording each request and each client built."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.clients: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(self._shape(request))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @staticmethod
    def _shape(request: httpx.Request) -> dict[str, Any]:
        content = request.content
        body: Any
        if not content:
            body = None
        elif request.headers.get("content-type") == "application/json":
            body = json.loads(content)
        else:
            body = {"bytes": content.decode("latin-1")}
        return {
            "method": request.method,
            "url": str(request.url),
            "headers": {k: _redact(v) for k, v in request.headers.items() if k not in _VOLATILE_HEADERS},
            "body": body,
            "timeout": request.extensions.get("timeout"),
        }


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[Reply]], _Wire]:
    real = httpx.AsyncClient

    def install(replies: list[Reply]) -> _Wire:
        node = _Wire(replies)

        class Recording(real):
            def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - httpx's own signature
                kwargs["transport"] = httpx.MockTransport(node.handle)
                super().__init__(*args, **kwargs)
                node.clients.append(
                    {
                        "baseUrl": str(self.base_url),
                        "timeout": self.timeout.as_dict(),
                        "followRedirects": self.follow_redirects,
                        "limits": "limits" in kwargs,
                    }
                )

        monkeypatch.setattr(httpx, "AsyncClient", Recording)
        return node

    return install


class _Credential:
    async def worker_credential(self) -> str:
        return "phw-SECRET-worker"


def _ok(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _refused(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": f"HTTP_AGENT_HARNESS_{code}", "message": "refused"}})


def _run(major: int = 2) -> dict[str, Any]:
    return {
        "runId": "run/1",
        "orgId": "org-1",
        "sessionId": "ses-1",
        "actorId": "actor-1",
        "userMessageKey": "m1",
        "idempotencyKey": "k1",
        "payloadHash": "sha256:x",
        "harnessMajor": major,
        "profile": "dispatched",
        "status": "running",
        "outcome": None,
        "revision": 4,
        "fence": 2,
        "attempts": 2,
        "notBefore": 0,
        "checkpointRef": None,
        "attemptId": "att-1",
        "workerId": "w1",
        "leaseExpiresAt": 5,
        "absoluteDeadlineAt": 10,
        "checkpoint": None,
        "input": {"query": "hello", "recordIds": [], "filters": {}, "attachments": [], "model": {"chatMode": "quick"}},
        "createdAt": 0,
        "updatedAt": 0,
    }


def _claimed(major: int = 2) -> httpx.Response:
    body: dict[str, Any] = {
        "run": _run(major),
        "journal": {"lastSeq": {"ses-1": 3}},
        "attemptToken": {"token": "tok-SECRET-attempt", "expiresAt": 1},
        "nodeCapabilities": {"features": ["turn_labels"]},
    }
    if major == 2:
        body["pinned"] = {
            "spec": dump_contract(minimal_run_spec()),
            "specDigest": "sha256:" + "1" * 64,
            "capabilities": {"journal.durable": True},
            "capabilitiesDigest": "sha256:" + "2" * 64,
        }
    return _ok(body)


def _envelope(seq: int) -> dict[str, Any]:
    return {
        "orgId": "org-1",
        "sessionId": "ses-1",
        "streamId": "ses-1",
        "seq": seq,
        "runId": "run/1",
        "attemptId": "att-1",
        "fence": 2,
        "type": "turn.started",
        "critical": True,
        "ts": "2026-09-21T00:00:00.000Z",
        "writer": "worker",
        "data": {"turn": 1},
    }


async def _journal() -> tuple[NodeJournal, NodeClaim]:
    journal = NodeJournal(base_url="http://node.internal:3000/", credentials=_Credential())
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    return journal, claim


ACCOUNT = RunAccountRef("org-1", "run/1")
_AMOUNT = {"usdMicros": 500, "tokens": 100}
_RESERVATION = {
    "key": "res/1",
    "state": "open",
    "requested": _AMOUNT,
    "granted": _AMOUNT,
    "expiresAt": 1_790_000_000_000,
}
_REMAINING = {
    "available": {"usdMicros": None, "tokens": 900},
    "committed": _AMOUNT,
    "reserved": _AMOUNT,
    "exhausted": False,
}
_SETTLED = {"reservation": _RESERVATION, "charged": _AMOUNT, "crossed": False, "remaining": _REMAINING}


def _reservation() -> Reservation:
    return Reservation(
        account=ACCOUNT,
        key="res/1",
        requested=Amount(500, 100),
        granted=Amount(500, 100),
        expires_at=datetime.fromisoformat("2026-09-21T00:00:00+00:00"),
        state=ReservationState.OPEN,
    )


def _ledger() -> NodeBudgetLedger:
    return NodeBudgetLedger(base_url="http://node.internal:3000/")


_GRANT = {
    "runId": "run-1",
    "routes": [{"provider": "openai", "model": "gpt-x", "modelKey": "key-1", "modelName": "gpt-x"}],
    "budgets": {"maxCostUsd": 1.5, "maxTokens": 1000},
    "reasoningEffort": "low",
    "actorId": "actor-1",
    "engine": "loop",
}


async def _grant() -> object:
    grants = NodeRunModelGrants(base_url="http://node.internal:3000/", monotonic=lambda: 0.0, wall=lambda: 1_000.0)
    return await grants.grant(GatewayClaims(run_binding("run/1"), "kid-1", 2_000), "gw-SECRET-run")


async def _allowed() -> object:
    sink = NodeRefusalSink(base_url="http://node.internal:3000/")
    return await sink.allowed("run/1", "gw-SECRET-run", EgressAllowance("api.github.com", "intercepted"))


async def _volume() -> object:
    sink = NodeRefusalSink(base_url="http://node.internal:3000/")
    alert = EgressVolumeAlert("org_baseline", "up", 734003200, 104857600, "tunnel", baseline_bytes=10485760)
    return await sink.volume("run/1", "gw-SECRET-run", alert)


async def _refusal() -> object:
    sink = NodeRefusalSink(base_url="http://node.internal:3000/")
    return await sink.record("run/1", "gw-SECRET-run", RefusalReport("model", "budget_exhausted", 403, host="h", param="p"))


async def _evidence() -> object:
    source = NodeRunEvidenceSource(base_url="http://node.internal:3000/", credentials=_Credential())
    return await source.evidence("org-1", "run/1")


_EVIDENCE = {
    "toolIds": ["t2", "t1"],
    "routeIds": ["r1"],
    "intents": [{"toolId": "t1", "attemptId": "att-1", "invocationId": "inv-1"}],
}


def _egress(labels: dict[str, str], attestation: str | None) -> EgressBinding:
    return EgressBinding("sbx-1", "10.213.1.2", "10.213.1.3", labels=labels, attestation=attestation)


async def _token(labels: dict[str, str] | None = None, attestation: str | None = "v1.broker-proof") -> object:
    tokens = NodeGatewayTokens(base_url="http://node.internal:3000/", credentials=_Credential())
    named = labels if labels is not None else {"orgId": "org-1", "runId": "run/1", "attemptId": "att-1"}
    binding = _egress(named, attestation)
    return await NodeGatewayTokenSource(tokens).issue(TokenRoute("ph-1", "model-gateway", "gateway:model"), binding)


async def _commit() -> object:
    journal, claim = await _journal()
    events = (NewEvent("turn.started", {"turn": 1}),)
    return await journal.commit(CommitRequest("cmd-1", claim.token, events, Suspend("approval", "ix-1")))


async def _read() -> object:
    journal, claim = await _journal()
    return await journal.read(claim.stream_id, after_seq=3)


async def _renew_then_release() -> object:
    journal, claim = await _journal()
    grant = await journal.renew(claim.token)
    await journal.release(claim.token, reason="yield")
    return grant


async def _put_content() -> object:
    _, claim = await _journal()
    assert claim.content is not None
    return await claim.content.put(b"sealed bytes")


async def _get_content() -> object:
    _, claim = await _journal()
    assert claim.content is not None
    return await claim.content.get("sealed:abc")


async def _spawn() -> object:
    journal, claim = await _journal()
    return await journal.spawn(claim.token, ChildRequest("inv-1", "find it", task_id="task-1"))


async def _route_get() -> object:
    journal, claim = await _journal()
    return await journal.attempt_route(claim.token).get("/workspace", params={"path": "a b"}, operation="ws.read")


async def _route_post_after_forget() -> object:
    journal, claim = await _journal()
    route = journal.attempt_route(claim.token)
    journal.forget(claim.token)
    return await route.post("/workspace", body={"path": "a"}, operation="ws.write")


async def _followups() -> object:
    _, claim = await _journal()
    assert claim.followups is not None
    return await claim.followups.deliver(["next?"])


async def _attempt_budget() -> object:
    _, claim = await _journal()
    assert claim.budget is not None
    reserved = await claim.budget.reserve(ACCOUNT, "res/1", Amount(500, 100), minimum=Amount(1, 1), ttl_s=30)
    return [reserved, await claim.budget.remaining(ACCOUNT)]


async def _claim_by_id() -> object:
    journal = NodeJournal(base_url="http://node.internal:3000", credentials=_Credential(), role="inline", lease_ms=30_000)
    return await journal.claim("run/1", worker_id="w1", org_id="org-1")


async def _claim_next() -> object:
    return await NodeJournal(base_url="http://node.internal:3000/", credentials=_Credential()).claim(worker_id="w1")



async def _lent_client_after_aclose(build: Callable[[httpx.AsyncClient], object]) -> object:
    lent = httpx.AsyncClient(base_url="http://node.internal:3000")
    await cast("Any", build(lent)).aclose()
    return {"lentClientClosed": lent.is_closed}


_URL = "http://node.internal:3000"


CASES: dict[str, tuple[Callable[[], Awaitable[object]], Callable[[], list[Reply]]]] = {
    # NodeJournal
    "journal.claim.ok": (_claim_next, lambda: [_claimed()]),
    "journal.claim.empty": (_claim_next, lambda: [httpx.Response(204)]),
    "journal.claim.not_claimable": (_claim_next, lambda: [_refused(409, "RUN_NOT_CLAIMABLE")]),
    "journal.claim.refused": (_claim_next, lambda: [_refused(401, "WORKER_CREDENTIAL")]),
    "journal.claim.busy": (_claim_next, lambda: [_refused(503, "UNAVAILABLE")]),
    "journal.claim.unreadable": (_claim_next, lambda: [httpx.Response(200, text="<html>")]),
    "journal.claim.unreachable": (_claim_next, lambda: [_UNREACHABLE]),
    "journal.claim_by_id.ok": (_claim_by_id, lambda: [_claimed()]),
    "journal.claim_by_id.v1_retired": (_claim_by_id, lambda: [_claimed(major=1), _ok({"replayed": False})]),
    "journal.commit.ok": (_commit, lambda: [_claimed(), _ok({"events": [_envelope(4)], "replayed": False})]),
    "journal.commit.conflict": (_commit, lambda: [_claimed(), _refused(409, "IDEMPOTENCY_CONFLICT")]),
    "journal.commit.stale": (_commit, lambda: [_claimed(), _refused(409, "STALE_ATTEMPT")]),
    "journal.commit.unavailable": (_commit, lambda: [_claimed(), _refused(429, "BUSY")]),
    "journal.commit.unreachable": (_commit, lambda: [_claimed(), _UNREACHABLE]),
    "journal.read.ok": (_read, lambda: [_claimed(), _ok({"events": [_envelope(4), _envelope(5)]})]),
    "journal.read.unreachable": (_read, lambda: [_claimed(), _UNREACHABLE]),
    "journal.renew_release.ok": (
        _renew_then_release,
        lambda: [_claimed(), _ok({"attemptToken": {"token": "tok-SECRET-renewed"}}), _ok({})],
    ),
    "journal.renew.lost": (_renew_then_release, lambda: [_claimed(), _refused(401, "ATTEMPT_CLAIMS")]),
    "journal.content_put.ok": (_put_content, lambda: [_claimed(), _ok({"ref": "sealed:abc"})]),
    "journal.content_put.no_ref": (_put_content, lambda: [_claimed(), _ok({})]),
    "journal.content_put.unreachable": (_put_content, lambda: [_claimed(), _UNREACHABLE]),
    "journal.content_get.ok": (_get_content, lambda: [_claimed(), httpx.Response(200, content=b"opened")]),
    "journal.content_get.missing": (_get_content, lambda: [_claimed(), _refused(404, "JOURNAL_PAYLOAD_MISSING")]),
    "journal.content_get.unreachable": (_get_content, lambda: [_claimed(), _UNREACHABLE]),
    "journal.spawn.ok": (_spawn, lambda: [_claimed(), _ok({"childRunId": "child-1", "created": True})]),
    "journal.spawn.refused": (_spawn, lambda: [_claimed(), _refused(409, "DEPTH_EXCEEDED")]),
    "journal.spawn.unreachable": (_spawn, lambda: [_claimed(), _UNREACHABLE]),
    "journal.route_get.ok": (_route_get, lambda: [_claimed(), _ok({"entries": []})]),
    "journal.route_get.unreachable": (_route_get, lambda: [_claimed(), _UNREACHABLE]),
    "journal.route_post_settling.ok": (_route_post_after_forget, lambda: [_claimed(), _ok({})]),
    "journal.followups.ok": (_followups, lambda: [_claimed(), _ok({})]),
    "journal.followups.refused": (_followups, lambda: [_claimed(), _refused(409, "STALE_ATTEMPT")]),
    # AttemptBudgetAccounts (over the journal's attempt route)
    "budget.attempt.ok": (
        _attempt_budget,
        lambda: [_claimed(), _ok({"reservation": _RESERVATION}), _ok({"remaining": _REMAINING})],
    ),
    "budget.attempt.unreachable": (_attempt_budget, lambda: [_claimed(), _UNREACHABLE]),
    # NodeBudgetLedger
    "budget.ledger.reserve.ok": (
        lambda: _ledger().reserve(
            ACCOUNT, "res/1", Amount(500, 100), minimum=Amount(1, 1), ttl_s=30, opening=Amount(9, 9),
            credential="gw-SECRET-run",
        ),
        lambda: [_ok({"reservation": _RESERVATION})],
    ),
    "budget.ledger.reserve.no_credential": (
        lambda: _ledger().reserve(ACCOUNT, "res/1", Amount(500, 100), minimum=Amount(1, 1), ttl_s=30),
        lambda: [_refused(401, "ATTEMPT_CLAIMS")],
    ),
    "budget.ledger.reserve.empty_credential": (
        lambda: _ledger().reserve(ACCOUNT, "res/1", Amount(500, 100), minimum=Amount(1, 1), ttl_s=30, credential=""),
        lambda: [_ok({"reservation": _RESERVATION})],
    ),
    "budget.ledger.reserve.exhausted": (
        lambda: _ledger().reserve(ACCOUNT, "k", Amount(1, 1), minimum=Amount(1, 1), ttl_s=30, credential="gw-SECRET-run"),
        lambda: [_refused(409, "BUDGET_EXHAUSTED")],
    ),
    "budget.ledger.commit.ok": (
        lambda: _ledger().commit(_reservation(), Amount(400, 80), credential="gw-SECRET-run"),
        lambda: [_ok(_SETTLED)],
    ),
    "budget.ledger.commit.usage": (
        lambda: _ledger().commit(_reservation(), Amount(400, 80), usage=LedgerUsage(70, 10, 40, 2), credential="gw-SECRET-run"),
        lambda: [_ok({**_SETTLED, "remaining": {**_REMAINING, "used": {
            "inputTokens": 70, "outputTokens": 10, "cacheReadTokens": 40, "cacheWriteTokens": 2}}})],
    ),
    "budget.ledger.release.unavailable": (
        lambda: _ledger().release(_reservation(), credential="gw-SECRET-run"),
        lambda: [_refused(503, "UNAVAILABLE")],
    ),
    "budget.ledger.remaining.ok": (
        lambda: _ledger().remaining(ACCOUNT, credential="gw-SECRET-run"),
        lambda: [_ok({"remaining": _REMAINING})],
    ),
    "budget.ledger.remaining.malformed": (
        lambda: _ledger().remaining(ACCOUNT, credential="gw-SECRET-run"),
        lambda: [httpx.Response(200, text="nope")],
    ),
    "budget.ledger.remaining.unreachable": (
        lambda: _ledger().remaining(ACCOUNT, credential="gw-SECRET-run"),
        lambda: [_UNREACHABLE],
    ),
    # NodeRunModelGrants
    "grants.ok": (_grant, lambda: [_ok(_GRANT)]),
    "grants.not_leased": (_grant, lambda: [_refused(409, "RUN_NOT_LEASED")]),
    "grants.unauthorized": (_grant, lambda: [_refused(401, "ATTEMPT_CLAIMS")]),
    "grants.server_error": (_grant, lambda: [httpx.Response(500)]),
    "grants.malformed": (_grant, lambda: [httpx.Response(200, text="nope")]),
    "grants.unreachable": (_grant, lambda: [_UNREACHABLE]),
    # NodeRefusalSink
    "refusals.recorded": (_refusal, lambda: [_ok({"outcome": "recorded"})]),
    "refusals.refused": (_refusal, lambda: [_refused(403, "ATTEMPT_CLAIMS")]),
    "refusals.server_error": (_refusal, lambda: [httpx.Response(500)]),
    "refusals.no_outcome": (_refusal, lambda: [_ok({})]),
    "refusals.unreachable": (_refusal, lambda: [_UNREACHABLE]),
    "egress.allowed.recorded": (_allowed, lambda: [_ok({"outcome": "recorded"})]),
    "egress.allowed.refused": (_allowed, lambda: [_refused(401, "ATTEMPT_CLAIMS")]),
    "egress.volume.recorded": (_volume, lambda: [_ok({"outcome": "recorded"})]),
    "egress.volume.refused": (_volume, lambda: [_refused(401, "ATTEMPT_CLAIMS")]),
    # NodeRunEvidenceSource
    "audit.ok": (_evidence, lambda: [_ok(_EVIDENCE)]),
    "audit.absent": (_evidence, lambda: [_refused(404, "RUN_NOT_FOUND")]),
    "audit.server_error": (_evidence, lambda: [httpx.Response(502)]),
    "audit.unreachable": (_evidence, lambda: [_UNREACHABLE]),
    # NodeGatewayTokens behind NodeGatewayTokenSource
    "tokens.issued": (_token, lambda: [_ok({"token": "gw-SECRET-run", "expiresAt": 1_790_000_600_000})]),
    "tokens.not_leased": (_token, lambda: [_refused(409, "RUN_NOT_LEASED")]),
    "tokens.malformed": (_token, lambda: [_ok({"token": "gw-SECRET-run"})]),
    "tokens.unreachable": (_token, lambda: [_UNREACHABLE]),
    # Who closes a client a caller lent
    "lifecycle.journal": (
        lambda: _lent_client_after_aclose(lambda c: NodeJournal(base_url=_URL, credentials=_Credential(), http_client=c)),
        lambda: [],
    ),
    "lifecycle.budget": (
        lambda: _lent_client_after_aclose(lambda c: NodeBudgetLedger(base_url=_URL, http_client=c)),
        lambda: [],
    ),
    "lifecycle.grants": (
        lambda: _lent_client_after_aclose(lambda c: NodeRunModelGrants(base_url=_URL, http_client=c)),
        lambda: [],
    ),
    "lifecycle.refusals": (
        lambda: _lent_client_after_aclose(lambda c: NodeRefusalSink(base_url=_URL, http_client=c)),
        lambda: [],
    ),
    "lifecycle.audit": (
        lambda: _lent_client_after_aclose(
            lambda c: NodeRunEvidenceSource(base_url=_URL, credentials=_Credential(), http_client=c)
        ),
        lambda: [],
    ),
    "tokens.unbound_sandbox": (lambda: _token({"runId": "run/1"}), lambda: []),
    # R38IDENT-FU-7: a binding the broker did not sign is never asked for.
    "tokens.unattested_sandbox": (lambda: _token(attestation=None), lambda: []),
}


async def _record(case: str, install: Callable[[list[Reply]], _Wire], caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    action, replies = CASES[case]
    node = install(replies())
    caplog.clear()
    outcome: dict[str, Any]
    with caplog.at_level(logging.DEBUG):
        try:
            value = await action()
        except Exception as exc:
            outcome = {"raised": _error(exc)}
            leaked = f"{exc!s} {exc!r}"
        else:
            canon = _claim_summary(value) if isinstance(value, NodeClaim) else _canon(value)
            # A token source returns its token by design: the golden holds its placeholder.
            outcome = {"returned": json.loads(_redact(json.dumps(canon)))}
            leaked = ""
    logs = [
        {
            "logger": record.name,
            "level": record.levelname,
            "event": getattr(record, "harness_event", None),
            "fields": {k: v for k, v in getattr(record, "harness", {}).items() if k != "ms"},
        }
        if hasattr(record, "harness_event")
        else {"logger": record.name, "level": record.levelname, "message": record.getMessage()}
        for record in caplog.records
        if not record.name.startswith("asyncio")
    ]
    leaked += " ".join(record.getMessage() for record in caplog.records)
    for secret in SECRETS:
        assert secret not in leaked, f"{case}: a secret reached an error or a log line"
    assert not node.replies, f"{case}: fewer requests than scripted replies"
    return {"clients": node.clients, "requests": node.requests, "outcome": outcome, "logs": logs}


def _load() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("case", sorted(CASES))
async def test_wp3_request_shape_outcome_and_logs_match_the_golden(
    case: str, wire: Callable[[list[Reply]], _Wire], caplog: pytest.LogCaptureFixture
) -> None:
    recorded = json.loads(json.dumps(await _record(case, wire, caplog)))
    if RECORD:
        goldens = _load()
        goldens[case] = recorded
        _ = GOLDEN.write_text(json.dumps(dict(sorted(goldens.items())), indent=1, sort_keys=True) + "\n")
        return
    assert recorded == _load()[case]


def test_wp3_the_golden_holds_every_case_and_nothing_else() -> None:
    assert sorted(_load()) == sorted(CASES)
