"""D-12: the Node-backed journal client passes S4b's ``JournalContract``.

The Node side is real: its internal router, controller, ExecutionService,
JournalService, attempt-token authority and error middleware over the
in-memory store (``tests/modules/agent-harness/support/journal-http-server.ts``).
Skipped where the Node workspace has no ``node_modules``.
"""

from __future__ import annotations

import contextlib
import os
import select
import shutil
import signal
import subprocess
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from app.harness.journal_node import NodeJournal
from harness_core.contracts import dump_contract
from harness_core.journal.types import db_timestamp
from harness_core.testing.contracts.children import ChildRunContract
from harness_core.testing.contracts.journal import JournalContract
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.journal.types import Claim, InteractionDecision

NODE_APP = Path(__file__).resolve().parents[4] / "nodejs" / "apps"
SERVER = "tests/modules/agent-harness/support/journal-http-server.ts"
LEASE_MS = 30_000


class _Credential:
    def __init__(self, value: str) -> None:
        self._value = value

    async def worker_credential(self) -> str:
        return self._value


# Under the 180 s hang guard the harness conftest gives every test that starts this server.
READY_TIMEOUT_S = 150
STOP_TIMEOUT_S = 10
COMMAND = ("npx", "ts-node", "--transpile-only", SERVER)


class _NodeServer:
    """The journal server as a process group of its own: npx runs ts-node in a child, which a kill of npx alone
    leaves running. Every wait is bounded, and a start or stop that outlasts its bound ends the whole group."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        *,
        command: Sequence[str] = COMMAND,
        ready_timeout_s: float = READY_TIMEOUT_S,
    ) -> None:
        env = {**os.environ, "TS_NODE_PROJECT": "tsconfig.test.json", "JOURNAL_HTTP_HOST": host}
        self._stderr = tempfile.TemporaryFile()  # read for a failure message; stop() closes it
        self.process = subprocess.Popen(
            list(command),
            cwd=NODE_APP,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        assert self.process.stdout is not None
        # Its own wait, inside the tests' guard, so a Node that never answers is named as that.
        ready, _, _ = select.select([self.process.stdout], [], [], ready_timeout_s)
        line = self.process.stdout.readline().split() if ready else []
        if len(line) not in (3, 4) or line[0] != "READY":
            self._end_group()
            stderr = self._stderr_tail()
            self._stderr.close()
            pytest.fail(f"Node journal server did not start in {ready_timeout_s:g} s: {line}; stderr: {stderr}")
        # Node keeps writing its log to stdout; drained, so nothing it writes waits on this process.
        threading.Thread(target=self._drain, name="journal-server stdout", daemon=True).start()
        self.base_url = f"http://{host}:{line[1]}"
        self.credential = line[2]
        # ADR-CRED-01: gateway tokens are minted with the egress proxy's own credential.
        self.proxy_credential = line[3] if len(line) == 4 else line[2]
        self.control = httpx.Client(base_url=f"{self.base_url}/__control", timeout=10)

    def _drain(self) -> None:
        assert self.process.stdout is not None
        with contextlib.suppress(OSError, ValueError):
            for _ in self.process.stdout:
                pass

    def _stderr_tail(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read()[-2000:].decode(errors="replace").strip() or "(empty)"

    def _end_group(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGKILL)
        try:
            _ = self.process.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pytest.fail(f"Node journal server (pid {self.process.pid}) did not exit {STOP_TIMEOUT_S} s after SIGKILL")

    def stop(self) -> None:
        """Closing stdin asks the server to exit; the group is killed after the bound or once it has exited."""
        if self.process.stdin is not None:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            _ = self.process.wait(timeout=STOP_TIMEOUT_S)
        try:
            self._end_group()
        finally:
            self._stderr.close()


@pytest.fixture(scope="module")
def node_server() -> Iterator[_NodeServer]:
    if not (NODE_APP / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.skip("the Node workspace is not installed (npm ci)")
    server = _NodeServer()
    yield server
    server.control.close()
    server.stop()


class _OrgJournal:
    """The contract claims by run id alone; Node looks a run up by org."""

    def __init__(self, journal: NodeJournal, org_id: str) -> None:
        self._journal = journal
        self._org_id = org_id
        self.commit = journal.commit
        self.read = journal.read
        self.last_seq = journal.last_seq
        self.renew = journal.renew
        self.release = journal.release

    async def claim(
        self, run_id: str | None = None, *, worker_id: str, org_id: str | None = None
    ) -> Claim | None:
        return await self._journal.claim(
            run_id, worker_id=worker_id, org_id=org_id or self._org_id
        )


class NodeHarness:
    def __init__(self, server: _NodeServer) -> None:
        self._control = server.control
        _ = self._control.post("/reset").raise_for_status()
        self.node = NodeJournal(
            base_url=server.base_url,
            credentials=_Credential(server.credential),
            lease_ms=LEASE_MS,
        )
        self._org_id = "org-contract"
        self.journal = _OrgJournal(self.node, self._org_id)

    @property
    def lease_s(self) -> float:
        return LEASE_MS / 1000

    def admit(
        self,
        run_id: str,
        *,
        session_id: str,
        input_text: str = "hello",
        stream_id: str | None = None,
        spec: RunSpec | None = None,
    ) -> None:
        body: dict[str, object] = {
            "runId": run_id,
            "sessionId": session_id,
            "inputText": input_text,
            "spec": dump_contract(spec or minimal_run_spec()),
        }
        if stream_id is not None:
            body["streamId"] = stream_id
        _ = self._control.post("/admit", json=body).raise_for_status()

    def advance(self, seconds: float) -> None:
        _ = self._control.post("/advance", json={"seconds": seconds}).raise_for_status()

    def sweep(self) -> None:
        _ = self._control.post("/sweep").raise_for_status()

    def request_cancel(self, run_id: str) -> None:
        _ = self._control.post("/cancel", json={"runId": run_id}).raise_for_status()

    def resolve(
        self, run_id: str, interaction_id: str, decision: InteractionDecision
    ) -> None:
        _ = self._control.post(
            "/resolve",
            json={"runId": run_id, "interactionId": interaction_id, "decision": decision},
        ).raise_for_status()

    def now_iso(self, plus_s: float = 0.0) -> str:
        now = int(self._control.get("/now").json()["now"])
        return db_timestamp(
            datetime.fromtimestamp(now / 1000, UTC) + timedelta(seconds=plus_s)
        )


class TestNodeJournalContract(JournalContract):
    @pytest.fixture
    def harness(self, node_server: _NodeServer) -> Iterator[NodeHarness]:  # type: ignore[override]
        yield NodeHarness(node_server)


class NodeChildHarness(NodeHarness):
    """CRC on a live Node: the child route, ``ChildRunService`` and the join, over HTTP."""

    @property
    def children(self) -> NodeJournal:
        return self.node

    @property
    def org_id(self) -> str:
        return self._org_id

    def admit_parent(self, run_id: str, *, session_id: str, max_children: int) -> None:
        body = {
            "runId": run_id,
            "sessionId": session_id,
            "inputText": "parent task",
            "spec": dump_contract(minimal_run_spec(max_children=max_children)),
        }
        _ = self._control.post("/admit", json=body).raise_for_status()


class TestNodeChildRunContract(ChildRunContract):
    @pytest.fixture
    def harness(self, node_server: _NodeServer) -> Iterator[NodeChildHarness]:  # type: ignore[override]
        yield NodeChildHarness(node_server)


# ---- steering over a live Node (P1b T2b, C-21c) ----


async def test_c21c_a_steering_message_over_the_inline_bound_reaches_the_worker_whole(
    node_server: _NodeServer,
) -> None:
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.journal.inline import inline_bytes

    harness = NodeHarness(node_server)
    harness.admit("run-steer", session_id="ses-steer")
    claim = await harness.journal.claim("run-steer", worker_id="w1")
    assert claim is not None
    long = "€" * 100_000
    queued = node_server.control.post("/steer", json={"sessionId": "ses-steer", "text": long})
    assert queued.raise_for_status().json() == {"messageSeq": 1}
    events = await harness.journal.read("ses-steer")
    (event,) = [e for e in events if e.type == "input.queued"]
    ref = ContentRef.model_validate(event.data["textRef"])
    assert inline_bytes(ref) == long.encode("utf-8")
    assert ref.size == len(long.encode("utf-8"))


async def test_c21b_attachments_by_reference_reach_the_worker_and_its_consumption_commits(
    node_server: _NodeServer,
) -> None:
    from harness_core.contracts.generated.events.input_queued import InputQueuedData
    from harness_core.journal.inline import inline_ref
    from harness_core.journal.types import CommitRequest, NewEvent

    harness = NodeHarness(node_server)
    harness.admit("run-refs", session_id="ses-refs")
    claim = await harness.journal.claim("run-refs", worker_id="w1")
    assert claim is not None
    attachment = {"recordId": "rec-1", "virtualRecordId": "vr-1", "recordName": "plan.pdf", "mimeType": "application/pdf"}
    queued = node_server.control.post(
        "/steer",
        json={"sessionId": "ses-refs", "text": "see the plan", "attachments": [attachment], "recordIds": ["rec-9"]},
    )
    assert queued.raise_for_status().json() == {"messageSeq": 1}
    (event,) = [e for e in await harness.journal.read("ses-refs") if e.type == "input.queued"]
    data = InputQueuedData.model_validate(event.data)
    assert data.by is not None and data.by.startswith("p_"), "QB-1: Node names the sender"
    assert [a.model_dump(by_alias=True, exclude_none=True) for a in data.attachments or []] == [attachment]
    assert [r.root for r in data.record_ids or []] == ["rec-9"]
    content = inline_ref(b'{"parts":[{"text":"see the plan","type":"text"}]}')
    receipt = await harness.journal.commit(
        CommitRequest(
            "w1:input:1",
            claim.token,
            (NewEvent("input.consumed", {"messageSeq": 1, "contentRef": content.model_dump(mode="json")}),),
        )
    )
    (consumed,) = [e for e in receipt.events if e.type == "input.consumed"]
    assert consumed.data["contentRef"] == content.model_dump(mode="json")
