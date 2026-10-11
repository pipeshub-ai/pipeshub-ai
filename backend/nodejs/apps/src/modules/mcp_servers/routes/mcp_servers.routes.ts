/**
 * MCP Servers Routes
 *
 * Auth/scope-gated proxy in front of the Python connectors service's
 * `/api/v1/mcp-servers` router (see `mcp_servers.controller.ts` for the
 * forwarding logic). Mirrors the `skills` module's route shape (auth +
 * `requireScopes` + per-route handler) rather than `toolsets`, since MCP is
 * a pure proxy with no Node-side Kafka/entity-event side effects.
 *
 * @module mcp_servers/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  listMcpCatalog,
  getMcpCatalogTemplate,
  listMcpInstances,
  createMcpInstance,
  getMcpInstance,
  updateMcpInstance,
  deleteMcpInstance,
  authenticateMcpInstance,
  updateMcpCredentials,
  removeMcpCredentials,
  autoAuthenticateMcpInstance,
  reauthenticateMcpInstance,
  getMcpOAuthAuthorizationUrl,
  handleMcpOAuthCallback,
  refreshMcpOAuthToken,
  getMcpOAuthConfig,
  updateMcpOAuthConfig,
  discoverMcpOAuthMetadata,
  getMyMcpServers,
  getMcpInstanceTools,
  getAgentMcpServers,
  authenticateAgentMcpInstance,
  updateAgentMcpCredentials,
  removeAgentMcpCredentials,
  reauthenticateAgentMcpInstance,
  getAgentMcpOAuthAuthorizationUrl,
  getMcpToolPolicy,
  updateMcpToolPolicy,
  getMyMcpToolRules,
  updateMyMcpToolRules,
  getAgentMcpToolRules,
  updateAgentMcpToolRules,
} from '../controller/mcp_servers.controller';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';

export function createMcpServersRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'typeId', 'instanceId', 'agentKey');
  const config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // ---- Catalog (read-only, in-memory templates) --------------------------

  router.get('/catalog', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), listMcpCatalog(config));
  router.get('/catalog/:typeId', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpCatalogTemplate(config));

  // ---- Discovery / consumption (before '/instances/:instanceId' so 'my-mcp-servers' never matches as an id) --

  router.get('/my-mcp-servers', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMyMcpServers(config));

  // ---- OAuth callback / discovery (state-keyed, instance-agnostic paths) --

  router.get('/oauth/callback', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), handleMcpOAuthCallback(config));
  // Discovery is required in *create* mode, before an instanceId exists — MCP_WRITE (not
  // READ): it is the same trust boundary as configuring an OAuth app. Python probes a
  // non-admin's URL under the public-only policy of a personal instance.
  router.post('/oauth/discover', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), discoverMcpOAuthMetadata(config));

  // ---- Instances (org-wide ones admin-managed, personal ones owner-managed) --

  router.get('/instances', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), listMcpInstances(config));
  router.post('/instances', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), createMcpInstance(config));
  router.get('/instances/:instanceId', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpInstance(config));
  router.put('/instances/:instanceId', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), updateMcpInstance(config));
  router.delete('/instances/:instanceId', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_DELETE), deleteMcpInstance(config));
  router.get('/instances/:instanceId/tools', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpInstanceTools(config));
  // Tool approvals: company rules (admins) and the caller's own rules.
  router.get('/instances/:instanceId/tool-policy', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpToolPolicy(config));
  router.put('/instances/:instanceId/tool-policy', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), updateMcpToolPolicy(config));
  router.get('/instances/:instanceId/my-tool-rules', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMyMcpToolRules(config));
  router.put('/instances/:instanceId/my-tool-rules', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), updateMyMcpToolRules(config));

  // ---- Auth — API token / headers -----------------------------------------

  router.post('/instances/:instanceId/authenticate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), authenticateMcpInstance(config));
  router.put('/instances/:instanceId/credentials', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), updateMcpCredentials(config));
  router.delete('/instances/:instanceId/credentials', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_DELETE), removeMcpCredentials(config));
  router.post('/instances/:instanceId/auto-authenticate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), autoAuthenticateMcpInstance(config));
  router.post('/instances/:instanceId/reauthenticate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), reauthenticateMcpInstance(config));

  // ---- OAuth + DCR (instance-scoped) --------------------------------------

  router.get('/instances/:instanceId/oauth/authorize', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpOAuthAuthorizationUrl(config));
  router.post('/instances/:instanceId/oauth/refresh', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), refreshMcpOAuthToken(config));
  router.get('/instances/:instanceId/oauth-config', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_READ), getMcpOAuthConfig(config));
  router.put('/instances/:instanceId/oauth-config', authMiddleware.authenticate, requireScopes(OAuthScopeNames.MCP_WRITE), updateMcpOAuthConfig(config));

  // ---- Agent-scoped (service-account agent) MCP credentials --------------
  // Mirrors toolsets' `/agents/:agentKey/...` routes; Python independently
  // re-verifies agent edit access on every one of these.

  router.get('/agents/:agentKey', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_READ), getAgentMcpServers(config));
  router.post('/agents/:agentKey/instances/:instanceId/authenticate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), authenticateAgentMcpInstance(config));
  router.put('/agents/:agentKey/instances/:instanceId/credentials', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), updateAgentMcpCredentials(config));
  router.delete('/agents/:agentKey/instances/:instanceId/credentials', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), removeAgentMcpCredentials(config));
  router.post('/agents/:agentKey/instances/:instanceId/reauthenticate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), reauthenticateAgentMcpInstance(config));
  router.get('/agents/:agentKey/instances/:instanceId/oauth/authorize', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), getAgentMcpOAuthAuthorizationUrl(config));
  router.get('/agents/:agentKey/instances/:instanceId/tool-rules', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_READ), getAgentMcpToolRules(config));
  router.put('/agents/:agentKey/instances/:instanceId/tool-rules', authMiddleware.authenticate, requireScopes(OAuthScopeNames.AGENT_WRITE), updateAgentMcpToolRules(config));

  return router;
}

export default createMcpServersRouter;
