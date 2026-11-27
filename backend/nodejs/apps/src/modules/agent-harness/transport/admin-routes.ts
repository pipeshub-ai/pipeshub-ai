/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { harnessEditionOf, mountEditionRoutes } from '../edition';
import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { transportError } from './errors';
import { dependencyHealth } from '../ops/dependency-health';
import { codingEngines, ENGINE_KIND } from './model-account-routes';
import { currentOrgContext } from '../../../libs/context/orgContext';
import { serviceHealthViews } from '../ops/service-health-checks';

export const AGENT_HARNESS_ADMIN_PREFIX = '/api/v1/agent-harness/admin';

export const putOrgCodingEngineSchema = z.object({
  body: z
    .object({
      defaultEngine: ENGINE_KIND.nullable().optional(),
      autoRoute: z.boolean().optional(),
    })
    .strict()
    .refine(
      (body) =>
        body.defaultEngine !== undefined || body.autoRoute !== undefined,
      { message: 'Name defaultEngine, autoRoute or both' },
    ),
});

/**
 * Org-admin routes of the harness. The org is always the caller's.
 */
export function createAgentHarnessAdminRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  // CH-07: which store this Node found degraded and since when, so an admin sees an outage the runs ride through.
  router.get(
    '/dependencies',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    (_req: AuthenticatedUserRequest, res: Response) => {
      res
        .status(200)
        .json({ ...dependencyHealth.view(), ...serviceHealthViews() });
    },
  );

  // ENGPICK: the org's default coding engine for the default chat's Agent mode; null: none. DEEPROUTE: `autoRoute`,
  // whether turns hand long work to the engine on their own. Either or both; each is its own audited write.
  router.put(
    '/coding-engine',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(putOrgCodingEngineSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const { defaultEngine, autoRoute } = req.body as z.infer<
          typeof putOrgCodingEngineSchema
        >['body'];
        const orgId = String(currentOrgContext().orgId);
        const userId = String(req.user?.userId);
        const engines = codingEngines(container);
        let view =
          defaultEngine !== undefined
            ? await engines.setOrgDefault(orgId, userId, defaultEngine)
            : undefined;
        if (autoRoute !== undefined) {
          view = await engines.setOrgAutoRoute(orgId, userId, autoRoute);
        }
        res.status(200).json(view ?? (await engines.view(orgId, userId)));
      } catch (error) {
        next(transportError(error));
      }
    },
  );

  mountEditionRoutes(
    router,
    container,
    harnessEditionOf(container).adminRoutes,
  );

  return router;
}
