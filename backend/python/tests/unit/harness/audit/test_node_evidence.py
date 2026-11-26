"""P9 AU-7 and RE-* on Node: the audit route of the real journal server (internal router, controller, worker
credential, ``ExecutionService.auditEvidence``) through ``NodeRunEvidenceSource``."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.audit.node_source import NodeRunEvidenceSource
from app.harness.journal_node import NodeJournal
from harness_core.audit import ExecutedCall, GrantAuditor
from harness_core.budget.types import LedgerUsage
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.journal.inline import inline_ref
from harness_core.journal.types import CommitRequest, Fail, NewEvent
from harness_core.testing.contracts.audit import (
    EvidenceEnv,
    RunEvidenceSourceContract,
    SeededRun,
)
from harness_core.testing.run_spec import minimal_run_spec
from tests.support.loopback_client import loopback_client
from tests.unit.harness.test_node_journal_contract import (
    _Credential,  # pyright: ignore[reportPrivateUsage]
    _NodeServer,  # pyright: ignore[reportPrivateUsage]
    node_server,  # noqa: F401 - the module fixture  # pyright: ignore[reportUnusedImport]
)

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-contract"
SECRET_ARGUMENT = "SENTINEL-argument-p9"
SINK = SinkDescriptor(egress="external", args_leave_boundary=True).model_dump(mode="json", by_alias=True, exclude_none=True)


class _NodeEnv:
    def __init__(self, server: _NodeServer) -> None:
        self._server = server

    async def run(
        self,
        *,
        tools: tuple[str, ...],
        intents: tuple[str, ...],
        more: tuple[NewEvent, ...] = (),
        transition: Fail | None = None,
    ) -> SeededRun:
        run_id = f"run-audit-{uuid.uuid4().hex[:8]}"
        spec = minimal_run_spec().model_dump(mode="json", by_alias=True, exclude_none=True)
        spec["grants"]["tools"] = [{"toolId": t, "descriptorDigest": "sha256:" + "0" * 64} for t in tools]
        body = {"runId": run_id, "sessionId": f"ses-{run_id}", "inputText": "audit me", "spec": spec}
        _ = self._server.control.post("/admit", json=body).raise_for_status()
        journal = NodeJournal(base_url=self._server.base_url, credentials=_Credential(self._server.credential))
        try:
            claim = await journal.claim(run_id, worker_id="w-audit", org_id=ORG)
            assert claim is not None
            events = tuple(
                NewEvent(
                    "tool.intent",
                    {"invocationId": f"inv-{n}", "toolId": tool, "argsDigest": "sha256:" + "1" * 64,
                     "effect": "write", "sink": SINK},
                )
                for n, tool in enumerate(intents)
            ) + more
            if events or transition is not None:
                _ = await journal.commit(CommitRequest(f"cmd-{run_id}", claim.token, events, transition))
        finally:
            await journal.aclose()
        routes = frozenset(f"{r['provider']}:{r['model']}" for r in spec["model"]["policy"])
        source = NodeRunEvidenceSource(base_url=self._server.base_url, credentials=_Credential(self._server.credential))
        return SeededRun(source, ORG, run_id, routes)


class TestNodeRunEvidenceSource(RunEvidenceSourceContract):
    @pytest.fixture
    def evidence_env(self, node_server: _NodeServer) -> Iterator[EvidenceEnv]:  # noqa: F811
        _ = node_server.control.post("/reset").raise_for_status()
        yield _NodeEnv(node_server)


async def test_au7_the_route_needs_the_worker_credential_and_carries_no_content(node_server: _NodeServer) -> None:  # noqa: F811
    _ = node_server.control.post("/reset").raise_for_status()
    seeded = await _NodeEnv(node_server).run(tools=("crm@inst-1/create",), intents=("crm@inst-1/create",))
    path = f"{node_server.base_url}/api/v1/agent-harness/internal/runs/{seeded.run_id}/audit"
    async with loopback_client() as client:
        assert (await client.get(path, params={"orgId": ORG})).status_code == 401
        wrong = await client.get(path, params={"orgId": ORG}, headers={"Authorization": "Bearer not-the-credential"})
        assert wrong.status_code == 401
        ok = await client.get(path, params={"orgId": ORG}, headers={"Authorization": f"Bearer {node_server.credential}"})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"orgId", "runId", "pinned", "toolIds", "routeIds", "intents", "ended", "usage", "ledger"}
    assert body["pinned"] is True
    assert set(body["intents"][0]) == {"invocationId", "toolId", "attemptId"}
    assert "audit me" not in ok.text and SECRET_ARGUMENT not in ok.text


async def _ended_run(server: _NodeServer, *, input_tokens: int, output_tokens: int) -> str:
    """A run whose one turn journaled ``model.response`` with those counts, then ended."""
    response = {
        "messageRef": inline_ref(b'{"text":"ok","toolCalls":[]}').model_dump(mode="json", by_alias=True, exclude_none=True),
        "toolCalls": [],
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens, "inputIncludesCached": True},
        "provider": "openai",
        "transport": "chat",
    }
    events = (NewEvent("turn.started", {"turn": 1}), NewEvent("model.response", response))
    seeded = await _NodeEnv(server).run(tools=(), intents=(), more=events, transition=Fail("kernel_error"))
    return seeded.run_id


async def test_au8_c901_an_ended_runs_journal_usage_is_reconciled_with_its_ledger(node_server: _NodeServer) -> None:  # noqa: F811
    """C9.01, EV-10 through the real route: the auditor reads the journal's usage and the ledger's ``used``; equal is
    reconciled, a difference is one ``usage_unreconciled`` violation, an account no settler told a usage is skipped."""
    _ = node_server.control.post("/reset").raise_for_status()
    matched = await _ended_run(node_server, input_tokens=40, output_tokens=7)
    drifted = await _ended_run(node_server, input_tokens=40, output_tokens=7)
    unmetered = await _ended_run(node_server, input_tokens=40, output_tokens=7)
    for run_id, usage in ((matched, (40, 7)), (drifted, (95, 9))):
        _ = node_server.control.post("/budget/open-root", json={"runId": run_id, "cap": {}}).raise_for_status()
        _ = node_server.control.post(
            "/budget/charge",
            json={"runId": run_id, "key": "call-1", "usage": {
                "inputTokens": usage[0], "outputTokens": usage[1], "cacheReadTokens": 0, "cacheWriteTokens": 0}},
        ).raise_for_status()
    _ = node_server.control.post("/budget/open-root", json={"runId": unmetered, "cap": {}}).raise_for_status()
    source = NodeRunEvidenceSource(base_url=node_server.base_url, credentials=_Credential(node_server.credential))
    try:
        found = await source.evidence(ORG, matched)
        assert found is not None and found.ended
        assert found.ledger is not None and found.ledger.used == LedgerUsage(40, 7)
        assert [(u.type, u.input_tokens, u.output_tokens) for u in found.usage] == [("model.response", 40, 7)]
        auditor = GrantAuditor(source)
        calls = [ExecutedCall("model_gateway", ORG, run, "unpinned-route") for run in (matched, drifted, unmetered)]
        report = await auditor.audit(calls)
    finally:
        await source.aclose()
    usage = [v for v in report.violations if v.code == "usage_unreconciled"]
    assert [v.call.run_id for v in usage] == [drifted]
    assert usage[0].detail["journalInputTokens"] == 40 and usage[0].detail["ledgerInputTokens"] == 95
    assert report.reconciled == 1
