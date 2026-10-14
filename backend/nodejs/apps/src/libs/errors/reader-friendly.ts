import { HttpError, InternalServerError } from './http.errors';

/**
 * Whether a 5xx message may be repeated to the person who made the request.
 *
 * The rule is an allowlist, not a filter: a service's own words are kept only
 * when we know a person wrote them, because guessing from shape lets anything
 * unlisted through — `connector-service connection refused at 10.0.0.4` reads
 * like a sentence and still describes our topology. Everything else is replaced
 * with a plain line and logged.
 *
 * Two ways to be on the list:
 *
 * - we built the error ourselves and marked it as we created it;
 * - a message from another service matches one we know was written for a
 *   reader, named below.
 */

/**
 * Errors whose message this codebase wrote for the person who made the request.
 * A set rather than a flag on the error: nothing arriving from another service
 * can put itself in here, and it keeps the marker out of the metadata we echo
 * back in development.
 */
const clientSafeErrors = new WeakSet<Error>();

/** Records that we wrote this error's message for the reader. */
export const markClientSafe = <T extends Error>(error: T): T => {
  clientSafeErrors.add(error);
  return error;
};

/** True when we built this error and wrote its message for the reader. */
export const isClientSafeError = (error: Error): boolean =>
  clientSafeErrors.has(error);

/**
 * Messages another PipesHub service sends that were written for a reader.
 * Each entry names where it comes from, so a wording change there shows up as
 * a failing test here rather than as a mystery.
 */
const READER_WRITTEN_MESSAGES: ReadonlySet<string> = new Set([
  // backend/python/app/api/middlewares/auth.py — the 503 when Node can't be
  // reached to check an access token.
  "We couldn't confirm your sign-in just now. Please try again in a few seconds.",
  // backend/python/app/modules/retrieval/retrieval_service.py
  // (PERMISSION_CHECK_UNAVAILABLE_MESSAGE) — the 503 when search cannot read
  // which documents the user may see, and shows none.
  "We couldn't check which documents you have access to just now, so no results are shown. Please try again in a minute.",
]);

const normalise = (text: string): string => text.trim().replace(/\s+/g, ' ');

/**
 * True when this exact message is one a person wrote. An unknown message is
 * not client-safe, however readable it looks.
 */
export const isReaderWritten = (text: string | undefined): boolean =>
  typeof text === 'string' && READER_WRITTEN_MESSAGES.has(normalise(text));

/**
 * What a reader is told when something failed on PipesHub's side. The failure's
 * own words describe the machine that broke, so they go to the log and this
 * goes to the person. ``operation`` finishes "tried to ..." — "create the
 * organisation", "send the sign-in code".
 */
export const serverFailureMessage = (operation: string): string => {
  const what = /^[A-Z][a-z]/.test(operation)
    ? operation.charAt(0).toLowerCase() + operation.slice(1)
    : operation;
  return `Something went wrong while PipesHub tried to ${what}. Please try again in a moment; if it keeps happening, ask your admin to check the services page.`;
};

/**
 * Keeps the wording of an error this code raised on purpose.
 *
 * A guard clause inside a `try` — "this list is empty", "that name is taken" —
 * is caught by the same `catch` that handles a database going away, and would
 * otherwise be flattened into the generic sentence. The status stays 500
 * because these are internal calls whose callers were built around that.
 */
export const keepDeliberateWording = (error: HttpError): InternalServerError =>
  markClientSafe(new InternalServerError(error.message));
