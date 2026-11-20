/**
 * What a regular (`jwtSecret`) token was issued for, in its `tokenClass` claim:
 *
 * - `session`: an interactive sign-in to the app, minted only by `authJwtGenerator` (every sign-in and the refresh);
 * - `run`: a token handed to a run: the stand-in a Slack or service turn is given, and the copy of the caller's
 *   session that every call to the AI backend carries in place of the session itself.
 *
 * OAuth access tokens and personal access tokens are told apart by the auth middleware (`req.user.isOAuth`).
 * A token without the claim was minted by some other path, or before the claim existed: never an app session.
 */
export const TOKEN_CLASS_CLAIM = 'tokenClass';

export const TOKEN_CLASS = {
  session: 'session',
  run: 'run',
} as const;

export type TokenClass = (typeof TOKEN_CLASS)[keyof typeof TOKEN_CLASS];

/** Whether an authenticated request's user is the app's own interactive session, and nothing a run or a client holds. */
export const isAppSession = (user: Record<string, unknown> | undefined): boolean =>
  user !== undefined &&
  user.isOAuth !== true &&
  user[TOKEN_CLASS_CLAIM] === TOKEN_CLASS.session;

/** Gives the run's copy of an app session token, or undefined for any other bearer. */
export type RunTokenMinter = (bearer: string) => string | undefined;

let minter: RunTokenMinter | undefined;

/** Set once at start, where the signing secret is known. */
export const useRunTokenMinter = (next: RunTokenMinter | undefined): void => {
  minter = next;
};

/**
 * `headers` (lower-case names) with an app session's bearer swapped for the run's copy, so a run never holds a token
 * that passes for the app: the same claims and lifetime, marked `run`. Any other bearer is left as it is.
 */
export function withRunBearer(headers: Record<string, string>): Record<string, string> {
  const authorization = headers.authorization;
  if (!minter || typeof authorization !== 'string' || !authorization.startsWith('Bearer ')) {
    return headers;
  }
  const run = minter(authorization.slice('Bearer '.length));
  return run === undefined ? headers : { ...headers, authorization: `Bearer ${run}` };
}
