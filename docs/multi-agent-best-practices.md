# Multi-Agent System Design: Recommended Practices

A research-backed reference for designing, reviewing, and evolving PipesHub's
multi-agent runtime (`app/agent_loop_lib` + `app/agents/agent_loop`). Sources
are listed at the end; the recommendations below synthesize Anthropic's
multi-agent research system write-up, the Berkeley MAST failure-taxonomy paper
(NeurIPS 2025), LangChain's plan-and-execute / deep-agents work, OpenAI's
orchestration guidance, and production post-mortems from 2025–2026.

---

## 1. When to go multi-agent at all

- **Start with a single agent.** Add a second agent only when you can name the
  specific constraint it relieves: context-window overflow, serial latency on
  parallelizable work, or genuinely different tool/permission/policy scopes.
  Anthropic measured single agents at ~4x chat-token cost and multi-agent runs
  at ~15x — the task's value must justify that multiplier.
- **Decompose by context boundary, not by role type.** Splitting
  planner/implementer/tester/reviewer (role-centric) creates a "telephone
  game" where every handoff loses fidelity. Split where context can be
  genuinely isolated: independent research paths, clean data contracts,
  black-box verifiable outputs. A sub-agent that constantly needs what another
  sub-agent knows was a bad split.
- **Multi-agent shines on breadth-first, read-heavy work** (research across
  many sources) and is risky on write-heavy, tightly-coupled work (code edits
  in one repo) where parallel workers produce conflicting output.

## 2. The two structural primitives

Everything else (hierarchies, debate, evaluator-optimizer) composes from:

1. **Orchestrator–worker ("agents as tools")** — a lead agent decomposes,
   delegates bounded subtasks, and owns the final synthesis. Use when a single
   accountable agent should own the user-facing answer. This is PipesHub's
   model (`AgentTool`, `spawn_agent`).
2. **Handoff** — control (and conversation ownership) transfers to a
   specialist. Use when the specialist should own the next user-facing
   response. PipesHub currently does not need this for chat Q&A; if it appears,
   it should be an explicit primitive, not an emergent behavior.

Rules that consistently hold for orchestrator–worker:

- **Workers are isolated.** A worker gets a self-contained task description, a
  scoped tool set, an output contract, and a fresh context window. It does not
  see the orchestrator's conversation and does not know sibling workers exist.
  Inter-worker coordination couples what parallelism needs decoupled.
- **The orchestrator never touches domain tools directly.** It plans,
  delegates, and synthesizes. (PipesHub's deep mode already enforces this:
  the orchestrator's grant is only the four coordination tools.)
- **Delegation quality is the top failure source.** Each subtask needs: an
  objective, task boundaries ("do NOT also…"), an output format, and tool/source
  guidance. Vague delegations produce duplicated or gapped work — Anthropic's
  canonical example is three sub-agents all searching the same supply-chain
  query.
- **Effort scaling lives in the orchestrator prompt, not code.** e.g. "simple
  query → answer directly or 1 worker with 3–10 tool calls; complex query →
  3–5 workers with partitioned search spaces." Hard-coding worker counts
  removes the flexibility that justified an orchestrator.
- **Synthesis is a reasoning step, not concatenation.** Beyond ~4 substantive
  worker outputs the orchestrator's context routinely overflows; workers must
  return *condensed, structured* findings, with full detail parked in an
  artifact store (see §4) rather than inlined.

## 3. Deterministic vs. probabilistic control

The system's core idea — deterministic invariants via middleware/hooks,
judgment calls via tools — matches the community consensus exactly:

- **Anything that must ALWAYS happen is code, not a prompt.** Budget caps,
  spawn-depth limits, permission checks, citation collection, context
  compaction, "critique must run after plan", "verification must pass before
  finish" — these belong in hooks/middleware or in the loop strategy, never in
  "please remember to…" prompt text. A tool description is a suggestion; a
  PRE_TOOL_USE middleware is a guarantee.
- **Anything that requires judgment is a tool the model chooses.** Which
  domain to search, whether a second opinion is needed, when results are thin.
- **Loop strategies are the third leg**: they fix the *shape* of a run
  (plan → critique → dispatch → verify) deterministically while each phase's
  content stays probabilistic. This is exactly LangGraph's "workflow vs agent"
  distinction: predefine the control flow where you can, let the model decide
  only where you must.
- Corollary: **when a deterministic phase exists, don't also prompt-beg for
  it.** If the loop programmatically dispatches the plan, the prompt should not
  simultaneously instruct the model to dispatch it "in the same turn" — dual
  ownership of one responsibility is where drift and double-execution bugs
  live.

## 4. Context engineering across the agent boundary

- **Typed contracts at every handoff.** If one agent's output feeds another's
  input, it crosses a schema (Pydantic model / JSON schema), not free prose.
  "Hand-offs are data, not narration." Validate at the boundary; reject and
  re-ask on schema failure rather than letting a malformed payload propagate.
- **Externalize large state to an artifact store / filesystem.** The
  deep-agents pattern (Claude Code, Manus, LangChain `deepagents`): workers
  write full results to files/artifacts and return a *pointer + summary*.
  Dependent workers read the artifact directly. This simultaneously solves
  (a) orchestrator context overflow, (b) lossy truncation of inter-agent
  payloads, and (c) "the sandbox of step 2 can't see step 1's file".
- **Compaction is middleware, not hope.** Auto-summarize or clear old tool
  results when context crosses a threshold (deepagents: results >20k tokens go
  to files; conversations >~85% of window get summarized). Protect
  load-bearing messages (the plan, the critique verdict) from clearing.
- **A todo/plan artifact keeps long tasks on track.** A `write_todos`-style
  no-op tool works purely by keeping the plan in the model's recent attention.
  For multi-turn/multi-request continuity, the plan must be *persisted* and
  re-injected, not reconstructed from scratch.
- **Instruction proximity wins.** Rules about how to treat a payload belong
  adjacent to the payload (result notes appended to tool results), not
  hundreds of thousands of tokens away in the system prompt. (PipesHub's
  `result_note` mechanism is the right instinct and is empirically supported.)

## 5. Dependencies between sub-agents

- **Model dependencies as an explicit DAG** with stable task ids and
  `depends_on` edges (LLMCompiler pattern). The scheduler runs independent
  nodes concurrently and holds dependents until prerequisites finish. Never
  rely on "call the dependent one in a later turn" — that's ordering by
  accident.
- **Validate the DAG before launch** (unknown ids, duplicates, cycles,
  self-edges) and return *actionable* per-task errors the model can fix next
  turn. (PipesHub's `spawn_scheduler.validate_spawn_batch` does this well.)
- **Pass prerequisite results by reference, not by value, when large.**
  Inlining a truncated 24k-char blob into the dependent's goal both bloats the
  dependent's context and silently drops data. The artifact store (§4) is the
  fix: dependent gets the summary inline + the full artifact mounted/readable.
- **A failed prerequisite must fail its dependents fast and loudly**, with the
  reason, rather than running them against missing data.
- **Prefer merging over chaining when the "dependency" is trivial.** Two steps
  where step 2 just reformats step 1's output should be one step. Every hop
  loses fidelity (MAST FC2); the minimum-hop plan is usually the best plan.

## 6. Human-in-the-loop (ask-user) in a nested system

The consensus pattern (LangGraph `interrupt`, Claude Agent SDK, OpenAI SDK):

1. **Pausing is a runtime capability, not a tool result.** An ask-user event
   should *suspend* the run — checkpoint the full execution state (message
   history, pending tool call, plan, spawn results) — and *resume* from that
   checkpoint when the answer arrives, injecting the answer as the pending
   tool call's result. Ending the run and rebuilding from scratch on the next
   user message silently discards everything gathered before the question:
   this is MAST's #1 category (context/state loss) in production.
2. **Only the root agent talks to the user.** Sub-agents must not have the
   ask-user tool: a sub-agent that blocks mid-parallel-batch on user input
   stalls its siblings, confuses the UI (question cards appearing while the
   orchestrator keeps streaming), and violates the isolation contract. The
   correct sub-agent behavior on missing information is to **return a
   structured `needs_input` outcome** (what's missing, why, suggested
   options) — the *orchestrator* then decides whether to answer from its own
   context, re-delegate differently, or surface a question to the user through
   its own ask-user tool.
3. **Ask early, not mid-flight, when possible.** A pre-run clarification gate
   (cheap intent pass that detects fatal ambiguity before spawning anything)
   is far cheaper than interrupting a 10-worker run. Mid-run asks should be
   reserved for gates before irreversible/side-effecting actions.
4. **Idempotency on resume.** Whatever unit re-executes after resume must be
   safe to re-run (or the checkpoint must be taken after side effects).

## 7. Verification and termination

- **"Stop when done" is not a stop condition.** Every agent runs under a hard
  step/turn budget owned by code, plus a wall-clock ceiling owned by the
  service boundary. (~22% of MAST failures are premature termination or
  missing/wrong verification.)
- **Verify high-stakes outputs with a separate pass** whose context is only
  the draft + the evidence (not the whole conversation): a critic that
  *judges* without rewriting. Citations, numbers, and claims should be checked
  against retrieved data, not against the model's memory of it.
- **Make verification verdicts structured** (`passed: bool`, `issues: [...]`)
  so the loop can branch on them deterministically.
- **Degrade gracefully at budget exhaustion**: return the best partial answer
  flagged as degraded rather than an opaque failure — but *flag* it, in the
  payload, so downstream consumers and evals can tell.

## 8. Failure modes to design against (MAST taxonomy)

Empirically, ~42% of multi-agent failures are specification issues, ~37% are
inter-agent misalignment, ~21% are weak verification. Concretely:

| Failure mode | Design countermeasure |
|---|---|
| Vague/overlapping delegations | Objective + boundaries + output format per subtask; orchestrator prompt reviewed as the highest-leverage artifact |
| Duplicated sibling work | Partitioned search spaces in the delegation; workers don't share scope |
| Context loss across handoff | Typed contracts; artifact store; never rebuild state from prose |
| Information withholding | Output contract requires reporting ids/keys discovered (e.g. record IDs) |
| Reasoning–action mismatch | Structured plans dispatched programmatically, not re-typed by the LLM |
| Infinite/looping delegation | Spawn-depth caps, per-run budgets, cycle detection in the task DAG |
| Premature termination | Completion gates (artifact produced? verification passed?) as POST_MODEL middleware |
| Shallow verification | Dedicated verifier context; verify against evidence, not conversation |

## 9. Observability, evals, and cost

- **Instrument the seams.** Every spawn, handoff payload, truncation event,
  compaction, and verification verdict gets a trace span with correlation ids
  (run_id / team_id / task_id). Debugging multi-agent systems is debugging
  emergent interactions; without full traces you only see symptoms.
- **Stream sub-agent activity to the user.** Long-running opaque work erodes
  trust; surface which workers are running and what they found (Anthropic
  added exactly this after launch).
- **Turn production failures into regression evals.** Small eval sets (~20
  representative queries) catch most regressions; LLM-as-judge against a
  rubric (factual accuracy, citation accuracy, completeness, tool efficiency)
  scales grading. End-state evaluation beats step-by-step trajectory
  assertions for flexible strategies.
- **Tier models by role.** Orchestrator/planner on the strongest model;
  workers on cheaper, faster models; verifier can be small if its context is
  small. 40–60% cost reduction is typical with no quality loss.
- **Checkpoint long runs and resume on transient failure** rather than
  restarting from zero; let the model adapt to tool failures (surface the
  error to it) instead of hard-failing the run.

## 10. Composability checklist (SOLID applied to agents)

- One primitive for "run a child agent" (`run_child`) that every composition
  path (static agent-as-tool, dynamic spawn, best-of-n) goes through. ✔ in
  PipesHub today.
- Loop strategies own only *shape*; hooks own *invariants*; tools own
  *choices*; specs are pure data. New behavior = new strategy/middleware/tool,
  not edits to the Agent core (open/closed).
- Agent-facing catalogs (domains, modes) are declarative data; adding an entry
  must not require touching control flow.
- Every cross-component contract (plan, spawn args, worker result, verifier
  verdict, needs-input outcome) is a versioned, typed schema owned by the
  library, not an ad-hoc dict.
- The user-interaction capability (ask-user, streaming events) is a *root*
  concern injected at the top; child agents receive at most a way to signal,
  never a way to talk to the user directly.

---

## Sources

- Anthropic — *How we built our multi-agent research system* (2025)
- Anthropic — *Building effective agents* (2024)
- Cemri et al. — *Why Do Multi-Agent LLM Systems Fail?* (MAST, NeurIPS 2025,
  arXiv:2503.13657)
- LangChain — *Plan-and-Execute agents*; *Deep Agents* / `deepagents` harness
  (planning tool, filesystem, sub-agents, context middleware)
- Kim et al. — *LLMCompiler: An LLM Compiler for Parallel Function Calling*
  (task DAG with data dependencies)
- OpenAI — *Agents SDK: Orchestration and handoffs* guidance
- LangGraph — *Human-in-the-loop / interrupts / persistence* documentation
- Production surveys: agentpatterns.ai orchestrator-worker pattern;
  HLD Handbook *Multi-Agent Orchestration*; Beam *6 Multi-Agent Orchestration
  Patterns for Production (2026)*
