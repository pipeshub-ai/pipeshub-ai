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
import { recordServiceActivity } from '../../../libs/services/telemetry/modules/activity-metrics';
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
/** What every MCP event carries. The address itself is never included: the
 *  domain says which organisation without naming a person, and the user id
 *  already identifies them where that is needed. */
function mcpEventBase(req: AuthenticatedUserRequest): Record<string, unknown> {
  const user = req.user ?? {};
  const email = typeof user.email === 'string' ? user.email : undefined;
  const clientId =
    typeof user.oauthClientId === 'string' ? user.oauthClientId : '';
  const authType = clientId.startsWith(PAT_APP_CLIENT_ID_PREFIX)
    ? 'pat'
    : user.isOAuth === true
      ? 'oauth'
      : 'session';
  return {
    orgId: typeof user.orgId === 'string' ? user.orgId : undefined,
    userId: typeof user.userId === 'string' ? user.userId : undefined,
    domain: domainFromEmail(email),
    auth_type: authType,
  };
}

/** Props for `mcp_connected`, or undefined when this request is not an
 *  initialize. Recording is left to the caller, which has to wait for the
 *  handshake to actually succeed. */
function mcpConnectedProps(
  req: AuthenticatedUserRequest,
): Record<string, unknown> | undefined {
  const body = req.body as
    | { method?: unknown; params?: Record<string, unknown> }
    | undefined;
  if (body?.method !== 'initialize') return undefined;
  const clientInfo = (body.params?.clientInfo ?? {}) as Record<string, unknown>;
  return {
    ...mcpEventBase(req),
    client_name:
      typeof clientInfo.name === 'string' ? clientInfo.name : undefined,
    client_version:
      typeof clientInfo.version === 'string' ? clientInfo.version : undefined,
  };
}

/** Props for `mcp_tool_called`, or undefined when this request is not a
 *  tools/call. Recorded once the transport has served the request, so a
 *  request the server could not handle at all is not counted as a call. */
function mcpToolCallProps(
  req: AuthenticatedUserRequest,
): Record<string, unknown> | undefined {
  const body = req.body as
    | { method?: unknown; params?: Record<string, unknown> }
    | undefined;
  if (body?.method !== 'tools/call') return undefined;
  return {
    ...mcpEventBase(req),
    tool: typeof body.params?.name === 'string' ? body.params.name : undefined,
  };
}

/** The Grafana counter for the same step: org and domain only. */
function recordActivityFromProps(
  activityName: string,
  props: Record<string, unknown>,
): void {
  recordServiceActivity(activityName, {
    org: typeof props.orgId === 'string' ? props.orgId : undefined,
    domain: typeof props.domain === 'string' ? props.domain : undefined,
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
      const connectedProps = mcpConnectedProps(req);
      const toolCallProps = mcpToolCallProps(req);
      await transport.handleRequest(req, res, req.body);
      // Each request gets its own server, so the client's later
      // "initialized" notification never reaches this one. Count an accepted
      // initialize: a refused one can still answer HTTP 200 with a JSON-RPC
      // error, and the server keeps the client's info only once it accepts.
      if (connectedProps && res.statusCode < 400 && mcpServer.server.getClientVersion()) {
        recordEvent('mcp_connected', connectedProps);
        recordActivityFromProps('mcp_connected', connectedProps);
      }
      // The transport answers JSON-RPC errors (unknown tool, tool failure)
      // inside a 200 body, so this counts "served", not "succeeded"; a
      // transport failure throws above and is not counted.
      if (toolCallProps && res.statusCode < 400) {
        recordEvent('mcp_tool_called', toolCallProps);
        recordActivityFromProps('mcp_tool_called', toolCallProps);
      }
    } catch (error: any) {
      logger.error('MCP request failed', {
        error: error.message,
        method: req.method,
        userId: req.user?.userId,
      });
      next(error);
    }
  };
