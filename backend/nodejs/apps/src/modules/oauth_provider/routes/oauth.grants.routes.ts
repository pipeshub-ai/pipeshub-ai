import { Router } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { OAuthGrantController } from '../controller/oauth.grant.controller';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  grantIdParamsSchema,
  listAdminGrantsQuerySchema,
} from '../validators/oauth.grant.validators';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';

export function createOAuthGrantsRouter(container: Container): Router {
  const router = Router();
  const controller = container.get<OAuthGrantController>(
    'OAuthGrantController',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const appConfig = container.get<AppConfig>('AppConfig');

  const grantsRateLimiter = createOAuthClientRateLimiter(
    logger,
    appConfig.maxOAuthClientRequestsPerMinute,
  );

  // All routes require authentication
  router.use(authMiddleware.authenticate.bind(authMiddleware));
  router.use(grantsRateLimiter);

  // User endpoints
  router.get('/', (req, res, next) => controller.listGrants(req, res, next));

  router.delete(
    '/:grantId',
    ValidationMiddleware.validate(grantIdParamsSchema),
    (req, res, next) => controller.revokeGrant(req, res, next),
  );

  // Admin endpoints
  router.get(
    '/admin',
    userAdminCheck,
    ValidationMiddleware.validate(listAdminGrantsQuerySchema),
    (req, res, next) => controller.adminListGrants(req, res, next),
  );

  router.delete(
    '/admin/:grantId',
    userAdminCheck,
    ValidationMiddleware.validate(grantIdParamsSchema),
    (req, res, next) => controller.adminRevokeGrant(req, res, next),
  );

  return router;
}
