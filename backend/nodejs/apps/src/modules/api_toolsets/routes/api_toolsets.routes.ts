/**
 * API Toolsets Routes
 *
 * Auth- and scope-gated proxy in front of the Python connectors service's
 * `/api/v1/api-toolsets` router. API Toolsets are toolsets, so they use the
 * same `connector:*` scopes as the built-in toolsets. Python decides which
 * routes are for administrators: the settings and the shared credential.
 *
 * @module api_toolsets/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  validateApiToolsetSpec,
  listApiToolsets,
  createApiToolset,
  getApiToolset,
  getApiToolsetConfig,
  updateApiToolset,
  deleteApiToolset,
  saveApiToolsetCredentials,
  deleteApiToolsetCredentials,
  saveApiToolsetSharedCredentials,
  deleteApiToolsetSharedCredentials,
  getApiToolsetSharedOAuthUrl,
  testApiToolset,
  getApiToolsetOAuthUrl,
  handleApiToolsetOAuthCallback,
} from '../controller/api_toolsets.controller';

export function createApiToolsetsRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'instanceId');
  const config = container.get<AppConfig>('AppConfig');
  // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor
  const { authenticate } = container.get<AuthMiddleware>('AuthMiddleware');
  const read = requireScopes(OAuthScopeNames.CONNECTOR_READ);
  const write = requireScopes(OAuthScopeNames.CONNECTOR_WRITE);
  const remove = requireScopes(OAuthScopeNames.CONNECTOR_DELETE);

  router.post('/validate', authenticate, write, validateApiToolsetSpec(config));

  router.get('/instances', authenticate, read, listApiToolsets(config));
  router.post('/instances', authenticate, write, createApiToolset(config));
  router.get(
    '/instances/:instanceId',
    authenticate,
    read,
    getApiToolset(config),
  );
  router.get(
    '/instances/:instanceId/config',
    authenticate,
    read,
    getApiToolsetConfig(config),
  );
  router.put(
    '/instances/:instanceId',
    authenticate,
    write,
    updateApiToolset(config),
  );
  router.delete(
    '/instances/:instanceId',
    authenticate,
    remove,
    deleteApiToolset(config),
  );

  router.put(
    '/instances/:instanceId/credentials',
    authenticate,
    write,
    saveApiToolsetCredentials(config),
  );
  router.delete(
    '/instances/:instanceId/credentials',
    authenticate,
    remove,
    deleteApiToolsetCredentials(config),
  );
  router.put(
    '/instances/:instanceId/shared-credentials',
    authenticate,
    write,
    saveApiToolsetSharedCredentials(config),
  );
  router.delete(
    '/instances/:instanceId/shared-credentials',
    authenticate,
    remove,
    deleteApiToolsetSharedCredentials(config),
  );
  router.post(
    '/instances/:instanceId/test',
    authenticate,
    write,
    testApiToolset(config),
  );

  router.get(
    '/instances/:instanceId/oauth/authorize',
    authenticate,
    write,
    getApiToolsetOAuthUrl(config),
  );
  router.get(
    '/instances/:instanceId/shared-credentials/oauth/authorize',
    authenticate,
    write,
    getApiToolsetSharedOAuthUrl(config),
  );
  router.get(
    '/oauth/callback',
    authenticate,
    write,
    handleApiToolsetOAuthCallback(config),
  );

  return router;
}
