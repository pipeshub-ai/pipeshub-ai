import type { NodeCapabilities } from '../contracts/generated/core/node-capabilities';

/**
 * What this Node accepts from a worker beyond every v2 Node (UX2), sent with
 * each claim: the mirror of a worker's advertisement. A worker newer than its
 * Node writes a newer shape only when the claim names it here, so a rolling
 * upgrade in either order never has Node refusing a worker's commit.
 *
 * `approval_reasons`: `interaction.explained` for UX1's flow rule and UX2's
 * `engine_writes`, and conversation grants. `approval_rule_layers`: that
 * event naming the layer that set the rule, and a layer's own approval rules
 * (`writes`, `effect_class`, `tool`; QA6-F6). `child_labels`: a child admission carrying `inheritedLabel`, pinned
 * as the child's `x.pipeshub.inheritedLabel`. `child_grants`: a child admission carrying `inheritedGrants`, the
 * conversation grants its parent held, pinned as the child's `x.pipeshub.inheritedGrants` (FLOWASK). `command_activity`:
 * `x.pipeshub.command.finished`, a sandbox command's record for the activity
 * panel (UX5). `followups`: `POST …/internal/runs/{runId}/followups`, the
 * suggested next questions of a completed run, sent after its terminal
 * commit (UX5). `task_asks_first`: `task.created`/`task.updated` carrying
 * `asksFirst`, a plan step the run's rules will ask about (UXR-B1).
 * `intent_allowed_by`: `tool.intent` carrying `allowedBy`, the rule that let
 * the call run without asking a person (CODEAUTO). `context_compacted`:
 * `x.pipeshub.context.compacted`, the run's record of the person's `/compact`
 * (SLASH2 C2). `approval_rule_grants`: `interaction.explained` carrying
 * `ruleGrant`, main's "Don't ask again" for the person's own tool (BUGS-B).
 * `context_compacted_auto`: that event with `trigger` `auto`, a compaction
 * nobody asked for: the loop's ladder or an engine's own (LC2FIX-3).
 * `skill_labels`: `x.pipeshub.citations.recorded` carrying `skillLabels`, the
 * labels of agent-written skill content a run read or an engine mounted
 * (FOLLOWUPS1-1). `native_events`: `x.pipeshub.native.unknown` and
 * `x.pipeshub.native.transcript`, what an engine sent natively, sealed and
 * never shown (C2.39, C2.40). An edition's features follow these: those of the
 * run fields it records (`RecordedRunField`). `approval_edits`: `interaction.explained`
 * offering `editable`, and an approval journaling the arguments the person
 * edited, which the worker runs in place of the call's own (AP-06).
 * `external_policy_asks`: `interaction.explained` with rule `external_policy`,
 * the org's policy webhook asking for a person (G-40, WP-39); never a
 * conversation grant, never a batch.
 */
export const APPROVAL_REASONS_FEATURE = 'approval_reasons';
export const APPROVAL_RULE_LAYERS_FEATURE = 'approval_rule_layers';
export const CHILD_LABELS_FEATURE = 'child_labels';
export const CHILD_GRANTS_FEATURE = 'child_grants';
export const COMMAND_ACTIVITY_FEATURE = 'command_activity';
export const FOLLOWUPS_FEATURE = 'followups';
export const TASK_ASKS_FIRST_FEATURE = 'task_asks_first';
export const INTENT_ALLOWED_BY_FEATURE = 'intent_allowed_by';
export const CONTEXT_COMPACTED_FEATURE = 'context_compacted';
export const APPROVAL_RULE_GRANTS_FEATURE = 'approval_rule_grants';
export const CONTEXT_COMPACTED_AUTO_FEATURE = 'context_compacted_auto';
export const SKILL_LABELS_FEATURE = 'skill_labels';
export const NATIVE_EVENTS_FEATURE = 'native_events';
export const APPROVAL_EDITS_FEATURE = 'approval_edits';
export const EXTERNAL_POLICY_ASKS_FEATURE = 'external_policy_asks';
/** `interaction.explained` carrying `fieldOrder`: the tool's argument order for the edit card (WP-56). */
export const APPROVAL_EDIT_ORDER_FEATURE = 'approval_edit_order';
/** `interaction.explained` carrying `argsDigest`: what a person's decline covers for their runs (ADR-GRANT-01 D9). */
export const APPROVAL_ARGS_DIGEST_FEATURE = 'approval_args_digest';
/**
 * `interaction.requested` with kind `connect`: a run waiting for its owner to
 * connect an item a call needs (G-12). Offered once the chat's connect card
 * could answer it: a run paused with no card would only wait out its deadline.
 */
export const CONNECT_PAUSES_FEATURE = 'connect_pauses';
/**
 * G-35: `x.pipeshub.knowledge.exported`, a run's knowledge export to its
 * sandbox. A worker offers the export tool only under a Node that names it.
 */
export const KNOWLEDGE_EXPORTS_FEATURE = 'knowledge_exports';

/**
 * G-45: claim-next naming `aboveClass` (a full worker making room by preemption) and `run.yielded` with reason
 * `preempted`. A worker preempts only under a Node that names it.
 */
export const PRIORITY_PREEMPTION_FEATURE = 'priority_preemption';

/**
 * WP-63 part A: `x.pipeshub.repository.fetched`, a public repository the
 * worker fetched for an engine run (its activity line and audit record).
 */
export const REPOSITORY_FETCHES_FEATURE = 'repository_fetches';
/**
 * WP-63: `interaction.explained` with rule `site_upload`, the upload card for
 * a read-write website held to read-only after the run read private data.
 */
export const SITE_UPLOAD_ASKS_FEATURE = 'site_upload_asks';
/**
 * ADR-RUN-01 D1: `x.pipeshub.outcome.recorded` and `.settled`, a run's outcome
 * ledger facts and its one settled outcome.
 */
export const OUTCOME_LEDGER_FEATURE = 'outcome_ledger';
/**
 * WP-89: `x.pipeshub.outcome.settled` carrying `presented`. The chat words the outcome in the reader's language
 * (`projection/outcome-view.ts`), so the worker sends only the model's prose as the answer.
 */
export const OUTCOME_WORDS_FEATURE = 'outcome_words';
/**
 * WP-96 (ADR-RUN-01 D3): `x.pipeshub.provenance.recorded`, each tool result's
 * provenance (who may read it, who wrote it, its sources), the session's
 * provenance ledger. Audited by its readers, integrity and source count.
 */
export const PROVENANCE_RECORDS_FEATURE = 'provenance_records';
/**
 * BGUI-10: a child admission carrying `task`, the job's task in its parts (`job-brief.schema.json` JobTask), kept
 * on the child's sealed input for its brief.
 */
export const CHILD_TASKS_FEATURE = 'child_tasks';
/**
 * R31HOT2 (ADR-OWD-07 D-LAZY-5): a child admission carrying `mcpPins`, the first-use pins of the MCP servers its parent
 * has listed, pinned narrowed as the child's `x.pipeshub.mcp[].pins`.
 */
export const CHILD_MCP_PINS_FEATURE = 'child_mcp_pins';
/**
 * R33JGAPS-FU-6: `x.pipeshub.answer.finalized` `limits` naming a file of the turn the model never read
 * (`attachment.not_accessible`, `attachment.unreadable`, with `file`). Without it the worker leads the answer with the
 * English line only, never an item this Node's schema would refuse.
 */
export const ATTACHMENT_LIMITS_FEATURE = 'attachment_limits';

/**
 * R41-CLONE: `x.pipeshub.answer.finalized` and `x.pipeshub.tool_result.presented` `limits` naming a repository the
 * task named and the run never cloned (`repository.not_cloned`). Without it the worker says so in words only.
 */
export const REPOSITORY_NOT_CLONED_FEATURE = 'repository_not_cloned';

/**
 * R36COVER-FU-3: `turn.started` carrying `skippedInputs`, the input artifacts a job refused on its own (over the
 * per-input cap, or gone since they were handed over) and went on without. Without it the worker fails the job on
 * such an input, as before.
 */
export const SKIPPED_INPUTS_FEATURE = 'skipped_inputs';

/**
 * R46FIX-2: `limits` naming a file handed to a job over the per-input cap as `attachment.too_large`. Without it the
 * worker sends such a file as `attachment.unreadable`.
 */
export const ATTACHMENT_TOO_LARGE_FEATURE = 'attachment_too_large';

/**
 * R46FIX-FU-2: an attachment-kind `limits` item with `count`, standing for several files handed to a job that it may
 * not name. Without it the worker sends that item without a count, read as one file.
 */
export const ATTACHMENT_COUNT_FEATURE = 'attachment_count';

export const NODE_CAPABILITIES: Readonly<NodeCapabilities> = Object.freeze({
  harnessMajor: 2 as const,
  features: [
    APPROVAL_REASONS_FEATURE,
    APPROVAL_RULE_LAYERS_FEATURE,
    CHILD_LABELS_FEATURE,
    CHILD_GRANTS_FEATURE,
    COMMAND_ACTIVITY_FEATURE,
    FOLLOWUPS_FEATURE,
    TASK_ASKS_FIRST_FEATURE,
    INTENT_ALLOWED_BY_FEATURE,
    CONTEXT_COMPACTED_FEATURE,
    APPROVAL_RULE_GRANTS_FEATURE,
    CONTEXT_COMPACTED_AUTO_FEATURE,
    SKILL_LABELS_FEATURE,
    NATIVE_EVENTS_FEATURE,
    APPROVAL_EDITS_FEATURE,
    EXTERNAL_POLICY_ASKS_FEATURE,
    KNOWLEDGE_EXPORTS_FEATURE,
    PRIORITY_PREEMPTION_FEATURE,
    REPOSITORY_FETCHES_FEATURE,
    CONNECT_PAUSES_FEATURE,
    SITE_UPLOAD_ASKS_FEATURE,
    APPROVAL_EDIT_ORDER_FEATURE,
    APPROVAL_ARGS_DIGEST_FEATURE,
    OUTCOME_LEDGER_FEATURE,
    PROVENANCE_RECORDS_FEATURE,
    OUTCOME_WORDS_FEATURE,
    CHILD_TASKS_FEATURE,
    CHILD_MCP_PINS_FEATURE,
    ATTACHMENT_LIMITS_FEATURE,
    REPOSITORY_NOT_CLONED_FEATURE,
    SKIPPED_INPUTS_FEATURE,
    ATTACHMENT_TOO_LARGE_FEATURE,
    ATTACHMENT_COUNT_FEATURE,
  ],
});

/** What this Node names on its claims: `NODE_CAPABILITIES` and what an edition adds. */
export function advertisedNodeCapabilities(
  bound: {
    /**
     * An edition's features: those of the run fields it records (`recordedRunFeatures`) and those its bound services
     * serve (`ClaimFeatureSource`); absent: none.
     */
    edition?: readonly string[];
  } = {},
): Readonly<NodeCapabilities> {
  const own = NODE_CAPABILITIES.features;
  return Object.freeze({
    harnessMajor: 2 as const,
    features: [
      ...own,
      ...(bound.edition ?? []).filter((feature) => !own.includes(feature)),
    ],
  });
}
