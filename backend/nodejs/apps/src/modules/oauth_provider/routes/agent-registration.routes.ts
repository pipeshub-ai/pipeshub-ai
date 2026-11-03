import { Router } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../config';
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { requireSessionAuth } from '../../../libs/middlewares/require-session-auth.middleware';
import { refuseServiceAccountCaller } from '../../user_management/middlewares/refuseServiceAccountCaller';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { AgentRegistrationController } from '../controller/agent-registration.controller';
import {
  agentIdentitySchema,
  agentClaimSchema,
  agentClaimVerifySchema,
  agentClaimCompleteSchema,
  agentRegistrationIdSchema,
} from '../validators/oauth.validators';

/** Mounted at /api/v1/agent. */
export function createAgentRegistrationRouter(container: Container): Router {
  const router = Router();
  const controller = container.get<AgentRegistrationController>(
    'AgentRegistrationController',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const appConfig = container.get<AppConfig>('AppConfig');
  const rateLimit = createOAuthClientRateLimiter(
    logger,
    appConfig.maxOAuthClientRequestsPerMinute,
  );

  // Public: the agent holds no session.
  router.post(
    '/identity',
    rateLimit,
    ValidationMiddleware.validate(agentIdentitySchema),
    controller.identity,
  );
  router.post(
    '/identity/claim',
    rateLimit,
    ValidationMiddleware.validate(agentClaimSchema),
    controller.claim,
  );

  // Session only, like the device consent: approving binds the agent to the signed-in person, so a bearer token must not drive it.
  const session = [
    rateLimit,
    authMiddleware.authenticate.bind(authMiddleware),
    requireSessionAuth,
    refuseServiceAccountCaller,
  ];
  router.post(
    '/identity/claim/verify',
    ...session,
    ValidationMiddleware.validate(agentClaimVerifySchema),
    controller.claimVerify,
  );
  router.post(
    '/identity/claim/complete',
    ...session,
    ValidationMiddleware.validate(agentClaimCompleteSchema),
    controller.claimComplete,
  );
  router.get('/registrations', ...session, controller.list);
  router.delete(
    '/registrations/:id',
    ...session,
    ValidationMiddleware.validate(agentRegistrationIdSchema),
    controller.revoke,
  );

  return router;
}
