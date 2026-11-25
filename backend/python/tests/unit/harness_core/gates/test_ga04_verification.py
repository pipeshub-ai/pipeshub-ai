"""GA-04 (G-05, C20.06): when the run says it is done, deterministic checks decide whether it is.

The scenario: an agent claims it is done, but the tests fail, the file it promised is not there, or a citation's
quoted text is absent from the record. The run ends ``unverified`` with each reason (``verification.recorded``), or with
``onFail: continue`` is told what failed and works on; it is ``verified`` only when every check passes. A check the
host cannot run fails closed.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunGates, RunSpec, VerifyGate
from harness_core.effects.types import ToolContext, ToolExecution
from harness_core.gates.verify import (
    CommandOutcome,
    RunVerifier,
    StoredWorkspaceFiles,
    VerifyPorts,
    check_citations,
)
from harness_core.testing.engines.durable import EngineWorld, engine_run_spec
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.types import ObjectScope, WorkspaceFile
from tests.unit.harness_core.durable.support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue


@dataclass
class Files:
    files: dict[str, bytes] = field(default_factory=dict[str, bytes])

    async def read(self, path: str) -> bytes | None:
        return self.files.get(path)


@dataclass
class Sources:
    texts: dict[str, str] = field(default_factory=dict[str, str])

    async def source_text(self, target: str) -> str | None:
        return self.texts.get(target)


@dataclass
class Commands:
    exit_code: int | None = 0
    tail: str = ""
    ran: list[list[str]] = field(default_factory=list[list[str]])

    async def run(self, argv: Sequence[str], *, timeout_s: int) -> CommandOutcome:
        self.ran.append(list(argv))
        return CommandOutcome(self.exit_code, self.tail)


def gate(checks: list[dict[str, JsonValue]], on_fail: str = "flag", rounds: int | None = None) -> VerifyGate:
    raw: dict[str, JsonValue] = {"checks": checks, "onFail": on_fail}
    if rounds is not None:
        raw["maxRounds"] = rounds
    return validate_contract(VerifyGate, raw)


def spec(verify: VerifyGate) -> RunSpec:
    return minimal_run_spec().model_copy(update={"gates": RunGates(verify=verify)})


def _docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


# GA-04-1: each check, deterministic.
@pytest.mark.parametrize(
    ("check", "files", "passed", "reason"),
    [
        ({"kind": "file", "path": "out/r.pdf"}, {}, False, "does not exist"),
        ({"kind": "file", "path": "out/r.pdf"}, {"out/r.pdf": b""}, False, "is empty"),
        ({"kind": "file", "path": "out/r.pdf", "format": "pdf"}, {"out/r.pdf": b"not a pdf"}, False, "complete PDF"),
        ({"kind": "file", "path": "out/r.pdf", "format": "pdf"}, {"out/r.pdf": b"%PDF-1.7\n...\n%%EOF\n"}, True, None),
        ({"kind": "file", "path": "a.json", "format": "json"}, {"a.json": b"{"}, False, "not valid JSON"),
        ({"kind": "file", "path": "r.docx", "format": "docx"}, {"r.docx": _docx()}, True, None),
        ({"kind": "file", "path": "r.xlsx", "format": "xlsx"}, {"r.xlsx": _docx()}, False, "not a XLSX document"),
        ({"kind": "file", "path": "r.csv", "format": "csv"}, {"r.csv": b"a,b\n1,2\n"}, True, None),
        ({"kind": "file", "path": "i.png", "format": "png"}, {"i.png": b"\x89PNG\r\n\x1a\n...."}, True, None),
        (
            {"kind": "json_schema", "path": "d.json", "schema": {"type": "object", "required": ["rows"]}},
            {"d.json": json.dumps({"cols": []}).encode()},
            False,
            "does not match its schema",
        ),
        (
            {"kind": "json_schema", "path": "d.json", "schema": {"type": "object", "required": ["rows"]}},
            {"d.json": json.dumps({"rows": []}).encode()},
            True,
            None,
        ),
    ],
)
async def test_ga04_1_file_and_schema_checks(
    check: dict[str, JsonValue], files: dict[str, bytes], passed: bool, reason: str | None
) -> None:
    result = await RunVerifier(gate([check]), VerifyPorts(files=Files(files))).verify("done")
    (only,) = result.checks
    assert only.passed is passed
    assert result.status == ("verified" if passed else "unverified")
    if reason is not None:
        assert only.reason is not None and reason in only.reason


async def test_ga04_1_tests_pass_only_when_the_command_exits_zero() -> None:
    failing = Commands(exit_code=1, tail="1 failed, 3 passed")
    result = await RunVerifier(gate([{"kind": "command", "argv": ["pytest", "-q"]}]), VerifyPorts(commands=failing)).verify("ok")
    (only,) = result.checks
    assert not only.passed and only.target == "pytest"
    assert only.reason is not None and "exited with code 1" in only.reason and "1 failed" in only.reason
    assert failing.ran == [["pytest", "-q"]]
    passing = await RunVerifier(gate([{"kind": "command", "argv": ["pytest"]}]), VerifyPorts(commands=Commands())).verify("ok")
    assert passing.status == "verified"


SOURCE = "Net revenue rose 12% in the third quarter, driven by enterprise renewals."


@pytest.mark.parametrize(
    ("answer", "passed", "reason"),
    [
        ("Revenue grew [Q3 report](ref1).", True, None),
        ('The report says "net revenue rose 12% in the third quarter" [Q3](ref1).', True, None),
        ('The report says "net revenue rose 40% in the third quarter" [Q3](ref1).', False, "not in the cited source"),
        ("Revenue grew [Q3 report](ref7).", False, "ref7, which is not a source"),
        ("No citations here at all.", True, None),
    ],
    ids=["cited", "quote-found", "quote-absent", "unknown-ref", "no-citations"],
)
async def test_ga04_1_citations_resolve_and_quotes_are_really_in_the_record(answer: str, passed: bool, reason: str | None) -> None:
    result = await check_citations(answer, Sources({"ref1": SOURCE}))
    assert result.passed is passed
    if reason is not None:
        assert result.reason is not None and reason in result.reason


# GA-04-2: a host without the port fails closed: never verified by default.
@pytest.mark.parametrize(
    "check",
    [{"kind": "file", "path": "a.txt"}, {"kind": "command", "argv": ["make", "test"]}, {"kind": "citations"}],
    ids=["file", "command", "citations"],
)
async def test_ga04_2_a_check_with_no_port_fails_closed(check: dict[str, JsonValue]) -> None:
    result = await RunVerifier(gate([check])).verify("see [x](ref1)")
    assert result.status == "unverified"
    assert result.checks[0].reason is not None and "could not be" in result.checks[0].reason


# GA-04-3: on the loop, the run ends unverified with its reasons, journaled and folded nowhere else.
def _world(verify: VerifyGate) -> World:
    world = World()
    world.admit(spec=spec(verify))
    return world


async def test_ga04_3_an_agent_that_claims_done_with_failing_tests_ends_unverified() -> None:
    world = _world(gate([{"kind": "command", "argv": ["pytest"]}, {"kind": "file", "path": "out/report.pdf"}]))
    port = scripted(reply("All done, tests pass and the report is written."))[1]
    ports = VerifyPorts(files=Files(), commands=Commands(exit_code=2))
    done = await world.run(world.kernel(port, verify=ports))
    assert done.status == "completed"
    (verified,) = world.events("verification.recorded")
    assert verified.data["status"] == "unverified" and verified.data["continuing"] is False
    reasons = [c.get("reason") for c in verified.data["checks"]]  # type: ignore[union-attr]
    assert "The command pytest exited with code 2." in reasons
    assert "The file out/report.pdf does not exist in the run's workspace." in reasons
    # It rides the run's ending: one commit with the final answer.
    final = next(e for e in world.events("message.completed") if e.data.get("isFinal"))
    assert verified.seq < final.seq + 4


async def test_ga04_3_every_check_passing_is_verified() -> None:
    world = _world(gate([{"kind": "file", "path": "out/report.pdf"}]))
    port = scripted(reply("Report written."))[1]
    await world.run(world.kernel(port, verify=VerifyPorts(files=Files({"out/report.pdf": b"%PDF"}))))
    (verified,) = world.events("verification.recorded")
    assert verified.data == {"status": "verified", "round": 1, "continuing": False, "checks": [
        {"kind": "file", "passed": True, "target": "out/report.pdf"}
    ]}


# GA-04-4: optional auto-continue: told what failed, works on, bounded.
async def test_ga04_4_on_fail_continue_sends_the_run_back_and_it_ends_verified_once_fixed() -> None:
    files = Files()

    async def write_report(args: object, ctx: ToolContext) -> ToolExecution:
        files.files["out/report.pdf"] = b"%PDF-1.7\n%%EOF\n"
        return ToolExecution(status="ok", content="written")

    world = _world(gate([{"kind": "file", "path": "out/report.pdf", "format": "pdf"}], "continue", rounds=2))
    world.tools.add("write_report", write_report, tags=[("category", "write")])
    port = scripted(reply("Done!"), reply("", ("write_report", {})), reply("Now it is really done."))[1]
    done = await world.run(world.kernel(port, verify=VerifyPorts(files=files)))
    assert done.status == "completed"
    rounds = [(e.data["status"], e.data["round"], e.data["continuing"]) for e in world.events("verification.recorded")]
    assert rounds == [("unverified", 1, True), ("verified", 2, False)]
    second = port.calls[1]  # type: ignore[attr-defined]
    assert any("does not exist" in m.model_dump_json() for m in second.messages)


async def test_ga04_4_continue_is_bounded_by_max_rounds_then_the_run_ends_unverified() -> None:
    world = _world(gate([{"kind": "file", "path": "missing.txt"}], "continue", rounds=1))
    port = scripted(reply("Done."), reply("Done, really."))[1]
    await world.run(world.kernel(port, verify=VerifyPorts(files=Files())))
    rounds = [(e.data["status"], e.data["round"], e.data["continuing"]) for e in world.events("verification.recorded")]
    assert rounds == [("unverified", 1, True), ("unverified", 2, False)]


async def test_ga04_4_a_citation_whose_quote_is_absent_from_the_record_is_unverified() -> None:
    world = _world(gate([{"kind": "citations"}]))
    port = scripted(reply('The report says "net revenue rose 40% in the third quarter" [Q3](ref1).'))[1]
    await world.run(world.kernel(port, verify=VerifyPorts(citations=Sources({"ref1": SOURCE}))))
    (verified,) = world.events("verification.recorded")
    assert verified.data["status"] == "unverified"
    assert "not in the cited source" in str(verified.data["checks"])


async def test_ga04_4_a_run_without_the_gate_journals_no_verification() -> None:
    world = World()
    world.admit()
    await world.run(world.kernel(scripted(reply("hi"))[1]))
    assert world.events("verification.recorded") == []


# GA-04-5: the workspace port reads the run's workspace as the store holds it.
async def test_ga04_5_stored_workspace_files_read_the_head_manifest() -> None:
    store, objects = InMemoryWorkspaceStore(), InMemoryBinaryObjectStore()
    scope = ObjectScope("org")
    ref = await objects.put(scope, b"%PDF-1.7\n%%EOF\n")
    await store.commit(
        scope, "ws:s", base_revision=0, changes=ChangeSet({"out/r.pdf": WorkspaceFile("out/r.pdf", ref)}), reason="seed"
    )
    files = StoredWorkspaceFiles(store, objects, scope, "ws:s")
    assert await files.read("out/r.pdf") == b"%PDF-1.7\n%%EOF\n"
    assert await files.read("out/missing.pdf") is None
    assert await files.read("../etc/passwd") is None


# GA-04-6: an engine run is checked on what its turns saved to the workspace, and flagged (it is never sent back).
def _engine_spec(verify: VerifyGate) -> RunSpec:
    return engine_run_spec().model_copy(update={"gates": RunGates(verify=verify)})


async def test_ga04_6_an_engine_that_says_done_without_the_file_ends_unverified() -> None:
    world = EngineWorld()
    world.admit("run-v1", "say all done, the report is written", spec=_engine_spec(gate([{"kind": "file", "path": "out/report.md"}])))
    assert (await world.attempt("run-v1")).status == "completed"
    (verified,) = [e for e in world.events("run-v1") if e.type == "verification.recorded"]
    assert verified.data["status"] == "unverified" and verified.data["continuing"] is False
    assert "does not exist" in str(verified.data["checks"])


async def test_ga04_6_an_engine_that_wrote_the_file_ends_verified() -> None:
    world = EngineWorld()
    script = "write out/report.md quarterly numbers\nsay all done"
    world.admit("run-v2", script, spec=_engine_spec(gate([{"kind": "file", "path": "out/report.md", "format": "text"}])))
    assert (await world.attempt("run-v2")).status == "completed"
    (verified,) = [e for e in world.events("run-v2") if e.type == "verification.recorded"]
    assert verified.data["status"] == "verified"
