"""What a row's turn did, read from what the user received, the journal and the effects, in the oracle's terms.

``Observation`` holds the facts the driver collected; ``observed(obs)`` turns them into the oracle's six answers
(route, executor, card, outcome, effects, child). Pure, so the mapping is table-tested without a stack
(``backend/python/tests/unit/harness/test_qa6_runner.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Json = dict[str, Any]
TERMINAL_RUN_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.quarantined"})
ENGINE_EVENT_PREFIX = "engine."
REFUSED_LEGACY = "approval_unavailable"
CHILD_GRANT_REFUSED = "child_grant_too_large"


@dataclass
class Observation:
    """Facts about one turn under test (a regenerate's own turn, when the row regenerates)."""

    route: str = "unknown"
    reason: str | None = None
    legacy_reasons: list[str] = field(default_factory=list[str])
    """The ``reason`` of every ``admission.legacy`` line Node logged for the turn's request."""
    session_id: str | None = None
    run_id: str | None = None
    first_frame: str | None = None
    terminal: str | None = None
    """``finished`` (RUN_FINISHED or legacy ``complete``), ``error`` (RUN_ERROR or legacy ``error``), or None."""
    error_code: str | None = None
    answer: str = ""
    marker: str = ""
    """What the row's script makes the model answer once its tool calls are done."""
    cards: list[Json] = field(default_factory=list[Json])
    """``approval_requested`` frame values, in order."""
    decisions: list[str] = field(default_factory=list[str])
    events: list[Json] = field(default_factory=list[Json])
    """The journal events of the turn's run (its own stream and its children's)."""
    session_events: list[Json] = field(default_factory=list[Json])
    """Every event of the session, every run's: what the history checks read."""
    effects: int = 0
    write_results: list[str] = field(default_factory=list[str])
    """What the external-write tool answered the model (frames' TOOL_CALL_RESULT content), legacy refusals among it."""
    wires: set[str] = field(default_factory=set[str])
    """The provider wires the scripted model served this turn on."""
    model_calls: list[Json] = field(default_factory=list[Json])
    """The calls the scripted model served this turn, in order: ``kind`` (``delegate`` is deep mode's coordinator,
    ``loop`` a run working through the script), ``wire`` and ``model`` as the request named them."""
    role_model: Json | None = None
    """The model the lane set the agent's ``default`` role to (``wire``, ``modelKey``, ``modelName``), if any."""
    child_refusals: list[Json] = field(default_factory=list[Json])
    """Node's ``child.refused`` log records of the turn's run."""
    frames: list[Json] = field(default_factory=list[Json])
    frame_ids: list[str | None] = field(default_factory=list[str | None])
    reconnects: list[tuple[str | None, list[str]]] = field(default_factory=list[tuple[str | None, list[str]]])
    audit: Json = field(default_factory=dict[str, Any])
    audit_types: list[Json] = field(default_factory=list[Json])
    firings: list[Json] = field(default_factory=list[Json])
    previous_run_id: str | None = None
    saved_messages: list[str] = field(default_factory=list[str])
    admin_view: Json = field(default_factory=dict[str, Any])
    health: int | None = None
    log_text: str = ""
    memories: list[str] = field(default_factory=list[str])
    """The saved conversation's message types, in order."""
    notes: list[str] = field(default_factory=list[str])

    def types(self) -> list[str]:
        return [str(e.get("type")) for e in self.events]

    def of_type(self, *types: str) -> list[Json]:
        return [e for e in self.events if e.get("type") in types]


@dataclass(frozen=True)
class Observed:
    route: str
    reason: str
    executor: str
    card: bool
    outcome: str
    effects: int
    child: str


def _engine_kind(data: Json) -> str:
    """An engine event names its engine as a kind, or as the pinned engine (``{kind, version, binaryDigest}``)."""
    engine = data.get("engine")
    if isinstance(engine, dict):
        return str(engine.get("kind") or "")
    return str(engine or data.get("kind") or "")


def _executor(obs: Observation) -> str:
    """The executor the run was pinned with (``run.spec_pinned``), whatever it got to do before it ended."""
    if obs.route != "durable":
        return "legacy" if obs.route == "legacy" else "unknown"
    for event in obs.of_type("run.spec_pinned"):
        spec = (event.get("data") or {}).get("spec") or {}
        executor = spec.get("executor") or {}
        # A run pinned as an engine run (before CTL-6, or an engine child observed on its own).
        if isinstance(executor.get("engine"), dict):
            return f"engine:{executor['engine'].get('kind')}"
        # CTL-6: a coder agent's turn is a loop run that pins the engine it may hand off to.
        authority = (((spec.get("extensions") or {}).get("x.pipeshub") or {}).get("codeTask") or {}).get("profile") or {}
        kind = (authority.get("engine") or {}).get("kind") if isinstance(authority, dict) else None
        if executor.get("kind") == "loop" and kind:
            return f"loop+code_task:{kind}"
        if executor.get("kind"):
            return str(executor["kind"])
    engines = {_engine_kind(e.get("data") or {}) for e in obs.events if str(e.get("type", "")).startswith(ENGINE_EVENT_PREFIX)}
    engines.discard("")
    return f"engine:{sorted(engines)[0]}" if engines else "loop"


def _denied_rule(obs: Observation) -> str | None:
    for event in obs.of_type("tool.denied"):
        rule = (event.get("data") or {}).get("rule") or (event.get("data") or {}).get("reason")
        if rule:
            return str(rule)
    return None


def _granted(obs: Observation) -> bool:
    return bool(obs.of_type("interaction.granted"))


def outcome(obs: Observation, *, sink: bool) -> str:
    """The oracle's outcome vocabulary (04 §4.3) for what happened."""
    if obs.error_code == "approval_expired":
        return "expired:approval_expired"
    if not sink:
        return "answered" if obs.terminal == "finished" else f"failed:{obs.error_code or obs.terminal or 'no_end'}"
    if obs.effects > 0:
        if not obs.cards:
            return "written"
        return "approved:written+grant" if _granted(obs) else "approved:written"
    if any(r.startswith(REFUSED_LEGACY) for r in obs.write_results):
        return "refused:approval_unavailable"
    rule = _denied_rule(obs)
    if obs.cards and "denied" in obs.decisions:
        return "denied:user"
    if rule is not None:
        return f"denied:{rule}"
    return f"not_written:{obs.error_code or obs.terminal or 'no_end'}"


def spawned(obs: Observation) -> list[Json]:
    """``child.spawned`` data of the turn's own run (the parent's stream), in order."""
    return [dict(e.get("data") or {}) for e in obs.of_type("child.spawned") if e.get("runId") == obs.run_id]


def pinned_specs(obs: Observation) -> dict[str, Json]:
    """Each run's pinned spec, by run id (a child's is on its own stream)."""
    return {str(e.get("runId")): dict((e.get("data") or {}).get("spec") or {})
            for e in obs.session_events if e.get("type") == "run.spec_pinned"}


def _route_model(spec: Json) -> str | None:
    policy = (spec.get("model") or {}).get("policy") or []
    return str(policy[0].get("model")) if policy and isinstance(policy[0], dict) else None


def child_model(obs: Observation) -> str:
    """Whose model the turn's child runs call, in the oracle's words (MA1 §3): ``inherit`` when no ``child.spawned``
    names a model, ``role:same`` or ``role:different`` by the run's own pinned route when one does, and
    ``refused:<dimension>`` when Node refused the grant and no child was spawned."""
    children = spawned(obs)
    named = [str(c["model"].get("model")) for c in children if isinstance(c.get("model"), dict)]
    if named:
        own = _route_model(pinned_specs(obs).get(str(obs.run_id), {}))
        return "role:same" if all(n == own for n in named) else "role:different"
    refused = any((e.get("data") or {}).get("rule") == CHILD_GRANT_REFUSED for e in obs.of_type("tool.denied"))
    if refused and not children:
        dimensions = sorted({str(r.get("dimension")) for r in obs.child_refusals if r.get("dimension")})
        return f"refused:{','.join(dimensions) or 'unnamed'}"
    return "inherit"


def observed(obs: Observation, *, sink: bool) -> Observed:
    return Observed(
        route=obs.route,
        reason=obs.reason or "",
        executor=_executor(obs),
        card=bool(obs.cards),
        outcome=outcome(obs, sink=sink),
        effects=obs.effects,
        child=child_model(obs),
    )
