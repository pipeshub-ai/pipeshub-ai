import { Request } from 'express';

/**
 * The admin UI's "show secrets" button refetches a config with `?reveal=true`.
 * OAuth-app tokens are refused: reveal is for an admin looking at their own
 * settings page, not for a delegated client holding `config:read`.
 */
export function isSecretRevealRequested(req: Request): boolean {
  const user = (req as (Request & { user?: { isOAuth?: boolean } }) | undefined)?.user;
  return req?.query?.reveal === 'true' && user?.isOAuth !== true;
}

/**
 * True when the caller holds a PAT or an OAuth access token rather than an
 * interactive session. Sign-in settings are never shown to such a caller, even
 * on an install that turned secret masking off for its own admins.
 */
export function isDelegatedClient(req: Request): boolean {
  const user = (req as (Request & { user?: { isOAuth?: boolean } }) | undefined)
    ?.user;
  return user?.isOAuth === true;
}

/**
 * Query string for a gateway call to a Python service. The gateway only passes
 * the request on; the service decides whether to honour it.
 */
export function revealQuery(req: Request): string {
  return isSecretRevealRequested(req) ? '?reveal=true' : '';
}

/** This edition can only ever hold one org, so there is no other tenant to leak to. */
export function isSecretRevealAvailable(): boolean {
  return true;
}

export function canRevealSecrets(req: Request): boolean {
  return isSecretRevealAvailable() && isSecretRevealRequested(req);
}
