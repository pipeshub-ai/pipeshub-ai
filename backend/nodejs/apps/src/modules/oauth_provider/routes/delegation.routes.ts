import { Router } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { DelegationController } from '../controller/delegation.controller';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  grantDelegationSchema,
  listDelegationEventsQuerySchema,
  listDelegationsQuerySchema,
  revokeDelegationSchema,
} from '../validators/delegation.validators';

export function createDelegationRouter(container: Container): Router {
  const router = Router();
  const controller = container.get<DelegationController>(
    'DelegationController',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const appConfig = container.get<AppConfig>('AppConfig');

  const rateLimiter = createOAuthClientRateLimiter(
    logger,
    appConfig.maxOAuthClientRequestsPerMinute,
  );

  router.use(authMiddleware.authenticate.bind(authMiddleware));
  router.use(rateLimiter);

  // Admin-only throughout, and for a stronger reason than the service-token
  // routes next door. Granting a delegation does not hand out access to a
  // document; it hands out the right to be a person. Nobody but an
  // administrator should be able to decide that, and nobody at all should be
  // able to do it with a credential that was minted for something narrower —
  // which is what the scope checks below are for, since requireScopes is a
  // no-op for session JWTs and the only thing enforced for OAuth tokens.
  router.use(userAdminCheck);

  router.get(
    '/',
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(listDelegationsQuerySchema),
    (req, res, next) => controller.list(req, res, next),
  );

  // USER_INVITE rather than USER_WRITE: this mirrors creating a principal
  // more than editing one, because what it produces is a new way for
  // something to act in the organisation.
  router.post(
    '/',
    requireScopes(OAuthScopeNames.USER_INVITE),
    ValidationMiddleware.validate(grantDelegationSchema),
    (req, res, next) => controller.grant(req, res, next),
  );

  // Declared before `/:subjectUserId` so the literal path is not swallowed by
  // the parameter route.
  router.get(
    '/events',
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(listDelegationEventsQuerySchema),
    (req, res, next) => controller.listEvents(req, res, next),
  );

  router.delete(
    '/:subjectUserId',
    requireScopes(OAuthScopeNames.USER_DELETE),
    ValidationMiddleware.validate(revokeDelegationSchema),
    (req, res, next) => controller.revoke(req, res, next),
  );

  return router;
}
