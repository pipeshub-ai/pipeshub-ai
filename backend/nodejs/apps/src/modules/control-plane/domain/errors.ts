import {
  ERROR_CODES,
  type ErrorCode,
  type Problem,
} from '../contracts/generated';
import type { ProblemFields } from './problem';

/**
 * A control-plane refusal: its code, HTTP status and retryability come from
 * the one catalogue (`contracts/control-plane/errors.json`), never a copy.
 */
export abstract class ControlPlaneError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  protected constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
    this.status = ERROR_CODES[code].status;
    this.retryable = ERROR_CODES[code].retryable;
  }
}

/**
 * A write lost a race it can win by planning again on a fresh read (a lost
 * compare-and-set on a store outside the transaction). The transaction
 * retries the whole write a bounded number of times, then gives up.
 */
export class ControlPlaneRetryableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ControlPlaneRetryableError';
  }
}

/** `CP_UNAVAILABLE` (02-lld §10): the write could not be committed with its audit entry. */
export class ControlPlaneUnavailableError extends ControlPlaneError {
  override readonly cause: unknown;

  constructor(message: string, options?: { cause?: unknown }) {
    super('CP_UNAVAILABLE', message);
    this.name = 'ControlPlaneUnavailableError';
    this.cause = options?.cause;
  }
}

/** A refusal a service or route raises with the catalogue's code, and the `Problem` fields that say what and where. */
export class CpRefusal extends ControlPlaneError {
  constructor(
    code: ErrorCode,
    message: string,
    readonly fields: ProblemFields = {},
  ) {
    super(code, message);
    this.name = 'CpRefusal';
  }
}

/** A `Problem` a resolver answered (a pin refused), passed to the caller as it is. */
export class CpProblemError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.message);
    this.name = 'CpProblemError';
  }
}

/** `CP_NOT_FOUND`: also what another org's object answers, so ids do not leak. */
export const notFound = (what: string): CpRefusal =>
  new CpRefusal('CP_NOT_FOUND', `No such ${what}`);

/** `CP_REVISION_CONFLICT`: the object changed since the caller read it. */
export class ControlPlaneRevisionConflictError extends ControlPlaneError {
  constructor(
    message: string,
    readonly current: string | null,
  ) {
    super('CP_REVISION_CONFLICT', message);
    this.name = 'ControlPlaneRevisionConflictError';
  }
}
