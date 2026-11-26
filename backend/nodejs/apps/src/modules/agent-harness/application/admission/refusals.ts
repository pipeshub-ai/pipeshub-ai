import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessForbiddenError,
  HarnessGroupAccessRefusedError,
  HarnessModelAccessRefusedError,
  HarnessOperatorControlRefusedError,
  HarnessQuotaExceededError,
  HarnessSessionRoleRefusedError,
  HarnessUnavailableError,
} from '../../domain/errors';
import { ExecutorKind } from '../../domain/run-spec';
import {
  SessionRole,
  TURN_REFUSAL_MESSAGES,
  turnRefusal,
} from '../../domain/session-roles';

/** A reason code; a store conflict is named by its code (`run_active`, ...). */
export type RefusalReason = string;

/** Marks where a refusal came from, so the log can name its reason. */
export class AdmissionRefusal extends Error {
  constructor(
    readonly reason: RefusalReason,
    readonly error: unknown,
  ) {
    super('admission refused');
  }
}

/** Reasons that name which authorization refused, rather than `forbidden`. */
const NAMED_FORBIDDEN: ReadonlySet<RefusalReason> = new Set([
  'narrowing',
  'references',
]);

/** The one classification of an error a step or the store threw. */
export const refusal = (
  error: unknown,
  fallback: RefusalReason,
): AdmissionRefusal =>
  new AdmissionRefusal(
    error instanceof HarnessQuotaExceededError
      ? 'quota'
      : error instanceof HarnessForbiddenError
        ? NAMED_FORBIDDEN.has(fallback)
          ? fallback
          : 'forbidden'
        : error instanceof HarnessUnavailableError
          ? 'unavailable'
          : error instanceof HarnessConflictError
            ? error.code.toLowerCase()
            : fallback,
    error,
  );

/** An error as the refusal it is, or `unavailable` for anything unclassified. */
export const asRefusal = (error: unknown): AdmissionRefusal =>
  error instanceof AdmissionRefusal ? error : refusal(error, 'unavailable');

/** A refusal admission explains to the caller (warning); anything else failed (error). */
export const isKnownRefusal = (error: unknown): boolean =>
  error instanceof HarnessModelAccessRefusedError ||
  error instanceof HarnessEngineProfileRefusedError ||
  error instanceof HarnessQuotaExceededError ||
  error instanceof HarnessForbiddenError ||
  error instanceof HarnessSessionRoleRefusedError ||
  error instanceof HarnessGroupAccessRefusedError ||
  error instanceof HarnessOperatorControlRefusedError ||
  error instanceof HarnessUnavailableError ||
  error instanceof HarnessConflictError;

/** A participant's refusal with their role's reason code; null when they may. */
export const refusedRole = (
  role: SessionRole | null,
  executor: ExecutorKind,
  collaboratorTurns = false,
): AdmissionRefusal | null => {
  const code =
    role === null ? null : turnRefusal(role, executor, collaboratorTurns);
  if (code === null) return null;
  return new AdmissionRefusal(
    code.toLowerCase(),
    new HarnessSessionRoleRefusedError(code, TURN_REFUSAL_MESSAGES[code]),
  );
};

/** A settled read's value, or its rejection rethrown. */
export const settled = <T>(result: PromiseSettledResult<T>): T => {
  if (result.status === 'rejected') throw result.reason;
  return result.value;
};
