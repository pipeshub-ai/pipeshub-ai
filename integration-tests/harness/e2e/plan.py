"""A row's setup as data (04 §4.2): one step per factor level, each a product setting or a lane fixture.

``plan(row)`` is pure: it reads the row and returns a ``RowPlan`` the driver applies to a running stack. Every factor
has a step in ``STEPS`` and every level of it is handled there (a unit test holds this against
``e2e-factors.json``), so a new level fails loudly instead of running a row that tests nothing. A level the lane
cannot set up yet is a ``Blocked`` with the finding that tracks it; the row is then reported blocked, never passed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

# routing_table lives in scripts/; importing this module must not depend on a sibling having put it on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import routing_table  # noqa: E402
from matrix import Row  # noqa: E402

Kind = Literal["agent", "assistant"]


@dataclass(frozen=True)
class Blocked:
    finding: str
    reason: str


@dataclass(frozen=True)
class ProcessEnv:
    """Environment of the stack's processes that read these settings: Node (whose admission pins the deployment's
    flow rule), the durable worker and the query service's legacy loop, as an operator sets the container's.

    Rows are ordered by it, so a stack restarts its processes only when the next row needs another environment."""

    flow_guard: bool = True
    compiler: bool = False
    kill_switch: bool = False
    engine_runs: bool = False
    sandbox_mode: str = "broker"
    allow_network: bool = False

    def env(self) -> dict[str, str]:
        return {
            "HARNESS_FLOW_GUARD": "true" if self.flow_guard else "false",
            "AGENT_HARNESS_KILL_SWITCH": "true" if self.kill_switch else "false",
            "HARNESS_CONTEXT_COMPILER": "true" if self.compiler else "false",
            "HARNESS_ENGINE_RUNS": "true" if self.engine_runs else "false",
            "SANDBOX_MODE": self.sandbox_mode,
            "SANDBOX_ALLOW_NETWORK": "true" if self.allow_network else "false",
        }


@dataclass(frozen=True)
class OrgSettings:
    """What is set for the org before the turn: Labs flags and the coding engine through Node's admin routes, the
    approval rules in the deployment's approvals document (``writes``, and the flow rule's Never run for
    ``flow_deny``), and for ``memory`` the admin's own memory switch."""

    writes: Literal["allow", "ask"] = "allow"
    flow_deny: bool = False
    memory: bool = False
    scheduled_runs: bool = False
    # The org's default coding engine (ENGPICK): what the default chat's Agent mode hands a job to; none: no engine.
    coding_engine: str = "none"


@dataclass(frozen=True)
class AgentSpec:
    """The agent the turn runs: its tool family, engine and sandbox egress."""

    family: str = "none"
    engine: str = "none"
    egress: Literal["none", "allowlist", "open"] = "none"
    child_model: str = "none"


@dataclass(frozen=True)
class TurnSpec:
    kind: Kind = "agent"
    chat_mode: str = "quick"
    project: bool = False
    action: str = "send"
    trigger: str = "interactive"
    decision: str = "none"
    wire: str = "openai_chat"
    # The body's harness field (``durable`` for the default chat's Agent mode, ASSIST1); None: absent.
    harness: str | None = None


@dataclass(frozen=True)
class RowPlan:
    row: Row
    process: ProcessEnv = field(default_factory=ProcessEnv)
    org: OrgSettings = field(default_factory=OrgSettings)
    agent: AgentSpec = field(default_factory=AgentSpec)
    turn: TurnSpec = field(default_factory=TurnSpec)
    role: str = "user"
    blocked: tuple[Blocked, ...] = ()

    @property
    def role_wire(self) -> str | None:
        """The wire of the model the agent's ``default`` role is set to (04 §4.2 ``child_model``); None: no role model.

        ``different`` and ``forbidden`` take a model on another wire than the turn's, so the scripted model's record of
        a call (its wire and model) shows whose model it named."""
        level = self.agent.child_model
        if level == "none":
            return None
        return self.turn.wire if level == "same" else OTHER_WIRE.get(self.turn.wire, DEFAULT_WIRE)

    def block(self, finding: str, reason: str) -> RowPlan:
        return replace(self, blocked=(*self.blocked, Blocked(finding, reason)))


Step = Callable[[RowPlan, str], RowPlan]

# routing.json's e2e modes, as the oracle reads them (R31JOURNEY-FU-7: never a copy): the route a mode's turn takes, the
# body's chatMode on it, whether it opens in a project, and the body's harness field.
_E2E_MODES: Mapping[str, Mapping[str, str]] = routing_table.table()["e2e"]["modes"]
MODES: Mapping[str, tuple[Kind, str, bool]] = {
    mode: (inp["kind"], inp["chatMode"], inp.get("project") == "request")  # type: ignore[misc]
    for mode, inp in _E2E_MODES.items()
}


DEFAULT_WIRE = "openai_chat"
OTHER_WIRE: Mapping[str, str] = {DEFAULT_WIRE: "anthropic_messages"}
ROLE_KEY = "default"
LEGACY_ROLE_MODELS = Blocked("QA37-F1", "MA1 §3 gives the legacy loop's sub-agents no rule for a role model: they are "
                                        "not child runs, so nothing here can be asserted until a rule is decided")
ORG_MODEL_ROUTES = Blocked("CPD-11", "no admin route writes an org's models.routes (CPD-11's), and the stack's Node "
                                     "runs CONTROL_PLANE_PIN=shadow, which counts a forbidden role model without "
                                     "refusing it")


def _levels(*names: str) -> Callable[[Step], Step]:
    """A step that refuses any level it was not written for."""

    def wrap(step: Step) -> Step:
        def checked(p: RowPlan, level: str) -> RowPlan:
            if level not in names:
                raise ValueError(f"no setup step for {level!r} (handled: {names})")
            return step(p, level)

        checked.levels = names  # type: ignore[attr-defined]
        return checked

    return wrap


def _turn(p: RowPlan, **changes: object) -> RowPlan:
    return replace(p, turn=replace(p.turn, **changes))


@_levels(*MODES)
def _mode(p: RowPlan, level: str) -> RowPlan:
    kind, chat_mode, project = MODES[level]
    harness = _E2E_MODES[level].get("harnessRequest", "none")
    return _turn(p, kind=kind, chat_mode=chat_mode, project=project, harness=None if harness == "none" else harness)


@_levels("send", "regenerate", "reconnect")
def _action(p: RowPlan, level: str) -> RowPlan:
    return _turn(p, action=level)


@_levels("durable", "kill_switch", "standalone_mongo")
def _path(p: RowPlan, level: str) -> RowPlan:
    # standalone_mongo is the stack's own MongoDB (ST-4, ST-7): the stack fixture brings it up, nothing to set here.
    # kill_switch is the operator's instance stop (CTL-5, FLAGS.md "Rolling back"), an env setting on Node: routing's
    # R02, `kill_switch`. Labs "Agent harness" off is the org's own switch, R03 `org_disabled`, not this level.
    return replace(p, process=replace(p.process, kill_switch=level == "kill_switch"))


@_levels("none", "opencode", "codex", "claude_code")
def _engine(p: RowPlan, level: str) -> RowPlan:
    p = replace(p, agent=replace(p.agent, engine=level), process=replace(p.process, engine_runs=level != "none"))
    # The default chat names no engine of its own: its Agent mode hands a job to the org's coding engine.
    return replace(p, org=replace(p.org, coding_engine=level)) if p.turn.kind == "assistant" else p


@_levels("openai_chat", "openai_responses", "anthropic_messages", "azure_openai", "gemini", "bedrock")
def _wire(p: RowPlan, level: str) -> RowPlan:
    return _turn(p, wire=level)


@_levels("off", "on")
def _compiler(p: RowPlan, level: str) -> RowPlan:
    return replace(p, process=replace(p.process, compiler=level == "on"))


@_levels("off", "ask", "org_deny")
def _flow_guard(p: RowPlan, level: str) -> RowPlan:
    return replace(p, process=replace(p.process, flow_guard=level != "off"),
                   org=replace(p.org, flow_deny=level == "org_deny"))


@_levels("allow", "ask")
def _write_policy(p: RowPlan, level: str) -> RowPlan:
    return replace(p, org=replace(p.org, writes=level))


@_levels("none", "allow_once", "allow_conversation", "deny", "expire")
def _decision(p: RowPlan, level: str) -> RowPlan:
    return _turn(p, decision=level)


@_levels("none", "knowledge", "mcp", "toolset", "run_code", "skills", "memory")
def _tool_family(p: RowPlan, level: str) -> RowPlan:
    p = replace(p, agent=replace(p.agent, family=level))
    return replace(p, org=replace(p.org, memory=True)) if level == "memory" else p


@_levels("unset", "docker", "broker_closed", "broker_allowlist", "broker_open")
def _sandbox(p: RowPlan, level: str) -> RowPlan:
    mode = {"unset": "", "docker": "docker"}.get(level, "broker")
    egress = {"broker_allowlist": "allowlist", "broker_open": "open"}.get(level, "none")
    return replace(p, process=replace(p.process, sandbox_mode=mode, allow_network=level == "broker_open"),
                   agent=replace(p.agent, egress=egress))


@_levels("interactive", "schedule_cron", "schedule_webhook")
def _trigger(p: RowPlan, level: str) -> RowPlan:
    p = _turn(p, trigger=level)
    return replace(p, org=replace(p.org, scheduled_runs=True)) if level != "interactive" else p


@_levels("user", "admin", "operator")
def _role(p: RowPlan, level: str) -> RowPlan:
    return replace(p, role=level)


@_levels("none", "same", "different", "forbidden")
def _child_model(p: RowPlan, level: str) -> RowPlan:
    p = replace(p, agent=replace(p.agent, child_model=level))
    if p.row.expected.child == "unspecified":
        p = p.block(LEGACY_ROLE_MODELS.finding, LEGACY_ROLE_MODELS.reason)
    return p.block(ORG_MODEL_ROUTES.finding, ORG_MODEL_ROUTES.reason) if level == "forbidden" else p


def _store(*levels: str) -> Step:
    # The store and deployment factors are the stack's (04 §4.1); the session fixture brings that stack up.
    @_levels(*levels)
    def step(p: RowPlan, _level: str) -> RowPlan:
        return p

    return step


STEPS: Mapping[str, Step] = {
    "mode": _mode, "action": _action, "path": _path, "engine": _engine, "wire": _wire, "compiler": _compiler,
    "flow_guard": _flow_guard, "write_policy": _write_policy, "decision": _decision, "tool_family": _tool_family,
    "sandbox": _sandbox, "trigger": _trigger, "role": _role, "child_model": _child_model,
    "graph": _store("neo4j", "arango"), "mongo": _store("replset", "standalone"), "kv": _store("redis", "etcd"),
    "broker": _store("kafka", "redis_streams"), "deploy": _store("compose", "helm"),
}


def plan(row: Row) -> RowPlan:
    p = RowPlan(row=row)
    for factor, level in row.factors.items():
        p = STEPS[factor](p, level)
    return p
