"""QA31-9..QA31-11: Claude Code's rows of the e2e matrix and EM-18, on the pinned binary (``impl`` CC1, 2.1.294).

Each row is one run of E2E3's matrix lane: the release image in the Docker broker's sandbox, the egress proxy, the
model gateway in front of the scripted model (Claude Code speaks Messages to it), Node's journal server and the
production engine assembler over the lane's Neo4j, fake Jira and echo MCP runner. ``matrix.rows`` plans the row and
reads the run back; what it must show is the plan's own ``oracle(row)``. Nothing is spent.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.journal.types import JournalEvent
from harness_core.testing.history import assert_journal_history
from tests.unit.harness.engines.matrix.assembly import matrix_spec
from tests.unit.harness.engines.matrix.fixtures import (  # noqa: F401  (the lane's fixtures)
    echo_template_fixture,  # pyright: ignore[reportUnusedImport]
    matrix_fixture,  # pyright: ignore[reportUnusedImport]
)
from tests.unit.harness.engines.matrix.rows import (
    APPROVALS_PATH,
    ArrayRow,
    RowPlan,
    engine_ran,
    engine_rows,
    observe,
    plan_row,
    prompt,
)
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from tests.unit.harness.engines.matrix.lane import MatrixLane, RunResult

pytestmark = [
    pytest.mark.docker,
    pytest.mark.timeout(1800, method="thread"),
    pytest.mark.asyncio(loop_scope="module"),
]

ENGINE = "claude_code"
ROWS = engine_rows(ENGINE)
# Past the window a chat run waiting for a person stays answerable (HARNESS_WAIT_FOR_PERSON_DAYS, default 7 days,
# on the journal server's injected clock); its own 24 h deadline no longer ends it while it waits.
PAST_DEADLINE_S = 8 * 24 * 3600


@pytest.fixture
def claude_code() -> str:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)
    return ENGINE


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.row_id)
async def test_qa31_a_claude_code_row_shows_what_the_oracle_expects(
    matrix: MatrixLane, claude_code: str, row: ArrayRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_row(row)
    org = matrix.org
    marker = f"QA31 {row.row_id} {uuid.uuid4().hex[:6]}"
    spec = matrix_spec(org, kind=claude_code, tools=plan.tools, mcp=plan.mcp)
    spec["extensions"]["x.pipeshub"]["agent"]["mode"] = plan.mode
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true" if plan.flow_guard else "false")
    config = matrix.services.config
    config.put(APPROVALS_PATH, plan.approvals_document(matrix.lane.org))
    try:
        result = await matrix.run(
            prompt(plan.script, private_record=org.ids["rec-alice"], marker=marker),
            spec,
            decide=None if plan.decision in ("none", "expire") else (lambda _: plan.decision),
            scope=plan.scope,
        )
        status, events = await _settled(matrix, result, plan)
    finally:
        config.put(APPROVALS_PATH, None)
    types = [str(e.get("type")) for e in events]
    seen = observe(
        events, status=status, effects=_effects(matrix, plan, result, marker),
        engine=claude_code if engine_ran(events) else None,
    )
    assert seen == row.expected(), (row.row_id, dict(plan.not_on_lane), status, types)
    assert_journal_history(JournalEvent.from_envelope(e) for e in events)
    _assert_family(plan, result, marker, status)


async def _settled(matrix: MatrixLane, result: RunResult, plan: RowPlan) -> tuple[str, list[dict[str, Any]]]:
    """The run's end. A row whose user never answers waits until Node's clock passes the answer window and the
    sweeper ends it; the clock is put back, so the next row's tokens and leases are on the wall's time again."""
    if plan.decision == "expire":
        assert result.status in ("waiting_approval", "suspended"), (result.status, result.dispositions)
        http = matrix.lane.http
        _ = (await http.post("/advance", json={"seconds": PAST_DEADLINE_S})).raise_for_status()
        try:
            _ = (await http.post("/sweep")).raise_for_status()
        finally:
            _ = (await http.post("/advance", json={"seconds": -PAST_DEADLINE_S})).raise_for_status()
    run = await matrix.lane.run(result.run_id)
    return str(run["status"]), await matrix.lane.events(result.run_id)


def _effects(matrix: MatrixLane, plan: RowPlan, result: RunResult, marker: str) -> int:
    """What the fake SaaS recorded for the run: Jira's creates of the marker, or the echo server's answers with it."""
    if plan.script == "read-then-write-toolset":
        assert matrix.jira is not None
        return matrix.jira.creates.get(marker, 0)
    if plan.script == "read-then-write-mcp":
        return sum(1 for answer in result.tool_results() if marker in answer)
    return 0


def _assert_family(plan: RowPlan, result: RunResult, marker: str, status: str) -> None:
    """04 §4.4 for the family: the read came back, the code ran, and a completed run's answer is the script's."""
    out = result.tool_results()
    if plan.script == "read-then-answer":
        assert any("ALPHA7319" in r for r in out), out
    if plan.script == "code-then-answer":
        assert any(marker in r for r in out), out
    if status == "completed":
        (answer,) = result.of("x.pipeshub.answer.finalized")
        assert f"{marker} done" in str(answer.get("answer")), answer
