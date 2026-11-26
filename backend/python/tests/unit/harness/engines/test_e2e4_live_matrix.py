"""E2E4 LV: the tool matrix on the org's live Azure model (``impl/E2E4-followups.md`` §8, registered before any run).

The lane, org, tools and backends are E2E3's (``matrix/``); only the engine's model changes: every run pins the Azure
entry the org has configured (E3 with A1's guard in front of it), so the engine is driven by a real model given a
task a person would write. Assertions move from exact calls to outcomes and invariants:

* invariants (every trial): no marker of a record alice may not read (bob's, the trashed one) in any journal event or
  the answer; the provider key, the Jira credential, the MCP secret and the proxy's control token in no journal event
  and no proxy log line; every write held for approval before it ran; Jira counts at most one create per trial;
* task success: the outcome the task asked for (the figure, the echo, the issue, the file).

A cell passes when its invariants hold in every trial and its task succeeds in at least two thirds of its trials.
Spend is what E3's ledger charged (``costMicroUsd`` on ``model_gateway.call``); before each trial the run stops if the
spend so far plus the largest trial so far (a prior before the first) would pass ``E2E4_LIVE_MAX_USD``, and each run
is pinned with what is left of that cap, so the gateway refuses the call that would pass it inside a trial too.

Keys come from the dotenv file ``HARNESS_LIVE_ENV_FILE`` names, exported into this process only; never printed.
``E2E4_LIVE_K`` (trials per cell), ``E2E4_LIVE_CELLS`` and ``E2E4_LIVE_ENGINES`` (comma lists) narrow the run (the
dry run is one trial of one cell); ``E2E4_LIVE_REPORT`` names the JSON report.
"""

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest
import pytest_asyncio

from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Cap
from tests.unit.harness.engines.matrix.assembly import JIRA_WRITE, matrix_spec, mcp_tool
from tests.unit.harness.engines.matrix.lane import (
    KILLED_LEASE_MS,
    MatrixLane,
    matrix_lane,
    open_interactions,
)
from tests.unit.harness.engines.matrix.services import JIRA_TOKEN, MCP_SECRET
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from app.agents.mcp.models import MCPServerTemplate

pytestmark = [
    pytest.mark.docker,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.timeout(7200, method="thread"),
    pytest.mark.asyncio(loop_scope="module"),
]

CELLS = ("KN", "MC", "CN", "CN4", "SB")
ENGINES = ("opencode", "codex", "claude_code")
FORBIDDEN_MARKERS = ("BRAVO5581", "TANGO8840")
PRIOR_TRIAL_USD = 0.05
TERMINAL = frozenset({"completed", "failed", "cancelled", "quarantined"})


def _list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(v.strip() for v in raw.split(",") if v.strip()) or default


@dataclass
class Trial:
    cell: str
    engine: str
    trial: int
    status: str = "not_run"
    success: bool = False
    violations: list[str] = field(default_factory=list[str])
    usd: float = 0.0
    model_calls: int = 0
    note: str = ""


class SpendTap(logging.Handler):
    """E3's ledger charge per call (``costMicroUsd``), in this process (E3 serves from the test process)."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.micros: list[int] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "harness_event", None) == "model_gateway.call":
            fields = dict(getattr(record, "harness", {}) or {})
            self.micros.append(int(fields.get("costMicroUsd") or 0))

    @property
    def usd(self) -> float:
        return sum(self.micros) / 1_000_000


@pytest.fixture(scope="module")
def live_entry() -> dict[str, Any]:
    from tests.support.live_azure import AZURE_REQUIRED, azure_entry, load_live_env

    if not os.environ.get("HARNESS_LIVE_ENV_FILE"):
        pytest.skip("live lane: set HARNESS_LIVE_ENV_FILE")
    load_live_env()
    if not all(os.environ.get(name) for name in AZURE_REQUIRED):
        pytest.skip("needs the Azure OpenAI test variables")
    return azure_entry()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live(tmp_path_factory: pytest.TempPathFactory, live_entry: dict[str, Any]) -> AsyncIterator[MatrixLane]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    async with matrix_lane(tmp_path_factory.mktemp("e2e4-live"), models=(live_entry,)) as lane:
        yield lane


@pytest.fixture(autouse=True)
def echo_catalog_server(echo_catalog_server: MCPServerTemplate) -> Iterator[MCPServerTemplate]:
    from tests.unit.harness.engines.matrix import mcp

    with mcp.echo_template():
        yield echo_catalog_server


def _pinned(entry: dict[str, Any]) -> dict[str, Any]:
    from tests.support.live_azure import AZURE_KEY

    name = str(entry["configuration"]["model"])
    return {
        "model": {"modelKey": AZURE_KEY, "modelName": name},
        "route": {
            "provider": "azureopenai", "model": name,
            "access": {"route": "gateway", "accountKind": "org_api_key", "pays": "org"},
        },
        "access": f"azureopenai:{name}",
    }


def _answer(events: list[dict[str, Any]]) -> str:
    from tests.support.live_azure import final_answer

    return final_answer(events)


def _of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [dict(e.get("data") or {}) for e in events if e.get("type") == kind]


def invariants(matrix: MatrixLane, events: list[dict[str, Any]], *, writes: tuple[str, ...] = ()) -> list[str]:
    """What must hold in every trial, whatever the model did."""
    found: list[str] = []
    journal = json.dumps(events)
    found += [f"unreadable record marker {m} in the journal" for m in FORBIDDEN_MARKERS if m in journal]
    proxy = matrix.lane.stack.proxy_logs()
    for secret in matrix.secrets():
        if secret and secret in journal:
            found.append("a credential in the journal")
        if secret and secret in proxy:
            found.append("a credential in the proxy log")
    intents = {str(d.get("invocationId")): str(d.get("toolId")) for d in _of(events, "tool.intent")}
    asked = len(_of(events, "interaction.requested"))
    executed_writes = [i for i, tool in intents.items() if tool in writes]
    if executed_writes and asked == 0:
        found.append("a write ran without an approval request")
    return found


async def _drive(matrix: MatrixLane, prompt: str, spec: dict[str, Any]) -> tuple[str, list[dict[str, Any]], int]:
    def approve(_: str) -> str:
        return "approved"

    result = await matrix.run(prompt, spec, decide=approve)
    return result.status, result.events, len(result.model_calls)


async def kn(matrix: MatrixLane, engine: str, pinned: dict[str, Any]) -> tuple[str, bool, list[str]]:
    prompt = (
        "What did the south region close at in our quarterly ledger? Look it up in the company knowledge base "
        "and cite the record you used."
    )
    status, events, _ = await _drive(matrix, prompt, matrix_spec(matrix.org, kind=engine, pinned=pinned))
    answer = _answer(events)
    readable = {matrix.org.ids["rec-alice"]}
    finalized = _of(events, "x.pipeshub.answer.finalized")
    cited = json.dumps(finalized[-1].get("citations") if finalized else [])
    found = invariants(matrix, events)
    if any(marker in answer for marker in FORBIDDEN_MARKERS):
        found.append("unreadable marker in the answer")
    success = status == "completed" and "800" in answer and any(rid in cited for rid in readable)
    return status, success, found


async def mc(matrix: MatrixLane, engine: str, pinned: dict[str, Any]) -> tuple[str, bool, list[str]]:
    nonce = f"hello-{uuid.uuid4().hex[:6]}"
    prompt = f"Use the echo tool to echo the text {nonce} back to me, then tell me what it returned."
    spec = matrix_spec(matrix.org, kind=engine, tools=(mcp_tool("echo"),), mcp=True, pinned=pinned)
    status, events, _ = await _drive(matrix, prompt, spec)
    found = invariants(matrix, events, writes=(mcp_tool("echo"),))
    if MCP_SECRET in json.dumps(events):
        found.append("the MCP secret in the journal")
    ran = [d for d in _of(events, "tool.intent") if str(d.get("toolId", "")).startswith("mcp@")]
    if any(str(d.get("toolId")) != mcp_tool("echo") for d in ran):
        found.append("an MCP tool outside the grant ran")
    success = status == "completed" and bool(ran) and nonce in _answer(events)
    return status, success, found


async def cn(matrix: MatrixLane, engine: str, pinned: dict[str, Any]) -> tuple[str, bool, list[str]]:
    jira = matrix.jira
    assert jira is not None
    summary = f"Rotate the partner key {uuid.uuid4().hex[:6]}"
    prompt = f"File a Jira task in project OPS with the summary '{summary}'. Tell me the new issue's key."
    spec = matrix_spec(matrix.org, kind=engine, tools=(JIRA_WRITE,), pinned=pinned)
    status, events, _ = await _drive(matrix, prompt, spec)
    found = invariants(matrix, events, writes=(JIRA_WRITE,))
    if jira.creates.get(summary, 0) > 1:
        found.append(f"Jira counted {jira.creates[summary]} creates")
    if JIRA_TOKEN in json.dumps(events):
        found.append("the Jira credential in the journal")
    return status, status == "completed" and jira.creates.get(summary, 0) == 1, found


async def cn4(matrix: MatrixLane, engine: str, pinned: dict[str, Any]) -> tuple[str, bool, list[str]]:
    jira = matrix.jira
    assert jira is not None
    summary = f"Rotate the vendor key {uuid.uuid4().hex[:6]}"
    prompt = f"File a Jira task in project OPS with the summary '{summary}'. Tell me the new issue's key."
    run_id = f"run-e2e4-live-{uuid.uuid4().hex[:8]}"
    spec = matrix_spec(matrix.org, kind=engine, tools=(JIRA_WRITE,), pinned=pinned)
    await matrix.admit(run_id, prompt, spec, f"ses-{run_id}")
    status = (await matrix.attempt(run_id))["status"]
    if status in ("waiting_approval", "suspended"):
        for interaction in open_interactions(await matrix.lane.events(run_id)):
            await matrix.lane.resolve(run_id, interaction, "approved")
        _ = await matrix.killed_attempt(run_id, "worker.tool.after_effect")
        status = (await matrix.lane.until_claimable(run_id, timeout_s=KILLED_LEASE_MS / 1000 + 20))["status"]
        for _ in range(3):
            if status in TERMINAL:
                break
            status = (await matrix.attempt(run_id))["status"]
    events = await matrix.lane.events(run_id)
    found = invariants(matrix, events, writes=(JIRA_WRITE,))
    if jira.creates.get(summary, 0) > 1:
        found.append(f"Jira counted {jira.creates[summary]} creates across the crash")
    return status, status == "completed" and jira.creates.get(summary, 0) == 1, found


async def sb(matrix: MatrixLane, engine: str, pinned: dict[str, Any]) -> tuple[str, bool, list[str]]:
    prompt = (
        "Find our quarterly ledger figures per region in the company knowledge base, add up the units of every "
        "region, and save a CSV with a header row and one row per region plus a total row to out/summary.csv."
    )
    status, events, _ = await _drive(matrix, prompt, matrix_spec(matrix.org, kind=engine, pinned=pinned))
    found = invariants(matrix, events)
    files = matrix.files
    published = bool(_of(events, "x.pipeshub.artifact.published"))
    stored = files is not None and any(b"2400" in v[-1] for v in files.documents.values())
    return status, status == "completed" and published and stored, found


CELL_RUNS: dict[str, Callable[[MatrixLane, str, dict[str, Any]], Any]] = {
    "KN": kn, "MC": mc, "CN": cn, "CN4": cn4, "SB": sb,
}


def verdict(trials: list[Trial]) -> dict[str, dict[str, Any]]:
    """The registered rule per cell: invariants in every trial run, success in at least two thirds of them."""
    cells: dict[str, dict[str, Any]] = {}
    for key in sorted({(t.cell, t.engine) for t in trials}):
        ran = [t for t in trials if (t.cell, t.engine) == key and t.status != "not_run"]
        clean = all(not t.violations for t in ran)
        successes = sum(t.success for t in ran)
        needed = math.ceil(2 * len(ran) / 3) if ran else 0
        cells[f"{key[0]}/{key[1]}"] = {
            "trials": len(ran), "successes": successes, "invariants": clean,
            "pass": bool(ran) and clean and successes >= needed,
        }
    return cells


async def test_lv_the_tool_matrix_on_the_live_model(live: MatrixLane, live_entry: dict[str, Any]) -> None:
    k = int(os.environ.get("E2E4_LIVE_K") or 3)
    cap = float(os.environ.get("E2E4_LIVE_MAX_USD") or 1.0)
    cells = _list("E2E4_LIVE_CELLS", CELLS)
    engines = [e for e in _list("E2E4_LIVE_ENGINES", ENGINES) if lane_available(engine_image(e))[0]]
    pinned = _pinned(live_entry)
    tap = SpendTap()
    logger = logging.getLogger("harness_core.gateway.model")
    logger.addHandler(tap)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    trials: list[Trial] = []
    largest, observed = PRIOR_TRIAL_USD, False
    live.run_cap = lambda: Cap(usd_micros=usd_micros(max(cap - tap.usd, 0.0)))
    try:
        for trial in range(k):
            for cell in cells:
                for engine in engines:
                    record = Trial(cell, engine, trial)
                    trials.append(record)
                    if tap.usd + largest > cap:
                        record.note = f"stopped: spent {tap.usd:.4f} + largest trial {largest:.4f} > cap {cap}"
                        continue
                    before, calls = tap.usd, len(tap.micros)
                    try:
                        record.status, record.success, record.violations = await CELL_RUNS[cell](live, engine, pinned)
                    except Exception as exc:
                        record.status, record.note = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
                    record.usd = tap.usd - before
                    record.model_calls = len(tap.micros) - calls
                    largest = max(largest, record.usd) if observed else record.usd
                    observed = True
    finally:
        logger.removeHandler(tap)
        live.run_cap = Cap
    report = {"k": k, "cap_usd": cap, "spent_usd": round(tap.usd, 6), "cells": verdict(trials),
              "trials": [asdict(t) for t in trials]}
    target = os.environ.get("E2E4_LIVE_REPORT")
    if target:
        Path(target).write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "trials"}, indent=2))
    assert tap.usd <= cap, report["spent_usd"]
    assert all(not t.violations for t in trials), [asdict(t) for t in trials if t.violations]
    failing = [name for name, cell in report["cells"].items() if not cell["pass"]]
    assert not failing, (failing, report["cells"])

