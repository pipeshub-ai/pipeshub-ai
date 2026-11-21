import { Router, type Request, type RequestHandler } from 'express';
import jwt from 'jsonwebtoken';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { stripTokenDisplayPrefix } from '../../oauth_provider/constants/constants';
import {
  agentHarnessSwitchedOn,
  orgHarnessOn,
  type AgentHarnessEnvironment,
} from '../config';

/** The harness's own route namespace: every path under it is a harness route. */
export const AGENT_HARNESS_API_PREFIX = '/api/v1/agent-harness';

export const AGENT_HARNESS_NOT_ENABLED =
  'The agent harness is not enabled on this install.';

export const AGENT_HARNESS_NOT_ON_FOR_ORG =
  'The agent harness is not turned on for this organization.';

type OrgHarness = (
  orgId: string,
  env: AgentHarnessEnvironment,
) => Promise<boolean>;

const skip: RequestHandler = (_req, _res, next) => {
  next();
};

/**
 * The org a request's bearer token names, read without verifying it. It only chooses which router answers: the
 * harness's routes and the classic ones each authenticate the request themselves, so a token that names another
 * org is refused by whichever router it reaches.
 */
export function claimedOrgId(req: Request): string | null {
  const header = req.headers.authorization;
  if (typeof header !== 'string') return null;
  const [scheme, token] = header.split(' ');
  if (scheme !== 'Bearer' || !token) return null;
  const claims = jwt.decode(stripTokenDisplayPrefix(token));
  if (claims === null || typeof claims !== 'object') return null;
  const orgId = (claims as Record<string, unknown>)['orgId'];
  return typeof orgId === 'string' && orgId !== '' ? orgId : null;
}

/**
 * Mounts a harness route family where the agent harness is on for the request's org (ADR-OPS-08). Elsewhere, and on
 * an install with the switch off (ADR-OPS-06), its requests fall through to the routes a release without the harness
 * has (a family nested in a classic prefix, such as `/api/v1/conversations/:id/runs`, reaches the classic router). A
 * request that names no org reaches the family, whose own authentication answers it.
 */
export const whenHarnessSwitchedOn = (
  router: RequestHandler,
  env: AgentHarnessEnvironment = process.env,
  harnessOn: OrgHarness = orgHarnessOn,
): RequestHandler => {
  if (!agentHarnessSwitchedOn(env)) return skip;
  const gated = Router();
  // `next('router')` leaves this router, so the request reaches what is mounted after it, as with the switch off.
  gated.use((req, _res, next) => {
    const orgId = claimedOrgId(req);
    if (orgId === null) {
      next();
      return;
    }
    harnessOn(orgId, env).then((on) => {
      next(on ? undefined : 'router');
    }, next);
  });
  gated.use(router);
  return gated;
};

/**
 * Every path in the harness's own namespace answers a JSON 404 that says why where the harness is off, before the
 * web app's SPA fallback could answer a GET with the page's HTML: an API client reads one plain answer, not a page.
 * Off on the install, every request; on, a request whose org has the harness off.
 */
export const harnessNamespaceWhileOff = (
  env: AgentHarnessEnvironment = process.env,
  harnessOn: OrgHarness = orgHarnessOn,
): RequestHandler => {
  if (!agentHarnessSwitchedOn(env)) {
    return (_req, _res, next) => {
      next(new NotFoundError(AGENT_HARNESS_NOT_ENABLED));
    };
  }
  return (req, _res, next) => {
    const orgId = claimedOrgId(req);
    if (orgId === null) {
      next();
      return;
    }
    harnessOn(orgId, env).then((on) => {
      next(on ? undefined : new NotFoundError(AGENT_HARNESS_NOT_ON_FOR_ORG));
    }, next);
  };
};
