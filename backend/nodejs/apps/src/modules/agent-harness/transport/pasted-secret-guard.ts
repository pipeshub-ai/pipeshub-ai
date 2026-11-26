import type { NextFunction, Request, RequestHandler, Response } from 'express';

import {
  BadRequestError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { CHAT_QUERY_MAX_CHARS } from '../domain/admission';
import {
  PastedSecretScanTimeout,
  redactPastedSecrets,
} from '../domain/pasted-secrets';

/** Names the kinds a request's text lost, never a value, so a client can say what was removed. */
export const SECRETS_REMOVED_HEADER = 'x-pipeshub-secrets-removed';

/**
 * Body fields a person types into: a chat message or steer, a denial's
 * reason, and a rating's comments (an object of texts, G-15's feedback).
 */
export const PERSON_TEXT_FIELDS: readonly string[] = [
  'query',
  'reason',
  'comments',
];

/** The longest text the guard scans: a chat message's own bound, so a valid message is never refused here. */
export const PERSON_TEXT_MAX_CHARS = CHAT_QUERY_MAX_CHARS;

/**
 * One request's whole scan (SEC-C-01). A 100 000-character message takes a few milliseconds on the linear
 * patterns; the budget only stops a pattern that regresses from holding the event loop for every tenant.
 */
export const PERSON_TEXT_SCAN_BUDGET_MS = 1_000;

// 400, as the schema refuses an over-long message, so a steer and a new turn are refused alike (C-21c).
const tooLong = (): BadRequestError =>
  new BadRequestError(
    `This message is longer than ${PERSON_TEXT_MAX_CHARS.toLocaleString('en-US')} characters. Shorten it, or attach the text as a file, and send it again.`,
  );

const scanTimedOut = (): ServiceUnavailableError =>
  new ServiceUnavailableError(
    'The message could not be checked for pasted passwords or keys in time. Send it again in a moment.',
  );

/**
 * SEC-CR-07: a secret a person pastes is removed before the request reaches
 * validation (which may log values), the /command expander, the durable
 * admission and the legacy controllers, so no copy of it is kept: not the
 * conversation, the title, the run input, the journal, logs or exports.
 * The composer warns first and offers the credential store; this is the
 * enforcement. Mount it only after authentication (`scanAfterAuthentication`):
 * an anonymous request never reaches the scan (SEC-C-01).
 */
export function removePastedSecrets(
  fields: readonly string[] = PERSON_TEXT_FIELDS,
  budgetMs: number = PERSON_TEXT_SCAN_BUDGET_MS,
): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    const raw: unknown = req.body;
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
      next();
      return;
    }
    const body = raw as Record<string, unknown>;
    const texts: Array<{
      holder: Record<string, unknown>;
      key: string;
      text: string;
    }> = [];
    for (const field of fields) {
      const value = body[field];
      if (typeof value === 'string') {
        texts.push({ holder: body, key: field, text: value });
        continue;
      }
      // One level of texts (`comments.negative`), never deeper: a person's fields are flat.
      if (value === null || typeof value !== 'object' || Array.isArray(value))
        continue;
      const nested = value as Record<string, unknown>;
      for (const [key, text] of Object.entries(nested)) {
        if (typeof text === 'string') texts.push({ holder: nested, key, text });
      }
    }
    // Every length is checked before any scan, so an over-long field costs nothing to refuse.
    if (texts.some(({ text }) => text.length > PERSON_TEXT_MAX_CHARS)) {
      next(tooLong());
      return;
    }
    const budget = { deadline: performance.now() + budgetMs };
    const kinds: string[] = [];
    try {
      for (const { holder, key, text } of texts) {
        const redacted = redactPastedSecrets(text, undefined, budget);
        if (redacted.kinds.length === 0) continue;
        holder[key] = redacted.text;
        kinds.push(...redacted.kinds);
      }
    } catch (error) {
      next(error instanceof PastedSecretScanTimeout ? scanTimedOut() : error);
      return;
    }
    if (kinds.length > 0 && !res.headersSent) {
      res.setHeader(SECRETS_REMOVED_HEADER, [...new Set(kinds)].join(','));
    }
    next();
  };
}

/** The authenticators a router that takes a person's text uses; each runs the pasted-secret guard after it. */
export type TextGuardedAuth = Pick<
  AuthMiddleware,
  'authenticate' | 'scopedTokenValidator'
>;

/** Marks a handler that authenticates and then scans, so a structural test can find a route without one. */
export const SCANS_AFTER_AUTH = Symbol('scansPastedSecretsAfterAuthentication');

const thenScan = (
  authenticator: RequestHandler,
  guard: RequestHandler,
): RequestHandler => {
  const handler: RequestHandler = (req, res, next) => {
    let called = false;
    const after = (error?: unknown): void => {
      // An authenticator that calls next twice must not scan (or continue) twice.
      if (called) return;
      called = true;
      if (error !== undefined && error !== null) {
        next(error);
        return;
      }
      void guard(req, res, next);
    };
    return authenticator(req, res, after);
  };
  Object.defineProperty(handler, SCANS_AFTER_AUTH, { value: true });
  return handler;
};

/**
 * SEC-C-01: the routers that take a person's text authenticate with this instead of the raw middleware, so
 * the pasted-secret scan runs on every route right after its authenticator and never on an anonymous body.
 */
export function scanAfterAuthentication(
  auth: TextGuardedAuth,
  guard: RequestHandler = removePastedSecrets(),
): TextGuardedAuth {
  const authenticate = thenScan(
    auth.authenticate.bind(auth) as RequestHandler,
    guard,
  );
  return {
    authenticate: authenticate as AuthMiddleware['authenticate'],
    scopedTokenValidator: (scope: string, audience?: string) =>
      thenScan(auth.scopedTokenValidator(scope, audience), guard),
  };
}
