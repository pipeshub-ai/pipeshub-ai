import { Request } from 'express';
import { isNativeId } from '../../../libs/repositories/ids';
import { PAT_APP_CLIENT_ID_PREFIX } from '../../oauth_provider/constants/constants';
import type { PrincipalKind } from '../domain/principal';
import { RunVia } from '../domain/run-spec';

export const identityString = (value: unknown): string | null => {
  if (typeof value === 'string' && value.length > 0) return value;
  if (isNativeId(value)) return String(value);
  return null;
};

/**
 * Whether the verified credential is an app or a personal access token
 * limited to some knowledge (main #4451). Python enforces that limit on every
 * request the token makes; a durable run does its work later without the
 * token, so it could not hold the run to the limit. Such a credential never
 * starts one: a chat turn is answered by today's loop, and the harness's own
 * routes refuse it.
 */
export function knowledgeLimited(req: Request): boolean {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  const limit = user?.['knowledgeLimit'];
  return typeof limit === 'object' && limit !== null;
}

export const KNOWLEDGE_LIMITED_MESSAGE =
  'This token is limited to some of your knowledge, and durable runs cannot hold to that limit yet. Use a token without a knowledge limit.';

/**
 * How the caller authenticated (x.pipeshub `via`, audited), read only from
 * what the auth middleware verified: a scoped service token (the Slack bot)
 * or a service-account identity is `service`, an OAuth access token is `pat`
 * or `oauth_app` by its client, anything else a user session. The body is
 * never consulted.
 */
export function requestVia(req: Request): RunVia {
  const verified = req as Request & {
    tokenPayload?: unknown;
    user?: Record<string, unknown>;
  };
  const user = verified.user;
  if (
    (verified.tokenPayload !== undefined && verified.tokenPayload !== null) ||
    user?.['isServiceAccount'] === true
  ) {
    return 'service';
  }
  if (user?.['isOAuth'] !== true) return 'session';
  const clientId = user['oauthClientId'];
  return typeof clientId === 'string' &&
    clientId.startsWith(PAT_APP_CLIENT_ID_PREFIX)
    ? 'pat'
    : 'oauth_app';
}

/**
 * Who a run acts as (OWD-2), read only from what the auth middleware
 * verified: a service-account identity (an agent whose service account
 * answers people who reach it, such as through Slack) is that agent's
 * service account; anyone else, the person. The body is never consulted.
 */
export function requestPrincipalKind(req: Request): PrincipalKind {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  return user?.['isServiceAccount'] === true ? 'agent_service' : 'user';
}
