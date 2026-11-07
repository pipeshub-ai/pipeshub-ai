/**
 * Why a request or call failed, as one of a fixed set of class names, and the
 * one level policy that goes with them.
 * Pure: no logging here.
 */
import { BaseError } from '../errors/base.error';
import { unansweredKind } from '../errors/backend-error';

export const ERROR_CLASSES = [
  'auth_expired',
  'forbidden',
  'not_found',
  'rate_limited',
  'timeout',
  'upstream_5xx',
  'bad_request',
  'conflict',
  'unavailable',
  'cancelled',
  'misconfigured',
  'internal',
] as const;
export type ErrorClass = (typeof ERROR_CLASSES)[number];

const MAX_CAUSE_DEPTH = 8;

/** A failure of our own code: decisive unless it wraps a cause (fetch's `TypeError: fetch failed`). */
const BUG_TYPES = [TypeError, ReferenceError, RangeError, SyntaxError];

/** Classes a 500 of ours may take from its cause: the upstream, not our code, failed. */
const UPSTREAM_CLASSES: ReadonlySet<ErrorClass> = new Set<ErrorClass>([
  'upstream_5xx',
  'unavailable',
  'timeout',
  'cancelled',
]);

const WARN_CLASSES: ReadonlySet<ErrorClass> = new Set<ErrorClass>([
  'auth_expired',
  'forbidden',
  'not_found',
  'rate_limited',
  'timeout',
  'upstream_5xx',
  'bad_request',
  'conflict',
  'unavailable',
]);

type Indexed = Record<string, unknown>;

function classifyStatus(status: number): ErrorClass {
  if (!Number.isInteger(status) || status < 400) return 'internal';
  switch (status) {
    case 401:
      return 'auth_expired';
    case 403:
      return 'forbidden';
    case 404:
    case 410:
      return 'not_found';
    case 408:
    case 504:
      return 'timeout';
    case 409:
      return 'conflict';
    case 429:
      return 'rate_limited';
    case 503:
      return 'unavailable';
    default:
      return status < 500 ? 'bad_request' : 'upstream_5xx';
  }
}

function statusOf(link: Indexed): number | undefined {
  if (link instanceof BaseError) return link.statusCode;
  const responseStatus = (link.response as Indexed | undefined)?.status;
  for (const value of [responseStatus, link.status, link.statusCode]) {
    if (typeof value === 'number' && Number.isInteger(value)) return value;
  }
  return undefined;
}

/** The class one link of a cause chain decides, or undefined to look at its cause. */
function linkClass(link: Indexed): ErrorClass | undefined {
  if (
    link.name === 'AbortError' ||
    link.name === 'CanceledError' ||
    link.code === 'ERR_CANCELED'
  ) {
    return 'cancelled';
  }
  const unanswered = unansweredKind(link);
  if (unanswered !== null) return unanswered;
  if (BUG_TYPES.some((type) => link instanceof type)) {
    return link.cause === undefined ? 'internal' : undefined;
  }
  const status = statusOf(link);
  // Our own 500 says nothing about why; its cause may.
  if (status === undefined || (link instanceof BaseError && status === 500)) {
    return undefined;
  }
  return classifyStatus(status);
}

function classifyChain(error: unknown): ErrorClass {
  const seen = new Set<unknown>();
  let link = error;
  for (let depth = 0; depth < MAX_CAUSE_DEPTH; depth += 1) {
    if (link === null || typeof link !== 'object' || seen.has(link)) break;
    seen.add(link);
    const decided = linkClass(link as Indexed);
    if (decided !== undefined) return decided;
    link = (link as Indexed).cause;
  }
  return 'internal';
}

/**
 * The class of a status (401 auth_expired, 403 forbidden, 404/410 not_found,
 * 408/504 timeout, 409 conflict, 429 rate_limited, other 4xx bad_request, 503
 * unavailable, other 5xx upstream_5xx; anything below 400 internal) or of an
 * error, walking its `cause` chain (outermost first, cycle-safe). Never throws.
 */
export function classifyError(errorOrStatus: unknown): ErrorClass {
  try {
    return typeof errorOrStatus === 'number'
      ? classifyStatus(errorOrStatus)
      : classifyChain(errorOrStatus);
  } catch {
    return 'internal';
  }
}

/**
 * The class of a request that failed with `error` and was answered
 * `answeredStatus`. It always agrees with that status: a 500 (or a failure
 * after a 2xx was sent) is ours (`internal`) unless our own code wrapped an
 * upstream failure on purpose (a BaseError with that cause). A driver or
 * library error nobody handled stays `internal`, even when a refused
 * connection sits in its chain (our own database down): it needs a human.
 * Any other status decides by itself.
 */
export function classifyRequestError(
  error: unknown,
  answeredStatus: number,
): ErrorClass {
  const fromError = classifyError(error);
  if (answeredStatus === 500 || answeredStatus < 400) {
    return error instanceof BaseError && UPSTREAM_CLASSES.has(fromError)
      ? fromError
      : 'internal';
  }
  return fromError === 'cancelled'
    ? 'cancelled'
    : classifyStatus(answeredStatus);
}

/** The level policy: our fault ERROR, cancelled INFO, every other class WARN; unknown ERROR. */
export function levelFor(errorClass: ErrorClass): 'error' | 'warn' | 'info' {
  if (errorClass === 'cancelled') return 'info';
  return WARN_CLASSES.has(errorClass) ? 'warn' : 'error';
}
