import {
  ERROR_CODES,
  WIRE_PREFIX,
  type ErrorCode,
  type ErrorCodeOf,
} from '../contracts/generated/ext/pipeshub/errors';
import type { DecisionRefusal } from './interactions';
import type {
  EngineProfileRefusal,
  EngineProfileRefusalCode,
  ProfileLayerName,
} from './engine-profiles';
import type { AccessRefusalCode } from './model-access';
import type { ModelAccountRefusalCode } from './model-accounts';
import type { TurnRefusal } from './session-roles';

/**
 * The wire codes of `contracts/harness/ext/pipeshub/errors.json`, which the
 * Python journal client reads too; Node answers them with the
 * `AGENT_HARNESS_` prefix.
 */
export type HarnessConflictCode = ErrorCodeOf<'conflict' | 'budget'>;

type Unprefixed<T, P extends string> = T extends `${P}${infer R}` ? R : never;

/** A catalogue code as Node answers it. */
export const harnessWireCode = (code: ErrorCode): string =>
  `${WIRE_PREFIX}${code}`;

export class HarnessConflictError extends Error {
  constructor(
    public readonly code: HarnessConflictCode,
    message: string,
    /** A finer machine reason the caller may read (an extension's own, WP-51); relayed as the error's `reasonCode`. */
    public readonly reasonCode?: string,
  ) {
    super(message);
    this.name = 'HarnessConflictError';
  }
}

/**
 * WP-64: a turn today's loop would serve that answers a run held to gates, or runs an agent whose safety settings set
 * them. That loop holds no gates, so the turn is refused in plain words, never run ungated.
 */
export const gatesNeedRuntime = (): HarnessConflictError =>
  new HarnessConflictError(
    'GATES_NEED_RUNTIME',
    ERROR_CODES.GATES_NEED_RUNTIME.nextAction.text,
  );

export class HarnessNotFoundError extends Error {
  constructor(message = 'Run not found') {
    super(message);
    this.name = 'HarnessNotFoundError';
  }
}

/**
 * The run's conversation was deleted while it ran (QA21-F4): its transcript has nowhere to go. A not-found to every
 * caller that only asks whether the run is still there; its own code to the worker, so the cause is named.
 */
export class ConversationDeletedError extends HarnessNotFoundError {
  readonly code = 'CONVERSATION_DELETED' satisfies ErrorCode;

  constructor() {
    super("The run's conversation was deleted");
    this.name = 'ConversationDeletedError';
  }
}

/** The activity panel is off for the org (UX5); the platform never answers so since CTL-5. */
export class ActivityPanelDisabledError extends Error {
  readonly code = 'ACTIVITY_PANEL_DISABLED' satisfies ErrorCodeOf<'activity'>;

  constructor() {
    super('The activity panel is turned off for this organization');
    this.name = 'ActivityPanelDisabledError';
  }
}

/** Follow-ups are stored for a run that ended `completed` only (UX5). */
export class RunNotCompletedError extends Error {
  readonly code = 'RUN_NOT_COMPLETED' satisfies ErrorCodeOf<'activity'>;

  constructor() {
    super('The run has not completed');
    this.name = 'RunNotCompletedError';
  }
}

export class HarnessUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'HarnessUnavailableError';
  }
}

/** A participant whose role does not allow sending turns (SS0); the code says which role. */
export class HarnessSessionRoleRefusedError extends Error {
  constructor(
    public readonly code: TurnRefusal,
    message: string,
  ) {
    super(message);
    this.name = 'HarnessSessionRoleRefusedError';
  }
}

/** Who may use the harness, an engine or a model, by the org's groups (G-41). */
export type GroupAccessCode = ErrorCodeOf<'group_access'>;

export type OperatorControlCode = ErrorCodeOf<'operator_control'>;

/**
 * ADR-OPS-01: a stop holds the turn (`OPERATOR_STOPPED`, with the scope it
 * stops), or an admin's control change is refused (scope, target).
 */
export class HarnessOperatorControlRefusedError extends Error {
  constructor(
    public readonly code: OperatorControlCode,
    public readonly details: Record<string, string> = {},
    message: string = ERROR_CODES[code].nextAction.text,
  ) {
    super(message);
    this.name = 'HarnessOperatorControlRefusedError';
  }
}

/** The actor is in none of the org's groups that may use the harness, this engine or this model now (G-41). */
export class HarnessGroupAccessRefusedError extends Error {
  constructor(
    public readonly code: GroupAccessCode,
    /** MODEL_GROUP_REQUIRED: the configured models the person's groups do allow, which the refusal names. */
    public readonly allowedModels?: readonly string[],
  ) {
    super(
      allowedModels === undefined
        ? ERROR_CODES[code].nextAction.text
        : `${ERROR_CODES[code].nextAction.text} ${
            allowedModels.length > 0
              ? `Models you can use: ${allowedModels.join(', ')}.`
              : 'No configured model is open to your groups now.'
          }`,
    );
    this.name = 'HarnessGroupAccessRefusedError';
  }
}

/** A person who may not make this decision on this run (P2.6c, ARCHITECTURE 4.9). */
export class HarnessDecisionRefusedError extends Error {
  constructor(
    public readonly code: DecisionRefusal,
    message: string,
  ) {
    super(message);
    this.name = 'HarnessDecisionRefusedError';
  }
}

/**
 * An engine profile layer that widens the one above it, or is not a narrowing
 * (E4a); names the dimension and layer, never the layer's content.
 */
export class HarnessEngineProfileRefusedError extends Error {
  readonly code: EngineProfileRefusalCode;
  readonly dimension?: string;
  readonly layer?: ProfileLayerName;

  constructor(refusal: EngineProfileRefusal) {
    super(
      refusal.code === 'ENGINE_PROFILE_WIDENING'
        ? `The ${refusal.layer ?? 'requested'} engine profile asks for more than allowed: ${refusal.dimension ?? 'engine'}`
        : `The ${refusal.layer ?? 'requested'} engine profile is not valid`,
    );
    this.name = 'HarnessEngineProfileRefusedError';
    this.code = refusal.code;
    if (refusal.dimension !== undefined) this.dimension = refusal.dimension;
    if (refusal.layer !== undefined) this.layer = refusal.layer;
  }
}

/** The catalogue's `approvals` group (ERR-UX): the web app words each. */
export type ApprovalsRefusalCode = ErrorCodeOf<'approvals'>;

/** An approvals policy write refused (P2.11e); names what, never the rules. */
export class HarnessApprovalsRefusedError extends Error {
  constructor(
    readonly code: ApprovalsRefusalCode,
    message: string,
  ) {
    super(message);
    this.name = 'HarnessApprovalsRefusedError';
  }
}

export type MemoryRefusalCode = ErrorCodeOf<'memory'>;

/** A memory read, write or review refused (P7.3); never names the content. */
export class HarnessMemoryRefusedError extends Error {
  constructor(
    readonly code: MemoryRefusalCode,
    message: string,
  ) {
    super(message);
    this.name = 'HarnessMemoryRefusedError';
  }
}

/**
 * A model-account write or lookup refused (P2.4b); names the field, never
 * its value: a refused key must not come back in the answer or a log.
 */
export class HarnessModelAccountRefusedError extends Error {
  readonly code: ModelAccountRefusalCode;
  readonly field?: string;

  constructor(code: ModelAccountRefusalCode, message: string, field?: string) {
    super(message);
    this.name = 'HarnessModelAccountRefusedError';
    this.code = code;
    if (field !== undefined) this.field = field;
  }
}

/**
 * A model access refused (A1): a vendor rule, the offering, the org's policy,
 * an account the actor does not own, release data that does not verify, or a
 * policy write that would not be a narrowing. `code` is the reason code Python
 * uses for the same cause; `rule` the deciding vendor row; `field` the policy
 * field a write named.
 */
export class HarnessModelAccessRefusedError extends Error {
  readonly code: AccessRefusalCode;
  readonly rule: string | null;
  readonly field?: string;

  constructor(
    code: AccessRefusalCode,
    message: string,
    rule: string | null = null,
    field?: string,
  ) {
    super(message);
    this.name = 'HarnessModelAccessRefusedError';
    this.code = code;
    this.rule = rule;
    if (field !== undefined) this.field = field;
  }
}

export class HarnessForbiddenError extends Error {
  constructor(message = 'The actor may not use this agent') {
    super(message);
    this.name = 'HarnessForbiddenError';
  }
}

export type JournalRejectionCode = Unprefixed<
  ErrorCodeOf<'journal_rejected'>,
  'JOURNAL_'
>;

/** An append was refused before anything was written. */
export class JournalRejectedError extends Error {
  constructor(
    public readonly code: JournalRejectionCode,
    /** Position of the offending event in the submitted batch. */
    public readonly index: number,
    message: string,
  ) {
    super(message);
    this.name = 'JournalRejectedError';
  }
}

/**
 * A claim-checked payload is gone or does not match its hash/size. Terminal
 * for the run (`failed{journal_payload_missing}`), never retried [R-5].
 */
export class JournalPayloadMissingError extends Error {
  readonly errorType = 'journal_payload_missing';

  constructor(
    public readonly ref: string,
    /** `shredded`: the org's data key was destroyed (erasure); `plaintext`: stored unsealed. */
    public readonly reason: 'missing' | 'corrupt' | 'shredded' | 'plaintext',
  ) {
    super(`Journal payload ${reason}`);
    this.name = 'JournalPayloadMissingError';
  }
}

/**
 * The session was erased (E7): what was asked for is gone on purpose. A
 * typed answer (410), never a missing-payload failure or a 500.
 */
export class SessionErasedError extends Error {
  readonly code = 'SESSION_ERASED';

  constructor() {
    super('This conversation was deleted and its data erased');
    this.name = 'SessionErasedError';
  }
}

export type SealedContentFailure =
  'corrupt' | 'unsupported_version' | 'shredded' | 'unwrap_failed';

/** Raised by the envelope cipher; readers map it to missing (terminal) or unavailable. */
export class SealedContentError extends Error {
  constructor(
    public readonly reason: SealedContentFailure,
    message: string,
  ) {
    super(message);
    this.name = 'SealedContentError';
  }
}

/**
 * `CONTROL_PLANE_PIN=on` and the run's pin could not be made (a store it reads
 * is unreachable, or the org's head kept moving under it). Typed apart from the other unavailabilities because the
 * chat routes hand those to today's loop, which would run the turn on the
 * live documents the pin exists to replace: this one is answered, never
 * handed over.
 */
export class HarnessRunConfigUnavailableError extends HarnessUnavailableError {
  readonly code = 'RUN_CONFIG_UNAVAILABLE' as const;

  constructor(message = "The run's configuration could not be resolved") {
    super(message);
    this.name = 'HarnessRunConfigUnavailableError';
  }
}

/**
 * Journal content that a newer build or the right key-encryption key could
 * read: retryable, never a reason to fail the run.
 */
export class JournalContentUnavailableError extends HarnessUnavailableError {
  constructor(public readonly reason: 'unsupported_version' | 'unwrap_failed') {
    super(`Journal content is not readable here (${reason})`);
    this.name = 'JournalContentUnavailableError';
  }
}

export type HarnessUnauthorizedReason =
  | 'credential'
  | 'malformed'
  | 'algorithm'
  | 'unknown_key'
  | 'signature'
  | 'issuer'
  | 'audience'
  | 'expired'
  | 'claims'
  | 'superseded';

export class HarnessUnauthorizedError extends Error {
  constructor(
    public readonly reason: HarnessUnauthorizedReason,
    message = 'Agent harness authority refused',
  ) {
    super(message);
    this.name = 'HarnessUnauthorizedError';
  }
}

/** An admission quota is full: 429 with Retry-After; nothing was written. */
export class HarnessQuotaExceededError extends Error {
  constructor(
    public readonly scope: 'org' | 'user',
    public readonly quota: 'active_runs' | 'admission_rate',
    public readonly retryAfterS: number,
  ) {
    super(
      quota === 'active_runs'
        ? `Too many agent runs are active for this ${scope}`
        : `Too many agent runs were started for this ${scope}; try again shortly`,
    );
    this.name = 'HarnessQuotaExceededError';
  }
}

/**
 * Writing a run's transcript failed inside its commit transaction, which
 * therefore wrote nothing (P1.8, L-11). Transient: the worker retries the
 * same command.
 */
export class TranscriptProjectionError extends Error {
  constructor(public override readonly cause: unknown) {
    super('The run transcript could not be written');
    this.name = 'TranscriptProjectionError';
  }
}

/**
 * The claim-check store could not be reached (not: the object is gone).
 * Retryable; the run waits or yields, it never fails for it (REPORT 6.17).
 */
export class PayloadStoreUnavailableError extends HarnessUnavailableError {
  constructor(
    public readonly operation: 'put' | 'get',
    public override readonly cause?: unknown,
  ) {
    super(`The journal payload store is unavailable (${operation})`);
    this.name = 'PayloadStoreUnavailableError';
  }
}

/** An anchor already exists at that seq with another hash: someone rewrote the chain. */
export class AuditAnchorConflictError extends Error {
  constructor(
    public readonly orgId: string,
    public readonly seq: number,
  ) {
    super('An audit anchor already exists at this position with another hash');
    this.name = 'AuditAnchorConflictError';
  }
}

/** The catalogue's `schedule` group (ERR-UX). */
export type ScheduleRefusalCode = ErrorCodeOf<'schedule'>;

/** A schedule request refused (P8a); names the field, never its value. */
export class HarnessScheduleRefusedError extends Error {
  readonly code: ScheduleRefusalCode;
  readonly field?: string;

  constructor(code: ScheduleRefusalCode, message: string, field?: string) {
    super(message);
    this.name = 'HarnessScheduleRefusedError';
    this.code = code;
    if (field !== undefined) this.field = field;
  }
}

/** A webhook delivery refused for its size or its rate (TR-04); nothing fired. */
export class HarnessTriggerRefusedError extends Error {
  constructor(
    readonly code: ErrorCodeOf<'trigger'>,
    message: string,
    readonly retryAfterS?: number,
  ) {
    super(message);
    this.name = 'HarnessTriggerRefusedError';
  }
}
