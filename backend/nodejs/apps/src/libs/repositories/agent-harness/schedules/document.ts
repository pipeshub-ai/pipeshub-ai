/** The schedule, counter and firing documents as every store keeps them: no driver. */
import type { RunInputSnapshot } from '../../../../modules/agent-harness/domain/execution';
import type { SealedContent } from '../../../../modules/agent-harness/domain/data-protection';
import type {
  AgentVersionPin,
  FiringEvent,
  FiringKind,
  FiringStatus,
  NotifyChannel,
  OverlapPolicy,
  RunTriggerContext,
  ScheduleChangedFrom,
  ScheduleOrigin,
  ScheduleRunnerKind,
  ScheduleStatus,
  ScheduleTrigger,
} from '../../../../modules/agent-harness/domain/schedules';

/** A schedule (P8a); `_id` is its id. The input template is the actor's content. */
export interface ScheduleDocument {
  _id: string;
  orgId: string;
  actorId: string;
  name: string;
  agentKey?: string;
  /** The agent version it runs (OWD-14); absent on a schedule from before versions. */
  agentVersion?: AgentVersionPin;
  input: RunInputSnapshot | null;
  trigger: ScheduleTrigger;
  maxCostUsd?: number;
  deadlineS: number;
  /** `deleted` only on a tombstone (ScheduleTombstoneDocument), which no read answers. */
  status: ScheduleStatus | 'deleted';
  statusReason: string | null;
  nextFireAt: number | null;
  webhookTokenHash: string | null;
  /** A signed webhook's key, sealed with the org's data key (plaintext only where no data key is configured). */
  webhookSigningKey?: SealedContent | string | null;
  overlap?: OverlapPolicy;
  notify?: NotifyChannel[];
  origin?: ScheduleOrigin;
  revision?: number;
  lastChangedFrom?: ScheduleChangedFrom;
  skipNextAt?: number;
  codingEngine?: string;
  /** A tombstone's (ScheduleTombstoneDocument) only. */
  deletedAt?: number;
  createdAt: number;
  updatedAt: number;
}

/**
 * A deleted chat-made schedule's tombstone (ADR-SCH-01 D3), in the schedules' own collection or table under the
 * schedule's id: what keeps a spent approval from inserting it again. Ids, its origin and when it went; no content.
 */
export interface ScheduleTombstoneDocument {
  _id: string;
  orgId: string;
  actorId: string;
  status: 'deleted';
  origin: ScheduleOrigin;
  input: null;
  deletedAt: number;
}

/**
 * The schedules an org or an actor held when its last create committed (H2):
 * `_id` is `org:<orgId>` or `actor:<orgId>:<actorId>`. Every create writes the
 * ones it counted in its own transaction, so of two concurrent creates that
 * counted the same schedules one meets a write conflict and counts again.
 */
export interface ScheduleCounterDocument {
  _id: string;
  orgId: string;
  count: number;
  updatedAt: number;
  /** A webhook's per-window delivery count (TR-04) goes at this time; the schedule counts never do. */
  expiresAt?: Date;
}

/** One slot of a schedule, fired, refused or skipped: ids, times and reason codes only. */
export interface ScheduleFiringDocument {
  _id: string;
  orgId: string;
  scheduleId: string;
  actorId: string;
  slotKey: string;
  slotAt: number;
  kind: FiringKind;
  status: FiringStatus;
  runId: string | null;
  sessionId: string | null;
  reason: string | null;
  missed: number;
  attempts: number;
  leaseUntil: number;
  createdAt: number;
  settledAt: number | null;
  watchUntil: number | null;
  notifiedAt: number | null;
  /** When the watch last looked at it (TR-07: the round goes oldest first). */
  watchCheckedAt?: number;
  event?: FiringEvent;
  context?: RunTriggerContext;
  queuedBehind?: string;
  watchEndedAt?: number;
  runner?: ScheduleRunnerKind;
  runRef?: string;
  costCapIgnored?: true;
}
