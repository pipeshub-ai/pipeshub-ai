/**
 * Admin settings for the inbound `/mcp` endpoint, per org. Mounted by the configuration
 * manager router at `/api/v1/configurationManager/mcp`.
 */

import { NextFunction, Request, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { currentOrgContext } from '../../../libs/context/orgContext';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import {
  McpAccessPolicy,
  McpAccessPolicyStore,
  mcpAccessPolicySchema,
} from '../access-policy';
import { McpToolCatalog } from '../tool-catalog';

const putPolicySchema = z.object({ body: mcpAccessPolicySchema });

/** userAdminCheck has already refused a caller without an org. */
const callerOrgId = (): string => String(currentOrgContext().orgId);

export function createMcpAccessRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/access',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        res.status(200).json(await McpAccessPolicyStore.read(callerOrgId()));
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/access',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(putPolicySchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const policy = req.body as McpAccessPolicy;
        await McpAccessPolicyStore.write(callerOrgId(), policy);
        res.status(200).json(policy);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/access/tools',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        res.status(200).json({ tools: await McpToolCatalog.list() });
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
