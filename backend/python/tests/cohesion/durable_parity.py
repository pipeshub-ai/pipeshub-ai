"""DUR1's parity inventory: every capability of today's agent loop, and what the durable loop does with it.

``ported``: the durable path has it (``where``). ``not_ported``: it does not, and ``reason`` says why.
``different``: it does the job another way, and ``reason`` says how and why. C-23
(``test_c23_durable_parity_inventory.py``) fails when legacy grows a hook, a registration, a mode or a tool this
table does not name, when a row the durable registry should carry is missing from it, or when a row that is not
``ported`` has no reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tests.edition_overlay import overlay

Status = Literal["ported", "not_ported", "different"]
Kind = Literal["hook", "registration", "mode", "chat_mode", "capability"]


@dataclass(frozen=True)
class Capability:
    key: str
    kind: Kind
    status: Status
    where: str = ""
    reason: str = ""


def _hook(key: str, where: str = "build_hooks (PipesHubAgentFactory._build_hooks)") -> Capability:
    return Capability(key, "hook", "ported", where)


_NOT_PORTED_REF = "app/harness/assembly/hooks.py NOT_PORTED"
# A legacy POST_AGENT hook the durable kernel runs as a journaled post-run step (``PostRunStep``) of this key.
POST_RUN_WHERE = "post-run step "

INVENTORY: tuple[Capability, ...] = (
    # ---- hooks: the middleware legacy registers (`.use(...)` in the factory, sandbox bridge, skills and lazy-tools
    # wiring, and the kernel's turn guards)
    _hook("artifact_context_reminder"),
    _hook("ask_user_question_sse"),
    _hook("attachment_rehydration"),
    _hook("citation_tracking"),
    _hook("progressive_entity_tools"),
    _hook("completion_gate", "PipesHubResponseReview (POST_MODEL)"),
    _hook("conversation_enrichment"),
    _hook("error_tracker.post_tool_use", "ToolErrorTracker, streaks rebuilt from the journal on resume"),
    _hook("error_tracker.pre_tool_use", "ToolErrorTracker"),
    _hook("repeated_reads.post_tool_use", "RepeatedReadGuard, its memory replayed from the journal on resume"),
    _hook("repeated_reads.pre_tool_use", "RepeatedReadGuard"),
    # Main's tool rules (#4003, #4256): on a durable run ToolRulesPolicy asks through the kernel's approval
    # interaction, and the gate (KERNEL_ASKS set) only refuses blocked calls.
    _hook("approval_gate", "build_hooks, with ToolRulesPolicy in the run's approval policy (app/harness/tool_rules.py)"),
    # Inert on a durable run: it settles a parked legacy turn's toolApproval answer, which never reaches a
    # durable run (Node keeps those turns on today's loop); the kernel's interaction resumes durable approvals.
    _hook("resume_parked_call", "build_hooks (inert: a durable run carries no toolApproval)"),
    _hook("result_accumulation"),
    _hook("seed_visible_tools_from_history"),
    # Main #3841 and #4401: the code-graph toolset's unlock, the visible-tool list the prompt reads, and the run's
    # selected nodes named in the prompt, all registered in build_hooks.
    _hook("code_graph_unlock_after_tools"),
    _hook("code_graph_unlock_on_turn"),
    _hook("sync_visible_tools_for_prompt"),
    _hook("selected_nodes_prompt"),
    _hook("stash_tool_call_metadata"),
    _hook("shape_artifact_registration"),
    _hook("shape_image_injection", "the turn shaper (compiler off); a compiler step L0 (P4.6)"),
    _hook("shape_retrieved_image_injection", "the turn shaper; a compiler step L0.1 (P4.6)"),
    _hook("shape_budget_reduction", "the turn shaper (L1)"),
    Capability(
        "shape_artifact_compaction", "hook", "not_ported",
        reason=f"L2: {_NOT_PORTED_REF} 'shape_artifact_compaction (L2)' (journaled context.compacted instead, "
        "LONGTASK-F4); a compiler step (P4.6)",
    ),
    Capability(
        "shape_tool_result_clearing", "hook", "not_ported",
        reason=f"L3: {_NOT_PORTED_REF} 'shape_tool_result_clearing (L3)' (journaled context.compacted instead, "
        "LONGTASK-F4)",
    ),
    Capability(
        "shape_loop_compaction", "hook", "not_ported",
        reason=f"L4: {_NOT_PORTED_REF} 'shape_loop_compaction (L4)' (journaled context.compacted instead, QAFIX4 B2)",
    ),
    Capability(
        "shape_sliding_window", "hook", "not_ported",
        reason=f"L5: {_NOT_PORTED_REF} 'shape_sliding_window (L5)' (journaled context.compacted instead, QAFIX4 B2)",
    ),
    Capability(
        "report_context_reduction", "hook", "different",
        reason=f"{_NOT_PORTED_REF} 'report_context_reduction (PRE_MODEL)': the durable loop journals its reductions "
        "as x.pipeshub.context.compacted (QAFIX4 B2)",
    ),
    Capability(
        "shape_deterministic_compact", "hook", "not_ported",
        reason=f"L6: {_NOT_PORTED_REF} 'shape_deterministic_compact (L6)' (journaled context.compacted instead, "
        "QAFIX4 B2)",
    ),
    Capability(
        "shape_synthesis_guard", "hook", "not_ported",
        reason=f"L8: {_NOT_PORTED_REF} 'shape_synthesis_guard (L8)' (journaled context.compacted instead, "
        "LONGTASK-F4)",
    ),
    # Main #4535 folded L9's pairing repair into its invariant guard (pairing, current request, model window).
    _hook("shape_context_invariants", "the turn shaper (L9, the shared context pipeline)"),
    Capability(
        "shape_offload", "hook", "different",
        reason="only the library's context profile offloads large results to a store; the chat profile both loops "
        "build (factory._chat_context_profile) leaves it out, as today's loop does",
    ),
    # Main #4570: a question past the request's card limit, or one already asked, is refused; and a reply that asks
    # in its text while a card could be shown is re-prompted once. Both are registered in build_hooks, so the
    # durable run's questions follow the same rules as today's.
    _hook("ask_user_question_gate"),
    _hook("ask_in_text_recovery"),
    _hook("tool_preloading", "build_hooks: register_tool_preloading"),
    _hook("skill_preloading", "build_hooks: register_skill_preloading"),
    _hook("period_summary_hint", "PipesHubTurnShaper._open: period_summary_section (main #4296)"),
    _hook("warn_before_deadline", "build_hooks: install_turn_guards"),
    _hook("default_truncation_recovery", "build_hooks: install_turn_guards"),
    _hook("CODING_SANDBOX_PATH_PATTERN", "build_hooks: register_coding_sandbox_hooks (agent-code runs, P5.11)"),
    Capability(
        "shape_auto_compact", "hook", "not_ported",
        reason=f"L7a/L7b: {_NOT_PORTED_REF} 'shape_auto_compact (L7a/L7b)' (journaled context.compacted instead)",
    ),
    Capability(
        "retry_with_status", "hook", "not_ported",
        reason=f"registered but PRE_MODEL_CALL is not dispatched: {_NOT_PORTED_REF} 'retry_with_status'",
    ),
    Capability(
        "check_not_cancelled", "hook", "not_ported",
        reason=f"the kernel checks the journaled cancel at every boundary: {_NOT_PORTED_REF}",
    ),
    Capability(
        "require_budget", "hook", "different",
        reason="legacy installs it only with a budget; durable runs charge the budget ledger (P6.5) and the kernel "
        "enforces the pinned budgets",
    ),
    Capability(
        "coding_sandbox_result_propagation", "hook", "not_ported",
        reason=f"POST_AGENT scratch rescue: {_NOT_PORTED_REF} (P5.11)",
    ),
    Capability("SkillLearning", "hook", "ported", f"{POST_RUN_WHERE}skill_learning (DUR2)"),
    _hook(
        "retrieval_context_emission",
        "build_hooks when the run is admitted with includeRetrievalContext; PipesHubPresenter journals its frames "
        "as x.pipeshub.telemetry.recorded, which the projector shows (SYNC2-F1)",
    ),
    Capability(
        "legacy_flow_guard", "hook", "different",
        reason=f"today's loop refuses an external write after a read; the durable kernel asks the user through "
        f"FlowGuardedPolicy (UX1): {_NOT_PORTED_REF} 'legacy_flow_guard' (UX3)",
    ),
    Capability(
        "post_turn_mw", "hook", "not_ported",
        reason="a caller-supplied POST_TURN guard (install_turn_guards(post_turn=)); the factory passes none, and "
        "the durable kernel does not dispatch POST_TURN",
    ),
    Capability(
        "pre_model_mw", "hook", "different",
        reason="a caller-supplied PRE_MODEL guard; the factory passes none; durable PRE_MODEL is the turn shaper",
    ),
    Capability(
        "supervisor_confidence_gate", "hook", "not_ported",
        reason="installed only for a supervisor loop (planExecute's critique), which stays on legacy",
    ),
    # ---- registrations the legacy factory makes
    Capability("_build_hooks", "registration", "ported", "app/harness/assembly/hooks.py build_hooks"),
    Capability(
        "register_legacy_flow_guard", "registration", "different",
        reason="the flow guard's legacy form (UX3); the durable kernel applies FlowGuardedPolicy (UX1)",
    ),
    Capability("register_coding_sandbox_hooks", "registration", "ported", "build_hooks (agent-code, P5.11)"),
    Capability(
        "register_extra_agent_hooks", "registration", "ported",
        "build_hooks (PipesHubAgentFactory._build_hooks calls the edition's hooks on both loops)",
    ),
    Capability("register_coding_sandbox_tools", "registration", "ported", "SandboxSource (agent-code, P5.11)"),
    Capability("register_skill_tools", "registration", "ported", "SkillsSource (pinned skills)"),
    Capability("register_skill_preloading", "registration", "ported", "build_hooks"),
    Capability("register_tool_preloading", "registration", "ported", "build_hooks"),
    Capability("register_lazy_tool_meta_tools", "registration", "ported", "MetaToolSource; Disclosure (journaled)"),
    Capability(
        "register_fetch_full_record", "registration", "ported",
        "PipesHubTurnShaper (session_tool_grant: fetch_record from the chat's first request, R4)",
    ),
    Capability(
        "register_domain_agents", "registration", "ported",
        "build_composition (react): delegates, domain lazy disclosure and shared skill tools (DUR1-P2)",
    ),
    Capability("_register_final_answer_if_enabled", "registration", "ported", "FinalAnswerSource"),
    Capability(
        "register_skill_learning", "registration", "ported",
        f"{POST_RUN_WHERE}skill_learning: app/harness/assembly/skill_learning.py, the legacy SkillLearning and its "
        "skill writer, at most once per run (DUR2)",
    ),
    Capability(
        "register_coordination_tools", "registration", "different",
        reason="deep mode: coordination tools on the durable run, each sub-agent a durable child run (K-L)",
    ),
    Capability(
        "install_phase_gate", "registration", "different",
        reason="deep mode's phase rules are decided by DeepDelegation (K-L)",
    ),
    Capability(
        "register_planning_tools", "registration", "not_ported",
        reason="planExecute stays on legacy: its phase loop has no durable director (rollout.ts, K-L)",
    ),
    # ---- agent chat modes (MODE_CATALOG names and aliases, and `auto`)
    Capability("auto", "mode", "ported", "GoalStep (the intent call picks the tier; planExecute downgraded to react)"),
    Capability("quick", "mode", "ported", "GoalStep, react loop"),
    Capability("react", "mode", "ported", "GoalStep, CompositionStep"),
    Capability("deep", "mode", "different", reason="sub-agents are durable child runs, not in-process (K-L)"),
    Capability(
        "planExecute", "mode", "not_ported",
        reason="no durable director for the plan/critique/execute phases; Node keeps it on legacy "
        "(chat_mode_not_supported)",
    ),
    Capability("verification", "mode", "not_ported", reason="planExecute's alias; as planExecute"),
    # ---- the built-in assistant's chat modes (`/chat/stream` policies and the old values it still accepts)
    Capability("internal_search", "chat_mode", "ported", "app/harness/assembly/chat.py"),
    Capability("web_search", "chat_mode", "ported", "app/harness/assembly/chat.py"),
    Capability(
        "agent", "chat_mode", "not_ported",
        reason="Universal Agent Mode: Node sends a mode naming `agent` to the agent route, which stays legacy "
        "(P1b)",
    ),
    *(
        Capability(
            legacy, "chat_mode", "not_ported",
            reason="a pre-migration chatMode value: legacy maps it to internal_search by its raw name, which the "
            "pin does not record, so Node keeps it on legacy (P1b)",
        )
        for legacy in ("analysis", "deep_research", "creative", "precise", "standard")
    ),
    # ---- cross-cutting capabilities (not enumerable from code; reviewed by DUR1, kept here so a change is a diff)
    Capability("tools.connector_toolsets", "capability", "ported", "ToolsetSource (grant pinned at admission)"),
    Capability("tools.knowledge_fetch_record_grant", "capability", "ported", "LATE_TOOLS"),
    Capability(
        "tools.mcp", "capability", "different",
        reason="the ENABLE_MCP gate moves to admission and a server that is not ready refuses the run, so the pin "
        "holds (P1a)",
    ),
    Capability(
        "tools.run_code_without_sandbox", "capability", "different",
        reason="legacy drops run_code and carries on when no sandbox; a durable run pinned agent-code refuses "
        "(IsolationUnavailable), never falls back to the host (X-3, P5.11)",
    ),
    Capability(
        "tools.sandbox_tool_order", "capability", "ported",
        "SandboxSource in registration order (DUR1-P1; was sorted)",
    ),
    Capability("tools.run_code_description", "capability", "ported", "_describe_run_code (P5.11 RC-F5)"),
    Capability(
        "tools.ask_user_question", "capability", "different",
        reason="the presenter journals the question card (x.pipeshub.question.asked) and the run ends needs_input",
    ),
    Capability("memory", "capability", "different", reason="durable only: governed memory (P7.3)"),
    Capability("steering", "capability", "different", reason="durable only: input.queued (P1b T2)"),
    Capability("attachments", "capability", "ported", "attachment hint and rehydration (PRE_TURN)"),
    Capability("history", "capability", "ported", "journal; session.imported (LegacyHistory, P1b T3)"),
    Capability("citations", "capability", "ported", "citation tracking + CitationJournal"),
    Capability("artifacts", "capability", "ported", "registration hook; presenter re-delivers from the journal"),
    Capability(
        "streaming", "capability", "different",
        reason="AG-UI frames are projected by Node from journaled x.pipeshub.* events (REPORT 8), not emitted by "
        "the loop",
    ),
    Capability(
        "cancellation", "capability", "different",
        reason="a journaled cancel checked by the kernel at every boundary (hooks.py NOT_PORTED)",
    ),
    Capability(
        "retry_status_frames", "capability", "different",
        reason="the durable model port classifies provider errors and the kernel backs off or yields (REPORT 6.4)",
    ),
    Capability("answer_finalization", "capability", "ported", "the presenter runs AnswerFinalizer"),
    Capability(
        "confidence", "capability", "ported",
        "agent runs report it; chat runs do not, as run_chat_stream (DUR1-P4)",
    ),
    Capability("user_context", "capability", "ported", "tool_environment / chat environment"),
    Capability("time_context", "capability", "ported", "the client's time, else the admission time (prompt_time)"),
    Capability("model_tier", "capability", "ported", "the same prompt builder over the model entry"),
    Capability(
        "transport.image_cap", "capability", "ported", "resolve_model: the provider's image policy (DUR1-P3)",
    ),
    Capability("transport.opik_main_call", "capability", "ported", "resolve_model traces the run's port (DUR1-P3)"),
    Capability(
        "transport.system_cache_blocks", "capability", "ported",
        "PipesHubTurnShaper.shape passes legacy's [stable, volatile] system blocks (DUR1-F5, fixed by P4.8); the "
        "Anthropic and Bedrock payloads carry the same cache breakpoint (DUR3-D2)",
    ),
    Capability(
        "record_id_shortening", "capability", "different",
        reason="an opt-in token experiment; the durable pin does not carry it, so a request asking for it stays on "
        "legacy (not in RUN_INPUT_FIELDS)",
    ),
    Capability(
        "project_chats", "capability", "not_ported",
        reason="project instructions are not pinned; the frontend and surfaces send project chats to legacy",
    ),
    Capability(
        "max_turns", "capability", "different", reason="the pinned budgets.maxTurns instead of the factory's 15",
    ),
    Capability(
        "entity_tools", "capability", "ported",
        "HarnessServices.entity_vector_store and ContainerServices.entity_vector_store (load_entity_vector_store, "
        "shared with the query service's routes) into the tool environment and the chat environment: "
        "knowledgegraph search_entities / find_records_by_entity are offered exactly when legacy offers them "
        "(DUR3-B2)",
    ),
    Capability(
        "plan_tool", "capability", "different",
        reason="UXR-B1, R49-PLAN: root quick, react and auto runs are offered write_todos, journaled as the run's plan "
        "(task.*) for the chat's plan view; today's loop never offered it. C-24 leaves the tool and its prompt line out "
        "of both sides (tests/harness_replay/wire.py); the assistant's search modes are unchanged",
    ),
    Capability(
        "max_turns_final_answer", "capability", "ported",
        "the durable driver's answer-only turn past budgets.maxTurns (driver._takes_final_answer_turn, slot "
        "turns.final_answer; DUR3-A4): legacy's FINAL_ANSWER_NOTE on the newest result's footer, only run-ending calls kept, the degraded "
        "tail when it answers nothing",
    ),
)

# An edition's own rows (``tests/edition_overlay.py``, ``durable_parity.json``): the hooks it adds through
# ``edition_services.register_extra_agent_hooks``, with where the durable path runs them or why it does not.
INVENTORY += tuple(Capability(**row) for row in overlay("durable_parity").get("rows") or ())

BY_KEY: dict[str, Capability] = {c.key: c for c in INVENTORY}

# DUR3 condition a: the only differences between the two loops the default may carry, each with its reason. A
# capability is either ported (C-24 holds it equal on the wire and in the outputs) or listed here.
ALLOWED_DIFFERENCES: tuple[Capability, ...] = tuple(c for c in INVENTORY if c.status != "ported")

# Hook events the durable kernel and runtime dispatch (`app/harness/assembly/loop.py`, `hooks.py` table).
DURABLE_DISPATCHED_EVENTS = frozenset(
    {"pre_agent", "pre_turn", "pre_model", "pre_tool_use", "post_tool_use", "post_model"}
)



def post_run_step(row: Capability) -> str | None:
    """The post-run step a ported row names, if it names one."""
    if row.status != "ported" or not row.where.startswith(POST_RUN_WHERE):
        return None
    return row.where.removeprefix(POST_RUN_WHERE).split(" ", 1)[0].rstrip(":")


__all__ = [
    "ALLOWED_DIFFERENCES",
    "BY_KEY",
    "DURABLE_DISPATCHED_EVENTS",
    "INVENTORY",
    "POST_RUN_WHERE",
    "Capability",
    "post_run_step",
]
