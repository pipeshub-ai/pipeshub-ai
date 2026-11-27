import express, {
  ErrorRequestHandler,
  NextFunction,
  Request,
  RequestHandler,
  Response,
} from 'express';
import { forOrg } from '../../../libs/context/orgContext';
import { HttpError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import { AttemptClaims } from '../domain/authority';
import { HarnessCredentialRole } from '../domain/credential-roles';
import {
  HarnessUnauthorizedError,
  HarnessUnauthorizedReason,
} from '../domain/errors';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';

/** Set by `requireAttemptToken`; the only source of org and worker authority after a claim. */
export interface HarnessAttemptRequest extends Request {
  harnessAttempt?: AttemptClaims;
}

const BEARER = /^Bearer\s+(\S+)$/i;

/**
 * How an internal route authenticates, carried by its first handler (C-26).
 * `run-token`: the handler's service verifies the run's gateway token (or the
 * run's attempt token) itself.
 */
export type HarnessRouteGuard =
  'worker-credential' | 'attempt-token' | 'run-token';

export const HARNESS_ROUTE_GUARD: unique symbol = Symbol.for(
  'pipeshub.agentHarness.routeGuard',
);

/** SEC-C-10: the machine roles a `worker-credential` route admits, carried like its guard. */
export const HARNESS_ROUTE_ROLES: unique symbol = Symbol.for(
  'pipeshub.agentHarness.routeRoles',
);

type GuardedHandler = RequestHandler & {
  [HARNESS_ROUTE_GUARD]?: HarnessRouteGuard;
  [HARNESS_ROUTE_ROLES]?: readonly HarnessCredentialRole[];
};

function guarded<T extends RequestHandler>(
  handler: T,
  guard: HarnessRouteGuard,
): T {
  (handler as GuardedHandler)[HARNESS_ROUTE_GUARD] = guard;
  return handler;
}

export function routeGuardOf(handler: unknown): HarnessRouteGuard | undefined {
  return typeof handler === 'function'
    ? (handler as GuardedHandler)[HARNESS_ROUTE_GUARD]
    : undefined;
}

export function routeRolesOf(
  handler: unknown,
): readonly HarnessCredentialRole[] | undefined {
  return typeof handler === 'function'
    ? (handler as GuardedHandler)[HARNESS_ROUTE_ROLES]
    : undefined;
}

/** A handler whose service verifies the caller's bearer token itself. */
export function selfAuthenticated<T extends RequestHandler>(
  guard: 'run-token',
  handler: T,
): T {
  return guarded(handler, guard);
}

export function bearer(req: Request): string | undefined {
  const header = req.headers.authorization;
  if (typeof header !== 'string') return undefined;
  return BEARER.exec(header.trim())?.[1];
}

/** The refusal's reason on its 401, for `countTokenRefusals`; a symbol, so the answer's body is unchanged. */
const REFUSAL_REASON: unique symbol = Symbol.for(
  'pipeshub.agentHarness.refusalReason',
);

type RefusalHttpError = HttpError & {
  [REFUSAL_REASON]?: HarnessUnauthorizedReason;
};

export function harnessUnauthorized(
  error: HarnessUnauthorizedError,
): HttpError {
  const code =
    error.reason === 'credential'
      ? 'AGENT_HARNESS_WORKER_CREDENTIAL'
      : `AGENT_HARNESS_ATTEMPT_${error.reason.toUpperCase()}`;
  const answer: RefusalHttpError = new HttpError(
    code,
    error.message,
    HTTP_STATUS.UNAUTHORIZED,
  );
  answer[REFUSAL_REASON] = error.reason;
  return answer;
}

const GUARD_KINDS: Readonly<Record<HarnessRouteGuard, string>> = {
  'worker-credential': 'machine_credential',
  'attempt-token': 'attempt_token',
  'run-token': 'gateway_token',
};

/** The credential the matched route asks for, read from its first guarded handler (C-26). */
function refusedKind(req: Request, reason: HarnessUnauthorizedReason): string {
  if (reason === 'credential') return 'machine_credential';
  const stack = (
    req.route as { stack?: Array<{ handle?: unknown }> } | undefined
  )?.stack;
  const guard = stack
    ?.map((layer) => routeGuardOf(layer.handle))
    .find((found) => found !== undefined);
  return guard === undefined ? 'other' : GUARD_KINDS[guard];
}

/**
 * SEC-D-13: every refusal the internal listener answers, counted once by kind and reason, where it leaves: each
 * guard, controller and service that refuses reaches the client through `harnessUnauthorized` and `next(error)`.
 */
export function countTokenRefusals(
  record: (kind: string, reason: string) => void,
): ErrorRequestHandler {
  return (error: unknown, req: Request, _res: Response, next: NextFunction) => {
    try {
      const reason =
        error instanceof HarnessUnauthorizedError
          ? error.reason
          : (error as RefusalHttpError | null)?.[REFUSAL_REASON];
      if (reason !== undefined) record(refusedKind(req, reason), reason);
    } catch {
      // The refusal is answered whether or not it could be counted.
    }
    next(error);
  };
}

/** An authority refusal, logged with its reason and route; never the token. */
export function logAuthorityRefusal(
  logger: HarnessLogger,
  req: Request,
  error: HarnessUnauthorizedError,
): void {
  const runId = req.params['runId'];
  const pattern: unknown = (req.route as { path?: unknown } | undefined)?.path;
  logger.warn(
    'authority.refused',
    harnessLogMeta('authority.refused', {
      reason: error.reason,
      method: req.method,
      route: `${req.baseUrl}${typeof pattern === 'string' ? pattern : req.path}`,
      runId: typeof runId === 'string' ? runId : undefined,
    }),
  );
}

const authFailure = (
  error: unknown,
  req: Request,
  logger: HarnessLogger,
): unknown => {
  if (!(error instanceof HarnessUnauthorizedError)) return error;
  try {
    logAuthorityRefusal(logger, req, error);
  } catch {
    // A logger that throws must not leave the request unanswered (and uncounted).
  }
  return harnessUnauthorized(error);
};

/** Claim: the per-deployment worker credential, never a scoped JWT. */
export function requireWorkerCredential(
  authority: HarnessAuthority,
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  return requireRoleCredential(authority, ['worker'], logger);
}

/**
 * SEC-C-10: a machine credential of one of `roles` (ADR-CRED-01). Any other role's credential is refused like
 * a wrong one, so the scraper's never claims a run and the egress proxy's never reads the live layer.
 */
export function requireRoleCredential(
  authority: HarnessAuthority,
  roles: readonly HarnessCredentialRole[],
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  // The worker's own guard asks for the worker credential alone, as it always has.
  const verify = (credential: string | undefined): Promise<unknown> =>
    roles.length === 1 && roles[0] === 'worker'
      ? authority.verifyWorker(credential)
      : authority.verifyRole(roles, credential);
  const handler = guarded(
    (req: Request, _res: Response, next: NextFunction): void => {
      verify(bearer(req)).then(
        () => {
          next();
        },
        (error: unknown) => {
          next(authFailure(error, req, logger));
        },
      );
    },
    'worker-credential',
  );
  (handler as GuardedHandler)[HARNESS_ROUTE_ROLES] = [...roles];
  return handler;
}

/**
 * Every post-claim route: a Node-issued attempt token for the path's run. The rest of the request runs as the org the
 * token's verified claims name (the claimed run's), never one the request names.
 */
export function requireAttemptToken(
  authority: HarnessAuthority,
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  return guarded((req: Request, _res: Response, next: NextFunction): void => {
    authority.verifyAttemptToken(bearer(req), String(req.params['runId'])).then(
      (claims) => {
        (req as HarnessAttemptRequest).harnessAttempt = claims;
        if (typeof claims.orgId !== 'string' || claims.orgId === '') {
          next(
            harnessUnauthorized(
              new HarnessUnauthorizedError(
                'claims',
                'Attempt token names no org',
              ),
            ),
          );
          return;
        }
        forOrg(claims.orgId, 'agent-harness.attempt', next);
      },
      (error: unknown) => {
        next(authFailure(error, req, logger));
      },
    );
  }, 'attempt-token');
}

/**
 * The work of a worker-credential route that names the org of its run (a claim by name, the grant auditor, the
 * egress proxy's token, the live layer). The credential is the deployment's and holds every org (claim-next hands it
 * any org's run), so the route runs as the org it names, and the store then finds the run only if that org holds it.
 * A user's credential or an attempt token never names its org: theirs is bound from the verified claims.
 */
export function asWorkerNamedOrg<T>(
  orgId: string,
  work: () => Promise<T>,
): Promise<T> {
  return forOrg(orgId, 'agent-harness.worker', work);
}

/**
 * SEC-C-09: the size of the JSON body each kind of internal route takes, parsed only after its guard. A worker's
 * journal commit is the largest (events and their payloads); a credential route takes a few fields; a gateway's
 * report is a few KiB.
 */
export const INTERNAL_JSON_LIMITS = {
  'worker-credential': '1mb',
  'attempt-token': '10mb',
  'run-token': '256kb',
  edition: '1mb',
} as const;

const jsonBody = (limit: string): RequestHandler => express.json({ limit });

/** A guard, then its route's JSON parser: nothing reads a body before the caller is verified. */
export function thenJson(
  guard: RequestHandler,
  kind: Exclude<HarnessRouteGuard, 'run-token'>,
): RequestHandler[] {
  return [guard, jsonBody(INTERNAL_JSON_LIMITS[kind])];
}

/**
 * A self-authenticating route's handlers: its service verifies the run's token, which needs the body, so the
 * body is parsed only when a bearer is there at all, and at the small size a gateway report has. An anonymous
 * request is refused before a byte is read.
 */
export function runTokenRoute(handler: RequestHandler): RequestHandler[] {
  const present = selfAuthenticated(
    'run-token',
    (req: Request, _res: Response, next: NextFunction): void => {
      next(
        bearer(req) === undefined
          ? harnessUnauthorized(
              new HarnessUnauthorizedError(
                'malformed',
                'A run token is required',
              ),
            )
          : undefined,
      );
    },
  );
  return [present, jsonBody(INTERNAL_JSON_LIMITS['run-token']), handler];
}

/**
 * In front of an edition's internal routes, which check their own credential: a body is parsed only for a
 * request that carries a bearer, so an anonymous one reaches its route's guard unread.
 */
export function editionJsonBodies(): RequestHandler {
  const parse = jsonBody(INTERNAL_JSON_LIMITS.edition);
  return (req: Request, res: Response, next: NextFunction): void => {
    if (bearer(req) === undefined) {
      next();
      return;
    }
    void parse(req, res, next);
  };
}
