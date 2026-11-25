import { NextFunction, Request, Response, Router } from 'express';
import type { Container, interfaces } from 'inversify';
import { z, type ZodType } from 'zod';
import type { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  BadRequestError,
  ForbiddenError,
} from '../../../libs/errors/http.errors';
import { ValidationError } from '../../../libs/errors/validation.error';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationUtils } from '../../../libs/utils/validation.utils';
import { ADMIN_ACCESS_REQUIRED_MESSAGE } from '../../user_management/services/user-admin.service';
import type { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import type { Problem } from '../contracts/generated';
import {
  ControlPlaneError,
  ControlPlaneRevisionConflictError,
  CpProblemError,
  CpRefusal,
} from '../domain/errors';
import { problem } from '../domain/problem';
import {
  ApprovalStateConflict,
  InvalidObject,
  ObjectExists,
  ObjectNotFound,
  ObjectTooLarge,
  WrongOrg,
} from '../domain/store-errors';
import type {
  Authorizer,
  AuthzActor,
  CpAction,
  ResourceRef,
} from '../ports/authorizer';
import { CONTROL_PLANE_AUTHORIZER } from './admin-keys';
import { getCurrentOrgId } from '../../../libs/context/orgContext';

export const CONTROL_PLANE_API_PREFIX = '/api/v1/control-plane';

export interface CpRequestContext<P, Q, B> {
  /** Both ids from the token, never from the request. */
  readonly actor: AuthzActor;
  readonly params: P;
  readonly query: Q;
  readonly body: B;
  /** The `If-Match` revision; null when absent (a route whose revision is optional). */
  readonly ifMatch: number | null;
  readonly requestId: string;
  /** A bound dependency; an unbound one is `CP_UNAVAILABLE` (the deployment did not compose it). */
  resolve<T>(key: interfaces.ServiceIdentifier<T>): T;
  /** Throws `CP_FORBIDDEN` unless the Authorizer allows; for checks that need a loaded object. */
  authorize(
    action: CpAction,
    resource: Omit<ResourceRef, 'orgId'>,
  ): Promise<void>;
}

export interface CpReply<R> {
  readonly status?: number;
  readonly body: R;
  /** Sent as `ETag: "<revision>"`. */
  readonly etag?: number | null;
}

export type CpAuthorization =
  | { readonly action: CpAction; readonly resource: Omit<ResourceRef, 'orgId'> }
  /** The handler checks what it loads (`ctx.authorize`), or acts on the caller's own objects only. */
  | 'in-handler';

type Schema<T> = ZodType<T>;

export interface CpRouteSpec<
  P = unknown,
  Q = unknown,
  B = unknown,
  R = unknown,
> {
  /** The row of the design's route table (`R5`), named by tests. */
  readonly id: string;
  readonly method: 'get' | 'post' | 'put' | 'patch' | 'delete';
  /** Relative to `/api/v1/control-plane`. */
  readonly path: string;
  /** OAuth scopes (any one); a user JWT passes, and the Authorizer is the gate. */
  readonly scopes: readonly OAuthScopeNames[];
  readonly params?: Schema<P>;
  readonly query?: Schema<Q>;
  readonly body?: Schema<B>;
  /** `required`: absent `If-Match` is 428. `create`: absent means "does not exist yet" (revision 0). */
  readonly revision?: 'required' | 'optional' | 'create';
  /** Writes are audited in their service's control-plane transaction (C-32). */
  readonly audited?: boolean;
  /**
   * A facade over a route that existed before the control plane (slice B):
   * its refusals keep today's bodies — a malformed request is the platform's
   * `ValidationError`, a denial its admin-access `ForbiddenError`, a bad
   * `If-Match` a `BadRequestError` — and `error` maps the handler's own
   * failures (it may set headers, as a conflict's `ETag`).
   */
  readonly legacy?: { error(error: unknown, res: Response): unknown };
  authorize(ctx: CpRequestContext<P, Q, B>): CpAuthorization;
  handle(ctx: CpRequestContext<P, Q, B>): Promise<CpReply<R>>;
}

/** A spec with its types erased, as the table holds it. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- the table is heterogeneous; each spec is typed where it is declared.
export type AnyCpRouteSpec = CpRouteSpec<any, any, any, any>;

export const cpRoute = <P, Q, B, R>(
  spec: CpRouteSpec<P, Q, B, R>,
): CpRouteSpec<P, Q, B, R> => spec;

const empty = z.object({}).strict();

/** `"7"`, `W/"7"` or `7`; anything else is a malformed precondition. */
export function parseRevisionHeader(header: string): number {
  if (!/^(?:W\/)?"?[0-9]{1,15}"?$/.test(header.trim())) {
    throw new CpRefusal(
      'CP_INVALID_CONFIG',
      'If-Match must be the revision the editor read',
    );
  }
  const revision = Number(
    header.trim().replace(/^W\//, '').replace(/^"|"$/g, ''),
  );
  if (!Number.isInteger(revision) || revision < 0) {
    throw new CpRefusal(
      'CP_INVALID_CONFIG',
      'If-Match must be the revision the editor read',
    );
  }
  return revision;
}

function ifMatchOf(req: Request, mode: CpRouteSpec['revision']): number | null {
  const raw = req.header('if-match')?.trim();
  // `*` matches any revision: the same as sending none.
  const header = raw === '' || raw === '*' ? undefined : raw;
  if (header === undefined) {
    if (mode === 'required') {
      throw new CpRefusal(
        'CP_PRECONDITION_REQUIRED',
        'This change needs If-Match with the revision you read',
      );
    }
    return mode === 'create' ? 0 : null;
  }
  return parseRevisionHeader(header);
}

function parsed<T>(
  schema: Schema<T> | undefined,
  value: unknown,
  where: string,
  legacy: boolean,
): T {
  const result = (schema ?? (empty as unknown as Schema<T>)).safeParse(
    value ?? {},
  );
  if (result.success) return result.data;
  if (legacy) {
    // As `ValidationMiddleware` words it, paths under the part they came from.
    const errors = ValidationUtils.formatZodError(
      new z.ZodError(
        result.error.issues.map((i) => ({ ...i, path: [where, ...i.path] })),
      ),
    );
    throw new ValidationError(
      ValidationUtils.formatValidationErrorMessage(errors),
      errors,
    );
  }
  const issue = result.error.issues[0];
  const path = [where, ...(issue?.path ?? [])].join('.');
  throw new CpRefusal(
    'CP_INVALID_CONFIG',
    `${path}: ${issue?.message ?? 'invalid'}`,
  );
}

/** A store or domain refusal as the catalogue's code; null for anything else (a 500 that hides its cause). */
export function refusalOf(
  error: unknown,
): { problem: Problem; current?: string | null } | null {
  if (error instanceof CpProblemError) return { problem: error.problem };
  if (error instanceof CpRefusal) {
    return { problem: problem(error.code, error.message, error.fields) };
  }
  if (error instanceof ControlPlaneRevisionConflictError) {
    return {
      problem: problem(error.code, error.message),
      current: error.current,
    };
  }
  if (error instanceof ControlPlaneError) {
    return { problem: problem(error.code, error.message) };
  }
  if (error instanceof ObjectNotFound || error instanceof WrongOrg) {
    return { problem: problem('CP_NOT_FOUND', 'No such object') };
  }
  if (error instanceof ObjectExists || error instanceof ApprovalStateConflict) {
    return { problem: problem('CP_REVISION_CONFLICT', error.message) };
  }
  if (error instanceof ObjectTooLarge) {
    return { problem: problem('CP_EFFECTIVE_SET_TOO_LARGE', error.message) };
  }
  if (error instanceof InvalidObject) {
    return { problem: problem('CP_INVALID_CONFIG', error.message) };
  }
  return null;
}

/**
 * Every refusal is one body (D1): `error` keeps today's frontend extractor
 * working, `problem` is the generated `Problem` that `readProblem` accepts.
 */
function sendRefusal(
  res: Response,
  requestId: string,
  refusal: { problem: Problem; current?: string | null },
): void {
  const body = { ...refusal.problem, requestId };
  if (refusal.current != null) res.setHeader('ETag', `"${refusal.current}"`);
  res.setHeader('Cache-Control', 'no-store');
  res.status(body.status).json({
    error: { code: body.code, message: body.message, requestId },
    problem: body,
  });
}

function actorOf(req: AuthenticatedUserRequest): AuthzActor {
  const orgId: unknown = getCurrentOrgId();
  const userId: unknown = req.user?.userId;
  if (
    typeof orgId !== 'string' ||
    typeof userId !== 'string' ||
    orgId === '' ||
    userId === ''
  ) {
    throw new CpRefusal('CP_FORBIDDEN', 'This route needs a signed-in user');
  }
  return { orgId, userId };
}

function contextOf<P, Q, B>(
  container: Container,
  spec: CpRouteSpec<P, Q, B>,
  req: Request,
  requestId: string,
): CpRequestContext<P, Q, B> {
  const actor = actorOf(req as AuthenticatedUserRequest);
  const legacy = spec.legacy !== undefined;
  const resolve = <T>(key: interfaces.ServiceIdentifier<T>): T => {
    if (!container.isBound(key)) {
      // `not_configured`: a client hides the control rather than offering a retry that cannot work.
      throw new CpRefusal(
        'CP_UNAVAILABLE',
        'The control plane is not configured on this deployment',
        { action: 'not_configured' },
      );
    }
    return container.get<T>(key);
  };
  return {
    actor,
    params: parsed(spec.params, req.params, 'params', legacy),
    query: parsed(spec.query, req.query, 'query', legacy),
    body: parsed(spec.body, req.body, 'body', legacy),
    ifMatch: ifMatchOf(req, spec.revision),
    requestId,
    resolve,
    async authorize(action, resource) {
      const decision = await resolve<Authorizer>(
        CONTROL_PLANE_AUTHORIZER,
      ).check(actor, action, { ...resource, orgId: actor.orgId });
      if (!decision.allow) {
        throw new CpRefusal('CP_FORBIDDEN', 'You cannot do this here', {
          action: 'ask_admin',
        });
      }
    },
  };
}

/** A facade's refusal as the route answered it before the control plane. */
function legacyRefusal(error: unknown): unknown {
  if (!(error instanceof CpRefusal)) return error;
  if (error.code === 'CP_FORBIDDEN')
    return new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
  if (error.code === 'CP_INVALID_CONFIG') {
    return new BadRequestError(
      'If-Match must name one policy revision, as the ETag gave it',
    );
  }
  return error;
}

/** The control plane's admin API from its route table; mounted at `CONTROL_PLANE_API_PREFIX`. */
export function createCpRouter(
  container: Container,
  table: readonly AnyCpRouteSpec[],
): Router {
  return registerCpRoutes(Router(), container, table);
}

/**
 * Registers each route of `table` on `router` (CPD-11 §2): for every route,
 * in this order, `authenticate` → `requireScopes` → parse params, query and
 * body → `If-Match` → Authorizer (actor from the token only) → handler →
 * `ETag`. Objects are read in the caller's org only, so another org's id is
 * 404, never 403.
 */
export function registerCpRoutes(
  router: Router,
  container: Container,
  table: readonly AnyCpRouteSpec[],
): Router {
  const authenticate = (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> | void =>
    container
      .get<AuthMiddleware>('AuthMiddleware')
      .authenticate(req as AuthenticatedUserRequest, res, next);
  for (const spec of table) {
    router[spec.method](
      spec.path,
      authenticate,
      requireScopes(...spec.scopes),
      async (req: Request, res: Response, next: NextFunction) => {
        const requestId =
          (req as { context?: { requestId?: string } }).context?.requestId ??
          'unknown';
        try {
          const ctx = contextOf(container, spec, req, requestId);
          const authorization = spec.authorize(ctx);
          if (authorization !== 'in-handler') {
            await ctx.authorize(authorization.action, authorization.resource);
          }
          const reply = await spec.handle(ctx);
          if (reply.etag != null)
            res.setHeader('ETag', `"${String(reply.etag)}"`);
          res.setHeader('Cache-Control', 'no-store');
          res.status(reply.status ?? 200).json(reply.body);
        } catch (error) {
          if (spec.legacy !== undefined) {
            next(spec.legacy.error(legacyRefusal(error), res));
            return;
          }
          const refusal = refusalOf(error);
          if (refusal === null) next(error);
          else sendRefusal(res, requestId, refusal);
        }
      },
    );
  }
  return router;
}
