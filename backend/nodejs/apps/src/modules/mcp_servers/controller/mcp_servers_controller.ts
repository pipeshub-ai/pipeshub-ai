/**
 * MCP Servers Controller
 *
 * Proxy layer between the frontend and the Python backend for MCP server
 * catalog, instance management, authentication (API token + OAuth), and tool discovery.
 */

import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
  handleConnectorResponse,
} from '../../tokens_manager/utils/connector.utils';
import { UserGroups } from '../../user_management/schema/userGroup.schema';

const BASE_PATH = '/api/v1/mcp-servers';

function buildUrl(appConfig: AppConfig, path: string, query?: URLSearchParams): string {
  const base = `${appConfig.connectorBackend}${BASE_PATH}${path}`;
  return query && query.toString() ? `${base}?${query.toString()}` : base;
}

async function forwardHeaders(req: AuthenticatedUserRequest): Promise<Record<string, string>> {
  const { userId, orgId } = req.user || {};
  let isAdmin = false;
  if (userId) {
    const groups = await UserGroups.find({ orgId, users: { $in: [userId] }, isDeleted: false }).select('type');
    isAdmin = groups.some((g: any) => g.type === 'admin');
  }
  return {
    ...(req.headers as Record<string, string>),
    'x-user-id': userId || '',
    'x-org-id': orgId || '',
    'x-is-admin': isAdmin ? 'true' : 'false',
  };
}

function requireUser(req: AuthenticatedUserRequest): string {
  const userId = req.user?.userId;
  if (!userId) throw new UnauthorizedError('User authentication required');
  return userId;
}

// ============================================================================
// Catalog
// ============================================================================

export const getCatalog =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const q = new URLSearchParams();
      if (req.query.page) q.append('page', String(req.query.page));
      if (req.query.limit) q.append('limit', String(req.query.limit));
      if (req.query.search) q.append('search', String(req.query.search));

      const r = await executeConnectorCommand(buildUrl(appConfig, '/catalog', q), HttpMethod.GET, await forwardHeaders(req));
      handleConnectorResponse(r, res, 'Get MCP catalog', 'Catalog not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get MCP catalog'));
    }
  };

export const getCatalogItem =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { typeId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/catalog/${typeId}`),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Get catalog item', 'Catalog item not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get MCP catalog item'));
    }
  };

// ============================================================================
// Instances CRUD
// ============================================================================

export const getInstances =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const r = await executeConnectorCommand(buildUrl(appConfig, '/instances'), HttpMethod.GET, await forwardHeaders(req));
      handleConnectorResponse(r, res, 'Get MCP instances', 'Instances not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get MCP instances'));
    }
  };

export const createInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const r = await executeConnectorCommand(
        buildUrl(appConfig, '/instances'),
        HttpMethod.POST,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Create MCP instance', 'Failed to create');
    } catch (error: any) {
      next(handleBackendError(error, 'create MCP instance'));
    }
  };

export const getInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}`),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Get MCP instance', 'Instance not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get MCP instance'));
    }
  };

export const updateInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}`),
        HttpMethod.PUT,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Update MCP instance', 'Failed to update');
    } catch (error: any) {
      next(handleBackendError(error, 'update MCP instance'));
    }
  };

export const deleteInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}`),
        HttpMethod.DELETE,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Delete MCP instance', 'Failed to delete');
    } catch (error: any) {
      next(handleBackendError(error, 'delete MCP instance'));
    }
  };

// ============================================================================
// User Authentication — API Token
// ============================================================================

export const authenticateInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/authenticate`),
        HttpMethod.POST,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Authenticate MCP instance', 'Auth failed');
    } catch (error: any) {
      next(handleBackendError(error, 'authenticate MCP instance'));
    }
  };

export const updateCredentials =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/credentials`),
        HttpMethod.PUT,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Update credentials', 'Failed to update');
    } catch (error: any) {
      next(handleBackendError(error, 'update MCP credentials'));
    }
  };

export const removeCredentials =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/credentials`),
        HttpMethod.DELETE,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Remove credentials', 'Failed to remove');
    } catch (error: any) {
      next(handleBackendError(error, 'remove MCP credentials'));
    }
  };

export const reauthenticateInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/reauthenticate`),
        HttpMethod.POST,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Reauthenticate MCP instance', 'Failed');
    } catch (error: any) {
      next(handleBackendError(error, 'reauthenticate MCP instance'));
    }
  };

// ============================================================================
// OAuth Flow
// ============================================================================

export const oauthAuthorize =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const q = new URLSearchParams();
      if (req.query.baseUrl) q.append('baseUrl', String(req.query.baseUrl));

      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/oauth/authorize`, q),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'OAuth authorize', 'OAuth authorize failed');
    } catch (error: any) {
      next(handleBackendError(error, 'MCP OAuth authorize'));
    }
  };

export const oauthCallback =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/oauth/callback`),
        HttpMethod.POST,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'OAuth callback', 'OAuth callback failed');
    } catch (error: any) {
      next(handleBackendError(error, 'MCP OAuth callback'));
    }
  };

export const oauthCallbackGlobal =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { baseUrl, code, state, error } = req.query;
      const q = new URLSearchParams();
      if (code) q.append('code', String(code));
      if (state) q.append('state', String(state));
      if (error) q.append('error', String(error));
      if (baseUrl) q.append('baseUrl', String(baseUrl));

      const r = await executeConnectorCommand(
        buildUrl(appConfig, '/oauth/callback', q),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'OAuth callback', 'OAuth callback failed');
    } catch (error: any) {
      next(handleBackendError(error, 'MCP OAuth callback'));
    }
  };

export const oauthRefresh =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/oauth/refresh`),
        HttpMethod.POST,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'OAuth refresh', 'Token refresh failed');
    } catch (error: any) {
      next(handleBackendError(error, 'MCP OAuth refresh'));
    }
  };

// ============================================================================
// OAuth Client Configuration (Admin — per instance)
// ============================================================================

export const getInstanceOauthConfig =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/oauth-config`),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Get instance OAuth config', 'OAuth config not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get MCP instance OAuth config'));
    }
  };

export const setInstanceOauthConfig =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/oauth-config`),
        HttpMethod.PUT,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Set instance OAuth config', 'Failed to save OAuth config');
    } catch (error: any) {
      next(handleBackendError(error, 'set MCP instance OAuth config'));
    }
  };

// ============================================================================
// User Views
// ============================================================================

export const getMyMcpServers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const q = new URLSearchParams();
      if (req.query.page) q.append('page', String(req.query.page));
      if (req.query.limit) q.append('limit', String(req.query.limit));
      if (req.query.search) q.append('search', String(req.query.search));
      if (req.query.includeRegistry) q.append('includeRegistry', String(req.query.includeRegistry));
      if (req.query.authStatus) q.append('authStatus', String(req.query.authStatus));

      const r = await executeConnectorCommand(
        buildUrl(appConfig, '/my-mcp-servers', q),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Get my MCP servers', 'Not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get my MCP servers'));
    }
  };

// ============================================================================
// Tool Discovery
// ============================================================================

export const discoverInstanceTools =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/instances/${instanceId}/tools`),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Discover MCP tools', 'Tools not found');
    } catch (error: any) {
      next(handleBackendError(error, 'discover MCP tools'));
    }
  };

// ============================================================================
// Agent-Scoped
// ============================================================================

export const getAgentMcpServers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { agentKey } = req.params;
      const q = new URLSearchParams();
      if (req.query.page) q.append('page', String(req.query.page));
      if (req.query.limit) q.append('limit', String(req.query.limit));
      if (req.query.search) q.append('search', String(req.query.search));
      if (req.query.includeRegistry) q.append('includeRegistry', String(req.query.includeRegistry));

      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/agents/${agentKey}`, q),
        HttpMethod.GET,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Get agent MCP servers', 'Not found');
    } catch (error: any) {
      next(handleBackendError(error, 'get agent MCP servers'));
    }
  };

export const authenticateAgentInstance =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { agentKey, instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/agents/${agentKey}/instances/${instanceId}/authenticate`),
        HttpMethod.POST,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Authenticate agent MCP instance', 'Auth failed');
    } catch (error: any) {
      next(handleBackendError(error, 'authenticate agent MCP instance'));
    }
  };

export const updateAgentCredentials =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { agentKey, instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/agents/${agentKey}/instances/${instanceId}/credentials`),
        HttpMethod.PUT,
        await forwardHeaders(req),
        req.body,
      );
      handleConnectorResponse(r, res, 'Update agent credentials', 'Failed');
    } catch (error: any) {
      next(handleBackendError(error, 'update agent MCP credentials'));
    }
  };

export const removeAgentCredentials =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      requireUser(req);
      const { agentKey, instanceId } = req.params;
      const r = await executeConnectorCommand(
        buildUrl(appConfig, `/agents/${agentKey}/instances/${instanceId}/credentials`),
        HttpMethod.DELETE,
        await forwardHeaders(req),
      );
      handleConnectorResponse(r, res, 'Remove agent credentials', 'Failed');
    } catch (error: any) {
      next(handleBackendError(error, 'remove agent MCP credentials'));
    }
  };
