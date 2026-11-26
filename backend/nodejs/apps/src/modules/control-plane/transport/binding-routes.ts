import { NextFunction, Request, Response, Router } from 'express';
import type { Container } from 'inversify';
import { z } from 'zod';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  BadRequestError,
  HttpError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import type { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import type { BindingService } from '../application/binding-service';
import { ERROR_CODES, type ErrorCode } from '../contracts/generated';
import { BindingNotImported } from '../domain/binding-errors';
import { MIGRATION_ACTOR, REFRESHER_ACTOR } from '../domain/bindings';
import { ControlPlaneError } from '../domain/errors';
import { ObjectExists, ObjectNotFound } from '../domain/store-errors';

export const CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX =
  '/api/v1/control-plane/internal/bindings';
/** Held by the Python services that import, revoke and refresh bindings (the migration, legacy routes, the token refresher). */
export const BINDINGS_TOKEN_SCOPE = TokenScopes.CONTROL_PLANE_BINDINGS;
/** A legacy route's save with the switch on, when its token names no user. */
const LEGACY_ROUTE_ACTOR = 'service:legacy-credentials';

/** The container key of the `BindingService` (bound by the control plane's composition). */
export const CONTROL_PLANE_BINDINGS = 'ControlPlaneBindings';

interface BindingRouteDeps {
  service(): BindingService;
  auth(): AuthMiddleware;
}

/** Both resolved at the first request: the routers are mounted before the containers are complete. */
function routeDeps(container: Container): BindingRouteDeps {
  return {
    service: () => {
      if (!container.isBound(CONTROL_PLANE_BINDINGS)) {
        throw new ServiceUnavailableError(
          'Connections are not configured on this deployment',
        );
      }
      return container.get<BindingService>(CONTROL_PLANE_BINDINGS);
    },
    auth: () => container.get<AuthMiddleware>('AuthMiddleware'),
  };
}

const problem = (code: ErrorCode, message: string): HttpError =>
  new HttpError(code, message, ERROR_CODES[code].status);

/** Store refusals as the catalogue's codes; anything else to the error middleware unchanged. */
function httpError(error: unknown): unknown {
  if (error instanceof ControlPlaneError)
    return problem(error.code, error.message);
  if (error instanceof ObjectNotFound)
    return new NotFoundError('No such binding');
  if (error instanceof ObjectExists || error instanceof BindingNotImported) {
    return new HttpError(error.reason.toUpperCase(), error.message, 409);
  }
  if (error instanceof z.ZodError) {
    const issue = error.issues[0];
    return new BadRequestError(
      `${issue?.path.join('.') || 'request'}: ${issue?.message ?? 'invalid'}`,
    );
  }
  return error;
}

type Handler<R extends Request> = (req: R, res: Response) => Promise<void>;
const handle =
  <R extends Request>(handler: Handler<R>) =>
  async (req: R, res: Response, next: NextFunction): Promise<void> => {
    try {
      await handler(req, res);
    } catch (error) {
      next(httpError(error));
    }
  };

const owner = z
  .string()
  .regex(/^(?:org|(?:agent|user):[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$/);
const bindingIdParam = z.object({
  bindingId: z.string().regex(/^bnd_[0-9A-HJKMNP-TV-Z]{26}$/),
});

const material = z
  .object({
    kind: z.enum(['api_key', 'oauth_tokens', 'header', 'record']),
    value: z.record(z.string(), z.unknown()),
    expiresAt: z.number().int().nonnegative().optional(),
  })
  .strict();
const ownerRef = z
  .object({ installation: z.string().min(1).max(256), owner })
  .strict();
const importBody = z
  .object({
    installation: z.string().min(1).max(256),
    owner,
    mode: z.enum(['oauth', 'api_key', 'header', 'dcr', 'model_account']),
    kind: z.string().min(1).max(64).nullable(),
    provider: z.string().min(1).max(128).nullable(),
    material,
    source: z
      .object({
        path: z.string().min(1).max(512),
        digest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
      })
      .strict()
      .nullable(),
    observed: z
      .object({
        path: z.string().min(1).max(512),
        digest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
      })
      .strict()
      .nullable()
      .optional(),
  })
  .strict();
/** The refresher replica asking (QA23-F2): what it is given, it holds until it writes or the claim lapses. */
const holderId = z.string().min(1).max(128);
const dueQuery = z
  .object({
    before: z.coerce.number().int().nonnegative(),
    limit: z.coerce.number().int().min(1).max(200).default(50),
    holder: holderId,
  })
  .strict();
const ownerQuery = ownerRef.extend({ holder: holderId }).strict();
const refreshedBody = z
  .object({ expectedRevision: z.number().int().min(1), material })
  .strict();
const failedBody = z
  .object({
    expectedRevision: z.number().int().min(1),
    code: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/),
    permanent: z.boolean(),
  })
  .strict();

/** The token's org; a token for no org writes nothing. */
function tokenOrg(req: AuthenticatedServiceRequest): string {
  const orgId: unknown = req.tokenPayload?.orgId;
  if (typeof orgId !== 'string' || orgId === '') {
    throw problem('CP_FORBIDDEN', 'The service token names no org');
  }
  return orgId;
}

const tokenUser = (req: AuthenticatedServiceRequest): string | null => {
  const userId: unknown = req.tokenPayload?.userId;
  return typeof userId === 'string' && userId !== '' ? `user:${userId}` : null;
};

/**
 * The binding writes of the Python services, under the service token of
 * scope `control-plane:bindings`: the migration's import and revert, a legacy
 * route's save and disconnect with the switch on, and the token refresher's
 * scan and outcomes. Each write is the binder's, audited in its transaction.
 * The org is always the token's; a body cannot name one.
 */
export function createInternalBindingRouter(container: Container): Router {
  const deps = routeDeps(container);
  const router = Router();
  const guard = (req: Request, res: Response, next: NextFunction): void => {
    void deps.auth().scopedTokenValidator(BINDINGS_TOKEN_SCOPE)(req, res, next);
  };

  router.post(
    '/import',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const body = importBody.parse(req.body);
      const actor =
        body.source !== null
          ? MIGRATION_ACTOR
          : (tokenUser(req) ?? LEGACY_ROUTE_ACTOR);
      res
        .status(200)
        .json(await deps.service().importRecord({ orgId, actor }, body));
    }),
  );

  router.post(
    '/import/revert',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const { installation, owner: who } = ownerRef.parse(req.body);
      const reverted = await deps
        .service()
        .revertImport({ orgId, actor: MIGRATION_ACTOR }, installation, who);
      res
        .status(200)
        .json(reverted ?? { binding: null, changedSinceImport: false });
    }),
  );

  router.post(
    '/revoke',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const { installation, owner: who } = ownerRef.parse(req.body);
      const actor = tokenUser(req) ?? LEGACY_ROUTE_ACTOR;
      res.status(200).json({
        binding: await deps
          .service()
          .revokeOwner({ orgId, actor }, installation, who),
      });
    }),
  );

  router.get(
    '/due',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const { before, limit, holder } = dueQuery.parse(req.query);
      res
        .status(200)
        .json({ bindings: await deps.service().due(before, limit, holder) });
    }),
  );

  router.get(
    '/owner',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const {
        installation,
        owner: who,
        holder: claimant,
      } = ownerQuery.parse(req.query);
      res.status(200).json({
        binding: await deps
          .service()
          .findForRefresh(orgId, installation, who, claimant),
      });
    }),
  );

  router.post(
    '/:bindingId/refreshed',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const { bindingId } = bindingIdParam.parse(req.params);
      const body = refreshedBody.parse(req.body);
      const binding = await deps
        .service()
        .refreshed(
          { orgId, actor: REFRESHER_ACTOR },
          bindingId,
          body.expectedRevision,
          body.material,
        );
      res.status(200).json({ binding });
    }),
  );

  router.post(
    '/:bindingId/refresh-failed',
    guard,
    handle<AuthenticatedServiceRequest>(async (req, res) => {
      const orgId = tokenOrg(req);
      const { bindingId } = bindingIdParam.parse(req.params);
      const body = failedBody.parse(req.body);
      const binding = await deps
        .service()
        .refreshFailed(
          { orgId, actor: REFRESHER_ACTOR },
          bindingId,
          body.expectedRevision,
          body.code,
          body.permanent,
        );
      res.status(200).json({ binding });
    }),
  );

  return router;
}
