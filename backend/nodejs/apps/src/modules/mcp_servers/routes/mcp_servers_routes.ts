/**
 * MCP Servers Routes
 *
 * RESTful routes for MCP server catalog, instance management, authentication
 * (API token + OAuth), tool discovery, and agent-scoped operations.
 */

import { Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  getCatalog,
  getCatalogItem,
  getInstances,
  createInstance,
  getInstance,
  updateInstance,
  deleteInstance,
  authenticateInstance,
  autoAuthenticateInstance,
  updateCredentials,
  removeCredentials,
  reauthenticateInstance,
  oauthAuthorize,
  oauthCallback,
  oauthCallbackGlobal,
  oauthRefresh,
  getInstanceOauthConfig,
  setInstanceOauthConfig,
  getMyMcpServers,
  discoverInstanceTools,
  getAgentMcpServers,
  authenticateAgentInstance,
  updateAgentCredentials,
  removeAgentCredentials,
} from '../controller/mcp_servers_controller';

// ============================================================================
// Validation Schemas
// ============================================================================

const listQuerySchema = z.object({
  query: z.object({
    page: z.preprocess((a) => (a === '' || a === undefined ? undefined : Number(a)), z.number().int().min(1)).optional(),
    limit: z.preprocess((a) => (a === '' || a === undefined ? undefined : Number(a)), z.number().int().min(1).max(200)).optional(),
    search: z.string().optional(),
  }),
});

const instanceIdParamSchema = z.object({
  params: z.object({
    instanceId: z.string().uuid(),
  }),
});

const typeIdParamSchema = z.object({
  params: z.object({
    typeId: z.string().min(1),
  }),
});

const agentKeyParamSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1),
  }),
});

const agentInstanceParamSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1),
    instanceId: z.string().uuid(),
  }),
});

// ============================================================================
// Route Creation
// ============================================================================

export function createMcpServersRouter(container: Container): Router {
  const router = Router();
  const appConfig = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  const auth = authMiddleware.authenticate.bind(authMiddleware);

  // Catalog
  router.get('/catalog', auth, ValidationMiddleware.validate(listQuerySchema), metricsMiddleware(container), getCatalog(appConfig));
  router.get('/catalog/:typeId', auth, ValidationMiddleware.validate(typeIdParamSchema), metricsMiddleware(container), getCatalogItem(appConfig));

  // User view (merged instances + auth status)
  router.get('/my-mcp-servers', auth, metricsMiddleware(container), getMyMcpServers(appConfig));

  // Instance CRUD
  router.get('/instances', auth, metricsMiddleware(container), getInstances(appConfig));
  router.post('/instances', auth, metricsMiddleware(container), createInstance(appConfig));
  router.get('/instances/:instanceId', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), getInstance(appConfig));
  router.put('/instances/:instanceId', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), updateInstance(appConfig));
  router.delete('/instances/:instanceId', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), deleteInstance(appConfig));

  // User authentication — API token
  router.post('/instances/:instanceId/auto-authenticate', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), autoAuthenticateInstance(appConfig));
  router.post('/instances/:instanceId/authenticate', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), authenticateInstance(appConfig));
  router.put('/instances/:instanceId/credentials', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), updateCredentials(appConfig));
  router.delete('/instances/:instanceId/credentials', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), removeCredentials(appConfig));
  router.post('/instances/:instanceId/reauthenticate', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), reauthenticateInstance(appConfig));

  // OAuth flow
  router.get('/oauth/callback', auth, metricsMiddleware(container), oauthCallbackGlobal(appConfig));
  router.get('/instances/:instanceId/oauth/authorize', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), oauthAuthorize(appConfig));
  router.post('/instances/:instanceId/oauth/callback', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), oauthCallback(appConfig));
  router.post('/instances/:instanceId/oauth/refresh', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), oauthRefresh(appConfig));

  // OAuth client configuration (admin — per instance)
  router.get('/instances/:instanceId/oauth-config', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), getInstanceOauthConfig(appConfig));
  router.put('/instances/:instanceId/oauth-config', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), setInstanceOauthConfig(appConfig));

  // Tool discovery
  router.get('/instances/:instanceId/tools', auth, ValidationMiddleware.validate(instanceIdParamSchema), metricsMiddleware(container), discoverInstanceTools(appConfig));

  // Agent-scoped
  router.get('/agents/:agentKey', auth, ValidationMiddleware.validate(agentKeyParamSchema), metricsMiddleware(container), getAgentMcpServers(appConfig));
  router.post('/agents/:agentKey/instances/:instanceId/authenticate', auth, ValidationMiddleware.validate(agentInstanceParamSchema), metricsMiddleware(container), authenticateAgentInstance(appConfig));
  router.put('/agents/:agentKey/instances/:instanceId/credentials', auth, ValidationMiddleware.validate(agentInstanceParamSchema), metricsMiddleware(container), updateAgentCredentials(appConfig));
  router.delete('/agents/:agentKey/instances/:instanceId/credentials', auth, ValidationMiddleware.validate(agentInstanceParamSchema), metricsMiddleware(container), removeAgentCredentials(appConfig));

  return router;
}
