"""The oracle's assertions and 04 §4.4's extra ones, as a registry of checks over an ``Observation``.

Each ``Check`` names the §4.4 row it holds, when it applies (from the row's plan) and what it asserts (a list of
problems, empty when it holds). Pure, so each is table-tested without a stack; the live inputs (journal, frames,
effects) come from ``driver.collect``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from harness_core.contracts.generated.enums import HOLD_RULES, RULE_LAYERS
from matrix import Expected
from observe import Observation, Observed, observed, pinned_specs, spawned
from plan import ROLE_KEY, RowPlan

Json = dict[str, Any]
CARD_RULES = frozenset(HOLD_RULES)
CARD_LAYERS = frozenset(RULE_LAYERS)
CARD_RESOLUTIONS = frozenset({"interaction.resolved", "interaction.granted"})


def oracle_problems(expected: Expected, got: Observed) -> list[str]:
    problems = []
    for name in ("route", "executor", "card", "outcome", "effects", "child"):
        want, have = getattr(expected, name), getattr(got, name)
        if want != have:
            problems.append(f"oracle {name}: expected {want!r}, observed {have!r}")
    if expected.route == "legacy" and got.route == "legacy" and expected.reason != got.reason:
        problems.append(f"oracle reason: expected {expected.reason!r}, Node logged {got.reason!r}")
    return problems


@dataclass(frozen=True)
class Check:
    name: str
    applies: Callable[[RowPlan, Observation], bool]
    verify: Callable[[RowPlan, Observation], list[str]]


def _history(_plan: RowPlan, obs: Observation) -> list[str]:
    from harness_core.journal.types import JournalEvent
    from harness_core.testing.history import journal_history_violations

    return [f"history {v}" for v in journal_history_violations(JournalEvent.from_envelope(e) for e in obs.session_events)]


def _first_frame(_plan: RowPlan, obs: Observation) -> list[str]:
    return [] if obs.first_frame == "conversation_created" else [f"first frame is {obs.first_frame!r}, not conversation_created"]


def _finished_with_result(_plan: RowPlan, obs: Observation) -> list[str]:
    finished = [f for f in obs.frames if f.get("type") == "RUN_FINISHED"]
    if obs.terminal == "finished" and finished and not finished[-1].get("result"):
        return ["RUN_FINISHED carries no result"]
    if obs.terminal is None:
        return ["the turn has no terminal frame (RUN_FINISHED or RUN_ERROR)"]
    return []


def _scripted_answer(_plan: RowPlan, obs: Observation) -> list[str]:
    """A turn that finished is the script's turn: its answer is the script's, not a step that lost the script."""
    if obs.terminal != "finished" or not obs.marker or obs.marker in obs.answer:
        return []
    return [f"the answer is {obs.answer[:80]!r}, not the script's ({obs.marker})"]


def canary_problems(texts: Iterable[str], canaries: Iterable[str]) -> list[str]:
    secrets = [c for c in canaries if c]
    return [f"a canary credential ({secret[:6]}…) is in what the user received" for secret in secrets
            if any(secret in text for text in texts)]


def _spec_pinned_first(_plan: RowPlan, obs: Observation) -> list[str]:
    types = obs.types()
    if "run.spec_pinned" not in types:
        return ["no run.spec_pinned in the run's journal"]
    first_model = next((i for i, t in enumerate(types) if t == "model.request"), None)
    if first_model is not None and first_model < types.index("run.spec_pinned"):
        return ["a model.request comes before run.spec_pinned"]
    return []


def _legacy_unadmitted(_plan: RowPlan, obs: Observation) -> list[str]:
    return ["a legacy turn journaled run.admitted"] if obs.of_type("run.admitted") else []


def _legacy_recorded(_plan: RowPlan, obs: Observation) -> list[str]:
    """One record per turn says why today's loop served it, the install-wide switch included (QA6-F11)."""
    if len(obs.legacy_reasons) == 1:
        return []
    return [f"Node logged {len(obs.legacy_reasons)} admission.legacy records for the turn: {obs.legacy_reasons}"]


def expected_card_rule(plan: RowPlan) -> tuple[str, str]:
    """The rule a row's card names and whose it is (QA6-F6): the flow rule where it is on (every sink script reads
    before it writes, so the run is tainted by then), else the deployment's ``writes: ask``, else an engine's default."""
    if plan.process.flow_guard:
        return ("flow_external_write", "flow")
    if plan.org.writes == "ask":
        return ("writes", "deployment")
    return ("engine_writes", "engine")


def _card_rule(plan: RowPlan, obs: Observation) -> list[str]:
    problems = []
    explained = {str((e.get("data") or {}).get("interactionId")): e.get("data") or {}
                 for e in obs.session_events if e.get("type") == "interaction.explained"}
    for card in obs.cards:
        reason = card.get("reason") or {}
        named = (reason.get("rule"), reason.get("layer"))
        if named[0] not in CARD_RULES or named[1] not in CARD_LAYERS:
            problems.append(f"the card {card.get('interactionId')} names rule {named[0]!r} of layer {named[1]!r}")
            continue
        if named != expected_card_rule(plan):
            problems.append(f"the card names {named}, the row's rule is {expected_card_rule(plan)}")
        journaled = explained.get(str(card.get("interactionId")), {})
        if (journaled.get("rule"), journaled.get("layer")) != named:
            problems.append(f"the journal explains the card as {(journaled.get('rule'), journaled.get('layer'))}, not {named}")
    # A sub-agent's card is journaled on the sub-agent's own run.
    requested = [e for e in obs.session_events if e.get("type") == "interaction.requested"]
    if not requested:
        problems.append("the card has no interaction.requested in the journal")
    if obs.decisions and obs.decisions != ["expire"]:
        chained = {str(r.get("type")) for r in obs.audit_types} & CARD_RESOLUTIONS
        if not chained:
            problems.append("the decision is not on the org's audit chain")
    if obs.audit and not obs.audit.get("ok"):
        problems.append(f"the org's audit chain does not verify: {obs.audit.get('problems')}")
    return problems


def _regenerated(_plan: RowPlan, obs: Observation) -> list[str]:
    """The regenerate is a run of its own in the same conversation, and the answer it replaces is retracted."""
    problems = []
    if obs.previous_run_id is None or obs.run_id in (None, obs.previous_run_id) or not obs.of_type("run.admitted"):
        problems.append(f"the regenerate is not a durable run of its own (first {obs.previous_run_id}, then {obs.run_id})")
    answers = obs.saved_messages.count("bot_response")
    if answers > 1 or (obs.terminal == "finished" and answers != 1):
        problems.append(f"the saved conversation keeps {answers} answers to the question, not the regenerated one")
    return problems


_POSITION = re.compile(r"^(?:j:(\d+)\.(\d+)/|d:(\d+)\.(\d+)\.(\d+)\.(\d+)/)[^+]*(?:\+(\d+))?$")
CARD_FRAMES = frozenset({"approval_requested", "unknown_effect"})


def stream_position(frame_id: str) -> tuple[int, ...] | None:
    """Where a durable frame id places its frame: the journal or delta position, then its place among the frames
    stating a sub-agent's card after it (``projection/stream-id.ts``). None for an id that names no position."""
    match = _POSITION.match(frame_id)
    if match is None:
        return None
    seq, k, dseq, dk, n, m, stated = match.groups()
    position = (int(seq), int(k), 0, 0) if seq is not None else (int(dseq), int(dk), int(n), int(m))
    return (*position, int(stated or 0))


def _stream_ids(_plan: RowPlan, obs: Observation) -> list[str]:
    """Over the whole turn, every connection of it: no id twice, no position before one already sent, and no card
    announced twice (QA6-F12). A reattach is told only what the client does not have."""
    problems = []
    ids = [i for i in obs.frame_ids if i]
    again = sorted({i for i in ids if ids.count(i) > 1})
    if again:
        problems.append(f"frame ids sent more than once: {again[:3]}")
    last: tuple[int, ...] | None = None
    for frame_id in ids:
        position = stream_position(frame_id)
        if position is None:
            continue
        if last is not None and position < last:
            problems.append(f"{frame_id} was sent after a later position")
            break
        last = position
    cards = [(str(value.get("childRunId") or ""), str(value.get("interactionId")))
             for frame in obs.frames if frame.get("type") == "CUSTOM" and frame.get("name") in CARD_FRAMES
             for value in [frame.get("value") or {}]]
    repeated = sorted({card for card in cards if cards.count(card) > 1})
    if repeated:
        problems.append(f"cards announced more than once: {repeated[:3]}")
    return problems


def _resumed(_plan: RowPlan, obs: Observation) -> list[str]:
    return [] if obs.reconnects else ["the client never reattached after dropping the stream"]


def _scheduled_once(plan: RowPlan, obs: Observation) -> list[str]:
    runs = {f.get("runId") for f in obs.firings if f.get("runId")}
    problems = [] if len(runs) == 1 else [f"{len(runs)} runs for one {plan.turn.trigger} delivery"]
    admitted = obs.of_type("run.admitted")
    if admitted and (admitted[0].get("data") or {}).get("runKind") not in ("scheduled", "schedule"):
        problems.append(f"the scheduled run is admitted as {(admitted[0].get('data') or {}).get('runKind')!r}")
    return problems


def _code_receipt(_plan: RowPlan, obs: Observation) -> list[str]:
    if obs.route != "durable":
        return []
    if not obs.of_type("x.pipeshub.command.finished"):
        return ["no execution receipt (x.pipeshub.command.finished) for run_code"]
    return []


def _engine_workspace(plan: RowPlan, obs: Observation) -> list[str]:
    """A regenerate replays the stored question, so its engine writes the same file again: nothing new to commit.

    Since CTL-6 the turn is the loop's and the engine is the child its ``code_task`` spawned, on its own stream: the
    engine's checkpoint is journaled there, not on the turn's (QAFINAL-FU-ST1, PW-050)."""
    runs = {obs.run_id, *(str(child.get("childRunId")) for child in spawned(obs))}
    types = {str(e.get("type")) for e in obs.session_events if e.get("runId") in runs}
    wanted = ("engine.checkpointed",) if plan.turn.action == "regenerate" else ("workspace.committed", "engine.checkpointed")
    return [f"no {t} in the engine run's journal" for t in wanted if t not in types]


def _wire(plan: RowPlan, obs: Observation) -> list[str]:
    if not obs.wires:
        return ["the scripted model served no call for the turn"]
    if plan.turn.wire not in obs.wires:
        return [f"the turn's model calls went over {sorted(obs.wires)}, not {plan.turn.wire}"]
    return []


def _compiled(_plan: RowPlan, obs: Observation) -> list[str]:
    """Compiler on: every model call journals the digest of the request it sent (the recompile from the journal that
    04 §4.4 names needs the worker's own host bands; QA-6 D-3)."""
    requests = obs.of_type("model.request")
    if not requests:
        return ["no model.request in the run's journal"]
    return [f"model.request #{e.get('seq')} has no contextDigest" for e in requests
            if not str((e.get("data") or {}).get("contextDigest", "")).startswith("sha256:")]


def _admin(_plan: RowPlan, obs: Observation) -> list[str]:
    problems = [] if "effectivePolicy" in obs.admin_view else [f"the admin's run view has no effectivePolicy ({obs.admin_view})"]
    if obs.audit and not obs.audit.get("ok"):
        problems.append(f"the org's audit chain does not verify: {obs.audit.get('problems')}")
    return problems


def _operator(_plan: RowPlan, obs: Observation, canaries: Iterable[str] = ()) -> list[str]:
    problems = [] if obs.health == 200 else [f"/api/v1/health answered {obs.health}"]
    return problems + [p.replace("what the user received", "Node's log") for p in canary_problems([obs.log_text], canaries)]


def _remembered(_plan: RowPlan, obs: Observation) -> list[str]:
    """The turn's proposal is the person's to see, whatever ran the turn: the loop or an engine (QA6-F7)."""
    if not any("bullet" in m for m in obs.memories):
        return [f"this turn's proposal is not in the person's memories ({len(obs.memories)} of this conversation listed)"]
    return []


def _role_child(obs: Observation, child: Json) -> list[str]:
    """A child whose role set its model: named on ``child.spawned`` and pinned as its spec's one route."""
    role, named = obs.role_model or {}, child.get("model") or {}
    want = {"model": role.get("modelName"), "role": ROLE_KEY}
    problems = []
    if {k: named.get(k) for k in want} != want or not named.get("provider"):
        problems.append(f"child.spawned names {named or 'no model'}, not {want} with its provider")
    spec = pinned_specs(obs).get(str(child.get("childRunId")), {})
    policy = (spec.get("model") or {}).get("policy") or []
    if [r.get("model") for r in policy] != [role.get("modelName")]:
        problems.append(f"the child's spec routes to {policy}, not to {role.get('modelName')} alone")
    pinned = ((spec.get("extensions") or {}).get("x.pipeshub") or {}).get("model") or {}
    if pinned.get("modelKey") != role.get("modelKey"):
        problems.append("the child's spec does not name the role's configured model (x.pipeshub.model)")
    return problems


def _child_model(plan: RowPlan, obs: Observation) -> list[str]:
    """MA1 §3 beyond the oracle's answer: a role's model is the child's pinned route and the one its calls name; the
    coordinator's calls stay on the turn's model; a refused role spawns nothing; no role model, no ``model`` member."""
    expected, children = plan.row.expected.child, spawned(obs)
    if expected == "inherit":
        return [f"child.spawned names a model ({c['model']}) though the agent sets no role model"
                for c in children if "model" in c]
    if expected.startswith("refused:"):
        return [f"{len(children)} child run(s) spawned though the role's model is refused"] if children else []
    if not expected.startswith("role:"):
        return []
    if not children:
        return ["the turn spawned no child run"]
    problems = [p for child in children for p in _role_child(obs, child)]
    role = obs.role_model or {}
    target = (role.get("wire"), role.get("modelName"))
    if not any((c.get("wire"), c.get("model")) == target for c in obs.model_calls if c.get("kind") == "loop"):
        problems.append(f"the scripted model served no sub-agent call on {target}")
    strayed = sorted({str(c.get("wire")) for c in obs.model_calls if c.get("kind") == "delegate"} - {plan.turn.wire})
    if strayed:
        problems.append(f"the coordinator's calls went over {strayed}, not the turn's {plan.turn.wire}")
    return problems


# Deep mode's coordination tools: only the coordinator is offered them (ADR-RUN-04).
COORDINATION_TOOLS = frozenset({"create_plan", "critique_plan", "spawn_agent", "verify_result"})


def _sub_agent_tools(_plan: RowPlan, obs: Observation) -> list[str]:
    """QAFINAL-FU-PW113: a sub-agent cannot start sub-agents, so no request of one offers planning or spawning."""
    offered = sorted({t for c in obs.model_calls if c.get("subAgent") for t in c.get("tools") or ()} & COORDINATION_TOOLS)
    return [f"a sub-agent was offered {offered}"] if offered else []


def _durable(_p: RowPlan, o: Observation) -> bool:
    return o.route == "durable"


def _child_ran(p: RowPlan) -> bool:
    """Deep mode's tool calls are its sub-agent's: a refused spawn leaves none to assert."""
    return not p.row.expected.child.startswith("refused:")


CHECKS: tuple[Check, ...] = (
    Check("always: journal history", _durable, _history),
    Check("always: first frame conversation_created", lambda p, o: True, _first_frame),
    Check("always: RUN_FINISHED carries a result", lambda p, o: True, _finished_with_result),
    Check("always: the script ran to its answer", lambda p, o: True, _scripted_answer),
    Check("always: the row's wire served the turn", lambda p, o: p.agent.engine == "none", _wire),
    Check("route durable: spec pinned first", _durable, _spec_pinned_first),
    Check("route legacy: nothing admitted", lambda p, o: o.route == "legacy", _legacy_unadmitted),
    Check("card: rule named, decision chained", lambda p, o: bool(o.cards), _card_rule),
    Check("action regenerate: a durable run of its own", lambda p, o: p.turn.action == "regenerate" and o.route == "durable",
          _regenerated),
    Check("route durable: ids unique and in order, each card announced once", lambda p, o: o.route == "durable",
          _stream_ids),
    Check("route legacy: one record says why", lambda p, o: o.route == "legacy" and p.turn.trigger == "interactive",
          _legacy_recorded),
    Check("action reconnect: the client reattached", lambda p, o: p.turn.action == "reconnect" and o.route == "durable",
          _resumed),
    Check("compiler on: every request's digest journaled", lambda p, o: p.process.compiler and o.route == "durable"
          and p.agent.engine == "none", _compiled),
    Check("role admin: effective policy, audit chain", lambda p, o: p.role == "admin" and o.route == "durable", _admin),
    Check("tool_family memory: the proposal is in My memories", lambda p, o: p.agent.family == "memory"
          and o.route == "durable" and _child_ran(p), _remembered),
    Check("trigger schedule: one run per delivery", lambda p, o: p.turn.trigger != "interactive", _scheduled_once),
    Check("tool_family run_code: execution receipt", lambda p, o: p.agent.family == "run_code"
          and p.agent.engine == "none" and _child_ran(p), _code_receipt),
    Check("engine set: workspace committed, checkpointed", lambda p, o: p.agent.engine != "none" and o.route == "durable"
          and o.terminal == "finished", _engine_workspace),
    Check("child_model: the role's model is the child's, the turn's the coordinator's", _durable, _child_model),
    Check("route durable: a sub-agent is offered no coordination tools", _durable, _sub_agent_tools),
)


def problems(plan: RowPlan, obs: Observation, canaries: Iterable[str]) -> list[str]:
    sink = plan.agent.family in ("mcp", "toolset")
    out = oracle_problems(plan.row.expected, observed(obs, sink=sink))
    out += canary_problems([str(f) for f in obs.frames], canaries)
    for check in CHECKS:
        if check.applies(plan, obs):
            out += [f"[{check.name}] {p}" for p in check.verify(plan, obs)]
    if plan.role == "operator":
        out += [f"[role operator: health, no canary in logs] {p}" for p in _operator(plan, obs, canaries)]
    return out


def live_problems(plan: RowPlan, obs: Observation) -> list[str]:
    """A live row (live.py): what holds whatever the model chooses to do."""
    expected, got = plan.row.expected, observed(obs, sink=False)
    out = [p for p in oracle_problems(expected, got) if p.startswith(("oracle route", "oracle executor", "oracle reason"))]
    if obs.terminal != "finished":
        out.append(f"the live turn did not finish ({obs.terminal}, {obs.error_code})")
    if obs.route == "durable":
        out += _history(plan, obs)
    return out
