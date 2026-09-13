/**
 * MCP (Model Context Protocol) Controller
 *
 * Handles MCP JSON-RPC requests by creating a per-request MCP server
 * connected via StreamableHTTP transport.
 */

import { Response, NextFunction } from 'express';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger, getLogLevel } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { recordEvent } from '../../../libs/services/telemetry/event-buffer';
import { domainFromEmail } from '../../../libs/services/telemetry/identity';
import { PAT_APP_CLIENT_ID_PREFIX } from '../../oauth_provider/constants/constants';

const logger = Logger.getInstance({
  service: 'MCPController',
});

// ESM-only modules — imported eagerly at module load, Node caches the result
const mcpServerModule = import('@pipeshub-ai/mcp/esm/mcp-server/server.js');
const coreModule = import('@pipeshub-ai/mcp/esm/core.js');

/**
 * Activation signals for the MCP path. The transport is stateless, so every
 * request carries one JSON-RPC message; `initialize` is a client connecting,
 * `tools/call` is it doing work. Only names are recorded, never arguments.
 */
function recordMcpEvent(req: AuthenticatedUserRequest): void {
  const body = req.body as
    | { method?: unknown; params?: Record<string, unknown> }
    | undefined;
  const method = typeof body?.method === 'string' ? body.method : undefined;
  if (method !== 'initialize' && method !== 'tools/call') return;

  const user = req.user ?? {};
  const email = typeof user.email === 'string' ? user.email : undefined;
  const clientId =
    typeof user.oauthClientId === 'string' ? user.oauthClientId : '';
  const authType = clientId.startsWith(PAT_APP_CLIENT_ID_PREFIX)
    ? 'pat'
    : user.isOAuth === true
      ? 'oauth'
      : 'session';
  const base = {
    orgId: typeof user.orgId === 'string' ? user.orgId : undefined,
    userId: typeof user.userId === 'string' ? user.userId : undefined,
    email,
    domain: domainFromEmail(email),
    auth_type: authType,
  };

  if (method === 'initialize') {
    const clientInfo = (body?.params?.clientInfo ?? {}) as Record<
      string,
      unknown
    >;
    recordEvent('mcp_connected', {
      ...base,
      client_name:
        typeof clientInfo.name === 'string' ? clientInfo.name : undefined,
      client_version:
        typeof clientInfo.version === 'string' ? clientInfo.version : undefined,
    });
    return;
  }
  recordEvent('mcp_tool_called', {
    ...base,
    tool: typeof body?.params?.name === 'string' ? body.params.name : undefined,
  });
}

/**
 * Handle an MCP JSON-RPC request (initialize, tool calls, SSE, session termination).
 * Creates a stateless MCP server per request, connected to the PipeshubCore SDK
 * using the caller's bearer token.
 */
export const handleMCPRequest =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract the raw Bearer token from the Authorization header for the MCP SDK
      const token = req.headers.authorization?.replace('Bearer ', '') || '';
      const serverURL = `${appConfig.oauthBackendUrl}/api/v1`;

      const { createMCPServer } = await mcpServerModule;
      const { PipeshubCore } = await coreModule;

      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: undefined,
      });

      const { server: mcpServer } = createMCPServer({
        logger: {
          level: getLogLevel(),
          info: logger.info.bind(logger),
          debug: logger.debug.bind(logger),
          warning: logger.warn.bind(logger),
          error: logger.error.bind(logger),
        },
        dynamic: false,
        serverURL,
        getSDK: () =>
          new PipeshubCore({
            security: { bearerAuth: token },
            serverURL,
          }),
      });

      await mcpServer.connect(transport);
      recordMcpEvent(req);
      await transport.handleRequest(req, res, req.body);
    } catch (error: any) {
      logger.error('MCP request failed', {
        error: error.message,
        method: req.method,
        userId: req.user?.userId,
      });
      next(error);
    }
  };
