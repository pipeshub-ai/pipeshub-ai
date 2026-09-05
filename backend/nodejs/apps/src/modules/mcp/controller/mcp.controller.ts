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

const logger = Logger.getInstance({
  service: 'MCPController',
});

// ESM-only modules — imported eagerly at module load, Node caches the result
const mcpServerModule = import('@pipeshub-ai/mcp/esm/mcp-server/server.js');
const coreModule = import('@pipeshub-ai/mcp/esm/core.js');

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
      const token = req.headers.authorization?.replace('Bearer ', '') ?? '';
      const serverURL = `${appConfig.oauthBackendUrl}/api/v1`;

      // Read inbound X-Pipeshub-Request-Id header (Express lowercases header keys)
      const requestId = req.headers['x-pipeshub-request-id'] as
        | string
        | undefined;

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

      // Echo the request ID back as a response header if the client sent one
      if (requestId !== undefined && requestId !== '') {
        res.setHeader('X-Pipeshub-Request-Id', requestId);
      }

      await transport.handleRequest(req, res, req.body);

      // Log success with request ID
      logger.info('MCP request completed', {
        method: req.method,
        userId: req.user?.userId as string | undefined,
        requestId,
      });
    } catch (error: unknown) {
      const err = error instanceof Error ? error : new Error(String(error));
      const requestIdHeader = req.headers['x-pipeshub-request-id'] as
        | string
        | undefined;
      logger.error('MCP request failed', {
        error: err.message,
        method: req.method,
        userId: req.user?.userId as string | undefined,
        requestId: requestIdHeader,
      });
      next(error);
    }
  };
