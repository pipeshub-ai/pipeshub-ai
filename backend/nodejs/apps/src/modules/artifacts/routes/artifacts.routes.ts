import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { COLLAB_TYPES } from '../../enterprise_search/services/collaboration/collab.types';
import { ConversationGuards } from '../../enterprise_search/services/collaboration/http/conversation-guards';
import {
  artifactIdParamsSchema,
  listArtifactsSchema,
} from '../validators/artifacts.validators';
import {
  getArtifact,
  listArtifactVersions,
  listArtifacts,
} from '../controllers/artifacts.controller';

export function createArtifactsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');
  // Conversation titles are joined with the same read filter as the conversation lists.
  const guards = container.get<ConversationGuards>(
    COLLAB_TYPES.ConversationGuards,
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(listArtifactsSchema),
    guards.listScope('any'),
    listArtifacts(appConfig),
  );

  router.get(
    '/:artifactId/versions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(artifactIdParamsSchema),
    listArtifactVersions(appConfig),
  );

  router.get(
    '/:artifactId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(artifactIdParamsSchema),
    guards.listScope('any'),
    getArtifact(appConfig),
  );

  return router;
}
