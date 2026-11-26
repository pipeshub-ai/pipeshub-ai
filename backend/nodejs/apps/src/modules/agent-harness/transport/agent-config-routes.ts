/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Request, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  BadRequestError,
  ForbiddenError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import type { AppConfig } from '../../tokens_manager/config/config';
import {
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_POLICY_ADMIN,
  ControlPlaneError,
  controlPlaneHttpError,
  type Authorizer,
} from '../../control-plane';
import type { AgentPolicySections } from '../ports/agent-policy-sections';
import { QueryAgentDefinitions } from '../adapters/query-agent-definitions';
import {
  AgentConfigRefusedError,
  AgentConfigTransfer,
  type Caller,
} from '../application/agent-config-transfer';
import { transportError } from './errors';
import { assertAgentEnginesUsable } from './model-account-routes';
import { identityString } from './request-identity';
import { harnessEditionOf, mountEditionRoutes } from '../edition';

/** Mounted beside the agents module's routes, on the same prefix. */
export const AGENT_CONFIG_PREFIX = '/api/v1/agents';

const agentKey = z.string().min(1).max(256);

export const exportAgentConfigSchema = z.object({
  params: z.object({ agentKey }),
  query: z
    .object({
      version: z
        .string()
        .regex(/^(?:draft|[1-9][0-9]{0,15})$/)
        .optional(),
    })
    .strict(),
});

function transfer(container: Container): AgentConfigTransfer<string> {
  const appConfig = container.get<AppConfig>('AppConfig');
  return new AgentConfigTransfer<string>({
    definitions: new QueryAgentDefinitions(() => appConfig.aiBackend),
    policy: container.isBound(CONTROL_PLANE_POLICY_ADMIN)
      ? container.get<AgentPolicySections>(CONTROL_PLANE_POLICY_ADMIN)
      : null,
    authorizer: container.isBound(CONTROL_PLANE_AUTHORIZER)
      ? container.get<Authorizer>(CONTROL_PLANE_AUTHORIZER)
      : null,
    assertEnginesUsable: (orgId, engines) =>
      assertAgentEnginesUsable(container, orgId, engines),
    now: Date.now,
  });
}

/** The caller from the verified credential, and their own Authorization header for the query service. */
function caller(req: AuthenticatedUserRequest): {
  who: Caller;
  authorization: string;
} {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  const orgId = identityString(user?.['orgId']);
  const userId = identityString(user?.['userId']);
  const authorization = req.headers.authorization;
  if (
    orgId === null ||
    userId === null ||
    typeof authorization !== 'string' ||
    authorization === ''
  ) {
    throw new UnauthorizedError(
      'An agent is exported and imported by a signed-in user',
    );
  }
  return { who: { orgId, userId }, authorization };
}

export function agentConfigHttpError(error: unknown): unknown {
  if (error instanceof AgentConfigRefusedError) {
    const field =
      error.field !== undefined ? { field: error.field } : undefined;
    switch (error.code) {
      case 'AGENT_CONFIG_INVALID':
        return new BadRequestError(error.message, field);
      case 'AGENT_CONFIG_FORBIDDEN':
        return new ForbiddenError(error.message, field);
      case 'AGENT_CONFIG_POLICY_UNAVAILABLE':
        return new ServiceUnavailableError(error.message, field);
    }
  }
  if (error instanceof ControlPlaneError) return controlPlaneHttpError(error);
  return transportError(error);
}

type Handler = (req: AuthenticatedUserRequest, res: Response) => Promise<void>;

const handle =
  (handler: Handler) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      await handler(req, res);
    } catch (error) {
      next(agentConfigHttpError(error));
    }
  };

function fileName(name: string): string {
  const safe = name
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return `${safe === '' ? 'agent' : safe}.agent.json`;
}

/**
 * Export and import of an agent with its engine profile and policy layer
 * (C4.08), for promotion between environments. The export is for whoever may
 * read the agent; the import creates a new agent of the caller's, in the
 * caller's org, from a document whose ids and owners are never read.
 */
export function createAgentConfigRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/:agentKey/export',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(exportAgentConfigSchema),
    handle(async (req, res) => {
      const { who, authorization } = caller(req);
      const version = (req.query as { version?: string }).version;
      const document = await transfer(container).export(
        who,
        authorization,
        String(req.params['agentKey']),
        version,
      );
      res
        .status(200)
        .setHeader(
          'Content-Disposition',
          `attachment; filename="${fileName(document.agent.name)}"`,
        )
        .json(document);
    }),
  );

  router.post(
    '/import',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    handle(async (req, res) => {
      const { who, authorization } = caller(req);
      res
        .status(201)
        .json(await transfer(container).import(who, authorization, req.body));
    }),
  );

  mountEditionRoutes(
    router,
    container,
    harnessEditionOf(container).agentRoutes,
  );

  return router;
}
