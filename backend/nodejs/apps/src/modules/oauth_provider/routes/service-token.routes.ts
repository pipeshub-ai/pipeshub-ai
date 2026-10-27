import { Router } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { ServiceTokenController } from '../controller/service-token.controller';
import { AppConfig } from '../../tokens_manager/config/config';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  createServiceTokenSchema,
  listServiceTokensQuerySchema,
  revokeServiceTokenSchema,
} from '../validators/service-token.validators';

export function createServiceTokenRouter(container: Container): Router {
  const router = Router();
  const controller = container.get<ServiceTokenController>(
    'ServiceTokenController',
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

  // Admin-only throughout. A service token grants a machine its own view of
  // the organisation's documents, so minting one is an act of granting access
  // — unlike a personal access token, which only ever hands someone what they
  // could already reach themselves.
  //
  // The scope checks on each route below are the other half of that, and they
  // are not redundant: requireScopes is a no-op for session JWTs and the only
  // thing enforced for OAuth tokens and personal access tokens. Without it an
  // administrator's narrowly scoped PAT could mint a credential with more
  // reach than the PAT itself has.
  router.use(userAdminCheck);

  router.get(
    '/',
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(listServiceTokensQuerySchema),
    (req, res, next) => controller.listTokens(req, res, next),
  );

  router.post(
    '/',
    requireScopes(OAuthScopeNames.USER_INVITE),
    ValidationMiddleware.validate(createServiceTokenSchema),
    (req, res, next) => controller.createToken(req, res, next),
  );

  router.get(
    '/scopes',
    requireScopes(OAuthScopeNames.USER_READ),
    (req, res, next) => controller.listScopes(req, res, next),
  );

  router.delete(
    '/:tokenId',
    requireScopes(OAuthScopeNames.USER_DELETE),
    ValidationMiddleware.validate(revokeServiceTokenSchema),
    (req, res, next) => controller.revokeToken(req, res, next),
  );

  return router;
}
