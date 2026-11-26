/**
 * Approvals and other interactions a run waits on (P2.6c, ARCHITECTURE 4.5,
 * 4.9). Pure: the stores and services apply these rules inside their own
 * transactions.
 *
 * A run tracks the interactions it opened and nobody resolved yet
 * (`openInteractions`, Node bookkeeping the worker never reads). A worker
 * commit's `interaction.requested` adds to it; a resolution removes from it.
 * A suspend for approval with nothing left open is refused: a decision that
 * landed while the worker held the call would otherwise strand the run
 * suspended with nobody to resume it.
 */
import { RULE_LAYERS, RuleLayer } from '../contracts/generated/core/enums';
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import { HarnessConflictError } from './errors';
import type {
  CommitBatch,
  ControlRun,
  HarnessRun,
  InteractionResolution,
  RunStatus,
} from './execution';
import { nodeRunEvent, resolvedData } from './execution';
import type { JournalEventInput } from './journal';
import {
  type ConversationGrant,
  type DeclinableAsk,
  declinedEvent,
  grantedEvent,
} from './conversation-grants';
import type { SessionRole } from './session-roles';
import {
  APPROVAL_EDITS_WORKER_FEATURE,
  type WorkerFeature,
} from './worker-compat';

/** The layer a journaled reason names (`interaction.explained`, QA6-F6), or undefined for none this build knows. */
export const ruleLayer = (value: unknown): RuleLayer | undefined =>
  (RULE_LAYERS as readonly unknown[]).includes(value)
    ? (value as RuleLayer)
    : undefined;

/** Statuses a resolution resumes: the run waits suspended for it. */
export const RESUMABLE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'waiting_approval',
  'paused',
  'recovering',
  'needs_reconciliation',
]);

/**
 * Statuses a decision can still reach (`assertResolvable` refuses a run being
 * cancelled and an ended one): the approvals queue lists only these.
 */
export const DECIDABLE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'running',
  ...RESUMABLE_RUN_STATUSES,
]);

/**
 * What a run asks of its person, as Needs you counts it (ADR-INBOX-02): its open interactions while a decision can
 * still reach them, and, for a root run, the question it ended on. Two runs with different keys mean the person's
 * waiting count may have moved, so their sessions are told again.
 */
export function runAsksKey(
  run: Pick<HarnessRun, 'status' | 'openInteractions' | 'outcome' | 'parent'>,
): string {
  const open = DECIDABLE_RUN_STATUSES.has(run.status)
    ? [...(run.openInteractions ?? [])].sort().join(',')
    : '';
  const asked =
    (run.parent ?? null) === null &&
    run.status === 'completed' &&
    run.outcome === 'needs_input';
  return `${open}|${asked ? 'question' : ''}`;
}

/** A call waiting for its run's owner to connect the item it needs (G-12): `ix.connect.<invocation>` or `ix.connect.k<digest>`. */
export const CONNECT_PREFIX = 'ix.connect.';

export const isConnectInteraction = (interactionId: string): boolean =>
  interactionId.startsWith(CONNECT_PREFIX);

/**
 * Saying an item is connected stands only on the run's actor (G-12): the
 * credential the call will use is theirs, so nobody can say it for them.
 * Declining follows the usual deny rule.
 */
export function connectRefusal(
  decider: Pick<Decider, 'isRunActor'>,
  decision: PersonDecision,
  interactionId: string,
): DecisionRefusal | null {
  if (!isConnectInteraction(interactionId)) return null;
  if (decision === 'answered') return 'DECISION_NOT_APPLICABLE';
  return decision === 'approved' && !decider.isRunActor
    ? 'CONNECT_OWNER_REQUIRED'
    : null;
}

/** `answered` is said only of an unknown effect: "it happened" (P2.9's applied, UX3). */
export type PersonDecision = 'approved' | 'denied' | 'answered';

/**
 * Why a person may not decide (typed; logged once at warning). `OPERATOR_REQUIRED`:
 * an effect that may have happened (`approved` on `ix.unknown.*`) is never re-called
 * from the conversation (P2.9), which could do it twice.
 * `DECISION_NOT_APPLICABLE`: "it happened" answers only an unknown effect.
 */
export type DecisionRefusal = ErrorCodeOf<'decision'>;

export interface Decider {
  /** The person the run acts as. */
  isRunActor: boolean;
  /** Someone the bound approver directory names an approver of the run's asks (none by default). */
  isApprover: boolean;
  /** Their role in the session; null when not a participant. */
  role: SessionRole | null;
  isOrgAdmin: boolean;
}

/**
 * An approval counted toward an ask a rule says needs several approvals,
 * journaled by Node (`ApprovalRules`) before the ask holds enough of them.
 */
export const APPROVAL_COUNTED = 'x.pipeshub.approval.counted';

/**
 * The usual rule: only the run's actor, or an approver a bound directory names, approves;
 * the session's owner and org admins may deny (or cancel), never approve for
 * someone else. Saying an unknown effect happened runs nothing, so it follows
 * the deny rule.
 */
export function decisionRefusal(
  decider: Decider,
  decision: PersonDecision,
): DecisionRefusal | null {
  if (decider.isRunActor || decider.isApprover) return null;
  if (decision === 'approved') return 'APPROVER_REQUIRED';
  if (decider.role === 'owner' || decider.isOrgAdmin) return null;
  return 'NOT_ALLOWED_TO_DECIDE';
}

/**
 * Whether the decision stands only on the person's approver standing (no
 * other role allows it), so the decision must hold that standing when it
 * commits (QA21-F2); the refusal they get without it. `refusal` is the rule
 * the ask follows (the usual one unless a rule decides it).
 */
export function approverOnlyRefusal(
  decider: Decider,
  decision: PersonDecision,
  refusal: (
    decider: Decider,
    decision: PersonDecision,
  ) => DecisionRefusal | null = decisionRefusal,
): DecisionRefusal | null {
  if (!decider.isApprover) return null;
  return refusal({ ...decider, isApprover: false }, decision);
}

/** The interactions a worker commit requests. */
export function requestedInteractions(
  batch: Pick<CommitBatch, 'events'>,
): string[] {
  return batch.events
    .filter((event) => event.type === 'interaction.requested')
    .map((event) => event.data['interactionId'])
    .filter((id): id is string => typeof id === 'string');
}

/** The run's open interactions once `batch` commits; undefined: not tracked (a run from before P2.6c). */
export function openAfterCommit(
  run: Pick<HarnessRun, 'openInteractions'>,
  batch: Pick<CommitBatch, 'events'>,
): string[] | undefined {
  const requested = requestedInteractions(batch);
  if (run.openInteractions === undefined && requested.length === 0) {
    return undefined;
  }
  return [...new Set([...(run.openInteractions ?? []), ...requested])];
}

export function assertSuspendable(
  run: Pick<HarnessRun, 'openInteractions'>,
  batch: Pick<CommitBatch, 'events' | 'transition'>,
): void {
  const detail = batch.transition?.detail;
  if (detail?.kind !== 'suspend' || detail.reason !== 'approval') return;
  const open = openAfterCommit(run, batch);
  if (open?.length === 0) {
    throw new HarnessConflictError(
      'INTERACTION_RESOLVED',
      'Every interaction the run waits on is already resolved',
    );
  }
}

/** Whether a resolution may land on a run in `status`, or why not. */
export function assertResolvable(
  run: Pick<HarnessRun, 'status' | 'openInteractions' | 'harnessMajor'>,
  interactionId: string,
): void {
  if (run.harnessMajor !== 2) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      'Only a harness v2 run resolves interactions',
    );
  }
  if (run.status === 'cancelling') {
    throw new HarnessConflictError(
      'RUN_CANCELLING',
      'The run is being cancelled; its interactions are closed',
    );
  }
  const ended = ['completed', 'failed', 'cancelled'].includes(run.status);
  const tracked = run.openInteractions;
  if (ended || (tracked !== undefined && !tracked.includes(interactionId))) {
    throw new HarnessConflictError(
      'INTERACTION_NOT_OPEN',
      'The run has no open interaction with that id',
    );
  }
}

/** What a run needs of its workers once these resolutions are journaled (AP-06: an approved edit). */
export const resolutionFeatures = (
  resolutions: readonly InteractionResolution[],
): WorkerFeature[] =>
  resolutions.some(
    (resolution) =>
      resolution.edits !== undefined && resolution.decision === 'approved',
  )
    ? [APPROVAL_EDITS_WORKER_FEATURE]
    : [];

/** The grant an approval gives, naming who gave it (WP-115), so an ask a held grant answers later is theirs. */
const grantBy = (
  offer: ConversationGrant | undefined,
  by: string,
): ConversationGrant | undefined =>
  offer === undefined ? undefined : { ...offer, by };

/**
 * `resolveActive`: a running (or queued) run only learns the decision; a
 * suspended one is resumed with it, as before.
 */
export function resolutionJournalEvents(
  run: Pick<HarnessRun, 'status'>,
  resolutions: InteractionResolution | readonly InteractionResolution[],
  by: string,
  /** FLOWGRANT: what each ask offered, so the grant names what it allows; absent, it names only its ask. */
  offers: ReadonlyMap<string, ConversationGrant> = new Map(),
  /**
   * ADR-GRANT-01 D9: the call each ask the run's own actor declined holds, so the decline holds for their runs in
   * the conversation; absent for anyone else's decision, which answers its one ask.
   */
  declinable: ReadonlyMap<string, DeclinableAsk> = new Map(),
): JournalEventInput[] {
  const all: readonly InteractionResolution[] = Array.isArray(resolutions)
    ? resolutions
    : [resolutions as InteractionResolution];
  const decided = all.flatMap((resolution): JournalEventInput[] => {
    const data = resolvedData(resolution, by);
    const edited =
      resolution.edits !== undefined && resolution.decision === 'approved'
        ? (data['answerRef'] as { hash: string }).hash
        : undefined;
    const resolved: JournalEventInput = {
      ...nodeRunEvent('interaction.resolved', data),
      // AP-06: the audit chain keeps the edit's hash beside the call's own (`tool.intent` `argsDigest`).
      ...(edited !== undefined
        ? { auditFields: { editedArgsDigest: edited } }
        : {}),
    };
    if (resolution.scope !== undefined && resolution.decision === 'approved') {
      return [
        resolved,
        grantedEvent(
          resolution.interactionId,
          grantBy(offers.get(resolution.interactionId), by),
        ),
      ];
    }
    const declined = declinable.get(resolution.interactionId);
    return resolution.decision === 'denied' && declined !== undefined
      ? [
          resolved,
          declinedEvent(resolution.interactionId, {
            ...declined,
            by,
            ...(resolution.reason !== undefined
              ? { reason: resolution.reason }
              : {}),
          }),
        ]
      : [resolved];
  });
  // One resume for the batch: the worker claims the run once and finds every decision.
  return RESUMABLE_RUN_STATUSES.has(run.status)
    ? [...decided, nodeRunEvent('run.resumed', { cause: 'resolved' })]
    : decided;
}

/**
 * DECIDE409: whether a control command still applies to the run as it now is. A resolve prepared against a
 * status, on a run that tracks its open interactions, commutes with every commit that leaves the status alone
 * (another decision, the worker's progress): the per-interaction open check is what refuses a second decision.
 */
export function assertControlCurrent(
  run: Pick<HarnessRun, 'revision' | 'status' | 'openInteractions'>,
  command: Pick<ControlRun, 'action' | 'expectedRevision' | 'expectedStatus'>,
): void {
  if (commutes(run, command)) {
    if (run.status === command.expectedStatus) return;
    throw new HarnessConflictError('REVISION_CONFLICT', 'Run status changed');
  }
  if (run.revision !== command.expectedRevision) {
    throw new HarnessConflictError('REVISION_CONFLICT', 'Run revision changed');
  }
}

/** A resolve the store checks by status and open interactions, not by revision. */
export const commutes = (
  run: Pick<HarnessRun, 'openInteractions'>,
  command: Pick<ControlRun, 'action' | 'expectedStatus'>,
): boolean =>
  command.action === 'resolve' &&
  command.expectedStatus !== undefined &&
  run.openInteractions !== undefined;

/** The open list once `interactionId` is resolved; undefined stays undefined. */
export const withoutInteraction = (
  open: readonly string[] | undefined,
  interactionId: string,
): string[] | undefined =>
  open === undefined ? undefined : open.filter((id) => id !== interactionId);
