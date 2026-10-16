import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  artifactIdParamsSchema,
  artifactShareSchema,
  listArtifactsSchema,
} from '../validators/artifacts.validators';
import {
  getArtifact,
  listArtifactMembers,
  listArtifactVersions,
  listArtifacts,
  shareArtifact,
  unshareArtifact,
} from '../controllers/artifacts.controller';

export function createArtifactsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const authenticate = authMiddleware.authenticate.bind(authMiddleware);
  const appConfig = container.get<AppConfig>('AppConfig');

  router.get(
    '/',
    authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(listArtifactsSchema),
    listArtifacts(appConfig),
  );

  router.get(
    '/:artifactId/versions',
    authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(artifactIdParamsSchema),
    listArtifactVersions(appConfig),
  );

  router.get(
    '/:artifactId/permissions',
    authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(artifactIdParamsSchema),
    listArtifactMembers(appConfig),
  );

  router.post(
    '/:artifactId/share',
    authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE, OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(artifactShareSchema),
    shareArtifact(appConfig),
  );

  router.post(
    '/:artifactId/unshare',
    authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE, OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(artifactShareSchema),
    unshareArtifact(appConfig),
  );

  router.get(
    '/:artifactId',
    authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(artifactIdParamsSchema),
    getArtifact(appConfig),
  );

  return router;
}
