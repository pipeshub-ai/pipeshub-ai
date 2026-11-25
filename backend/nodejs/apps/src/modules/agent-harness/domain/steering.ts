import { withoutRecordedRunFields } from './recorded-run-fields';
import { inlineContentRef } from './run-content';
import { HarnessConflictError } from './errors';
import type {
  CommitBatch,
  HarnessRun,
  RunInputSnapshot,
  RunStatus,
} from './execution';
import type { JournalEventInput } from './journal';
import type { CheckedSource } from '../projection/citation-source';
import { attemptAccepts, WorkerFeature } from './worker-compat';
import { principalRefFor } from './run-spec';

/**
 * Steering (REPORT D19, 6.2.2; P1b T2): a message sent while the session's
 * root run is active joins that run as `input.queued` instead of starting a
 * second root run; the worker consumes it at its next boundary
 * (`input.consumed`). A session with no active run starts a new one, as before.
 */

/**
 * A v2 run in these statuses reaches a boundary again (once claimed or
 * resumed). A run being cancelled does not: a message sent after the stop
 * starts a new run once this one has ended.
 */
export const STEERABLE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'running',
  'waiting_approval',
  'paused',
]);

/** Why a message cannot join the active run (it is then `RUN_ACTIVE`). */
export type SteeringRefusal =
  'not_durable' | 'cancelling' | 'worker' | 'input_artifacts';

export function steeringRefusal(
  run: Pick<HarnessRun, 'harnessMajor' | 'status' | 'attemptFeatures'>,
  input: Pick<RunInputSnapshot, 'attachments' | 'recordIds' | 'inputArtifacts'>,
): SteeringRefusal | null {
  if (run.harnessMajor !== 2) return 'not_durable';
  // A queued message reaches a running turn as text: the artifacts it names would be dropped (E1b).
  if ((input.inputArtifacts?.length ?? 0) > 0) return 'input_artifacts';
  if (!STEERABLE_RUN_STATUSES.has(run.status)) return 'cancelling';
  // The attempt running it would fold input it cannot read (RU-2f).
  if (
    run.status === 'running' &&
    !steeringFeatures(input).every((feature) => attemptAccepts(run, feature))
  ) {
    return 'worker';
  }
  return null;
}

/**
 * Only the run's own actor steers it (BUGS-B). The worker reads a steered
 * message's attachments and searches as the run's actor with the run's pin,
 * so a steer by anyone else would use the actor's access instead of the
 * sender's; admission sends that message to a next run (`waitsAsNextRun`),
 * which runs as its sender. Checked again at the write so no path widens.
 */
export function assertSteeredByActor(
  run: Pick<HarnessRun, 'actorId'>,
  senderId: string,
): void {
  if (run.actorId !== senderId) {
    throw new HarnessConflictError(
      'RUN_ACTIVE',
      'Only the person running this task can add to it; the message waits as a next run',
    );
  }
}

type SteeringInput = Pick<
  RunInputSnapshot,
  'query' | 'attachments' | 'recordIds'
>;

/** The attachments and records a message names by reference (C-21). */
export const hasReferences = (
  input: Pick<RunInputSnapshot, 'attachments' | 'recordIds'>,
): boolean => input.attachments.length > 0 || input.recordIds.length > 0;

/**
 * What a worker must fold to take this message: its sender (`by`, SS1)
 * always, its references since T2b. The message stays on the session's root
 * stream, so every later run of the session needs the same.
 */
export const steeringFeatures = (
  input: Pick<RunInputSnapshot, 'attachments' | 'recordIds'>,
): WorkerFeature[] =>
  hasReferences(input)
    ? ['steering', 'steering_by', 'steering_references']
    : ['steering', 'steering_by'];

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/**
 * What the message names, as the record authorization checks it: an
 * attachment by its record and the content it is read by (the worker opens
 * it by `virtualRecordId`, so that must be the record's own), a record by id.
 */
export function steeringReferences(
  input: Pick<RunInputSnapshot, 'attachments' | 'recordIds'>,
): CheckedSource[] {
  return [
    ...input.attachments.map((attachment): CheckedSource => {
      const virtualRecordId = text(attachment['virtualRecordId']);
      return {
        kind: 'attachment',
        recordId: String(attachment['recordId']),
        ...(virtualRecordId !== undefined ? { virtualRecordId } : {}),
      };
    }),
    ...input.recordIds.map((recordId): CheckedSource => ({
      kind: 'indexed',
      recordId,
    })),
  ];
}

/**
 * A message with references reached a store before they were authorized: the
 * admission service checks them against the caller's access and admits it
 * again. Nothing was written.
 */
export class SteeringReferencesUnchecked extends Error {
  constructor() {
    super('steering references need authorization');
    this.name = 'SteeringReferencesUnchecked';
  }
}

/** Refuses a steering message whose references were not authorized first. */
export function assertReferencesChecked(
  input: Pick<RunInputSnapshot, 'attachments' | 'recordIds'>,
  checked: boolean | undefined,
): void {
  if (hasReferences(input) && checked !== true) {
    throw new SteeringReferencesUnchecked();
  }
}

export const noRunToSteer = (): HarnessConflictError =>
  new HarnessConflictError(
    'RUN_NOT_ACTIVE',
    'The session has no active run to add this message to',
  );

export function steeringConflict(
  reason: SteeringRefusal,
): HarnessConflictError {
  return new HarnessConflictError(
    'RUN_ACTIVE',
    `The session already has an active root run that cannot take this message (${reason})`,
  );
}

/**
 * The text rides inline in its ref, as a new turn's query does in the run's
 * input (bounded alike, `CHAT_QUERY_MAX_CHARS`); an event over the journal's
 * inline bound is claim-checked, sealed, when its append is prepared. `by`
 * is the admitted command's actor, which admission took from the verified
 * credential: nothing in the request names it.
 */
export function inputQueuedEvent(
  messageSeq: number,
  input: SteeringInput,
  actorId: string,
): JournalEventInput {
  return {
    type: 'input.queued',
    schemaVersion: 1,
    critical: true,
    visibility: 'owner',
    data: {
      messageSeq,
      by: principalRefFor(actorId),
      textRef: inlineContentRef(Buffer.from(input.query, 'utf8')),
      ...(input.attachments.length > 0
        ? { attachments: input.attachments.map(attachmentRef) }
        : {}),
      ...(input.recordIds.length > 0
        ? { recordIds: [...input.recordIds] }
        : {}),
    },
  };
}

const ATTACHMENT_FIELDS = [
  'recordId',
  'virtualRecordId',
  'recordName',
  'mimeType',
  'extension',
  'source',
] as const;

/** The chat request's attachment fields, as the worker's resolvers read them. */
const attachmentRef = (
  attachment: Record<string, unknown>,
): Record<string, unknown> =>
  Object.fromEntries(
    ATTACHMENT_FIELDS.filter((key) => attachment[key] !== undefined).map(
      (key) => [key, attachment[key]],
    ),
  );

/** The `messageSeq`s a worker commit consumes. */
export const consumedInputs = (batch: Pick<CommitBatch, 'events'>): number[] =>
  batch.events
    .filter((event) => event.type === 'input.consumed')
    .map((event) => event.data['messageSeq'])
    .filter((seq): seq is number => typeof seq === 'number');

/** The run's queued inputs once `batch` commits (what it consumes is gone). */
export function remainingInputs(
  run: Pick<HarnessRun, 'pendingInputs'>,
  batch: Pick<CommitBatch, 'events'>,
): number[] {
  const consumed = new Set(consumedInputs(batch));
  return (run.pendingInputs ?? []).filter((seq) => !consumed.has(seq));
}

/** Messages the run ends without taking; its transcript keeps them (C-21). */
export const leftInputs = (run: Pick<HarnessRun, 'pendingInputs'>): number =>
  run.pendingInputs?.length ?? 0;

/**
 * The user was told the run would take their message: a completion that
 * leaves it queued is refused, and the worker takes it in a follow-up turn.
 * Only a worker with no turn left says it leaves the input.
 */
export function assertInputTaken(
  run: Pick<HarnessRun, 'pendingInputs'>,
  batch: Pick<CommitBatch, 'events' | 'transition'>,
): void {
  const transition = batch.transition;
  if (transition?.status !== 'completed' || transition.leavesInput === true) {
    return;
  }
  if (remainingInputs(run, batch).length > 0) {
    throw new HarnessConflictError(
      'INPUT_PENDING',
      'The user queued input this run has not taken',
    );
  }
}

/**
 * A run as a worker reads it: Node's own bookkeeping stays Node's (the
 * worker folds `input.*` from the journal; how a claim was taken is logged,
 * not acted on), and the worker's run model is strict, so a field it does
 * not know would fail every claim of the run.
 */
export function workerRun(run: HarnessRun): HarnessRun {
  const {
    inputSeq: _seq,
    pendingInputs: _pending,
    openInteractions: _open,
    claimedFrom: _from,
    requires: _requires,
    attemptFeatures: _features,
    waitingForWorker: _waiting,
    parent: _parent,
    children: _children,
    suspendedFor: _suspendedFor,
    workLeftMs: _workLeft,
    waitingChildren: _waitingChildren,
    // The worker reads whose turns the run sees from the pinned spec (SS2).
    sessionRole: _sessionRole,
    // The worker reads who the run acts as from the pinned spec and run.admitted (OWD-2).
    attribution: _attribution,
    // The worker reads the agent version from the pinned spec (OWD-14).
    agentVersion: _agentVersion,
    // The claim answer names the class beside the run (G-45).
    priorityClass: _priorityClass,
    priorityRank: _priorityRank,
    // The worker enforces the gates its pinned spec holds (WP-64: Node keeps them on the run for the routes).
    gates: _gates,
    ...rest
  } = run;
  // The fields an edition records are the model gateway's; the worker folds its own.
  return withoutRecordedRunFields(rest);
}
