"""P9 AU-5 and AU-6: the auditor process over request-log files, and a planted ungranted call it must flag."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.harness.audit.runner import LogCursor, parser, run
from harness_core.audit import InMemoryRunEvidenceSource
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    GRANTED,
    build_endpoint,
    claims_for,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

ORG, RUN = BINDING.tenant, BINDING.run_id
LINE = "2026-10-05 07:00:00,000 - harness_core.gateway - INFO - [logs.py:108] - gateway.call orgId={org} runId={run} attemptId=a1 invocationId={inv} disposition=executed toolId={tool}"


def source() -> InMemoryRunEvidenceSource:
    found = InMemoryRunEvidenceSource()
    found.pin(ORG, RUN, tools=tuple(t.effect.tool_id for t in GRANTED), routes=("openai:gpt-x",))
    return found


def args(*extra: str):  # noqa: ANN201 - argparse's namespace
    return parser().parse_args(list(extra))


async def test_au5_once_is_clean_on_granted_calls_and_fails_on_an_ungranted_one(tmp_path: Path) -> None:
    log = tmp_path / "worker.log"
    lookup = next(t.effect.tool_id for t in GRANTED if t.name == "lookup")
    log.write_text(LINE.format(org=ORG, run=RUN, inv="i1", tool=lookup) + "\nnoise line\n")
    assert await run(args("--log", str(log), "--once"), source()) == 0
    with log.open("a") as out:
        out.write(LINE.format(org=ORG, run=RUN, inv="i2", tool="crm@inst-1/delete") + "\n")
    assert await run(args("--log", str(log), "--once"), source()) == 1


async def test_au5_a_cursor_reads_each_line_once_and_restarts_a_rotated_file(tmp_path: Path) -> None:
    log = tmp_path / "gw.log"
    log.write_text(LINE.format(org=ORG, run=RUN, inv="i1", tool="x@y/z") + "\n" + "partial line without newline")
    cursor = LogCursor()
    calls, _ = cursor.read_new([log])
    assert len(calls) == 1
    assert cursor.read_new([log]) == ([], 0)
    rotated = tmp_path / "gw.log.new"
    rotated.write_text(LINE.format(org=ORG, run=RUN, inv="i9", tool="x@y/z") + "\n")
    rotated.replace(log)
    assert len(cursor.read_new([log])[0]) == 1


async def test_au5_the_state_file_keeps_the_cursor_across_passes(tmp_path: Path) -> None:
    log, state = tmp_path / "gw.log", tmp_path / "state.json"
    log.write_text(LINE.format(org=ORG, run=RUN, inv="i2", tool="crm@inst-1/delete") + "\n")
    assert await run(args("--log", str(log), "--once", "--state", str(state)), source()) == 1
    # Nothing new since the state was saved: a clean pass.
    assert await run(args("--log", str(log), "--once", "--state", str(state)), source()) == 0


async def test_au6_a_planted_ungranted_call_is_flagged_and_the_granted_one_beside_it_is_not(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The test-only path: an endpoint whose catalog is not narrowed to the run's grant executes ``delete``."""
    world = build_endpoint(narrowed=False)
    log = tmp_path / "worker.log"
    handler = logging.FileHandler(log)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    gateway = logging.getLogger("harness_core.gateway")
    gateway.addHandler(handler)
    gateway.setLevel(logging.INFO)
    try:
        granted = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
        planted = await world.endpoint.call_tool(claims_for(BINDING), "delete", {"q": "x"})
    finally:
        gateway.removeHandler(handler)
        handler.close()
    assert granted.disposition == planted.disposition == "executed"
    assert "delete" in world.runs, "the planted call reached the tool"
    with caplog.at_level(logging.ERROR, logger="app.harness.audit"):
        assert await run(args("--log", str(log), "--once"), source()) == 1
    flagged = [r.__dict__["harness"] for r in caplog.records if getattr(r, "harness_event", "") == "audit.violation"]
    assert [(f["reason"], f["subject"]) for f in flagged] == [("tool_not_granted", "crm@inst-1/delete")]


async def test_au6_the_narrowed_endpoint_never_executes_it_and_the_auditor_stays_clean(tmp_path: Path) -> None:
    """The production path refuses the same call (G2 T-1): nothing executed, nothing to flag."""
    world = build_endpoint()
    log = tmp_path / "worker.log"
    handler = logging.FileHandler(log)
    gateway = logging.getLogger("harness_core.gateway")
    gateway.addHandler(handler)
    gateway.setLevel(logging.INFO)
    try:
        refused = await world.endpoint.call_tool(claims_for(BINDING), "delete", {"q": "x"})
    finally:
        gateway.removeHandler(handler)
        handler.close()
    assert refused.disposition == "refused" and "delete" not in world.runs
    assert await run(args("--log", str(log), "--once"), source()) == 0
