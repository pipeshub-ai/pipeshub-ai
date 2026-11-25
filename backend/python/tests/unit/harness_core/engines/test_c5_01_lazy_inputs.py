"""LI-1..LI-7 (C5.01, J-26): inputs are staged at a turn's start if small; a large one is copied in on first read.

§5 of the checklist: "Inputs / attachments: staged at start if small; large files streamed on first read". A file
under ``in/`` over the controller's ``input_stage_max_bytes`` stays in the workspace but the sync hides it
(``ON_FIRST_READ``); the task names it with its size and the command that places it; ``ph inputs get <name>`` asks
the controller (``INPUT_FETCH_TOOL``), which writes it as the workspace holds it, so no save changes anything.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.briefing import BriefedFile, workspace_briefing
from harness_core.engines.input_staging import (
    INPUT_FETCH_TOOL,
    SESSION_PH,
    STAGE_AT_START_MAX_BYTES,
    deferred_inputs,
    input_path,
)
from harness_core.gateway.errors import BindingMismatch
from harness_core.gateway.types import GatewayClaims, McpResult
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import InputArtifactRef
from harness_core.testing.contracts.workspace_sync import acquire, commit, tree
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)
from harness_core.testing.engines.loop import ToolOutcome
from harness_core.workspace.sync import TAR
from harness_core.workspace.sync.types import ON_FIRST_READ
from harness_core.workspace.types import ObjectScope
from harness_engines.pipeshub import ph
from tests.unit.harness_core.engines.durable.test_inputs import _Opening, finish, upload
from tests.unit.harness_core.workspace.sync.support import ROOT, FakeSyncHarness

if TYPE_CHECKING:
    from harness_core.testing.sandbox.fake import FakeSandboxHandle
    from harness_core.workspace.types import WorkspaceFile

BIG = b"%PDF-1.7\n" + b"x" * 4096
SMALL = b"region,amount\nnorth,1\n"
LIMIT = 1024


def test_li1_only_large_files_under_in_wait_for_their_first_read() -> None:
    """LI-1: the rule: a file under ``in/`` over the limit (never a link, never a file elsewhere); the release limit
    keeps a document or a table staged at the start."""
    from harness_core.testing.contracts.workspace import file

    files: dict[str, WorkspaceFile] = {
        "in/big.pdf": file("in/big.pdf", "a" * (LIMIT + 1)),
        "in/small.csv": file("in/small.csv", "b"),
        "out/big.bin": file("out/big.bin", "c" * (LIMIT + 1)),
    }
    assert deferred_inputs(files, LIMIT) == frozenset({"in/big.pdf"})
    assert STAGE_AT_START_MAX_BYTES == 8 * 1024 * 1024
    for given in ("big.pdf", "in/big.pdf", "/pipeshub/inputs/big.pdf", " ./big.pdf "):
        assert input_path({"name": given}) == "in/big.pdf", given
    for refused in ("", "../etc/passwd", "a/b.pdf", None, 3):
        assert input_path({"name": refused}) is None, refused  # type: ignore[dict-item]


async def test_li2_the_sync_holds_a_deferred_file_back_and_stages_it_unchanged() -> None:
    """LI-2: materialize leaves a deferred file out (hidden ``ON_FIRST_READ``), a save never deletes it; ``stage``
    writes it with the workspace's bytes and mode, and the next collect finds nothing to change or upload."""
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/big.pdf": BIG, "in/small.csv": SMALL, "notes.md": b"n"})
    sandbox = await acquire(h)
    state = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    assert set(await tree(sandbox, ROOT)) == {"in/small.csv", "notes.md"}
    assert state.hidden == {"in/big.pdf": ON_FIRST_READ}
    collected = await h.sync.collect(sandbox, state)
    assert collected.changes.is_empty
    puts = h.objects.puts
    state = await h.sync.stage(sandbox, state, "in/big.pdf")
    files = await tree(sandbox, ROOT)
    assert files["in/big.pdf"] == (manifest.files["in/big.pdf"].mode, BIG)
    assert "in/big.pdf" not in state.hidden
    after = await h.sync.collect(sandbox, state)
    assert after.changes.is_empty and h.objects.puts == puts
    with pytest.raises(KeyError):
        _ = await h.sync.stage(sandbox, state, "in/small.csv")


async def test_li2_a_sandbox_that_already_holds_it_keeps_it_and_a_stale_copy_goes() -> None:
    """LI-2: a kept sandbox that staged the file keeps it visible at the next materialize; a different copy under
    the same name is removed and the file waits again."""
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/big.pdf": BIG})
    sandbox = await acquire(h)
    state = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    state = await h.sync.stage(sandbox, state, "in/big.pdf")
    again = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    assert again.hidden == {} and (await tree(sandbox, ROOT))["in/big.pdf"][1] == BIG
    await h.hands.write(sandbox, f"{ROOT}/in/big.pdf", b"stale")
    stale = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    assert stale.hidden == {"in/big.pdf": ON_FIRST_READ} and "in/big.pdf" not in await tree(sandbox, ROOT)


def _world() -> tuple[EngineWorld, _Opening]:
    adapter = _Opening()
    w = EngineWorld(adapter=adapter)
    w.config = dataclasses.replace(w.config, autosave_changes=2, input_stage_max_bytes=LIMIT)
    return w, adapter


async def test_li3_a_turn_starts_without_the_large_input_and_is_told_how_to_get_it() -> None:
    """LI-3: the engine opens with the small input and without the large one; its task lists the large one with its
    size and the session ``ph`` command, and its bytes are never read for the briefing."""
    w, adapter = _world()
    big = await upload(w, "scan.pdf", BIG)
    small = await upload(w, "sales.csv", SMALL)
    w.admit("run-li3", "say read", inputs=[InputArtifactRef(big), InputArtifactRef(small)])
    assert await finish(w, "run-li3") == "completed"
    assert adapter.opened == [{"in/sales.csv": SMALL}]
    prompt = next(m.content for c in w.gateway.model_calls for m in c.messages if m.role == "user")
    assert f"- in/scan.pdf (4.0 KB): not copied in yet (large): run `{SESSION_PH} inputs get scan.pdf`" in prompt
    assert "- in/sales.csv" in prompt and "region, amount" in prompt
    head = await w.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    assert set(head.files) == {"in/scan.pdf", "in/sales.csv"}


class _EngineShell:
    """The engine's shell for ``ph inputs get``: the real ``ph`` (``main``), its gateway the endpoint the turn's
    tool host serves (as the sandbox reaches it through the egress proxy), and the sandbox's files after it ran."""

    def __init__(self, w: EngineWorld) -> None:
        self.w = w
        self.after: list[dict[str, bytes]] = []

    async def __call__(self, sandbox: FakeSandboxHandle, workdir: str, command: str) -> ToolOutcome:
        mounts = self.w.host._mounts  # pyright: ignore[reportPrivateUsage]
        ((endpoint, binding),) = mounts.values()
        claims = GatewayClaims(binding, kid="test", expires_at=datetime.max.replace(tzinfo=UTC))
        loop = asyncio.get_running_loop()

        class Gw:
            def call(self, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:  # noqa: ANN401
                result = asyncio.run_coroutine_threadsafe(endpoint.call_tool(claims, name, arguments), loop).result(10)
                return {"isError": result.is_error, "content": [{"type": "text", "text": result.text}]}

        argv = command.removeprefix(f"{SESSION_PH} ").split()
        out, err = io.StringIO(), io.StringIO()
        code = await asyncio.to_thread(ph.main, ["--json", *argv], out=out, err=err, gw=Gw())  # type: ignore[arg-type]
        root = workdir.rstrip("/") + "/"
        self.after.append({p.removeprefix(root): bytes(f) for p, f in sandbox.files.items() if p.startswith(root)})
        return ToolOutcome(out.getvalue(), is_error=code != 0)


async def test_li4_ph_inputs_get_places_it_once_and_the_save_changes_nothing() -> None:
    """LI-4: the session's ``ph inputs get``, run from the engine's shell, places the input with the workspace's
    bytes; again, it answers that it is there; an unknown name lists what still waits. No save follows from it, and
    no step, approval or activity: it is the controller's own answer."""
    shell = _EngineShell.__new__(_EngineShell)
    adapter = ScriptedEngineAdapter(shell=shell)
    w = EngineWorld(adapter=adapter)
    w.config = dataclasses.replace(w.config, autosave_changes=2, input_stage_max_bytes=LIMIT)
    shell.__init__(w)
    big = await upload(w, "scan.pdf", BIG)
    prompt = "\n".join([
        f"shell {SESSION_PH} inputs get scan.pdf",
        f"shell {SESSION_PH} inputs get in/scan.pdf",
        f"shell {SESSION_PH} inputs get other.pdf",
        "say read",
    ])
    w.admit("run-li4", prompt, inputs=[InputArtifactRef(big)])
    assert await finish(w, "run-li4") == "completed"
    first, second, unknown = (json.loads(r) for r in [
        m.content for c in w.gateway.model_calls[-1:] for m in c.messages if m.role == "tool"
    ])
    assert first == {"ok": True, "command": "inputs.get", "data": {"path": "in/scan.pdf", "bytes": len(BIG), "staged": True}}
    assert second["data"] == {"path": "in/scan.pdf", "bytes": len(BIG), "staged": False}
    assert unknown["ok"] is False and "other.pdf is no input waiting to be read. Inputs still to fetch: none." in unknown["error"]["message"]
    assert shell.after[0] == {"in/scan.pdf": BIG}
    commits = [e.data["changed"] for e in w.events("run-li4") if e.type == "workspace.committed"]
    assert commits == [["/in/scan.pdf"]], commits
    assert not [e for e in w.events("run-li4") if e.type in ("tool.invoked", "interaction.requested")]


async def test_li5_a_call_for_another_runs_binding_is_refused() -> None:
    """LI-5: the controller checks the token's binding itself, as the gateway does, before touching the sandbox."""
    from harness_core.engines.controller import (
        _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
    )

    binding = RunBinding(ORG, SESSION, "run-li5")
    endpoint = _SavingEndpoint(object(), binding)  # type: ignore[arg-type]
    called: list[Any] = []

    async def stage(arguments: Any) -> McpResult:  # noqa: ANN401
        called.append(arguments)
        return McpResult("{}", is_error=False, disposition="executed", retryable=False)

    endpoint.inputs = stage
    claims = type("Claims", (), {"binding": RunBinding(ORG, SESSION, "another-run")})()
    with pytest.raises(BindingMismatch):
        _ = await endpoint.call_tool(claims, INPUT_FETCH_TOOL, {"name": "x"})  # type: ignore[arg-type]
    assert called == []
    mine = type("Claims", (), {"binding": binding})()
    _ = await endpoint.call_tool(mine, INPUT_FETCH_TOOL, {"name": "x"})  # type: ignore[arg-type]
    assert called == [{"name": "x"}]


def test_li6_ph_inputs_get_calls_the_controllers_tool() -> None:
    """LI-6: ``ph inputs get <name>`` (the session's ``ph``) sends ``{"name"}`` to ``INPUT_FETCH_TOOL`` and prints
    where the file now is; a refusal is a tool error with the controller's words."""
    assert ph.INPUT_FETCH == INPUT_FETCH_TOOL

    class Gw:
        def __init__(self, answer: dict[str, Any]) -> None:
            self.answer = answer
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def call(self, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:  # noqa: ANN401
            self.calls.append((name, arguments))
            return self.answer

    placed = Gw({"content": [{"type": "text", "text": json.dumps({"path": "in/scan.pdf", "bytes": 9, "staged": True})}]})
    out, err = io.StringIO(), io.StringIO()
    assert ph.main(["inputs", "get", "scan.pdf"], out=out, err=err, gw=placed) == 0  # type: ignore[arg-type]
    assert placed.calls == [(INPUT_FETCH_TOOL, {"name": "scan.pdf"})]
    assert out.getvalue().strip() == "Placed in/scan.pdf (9 bytes)."
    refused = Gw({"isError": True, "content": [{"type": "text", "text": "x.pdf is no input waiting to be read."}]})
    out, err = io.StringIO(), io.StringIO()
    assert ph.main(["--json", "inputs", "get", "x.pdf"], out=out, err=err, gw=refused) == ph.EX_TOOL_ERROR  # type: ignore[arg-type]
    envelope = json.loads(out.getvalue())
    assert envelope["command"] == "inputs.get" and "no input waiting" in envelope["error"]["message"]


def test_li7_the_briefing_never_shows_a_deferred_inputs_bytes() -> None:
    """LI-7: a deferred input is described by name and size alone (its bytes may be gigabytes the worker never
    reads at the turn's start), and the command named is the session's ``ph``, present on every image."""
    note = workspace_briefing([BriefedFile("scan.pdf", b"", on_read=True, size_bytes=50 * 1024 * 1024)])
    assert f"- in/scan.pdf (50.0 MB): not copied in yet (large): run `{SESSION_PH} inputs get scan.pdf`" in note
    assert SESSION_PH == f"python3 /pipeshub/session/{'ph.py'}" and sys.version_info >= (3, 12)
