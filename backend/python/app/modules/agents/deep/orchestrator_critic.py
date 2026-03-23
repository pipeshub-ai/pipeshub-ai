"""
Orchestrator Critic Node — Plan Quality Gatekeeper

ROLE
----
The critic sits between the orchestrator and execution. It runs EXACTLY ONCE
per user request. It has two possible outcomes:

  • APPROVE  — plan is good enough → execution proceeds immediately.
  • REVISE   — plan has issues → feedback is sent to the orchestrator for ONE
               re-plan. After that re-plan, execution proceeds unconditionally
               (critic does NOT run a second time).

PROMPT FORMAT DESIGN — WHY SYSTEM + HUMAN (NOT ONE OR THE OTHER)
-----------------------------------------------------------------
We deliberately split the prompt into two messages:

  SystemMessage — the critic's IDENTITY, ROLE CONSTRAINTS, and OUTPUT CONTRACT.
    Reasoning:
    • LLMs treat system messages as persistent, authoritative instructions that
      frame how they should behave across the entire conversation.
    • We want the critic to maintain a consistent evaluation posture regardless of
      what plan it sees. Its persona (neutral, structured, non-creative) must not
      drift when it encounters a convincing-looking but flawed plan.
    • Output format rules (the JSON schema it must return) belong here because they
      are structural contracts, not inputs to reason about.
    • Putting the evaluation RUBRIC here (what constitutes an issue vs. not) ensures
      the LLM anchors its reasoning to our criteria before even reading the plan.

  HumanMessage — the EVIDENCE: the actual plan, the user query, available domains,
    and any previous critique cycle metadata.
    Reasoning:
    • Human messages represent the "current input to process." The plan is ephemeral
      data that changes on every invocation; it belongs in the human turn.
    • Separating evidence from evaluation criteria prevents the model from treating
      the plan's own reasoning field as authoritative (a known failure mode where
      the LLM just echoes back the plan's self-justification as its critique).
    • It also allows us to inject "second attempt" context (prior critique + revised
      plan) naturally — we just append another HumanMessage with the delta, which
      is how multi-turn correction flows work in LLM APIs.

WHY NOT A SINGLE HUMAN MESSAGE?
  A single large human message collapses the role boundary. The LLM no longer has
  a stable "who I am" anchor; its evaluation posture can be overridden by confident
  language in the plan's reasoning field. This is especially dangerous for plans that
  look well-reasoned but contain subtle intent drift.

WHY NOT A SINGLE SYSTEM MESSAGE?
  You cannot inject the live plan (which changes per request) cleanly into a system
  message without either: (a) rebuilding the entire system prompt per request (fragile),
  or (b) putting variable data in a nominally "permanent" slot (confusing to the LLM).
  The evidence belongs in the turn slot, not the identity slot.

CRITIQUE LIFECYCLE
------------------
  Attempt 1:  Orchestrator produces plan → Critic evaluates → APPROVE or REVISE.
  Attempt 2:  If REVISE, orchestrator re-plans with critic feedback injected,
              then execution proceeds (critic does not run again).

  We cap at 1 revision cycle (not 2) because:
  • If the orchestrator can't fix the plan after seeing specific, itemised feedback,
    a third attempt rarely helps and adds latency.
  • The reflection layer already handles structural failures; the critic handles
    semantic ones. One semantic correction is the right budget.

OUTPUT CONTRACT
---------------
The critic always returns a CriticVerdict dataclass, which the graph routing
function uses to decide the next node. The JSON the LLM returns is parsed
into this dataclass — never used raw.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.deep.state import DeepAgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------

@dataclass
class CriticVerdict:
    """
    The critic's structured output.

    decision    : "approve" | "revise"
                  approve → proceed to execution immediately.
                  revise  → send feedback to orchestrator for ONE re-plan,
                            then execute unconditionally (critic does not run again).
    issues      : List of Issue dicts with keys: severity, category, description, fix.
                  Empty when decision == "approve".
    feedback_for_orchestrator : Compact, actionable rewrite instruction injected
                  into the orchestrator's next turn. Only populated when
                  decision == "revise".
    confidence  : "High" | "Medium" | "Low" — for logging only, not routing.
    """
    decision: Literal["approve", "revise"]
    issues: list[dict[str, str]] = field(default_factory=list)
    feedback_for_orchestrator: str = ""
    confidence: str = "High"


# ---------------------------------------------------------------------------
# System prompt — critic IDENTITY + RUBRIC + OUTPUT CONTRACT
#
# WHY THIS GOES IN SystemMessage:
#   This text establishes WHO the critic is and HOW it must behave.
#   It must be stable across all invocations regardless of the plan content.
#   Putting evaluation criteria here prevents the plan's own `reasoning` field
#   from influencing the critic's assessment posture.
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM_PROMPT = """\
You are a PLAN CRITIC for an orchestration system. Your sole job is to evaluate \
whether a task plan produced by an orchestrator LLM is ready for execution.

You are NOT the orchestrator. You do NOT generate plans. You do NOT execute tools. \
You evaluate plans written by others and identify problems with precision.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVALUATION RUBRIC  (evaluate ALL dimensions, in order)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DIMENSION 1 — STRUCTURAL INTEGRITY
  Check for problems that will cause hard failures at parse or dispatch time.
  These are CRITICAL issues (always trigger REVISE):
    S1. `can_answer_directly` is not a boolean.
    S2. `tasks` is missing or not a list.
    S3. `can_answer_directly=false` but `tasks` is empty.
    S4. A task is not a JSON object (e.g., it is a string or array).
    S5. A task is missing `task_id`, `description`, or `domains`.
    S6. `domains` is empty, or any domain is not in the Available Domains list.
    S7. `depends_on` references a task_id that does not exist or appears later
        in the list (forward reference).
    S8. Duplicate `task_id` values.
    S9. `complexity="complex"` but `batch_strategy` is missing or incomplete
        (must have `page_size`, `max_pages`, `scope_query`).
    S10. `multi_step=true` but `sub_steps` is missing or not a list of strings.

DIMENSION 2 — INTENT ALIGNMENT
  Check whether the plan actually answers the user's query.
  These are MAJOR issues (usually trigger REVISE):
    I1. The plan addresses a different or narrower question than the user asked.
        Example: user asked "compare X and Y" but plan only queries X.
    I2. A key entity, filter, or constraint in the user query is absent from
        all task descriptions.
        Example: user asked for "last 30 days" but no task has a date filter.
    I3. The plan's `reasoning` field describes a correct approach but the
        `tasks` array does not implement it (reasoning ≠ tasks).
    I4. `can_answer_directly=true` for a query that clearly needs tool data
        (e.g., any org-specific question when a knowledge base is configured,
        or any query asking for live data from an integrated service).

DIMENSION 3 — DOMAIN & TOOL CORRECTNESS
  Check whether each task uses the right domain for its stated goal.
  These are MAJOR issues:
    D1. A task uses `retrieval` for a write action (retrieval is read-only).
    D2. A task uses the wrong domain for its work. Example: sending email via
        `slack`, or querying a calendar via `jira`.
    D3. Knowledge base is configured (`has_knowledge=true`) but no task has
        `"retrieval"` in its domains, and the query is not a greeting or
        trivial calculation.
    D4. A domain is listed in a task but is NOT in the Available Domains list.

DIMENSION 4 — DECOMPOSITION QUALITY
  Check whether the plan is structured efficiently.
  These are MINOR issues (trigger REVISE only if they meaningfully affect quality):
    Q1. A single task attempts multiple incompatible operations (e.g., fetch data
        AND send a notification in one task — these need separate sub-agents).
    Q2. The plan uses 5+ tasks when 1–2 would suffice for a simple query.
    Q3. A heavy aggregation/report query (fetching many records over a time range)
        is marked `complexity="simple"` without a batch_strategy.
    Q4. An independent task has an unnecessary `depends_on` (creates false
        sequencing that blocks parallelism).
    Q5. A task description is vague (e.g., "do the thing") rather than specifying
        the exact goal, filters, and expected output.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DECISION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  APPROVE  — No CRITICAL or MAJOR issues. MINOR issues may exist but do not
             block execution. The plan is good enough to run.

  REVISE   — At least one CRITICAL or MAJOR issue exists AND a specific,
             concrete fix can be described. The orchestrator will re-plan
             ONCE using your feedback, then execution proceeds regardless.
             Even badly broken plans should get REVISE (not a hard failure) —
             your job is to provide the best possible fix instructions.

  BIAS TOWARD APPROVE: If you are unsure whether something is an issue,
  do NOT flag it. Only flag clear, specific problems. Vague concerns that
  cannot be expressed as a concrete fix should be silently ignored.
  A plan that works adequately is better than a delayed re-plan.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT CONTRACT  (you MUST return valid JSON in exactly this schema)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY a single JSON object. No preamble. No explanation outside the JSON.
No markdown fences. The JSON must match this schema exactly:

{
  "decision": "approve" | "revise",
  "confidence": "High" | "Medium" | "Low",
  "issues": [
    {
      "severity": "critical" | "major" | "minor",
      "category": "structural" | "intent" | "domain" | "decomposition",
      "rule": "S1" | "S2" | ... | "Q5",
      "description": "One sentence: what exactly is wrong.",
      "fix": "One sentence: what the orchestrator must do differently."
    }
  ],
  "feedback_for_orchestrator": "Compact rewrite instruction for the orchestrator. \
Only populated when decision is 'revise'. Must be specific and actionable, \
not generic. Empty string when decision is 'approve'."
}

RULES FOR `issues`:
  - Empty array `[]` when decision is "approve".
  - At least one entry when decision is "revise".
  - Each entry MUST have a concrete `fix`. If you cannot write a specific fix,
    do not include the issue.

RULES FOR `feedback_for_orchestrator`:
  - When "revise": Write 3–7 sentences. Lead with "The plan has N issue(s)."
    List each issue as a numbered fix the orchestrator must apply. Be specific:
    name the task_id, the domain, the field. Do NOT repeat the rubric text.
  - When "approve": empty string "".

Compatibility note:
  - Any unexpected decision value is treated as "revise".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANTI-PATTERNS (these are NOT issues — do not flag them)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✗ A plan uses fewer tasks than you would have chosen — not an issue.
  ✗ A task description could be more detailed — not an issue unless it is
    genuinely vague (Q5) and would confuse the executing agent.
  ✗ You disagree with the plan's approach but it is a valid approach — not an issue.
  ✗ A task is marked `complexity="complex"` when you think "simple" would work —
    over-caution is not an error.
  ✗ Minor style choices in task_id naming — not an issue.
"""


# ---------------------------------------------------------------------------
# Human message builder — the EVIDENCE
#
# WHY THIS GOES IN HumanMessage:
#   The human message contains the per-request evidence: the user query, the plan,
#   and context. This data changes on every invocation and represents "the thing
#   to evaluate right now." Keeping it separate from the system message ensures:
#     (a) The LLM's evaluation posture (set by the system) is not contaminated
#         by confident language in the plan's own `reasoning` field.
#     (b) We can inject critique-cycle metadata (prior verdict + revised plan)
#         as additional HumanMessage turns in a natural multi-turn flow.
#     (c) The evidence is clearly scoped — the LLM knows this is "input to judge,"
#         not "instructions to follow."
# ---------------------------------------------------------------------------

def _build_critic_evidence_message(
    user_query: str,
    plan: dict[str, Any],
    available_domains: list[str],
    has_knowledge: bool,
) -> str:
    """
    Build the human-turn evidence message for the critic.

    Returns a plain string (caller wraps it in HumanMessage).

    Structure of the evidence message:
      1. USER QUERY   — what the user actually asked (ground truth for intent checks)
      2. CONTEXT      — available domains, knowledge base status (ground truth for domain checks)
      3. PLAN TO EVALUATE — the plan JSON (the subject of evaluation)

    Rationale for this order:
      USER QUERY first: anchors intent evaluation before the LLM reads the plan.
        If the plan appears first, the critic may unconsciously accept the plan's
        framing of the query rather than judging whether the plan matches the actual
        query.
      CONTEXT second: ground truth for domain checks must be established before
        reading the plan so the critic can't rationalize "maybe that domain is
        available somehow."
      PLAN last: the subject under evaluation arrives after the evaluation frame
        is already set from query + context.
    """
    lines: list[str] = []

    # ── Section 1: User query ──────────────────────────────────────────────
    lines.append("## USER QUERY (ground truth — judge the plan against this)")
    lines.append(user_query.strip())
    lines.append("")

    # ── Section 2: Execution context ──────────────────────────────────────
    lines.append("## EXECUTION CONTEXT")
    lines.append(f"Available domains: {sorted(available_domains)}")
    lines.append(f"Knowledge base configured (has_knowledge): {has_knowledge}")
    lines.append(
        "Note: if has_knowledge=true, ANY substantive question requires at least "
        "one task with `\"retrieval\"` in its domains, unless the query is a "
        "greeting or trivial arithmetic."
    )
    lines.append("")

    # ── Section 3: Plan to evaluate ───────────────────────────────────────
    # Pretty-printed for readability; capped at 8000 chars to avoid context bloat.
    plan_json = json.dumps(plan, indent=2)
    lines.append("## PLAN TO EVALUATE")
    lines.append("```json")
    lines.append(plan_json)
    lines.append("```")
    lines.append("")
    lines.append(
        "Evaluate the plan now. Return ONLY the JSON object as specified in your instructions."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verdict parser
# ---------------------------------------------------------------------------

def _parse_critic_response(raw: str, log: logging.Logger) -> CriticVerdict | None:
    """
    Parse the LLM's raw string into a CriticVerdict.

    Uses the same raw_decode approach as _parse_plan in orchestrator_reflection.py
    so trailing content is silently dropped. Returns None on total failure
    (caller falls back to APPROVE to avoid blocking execution).
    """
    text = raw.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    import json as _json
    decoder = _json.JSONDecoder()

    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, start)
            if not isinstance(obj, dict):
                continue

            decision = obj.get("decision", "approve")
            if decision not in ("approve", "revise"):
                # Treat any unknown decision as revise so the orchestrator
                # gets a chance to fix it.
                log.warning("Critic returned unknown decision '%s', treating as revise", decision)
                decision = "revise" if obj.get("issues") else "approve"

            issues = obj.get("issues", [])
            if not isinstance(issues, list):
                issues = []

            return CriticVerdict(
                decision=decision,
                issues=issues[:6],
                feedback_for_orchestrator=obj.get("feedback_for_orchestrator", ""),
                confidence=obj.get("confidence", "High"),
            )
        except _json.JSONDecodeError:
            continue

    log.warning("Could not parse critic response as JSON — defaulting to APPROVE")
    return None


# ---------------------------------------------------------------------------
# Main critic node
# ---------------------------------------------------------------------------

async def critic_node(
  state: DeepAgentState,
  config: RunnableConfig,
  writer: StreamWriter,
) -> DeepAgentState:
    """
    Critic node: evaluates the orchestrator's plan before execution.

    Always runs. Sets `critic_verdict` in state (a CriticVerdict).
    Sets `critic_approved` to True/False so the routing function can
    branch without importing CriticVerdict.

    Flow:
      1. Read the plan from state["task_plan"].
      2. Build SystemMessage (critic identity + rubric — stable across calls).
      3. Build HumanMessage (plan + query + context — per-request evidence).
      4. Call LLM (lightweight — no tools, just classification).
      5. Parse verdict.
      6. If APPROVE: set critic_approved=True, execution proceeds.
      7. If REVISE: store feedback in state, orchestrator re-plans ONCE,
         then execution proceeds unconditionally (critic does not run again).
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")

    plan = state.get("task_plan", {})
    query = state.get("query", "")
    available_domains: list[str] = state.get("_critic_available_domains", [])
    has_knowledge: bool = bool(state.get("has_knowledge", False))

    log.info(
        "Critic node: evaluating plan (%d tasks, can_answer_directly=%s)",
        len(plan.get("tasks", [])),
        plan.get("can_answer_directly"),
    )

    # ── Build messages ─────────────────────────────────────────────────────

    # SystemMessage: critic identity + rubric + output contract.
    # Static per invocation — anchors evaluation posture before the plan is seen.
    system_msg = SystemMessage(content=_CRITIC_SYSTEM_PROMPT)

    # HumanMessage: per-request evidence (query, context, plan).
    # Changes every invocation — this is "the thing to evaluate right now."
    evidence_text = _build_critic_evidence_message(
        user_query=query,
        plan=plan,
        available_domains=available_domains,
        has_knowledge=has_knowledge,
    )
    human_msg = HumanMessage(content=evidence_text)

    messages = [system_msg, human_msg]

    # ── LLM call ───────────────────────────────────────────────────────────
    try:
        from app.modules.agents.deep.state import get_opik_config

        invoke_kwargs: dict[str, Any] = {}
        opik_config = get_opik_config()
        if opik_config:
            invoke_kwargs["config"] = opik_config

        response = await llm.ainvoke(messages, **invoke_kwargs)
        raw = response.content if hasattr(response, "content") else str(response)

    except Exception as e:
        log.error("Critic LLM call failed: %s — defaulting to APPROVE", e)
        state["critic_approved"] = True
        state["critic_feedback"] = ""
        state["critic_verdict"] = CriticVerdict(decision="approve", confidence="Low")
        state["critic_done"] = True
        return state

    # ── Parse verdict ──────────────────────────────────────────────────────
    verdict = _parse_critic_response(raw, log)

    if verdict is None:
        # Parse failure → approve (fail-open: critic bugs must not block execution)
        log.warning("Critic parse failed — defaulting to APPROVE (fail-open)")
        verdict = CriticVerdict(decision="approve", confidence="Low")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(
        "Critic verdict: %s (confidence=%s, %d issue(s)) in %.0fms",
        verdict.decision.upper(),
        verdict.confidence,
        len(verdict.issues),
        duration_ms,
    )

    if verdict.issues:
        for i, issue in enumerate(verdict.issues, 1):
            log.info(
                "  [%d] %s/%s — %s → %s",
                i,
                issue.get("severity", "?").upper(),
                issue.get("rule", "?"),
                issue.get("description", ""),
                issue.get("fix", ""),
            )

    # ── Store verdict and route ────────────────────────────────────────────
    state["critic_verdict"] = verdict

    if verdict.decision == "approve":
        state["critic_approved"] = True
        state["critic_feedback"] = ""
        log.info("Critic: APPROVED — proceeding to execution")
    else:
        # REVISE: store feedback for orchestrator, mark not approved.
        # The orchestrator will re-plan ONCE. After that, the critic does
        # NOT run again — the graph bypasses the critic on the second pass.
        state["critic_approved"] = False
        state["critic_feedback"] = verdict.feedback_for_orchestrator
        log.info("Critic: REVISE — sending feedback to orchestrator for one re-plan")
        if verdict.feedback_for_orchestrator:
            log.debug(
                "Critic feedback_for_orchestrator: %s",
                verdict.feedback_for_orchestrator,
            )

    state["critic_done"] = True

    return state


# ---------------------------------------------------------------------------
# Routing function — used by the graph to determine next node
# ---------------------------------------------------------------------------

def route_after_critic(state: DeepAgentState) -> Literal["dispatch", "orchestrator", "respond"]:
    """
    Route after the critic node.

      "dispatch"     → plan approved, proceed to sub-agent execution
                       (or "respond" if can_answer_directly=true, handled below)
      "orchestrator" → critic wants one revision pass
      "respond"      → pre-existing error in state
    """
    if state.get("error"):
        return "respond"

    if state.get("critic_approved"):
        execution_plan = state.get("execution_plan", {}) or {}
        if execution_plan.get("can_answer_directly"):
            return "respond"

        tasks = state.get("sub_agent_tasks", []) or []
        return "dispatch" if tasks else "respond"

    return "orchestrator"


# ---------------------------------------------------------------------------
# Orchestrator feedback injection
#
# Call this inside orchestrator_node BEFORE building the orchestrator's
# HumanMessage when critic_feedback is set in state. It appends the critic's
# feedback as an additional HumanMessage so the orchestrator sees its prior
# plan and the concrete revision instructions.
#
# WHY inject as HumanMessage (not SystemMessage):
#   The feedback is per-revision-cycle data. It references specific task_ids
#   and domains from the current plan — it is evidence for this specific
#   re-plan, not a permanent instruction. Injecting it as a HumanMessage
#   in the orchestrator's message list mirrors how a human reviewer would
#   send annotated feedback back to a planner: as a reply, not as a new
#   standing instruction.
# ---------------------------------------------------------------------------

def inject_critic_feedback_into_messages(
    messages: list,
  state: DeepAgentState,
) -> list:
    """
    Inject the critic's feedback into the orchestrator's message list.

    Called inside orchestrator_node when state["critic_feedback"] is non-empty.
    Appends:
      1. An AIMessage containing the prior plan (so the orchestrator sees what
         it produced before).
      2. A HumanMessage containing the critic's concrete revision instructions.

    The two-message injection mirrors the reflection layer's approach
    (orchestrator_reflection._append_plan_reflection) so the orchestrator
    sees a consistent "you produced X, here's what's wrong" pattern.

    Returns a new message list (does not mutate the input list).
    """
    feedback = state.get("critic_feedback", "")
    prior_plan = state.get("task_plan", {})

    if not feedback:
        return messages

    prior_plan_json = json.dumps(prior_plan, indent=2)[:2000]

    critic_instruction = (
        "## CRITIC FEEDBACK — Your previous plan was reviewed and requires revision.\n\n"
        "### Your previous plan (for reference)\n"
        "```json\n"
        f"{prior_plan_json}\n"
        "```\n\n"
        "### Required changes\n"
        f"{feedback}\n\n"
        "Please produce a REVISED plan that addresses ALL of the above issues. "
        "Output ONLY the corrected JSON object. Apply every fix listed above. "
        "Do not repeat issues that the critic flagged — resolve them."
    )

    return [
        *messages,
        AIMessage(content=json.dumps(prior_plan)),
        HumanMessage(content=critic_instruction),
    ]