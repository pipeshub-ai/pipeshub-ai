/**
 * MCP (Model Context Protocol) Controller
 *
 * Handles MCP JSON-RPC requests by maintaining a per-session MCP server
 * connected via StreamableHTTP transport. Sessions survive across multiple
 * HTTP requests so clients like mcp-remote can initialize once and then
 * issue follow-up tool calls against the same session ID.
 */

import { randomUUID } from 'crypto';
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
 * In-memory registry of active MCP transports keyed by session ID.
 *
 * Sessions are cleaned up automatically when the underlying transport closes.
 * For a single-process Node API this is sufficient; if this service is ever
 * horizontally scaled, session affinity must be solved at the load-balancer
 * (sticky sessions) or this map replaced with a shared store (e.g. Redis).
 */
const transports: Record<string, StreamableHTTPServerTransport> = {};

/**
 * Inline initialize-request check — avoids relying on the SDK's
 * `isInitializeRequest` helper whose subpath export is unreliable across
 * bundler/runtime combinations. Handles both single messages and batched
 * JSON-RPC arrays, matching the SDK's own semantics.
 */
const isInitializeRequest = (body: unknown): boolean => {
  const check = (m: unknown): boolean =>
    typeof m === 'object' && m !== null && (m as any).method === 'initialize';
  return Array.isArray(body) ? body.some(check) : check(body);
};

/**
 * Normalize the `mcp-session-id` header.
 * Express types it as `string | string[] | undefined` (HTTP allows repeated
 * headers); we always use the first value.
 */
const getSessionId = (
  header: string | string[] | undefined,
): string | undefined => (Array.isArray(header) ? header[0] : header);

/**
 * Handle an MCP JSON-RPC request (initialize, tool calls, SSE, session termination).
 *
 * Session lifecycle follows the official MCP StreamableHTTP pattern:
 *   - existing mcp-session-id → reuse the stored transport
 *   - no session id + initialize request → create a new server + transport
 *   - anything else → 400 Bad Request
 */
export const handleMCPRequest =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const sessionId = getSessionId(req.headers['mcp-session-id']);
      const serverURL = `${appConfig.oauthBackendUrl}/api/v1`;

      if (sessionId && transports[sessionId]) {
        // Reuse existing session
        await transports[sessionId].handleRequest(req, res, req.body);
        return;
      }

      if (!sessionId && isInitializeRequest(req.body)) {
        // New session — create a fresh MCP server bound to this caller's token
        const token = req.headers.authorization?.replace('Bearer ', '') || '';

        const { createMCPServer } = await mcpServerModule;
        const { PipeshubCore } = await coreModule;

        const transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (newSessionId: string) => {
            transports[newSessionId] = transport;
            logger.debug('MCP session initialized', {
              sessionId: newSessionId,
              userId: req.user?.userId,
            });
          },
        });

        transport.onclose = () => {
          const sid = transport.sessionId;
          if (sid && transports[sid]) {
            delete transports[sid];
            logger.debug('MCP session closed', { sessionId: sid });
          }
        };

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
        await transport.handleRequest(req, res, req.body);
        return;
      }

      // No matching session and not an initialize request
      res.status(400).json({
        jsonrpc: '2.0',
        error: {
          code: -32000,
          message:
            'Bad Request: No valid session ID provided, and request is not an initialize request',
        },
        id: null,
      });
    } catch (error: any) {
      logger.error('MCP request failed', {
        error: error.message,
        method: req.method,
        userId: req.user?.userId,
      });
      next(error);
    }
  };
