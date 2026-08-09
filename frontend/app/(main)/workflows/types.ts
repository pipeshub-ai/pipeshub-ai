/**
 * Types for the Workflows dashboard -- mirrors the camelCase shape returned
 * by the Python `app/api/routes/workflows.py` REST routes (proxied 1:1 by
 * the Node.js `workflows.controller.ts`). "Workflow" is the only
 * user-facing noun; `workflowId` is the same id as the internal task
 * engine's `taskId`, but that vocabulary never surfaces here.
 */

export type WorkflowKind = 'agent_task' | 'code';

export type WorkflowStatus = 'active' | 'paused' | 'disabled' | 'draft' | 'completed';

export type TriggerKind = 'one_time' | 'cron' | 'interval' | 'event' | 'webhook';

export type MisfirePolicy = 'fire_now' | 'skip' | 'queue';

// Mirrors `RunStatus` (backend/python/app/services/tasks/domain/models.py)
// verbatim -- these are the literal wire values `status.value` serializes to.
export type WorkflowRunStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'abandoned'
  | 'awaiting_input'
  | 'dlq'
  | 'cancelled';

export interface WorkflowTriggerSummary {
  triggerId: string;
  kind: string;
  nextRunAt?: string | null;
  lastFireAt?: string | null;
  enabled: boolean;
}

export interface Workflow {
  workflowId: string;
  orgId: string;
  kind: WorkflowKind;
  name: string;
  description?: string | null;
  currentVersionId?: string | null;
  triggers: WorkflowTriggerSummary[];
  status: WorkflowStatus;
  requiredScopes: string[];
  executionKind: string;
  createdByUserId: string;
  createdAt: string;
  updatedAt: string;
  /** The conversation this workflow was created from, if any. */
  conversationId?: string | null;
  /** Tool names this workflow is allowed to use. */
  toolNames?: string[];
  /** Connector instance IDs this workflow depends on. */
  connectorIds?: string[];
  /** Knowledge-base collection IDs this workflow may read. */
  collectionIds?: string[];
  /** Max agent turns per run. */
  maxTurns?: number | null;
  /** Wall-clock timeout per run (seconds). */
  timeoutSeconds?: number | null;
}

export interface WorkflowTrigger {
  triggerId: string;
  workflowId: string;
  kind: TriggerKind;
  cronExpression?: string | null;
  intervalSeconds?: number | null;
  fireAt?: string | null;
  timezone?: string | null;
  eventFilter?: Record<string, unknown> | null;
  webhookId?: string | null;
  nextRunAt?: string | null;
  lastFireAt?: string | null;
  misfirePolicy: MisfirePolicy;
  maxRuns?: number | null;
  runCount: number;
  enabled: boolean;
}

export interface WorkflowRun {
  runId: string;
  workflowId: string;
  triggerId?: string | null;
  status: WorkflowRunStatus;
  attempt: number;
  completedSteps: string[];
  failedStepId?: string | null;
  skippedSteps: string[];
  outputSummary?: string | null;
  error?: string | null;
  usage?: Record<string, unknown> | null;
  scheduledFor?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  createdAt: string;
  agentRunId?: string | null;
  /** A rehearsal: the run executed reads but every write was simulated. */
  isDryRun?: boolean;
  /** "approval" or "wait_for_event" while `status` is `awaiting_input`. */
  suspensionKind?: string | null;
}

export interface WorkflowsListResponse {
  workflows: Workflow[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface WorkflowRunsListResponse {
  runs: WorkflowRun[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

/**
 * A trigger to attach. `webhookId` is deliberately absent: the server mints it,
 * so that a caller cannot bind a trigger to a webhook whose secret is someone
 * else's.
 */
export interface CreateTriggerRequest {
  kind: TriggerKind;
  cronExpression?: string | null;
  intervalSeconds?: number | null;
  fireAt?: string | null;
  timezone?: string;
  eventFilter?: Record<string, unknown> | null;
  maxRuns?: number | null;
}

export interface WorkflowTriggerListResponse {
  triggers: WorkflowTrigger[];
}

export interface WorkflowIRNode {
  node_id: string;
  kind: string;
  label: string;
  source_start?: number | null;
  source_end?: number | null;
  children: string[];
  metadata: Record<string, unknown>;
}

export interface WorkflowIREdge {
  from_node: string;
  to_node: string;
  label?: string | null;
}

export interface WorkflowIR {
  nodes: WorkflowIRNode[];
  edges: WorkflowIREdge[];
  entry_node_id?: string | null;
}

/**
 * One step of a run. The backend normalises the code workflow's replay journal
 * and the agent task's timeline into this single shape.
 */
export interface WorkflowTraceEntry {
  seq: number;
  /** Journal entry kind (`tool`, `agent`, `state`, ...) or agent event type. */
  kind: string;
  label: string;
  outcome: string;
  /** Tool path / agent id / state key; matches `WorkflowIRNode.metadata.tool_path`. */
  target: string | null;
  timestamp: string | null;
  error: string | null;
  attempt: number;
  detail: Record<string, unknown>;
}

/**
 * Result of `POST /edit`. Nothing is persisted at this point — `source` is a
 * proposal the user reviews and then commits. `baseVersionId` is the version
 * the proposal was generated against and must be echoed back on commit so a
 * concurrent edit is rejected rather than silently overwritten.
 */
export interface WorkflowEditResult {
  source: string;
  ir: WorkflowIR;
  previousSource: string;
  baseVersionId: string | null;
}
