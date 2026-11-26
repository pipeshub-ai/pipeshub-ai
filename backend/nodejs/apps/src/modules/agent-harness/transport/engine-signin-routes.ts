/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ServiceUnavailableError } from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import type { EngineSignInService } from '../application/engine-signin-service';
import { AGENT_HARNESS_ENGINE_SIGNINS } from '../binders/tokens';
import { SIGNIN_ATTEMPT, SIGNIN_CODE } from '../domain/engine-signin';
import {
  HarnessModelAccountRefusedError,
  HarnessNotFoundError,
} from '../domain/errors';
import { transportError } from './errors';
import { currentOrgContext } from '../../../libs/context/orgContext';

export const ENGINE_ACCOUNTS_PREFIX = '/api/v1/agent-harness/engine-accounts';

export const putClaudeSignInSchema = z.object({
  body: z.object({ enabled: z.boolean() }).strict(),
});

const attemptParams = z.object({ attemptId: z.string().regex(SIGNIN_ATTEMPT) });

export const signInAttemptSchema = z.object({ params: attemptParams });

export const signInCodeSchema = z.object({
  params: attemptParams,
  // The code is checked for shape only; it goes straight to the worker and is never stored or logged.
  body: z
    .object({ code: z.string().trim().max(1100).regex(SIGNIN_CODE) })
    .strict(),
});

type Handler = (req: AuthenticatedUserRequest, res: Response) => Promise<void>;

const handle =
  (handler: Handler) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      await handler(req, res);
    } catch (error) {
      next(transportError(error));
    }
  };

/**
 * A person's own sign-in is theirs: no connected app or token acts on it,
 * only their own session at the web app (SIGNIN invariant 2).
 */
const firstPartyOnly = (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void => {
  if (req.user?.isOAuth === true) {
    next(
      transportError(
        new HarnessModelAccountRefusedError(
          'MODEL_ACCOUNT_FORBIDDEN',
          'Personal Claude sign-in is managed by the person, not by connected apps',
        ),
      ),
    );
    return;
  }
  next();
};

/** Nothing about a sign-in is cached anywhere: the link lives as long as the attempt. */
const noStore = (_req: unknown, res: Response, next: NextFunction): void => {
  res.set('Cache-Control', 'no-store');
  next();
};

function service(container: Container): EngineSignInService {
  if (!container.isBound(AGENT_HARNESS_ENGINE_SIGNINS)) {
    throw new ServiceUnavailableError(
      'Personal engine sign-in is not configured on this deployment',
    );
  }
  return container.get<EngineSignInService>(AGENT_HARNESS_ENGINE_SIGNINS);
}

/**
 * QA-26: an attempt exists only where sign-in is configured, so without it every attempt is not found: the
 * deployment's configuration is never what a request about someone else's attempt learns.
 */
function attemptService(container: Container): EngineSignInService {
  if (!container.isBound(AGENT_HARNESS_ENGINE_SIGNINS))
    throw new HarnessNotFoundError('No such sign-in');
  return container.get<EngineSignInService>(AGENT_HARNESS_ENGINE_SIGNINS);
}

const caller = (
  req: AuthenticatedUserRequest,
): { orgId: string; userId: string } => ({
  orgId: String(currentOrgContext().orgId),
  userId: String(req.user?.userId),
});

/** The caller's own engine sign-in (SIGNIN): the owner is always the caller; paths never name a user. */
export function createEngineSignInRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  router.use(noStore);

  router.get(
    '/me',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    handle(async (req, res) => {
      const { orgId, userId } = caller(req);
      res.status(200).json(await service(container).view(orgId, userId));
    }),
  );

  router.put(
    '/me/claude',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(putClaudeSignInSchema),
    handle(async (req, res) => {
      const { orgId, userId } = caller(req);
      const { enabled } = req.body as z.infer<
        typeof putClaudeSignInSchema
      >['body'];
      res
        .status(200)
        .json(await service(container).setEnabled(orgId, userId, enabled));
    }),
  );

  router.delete(
    '/me/claude',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    handle(async (req, res) => {
      const { orgId, userId } = caller(req);
      res
        .status(200)
        .json(
          await service(container).disconnect(orgId, userId, userId, 'user'),
        );
    }),
  );

  router.get(
    '/signin/:attemptId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    firstPartyOnly,
    ValidationMiddleware.validate(signInAttemptSchema),
    handle(async (req, res) => {
      res
        .status(200)
        .json(
          await attemptService(container).describe(
            caller(req),
            String(req.params.attemptId),
          ),
        );
    }),
  );

  router.post(
    '/signin/:attemptId/code',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(signInCodeSchema),
    handle(async (req, res) => {
      const { code } = req.body as z.infer<typeof signInCodeSchema>['body'];
      res
        .status(200)
        .json(
          await attemptService(container).submitCode(
            caller(req),
            String(req.params.attemptId),
            code,
          ),
        );
    }),
  );

  router.delete(
    '/signin/:attemptId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(signInAttemptSchema),
    handle(async (req, res) => {
      await attemptService(container).cancel(
        caller(req),
        String(req.params.attemptId),
      );
      res.status(204).end();
    }),
  );

  return router;
}
