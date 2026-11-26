"""WP-64 B end to end: a toolset's sign-in rejected mid-run pauses the run on Node, and a reconnect resumes it.

The real Node journal (journal-http-server.ts: internal routes, attempt tokens, interactions, resolveActive) over
HTTP, the real assembler, RunDriver and loop kernel, a scripted model and the assembly world's Jira. Jira rejects the
saved sign-in on the first search (WP62-TS withdraws it); the second search waits on a connect (the chat's connect
card, the inbox and a link all show Node's open ``ix.connect.*``). Reconnected and answered, Node resumes the run and
the search runs once; skipped, the run goes on without Jira and the model is told why.
"""

from __future__ import annotations

import shutil
from dataclasses import fields
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.agents.actions.util.sign_in_rejected import note_sign_in_rejected
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.journal_node import NodeJournal
from harness_core.contracts import dump_contract
from harness_core.driver import RunDriver
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w
from tests.unit.harness.test_node_journal_contract import (
    NODE_APP,
    _Credential,
    _NodeServer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-contract"
JIRA_KEY = f"/services/toolsets/{w.JIRA}/{w.ACTOR}"
VENDOR_SAID = "Jira did not accept the saved sign-in. Jira said: Bad credentials. Reconnect Jira under Your actions."
WAIT = "ix.connect.t2.0"


@pytest.fixture(scope="module")
def node_server() -> Iterator[_NodeServer]:
    if not (NODE_APP / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.skip("the Node workspace is not installed (npm ci)")
    server = _NodeServer()
    yield server
    server.control.close()
    server.stop()


class _Attach:
    def __init__(self, inner: PipesHubRuntimeAssembler) -> None:
        self._inner = inner

    async def build(self, claim: Any) -> Any:  # noqa: ANN401
        node = w.claim(claim.spec, query=claim.input_text, run_id=claim.run_id)
        values = {f.name: getattr(claim, f.name) for f in fields(claim) if f.name != "run"}
        return await self._inner.build(type(node)(**values, run=node.run))


class _Jira:
    def __init__(self) -> None:
        self.rejecting = True
        self.searched: list[str] = []


@pytest.fixture
def jira(monkeypatch: pytest.MonkeyPatch) -> _Jira:
    held = _Jira()

    async def search_issues(self: w.FakeJira, jql: str) -> Any:  # noqa: ANN401
        held.searched.append(jql)
        if held.rejecting:
            note_sign_in_rejected()
            return False, VENDOR_SAID
        return f'{{"issues": ["{jql}"]}}'

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)
    return held


def _script() -> ScriptedModelPort:
    search = lambda jql: Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": jql})]))  # noqa: E731
    return ScriptedModelPort(
        ScriptedModel(ModelScript(id="wp64", turns=[search("first"), search("second"), Turn(reply=Reply(text="done"))]))
    )


async def _settled(
    node_server: _NodeServer, services: w.Services, port: ScriptedModelPort, run_id: str
) -> tuple[Any, list[Any]]:
    """Attempts until the run suspends or ends: a decision that lands before an attempt's tools load yields it once
    (``capability_wait``), and the next attempt assembles them afresh (G-12)."""
    for _ in range(3):
        done, events = await _attempt(node_server, services, port, run_id)
        if done.status != "yielded":
            return done, events
    raise AssertionError(f"the run kept yielding: {done}")


async def _attempt(node_server: _NodeServer, services: w.Services, port: ScriptedModelPort, run_id: str) -> tuple[Any, list[Any]]:
    journal = NodeJournal(
        base_url=node_server.base_url,
        credentials=_Credential(node_server.credential),
        http_client=httpx.AsyncClient(base_url=node_server.base_url),
        lease_ms=30_000,
    )
    try:
        assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
        driver = RunDriver(journal=journal, assembler=_Attach(assembler), clock=ManualClock(), renew_interval_s=None)
        claim = await journal.claim(run_id, worker_id="wp64-worker", org_id=ORG)
        assert claim is not None
        _ = await journal.renew(claim.token)
        done = await driver.run_claimed(claim)
        events = await journal.read(claim.stream_id)
    finally:
        await journal.aclose()
    return done, events


def _admit(node_server: _NodeServer, run_id: str) -> None:
    _ = node_server.control.post("/reset").raise_for_status()
    spec = w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"], kb=[])
    _ = node_server.control.post(
        "/admit",
        json={"runId": run_id, "sessionId": f"ses-{run_id}", "inputText": "find my issues", "spec": dump_contract(spec)},
    ).raise_for_status()


async def test_wp64_e2e_1_a_rejected_sign_in_pauses_on_node_and_reconnecting_resumes_the_run(
    node_server: _NodeServer, jira: _Jira
) -> None:
    _admit(node_server, "run-reconnect")
    services = w.Services(org=ORG)
    port = _script()
    with w.deployment():
        paused, events = await _attempt(node_server, services, port, "run-reconnect")
        assert paused.status == "suspended" and paused.reason == WAIT, paused
        (requested,) = [e for e in events if e.type == "interaction.requested"]
        assert requested.data["kind"] == "connect"
        assert requested.data["connect"] == {"item": "Jira", "itemKind": "toolset", "instanceId": w.JIRA, "reason": "sign_in_rejected"}
        assert jira.searched == ["first"], "the second search waits; Jira never sees the rejected sign-in again"
        assert (await services.config.get_config(JIRA_KEY))["deauthReason"] == "sign_in_rejected"

        # The person reconnects Jira (the connect card's sign-in writes a new record) and the card answers the wait.
        services.config.put(JIRA_KEY, {"isAuthenticated": True, "auth": {"token": "new-token"}})
        jira.rejecting = False
        _ = node_server.control.post(
            "/resolve", json={"runId": "run-reconnect", "interactionId": WAIT, "decision": "approved"}
        ).raise_for_status()
        resumed, events = await _settled(node_server, services, port, "run-reconnect")
    assert resumed.status == "completed", resumed
    assert jira.searched == ["first", "second"], "the waiting search ran exactly once after the reconnect"
    assert events[-1].type == "run.completed"


async def test_wp64_e2e_2_skipped_the_run_goes_on_without_the_toolset_and_says_why(
    node_server: _NodeServer, jira: _Jira
) -> None:
    _admit(node_server, "run-skip")
    services = w.Services(org=ORG)
    port = _script()
    with w.deployment():
        paused, _ = await _attempt(node_server, services, port, "run-skip")
        assert paused.reason == WAIT
        _ = node_server.control.post(
            "/resolve", json={"runId": "run-skip", "interactionId": WAIT, "decision": "denied"}
        ).raise_for_status()
        done, events = await _settled(node_server, services, port, "run-skip")
    assert done.status == "completed", done
    assert jira.searched == ["first"]
    denied = [e for e in events if e.type == "tool.denied"]
    assert len(denied) == 1 and denied[0].data["rule"] == "interaction_denied"
    assert "Jira was not connected" in str(port.calls[-1].messages[-1]), "the model is told, in plain words"


def test_wp64_e2e_3_the_claim_reads_the_trees_skips_as_node_names_them() -> None:
    """Node names the run tree's skipped items on the claim (`skippedConnects`); an older Node names none."""
    from app.harness.journal_node.client import _skipped_connects  # pyright: ignore[reportPrivateUsage]

    assert _skipped_connects(["gh-1", "jira-2", "", 7]) == frozenset({"gh-1", "jira-2"})
    assert _skipped_connects(None) == frozenset()
