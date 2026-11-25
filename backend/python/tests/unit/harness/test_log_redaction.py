"""LG-4: no prompt, tool argument, tool result, answer, token or credential reaches a log.

One durable run end to end, captured at DEBUG for every logger: the Node
journal (real router, attempt-token authority) over HTTP, the real
assembler, RunDriver and kernel, a scripted model. Sentinels are placed in
the prompt, the tool arguments, the tool result and the answer; the worker
credential, every attempt token Node issued and a toolset credential must
not appear either.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import fields
from typing import TYPE_CHECKING, Any

import httpx
import pytest

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

PROMPT = "SENTINEL-PROMPT-7f3a"
ARGS = "SENTINEL-ARGS-19bc"
RESULT = "SENTINEL-RESULT-c0de"
ANSWER = "SENTINEL-ANSWER-55aa"
TOOLSET_SECRET = "secret-jira-token"


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
        # The snapshot Node returned carries Node's actor; the world's graph knows ours.
        node = w.claim(claim.spec, query=claim.input_text, run_id=claim.run_id)
        values = {f.name: getattr(claim, f.name) for f in fields(claim) if f.name != "run"}
        return await self._inner.build(type(node)(**values, run=node.run))


async def test_lg4_no_content_token_or_credential_reaches_any_log(
    node_server: _NodeServer, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    _ = node_server.control.post("/reset").raise_for_status()
    spec = w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"], kb=[])
    _ = node_server.control.post(
        "/admit",
        json={"runId": "run-redact", "sessionId": "ses-redact", "inputText": f"find {PROMPT}", "spec": dump_contract(spec)},
    ).raise_for_status()

    tokens: set[str] = set()

    async def record(response: httpx.Response) -> None:
        await response.aread()
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        issued = body.get("attemptToken") if isinstance(body, dict) else None
        if isinstance(issued, dict) and isinstance(issued.get("token"), str):
            tokens.add(issued["token"])

    http = httpx.AsyncClient(base_url=node_server.base_url, event_hooks={"response": [record]})
    journal = NodeJournal(
        base_url=node_server.base_url,
        credentials=_Credential(node_server.credential),
        http_client=http,
        lease_ms=30_000,
    )
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="redaction",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": ARGS})])),
                    Turn(reply=Reply(text=ANSWER)),
                ],
            )
        )
    )

    seen_args: list[str] = []

    async def search_issues(self: w.FakeJira, jql: str) -> str:
        seen_args.append(jql)
        return f'{{"issues": ["{RESULT}"]}}'

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)
    with w.deployment():
        assembler = PipesHubRuntimeAssembler(w.Services(org="org-contract"), model_factory=w.model_factory(port))
        driver = RunDriver(journal=journal, assembler=_Attach(assembler), clock=ManualClock(), renew_interval_s=None)
        claim = await journal.claim("run-redact", worker_id="redaction-worker", org_id="org-contract")
        assert claim is not None
        _ = await journal.renew(claim.token)
        done = await driver.run_claimed(claim)
        journaled = [event.type for event in await journal.read(claim.stream_id)]
    await journal.aclose()
    assert "tool.outcome" in journaled and journaled[-1] == "run.completed"

    assert done.status == "completed", done
    assert seen_args == [ARGS]
    assert tokens, "Node issued no attempt token"
    assert any(getattr(r, "harness_event", None) == "journal.commit" for r in caplog.records)
    captured = "\n".join(
        f"{record.getMessage()} {getattr(record, 'harness', '')} {record.exc_text or ''}"
        for record in caplog.records
    )
    for secret in (PROMPT, ARGS, RESULT, ANSWER, TOOLSET_SECRET, node_server.credential, *tokens):
        assert secret not in captured, f"a log line carries {secret[:12]}..."
