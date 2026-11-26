import express, { RequestHandler } from 'express';
import { hardenedProfile } from '../utils/security-profile';

/**
 * SEC-C-01: the routes that take a person's typed text parse JSON at a bound sized to it, below the app's shared
 * 10 MB. A message is at most 100 000 characters, at most 600 KB as escaped JSON; 2 MB leaves room for the rest
 * of a turn. Mounted before the shared parser, which skips a body an earlier parser has read.
 */
export const PERSON_TEXT_JSON_LIMIT = '2mb';
/** The shared bound, which these routes had before SEC-C-01: the compatibility profile keeps it (ADR-OPS-07). */
export const COMPAT_PERSON_TEXT_JSON_LIMIT = '10mb';

/** Chat and agent conversations and the harness's chat and agent runs: every route that takes a person's text. */
export const PERSON_TEXT_ROUTES: readonly (string | RegExp)[] = [
  '/api/v1/conversations',
  /^\/api\/v1\/agents\/[^/]+\/(?:conversations|runs)(?=\/|$)/,
];

export function personTextJsonBody(
  env: NodeJS.ProcessEnv = process.env,
): RequestHandler {
  return express.json({
    limit: hardenedProfile(env)
      ? PERSON_TEXT_JSON_LIMIT
      : COMPAT_PERSON_TEXT_JSON_LIMIT,
  });
}
