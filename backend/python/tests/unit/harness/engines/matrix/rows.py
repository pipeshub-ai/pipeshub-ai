"""QA-31: one engine's rows of the e2e matrix (04 §2), as runs of E2E3's matrix lane.

What a row must show is ``oracle(row)`` of ``scripts/e2e_covering_array.py``, the function the plan renders its
tables from, so the plan and this lane cannot disagree. ``plan_row`` turns a row into what the lane can apply (04 §4.2:
the tool family's script, the org's approvals document, the flow-guard switch, the user's decision) and names every
factor it leaves to the full-stack runner (QA-6), so none is dropped silently. ``observe`` reads the oracle's
answers back from a run's journal and its external effects. Pure: the Docker module drives the lane.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from harness_core.tools.call_tool import SEARCH_TOOLS
from tests.unit.harness.engines.matrix.assembly import JIRA_READ, JIRA_WRITE, mcp_tool

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[7]
DATA = ROOT / "docs/test-plan/data"
ARRAYS = ("e2e-pairwise.json", "e2e-threeway-core.json")
MUST_RUN = DATA / "e2e-must-run.json"

FETCH = "knowledgegraph__fetch_record"
SEARCH = "knowledgegraph__search"
ECHO = "mcp_echo_echo"
JIRA_SEARCH, JIRA_CREATE = "jira__search_issues", "jira__create_issue"
APPROVALS_PATH = "/services/agentHarness/approvals"
SINKS = frozenset({"mcp", "toolset"})
# Factors the matrix lane does not set up: the store and deployment profile, the turn action, the trigger, the
# asserted surface and the sandbox's egress level (an engine's sandbox reaches the gateways only, at every level).
NOT_ON_LANE = ("graph", "mongo", "kv", "broker", "deploy", "action", "trigger", "role", "sandbox")
FAMILY_NOT_ON_LANE = frozenset({"skills", "memory"})
# An engine run spawns no child run, so the matrix gives its rows no role model (the `child_model_needs_delegation`
# constraint); a row that names one is the full-stack runner's.
NO_ROLE_MODEL = "none"
E2E_RUNNER = ROOT / "integration-tests/harness/e2e"

Decision = Literal["none", "approved", "denied", "expire"]
DECISIONS: dict[str, Decision] = {
    "none": "none", "allow_once": "approved", "allow_conversation": "approved", "deny": "denied", "expire": "expire",
}


def _script_module(name: str, folder: Path = ROOT / "scripts", file: str | None = None) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, folder / f"{file or name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def covering() -> ModuleType:
    """``scripts/e2e_covering_array.py`` (its ``routing_table`` import is a sibling script)."""
    _ = _script_module("routing_table")
    return _script_module("e2e_covering_array")


def script_name(family: str) -> str:
    """The tool family's script (04 §4.2), from the full-stack runner's table (``integration-tests/harness/e2e``) so
    the two lanes name one script per family; skills and memory have no lane wiring for an engine yet (QA31-O2)."""
    if family in FAMILY_NOT_ON_LANE:
        return "answer"
    return str(_script_module("qa6_e2e_scripts", E2E_RUNNER, "scripts").SCRIPT_NAMES[family])


@dataclass(frozen=True)
class Outcome:
    """The oracle's answers for a row (04 §4.3)."""

    route: str
    executor: str
    card: bool
    outcome: str
    effects: int
    child: str = "inherit"


@dataclass(frozen=True)
class ArrayRow:
    row_id: str
    factors: Mapping[str, str]

    def expected(self) -> Outcome:
        """What the matrix lane must see: the oracle's answers, the executor as the engine itself. The lane runs the
        engine directly, as the run a coder agent's turn hands its work to (CTL-6: the oracle's ``loop+code_task:<k>``)."""
        answer = covering().oracle(dict(self.factors))
        executor = str(answer["executor"]).replace("loop+code_task:", "engine:", 1)
        return Outcome(answer["route"], executor, answer["card"], answer["outcome"], answer["effects"], answer["child"])


def engine_rows(engine: str) -> tuple[ArrayRow, ...]:
    """Every row of both committed arrays whose engine is ``engine``, then the must-run scenarios that name it."""
    arrays = [raw for name in ARRAYS for raw in json.loads((DATA / name).read_text())["rows"]]
    must_run = json.loads(MUST_RUN.read_text())
    return (
        *(ArrayRow(r["id"], {k: v for k, v in r.items() if k not in ("id", "stack")}) for r in arrays if r["engine"] == engine),
        *(
            ArrayRow(s["id"], {**must_run["defaults"], **s["factors"]})
            for s in must_run["scenarios"] if s["factors"].get("engine") == engine
        ),
    )


@dataclass(frozen=True)
class RowPlan:
    """What the matrix lane applies for a row, and what it leaves to the full-stack runner."""

    row: ArrayRow
    script: str
    tools: tuple[str, ...]
    mcp: bool
    mode: str
    # The deployment's layer (``default``) of ``/services/agentHarness/approvals``; ``None`` leaves the document absent.
    approvals_layer: Mapping[str, Any] | None
    flow_guard: bool
    decision: Decision
    scope: str | None
    not_on_lane: Mapping[str, str] = field(default_factory=dict[str, str])

    def approvals_document(self, _org_id: str) -> dict[str, Any] | None:
        return None if self.approvals_layer is None else {"default": dict(self.approvals_layer)}


def plan_row(row: ArrayRow) -> RowPlan:
    f = row.factors
    family = f["tool_family"]
    tools: tuple[str, ...] = {"toolset": (JIRA_READ, JIRA_WRITE), "mcp": (mcp_tool("echo"),)}.get(family, ())
    layer: dict[str, Any] = {}
    if f["write_policy"] == "ask":
        layer["writes"] = "ask"
    if f["flow_guard"] == "org_deny":
        layer["flow"] = {"level": "deny"}
    decision = DECISIONS[f["decision"]]
    not_on_lane = {name: f[name] for name in NOT_ON_LANE}
    if family in FAMILY_NOT_ON_LANE:
        not_on_lane["tool_family"] = family
    if f["child_model"] != NO_ROLE_MODEL:
        not_on_lane["child_model"] = f["child_model"]
    return RowPlan(
        row=row,
        script=script_name(family),
        tools=tools,
        mcp=family == "mcp",
        mode=f["mode"],
        approvals_layer=layer or None,
        flow_guard=f["flow_guard"] != "off",
        decision=decision,
        scope="conversation" if f["decision"] == "allow_conversation" else None,
        not_on_lane=not_on_lane,
    )


def tool(name: str, **arguments: Any) -> str:  # noqa: ANN401
    return f"tool {name} {json.dumps(arguments)}"


def prompt(script: str, *, private_record: str, marker: str) -> str:
    """The scripted model's directives for ``script``. Every sink script reads a private record first, so the run is
    tainted when it writes (04 §4.3); ``marker`` is what the write sends and the effect count looks for."""
    read = tool(FETCH, record_ids=[private_record])
    lines = {
        "answer": [],
        "read-then-answer": [tool(SEARCH, query="regional sales"), read],
        # D-LAZY-4: an engine's list never carries an MCP tool; the gateway's search finds it.
        "read-then-write-mcp": [read, tool(SEARCH_TOOLS, query="echo"), tool(ECHO, text=marker)],
        "read-then-write-toolset": [
            tool(JIRA_SEARCH, jql="project = OPS"),
            read,
            tool(JIRA_CREATE, project_key="OPS", summary=marker, issue_type_name="Task"),
        ],
        "code-then-answer": [f"shell mkdir -p out && echo {marker} > out/qa31.txt && cat out/qa31.txt"],
    }[script]
    return "\n".join([*lines, f"say {marker} done"])


def _data(events: Sequence[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [dict(e.get("data") or {}) for e in events if e.get("type") == kind]


ENGINE_PROGRESS = "x.pipeshub.engine.progress"


def engine_ran(events: Sequence[Mapping[str, Any]]) -> bool:
    """Whether an engine executed the run: its own journal events (``engine.*``) or the progress lines only the
    engine path presents. A turn lost before the engine checkpointed leaves only the progress lines (QA31F)."""
    return any(str(e.get("type")).startswith("engine.") or e.get("type") == ENGINE_PROGRESS for e in events)


def observe(events: Sequence[Mapping[str, Any]], *, status: str, effects: int, engine: str | None) -> Outcome:
    """The oracle's answers as a run's journal shows them, as Node's timeline reads it: an approval still open
    when the run fails at its deadline expired. ``engine`` is the kind the run's engine sessions ran (``None`` when no
    engine ran); ``effects`` what the fake SaaS or MCP server recorded for the run. A child is ``inherit`` unless a
    ``child.spawned`` names a model a role set (MA1)."""
    types = [str(e.get("type")) for e in events]
    resolved = _data(events, "interaction.resolved")
    failed = _data(events, "run.failed")
    error_type = str(failed[-1].get("errorType")) if failed else None
    card = "interaction.requested" in types
    route = "durable" if "run.spec_pinned" in types or engine is not None else "legacy"
    executor = f"engine:{engine}" if engine is not None else ("loop" if route == "durable" else "legacy")
    decisions = {str(r.get("decision")) for r in resolved}
    if not card:
        outcome = ("written" if effects else "answered") if status == "completed" else f"{status}:{error_type}"
    elif status == "failed" and error_type == "deadline_exceeded" and decisions <= {"expired"}:
        outcome = "expired:approval_expired"
    elif decisions == {"denied"}:
        outcome = "denied:user"
    elif decisions == {"approved"} and effects:
        granted = any(g.get("scope") == "conversation" for g in _data(events, "interaction.granted"))
        outcome = "approved:written+grant" if granted else "approved:written"
    else:
        outcome = f"{status}:{sorted(decisions)}"
    child = "role:set" if any("model" in spawned for spawned in _data(events, "child.spawned")) else "inherit"
    return Outcome(route, executor, card, outcome, effects, child)


__all__ = [
    "APPROVALS_PATH",
    "ArrayRow",
    "Outcome",
    "RowPlan",
    "covering",
    "engine_rows",
    "engine_ran",
    "observe",
    "plan_row",
    "prompt",
]
