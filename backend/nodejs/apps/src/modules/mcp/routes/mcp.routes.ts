/**
 * MCP (Model Context Protocol) Routes
 *
 * Exposes the Streamable HTTP transport endpoints for MCP clients.
 * All routes require authentication via bearer token or OAuth.
 *
 * @module mcp/routes
 */

import { NextFunction, Request, Response, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../config';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { handleMCPRequest } from '../controller/mcp.controller';

/**
 * Create and configure the MCP router.
 *
 * @param container - Dependency injection container
 * @returns Configured Express router
 */
export function createMCPRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  /**
   * POST /
   * Handles all MCP JSON-RPC requests (initialize, tool calls, etc.)
   */
  router.post(
    '/',
    authMiddleware.authenticate,
    handleMCPRequest(appConfig),
  );

  /**
   * GET /
   * Used by clients for SSE streaming — in stateless mode the transport returns 405
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    handleMCPRequest(appConfig),
  )

  // An MCP client learns where to authorize from this header on a 401 (MCP
  // authorization spec, RFC 9728 section 5.1); without it, one with no token
  // cannot start OAuth. The error then goes on to the app's error handler.
  router.use(
    (err: unknown, _req: Request, res: Response, next: NextFunction) => {
      if (err instanceof UnauthorizedError) {
        res.setHeader(
          'WWW-Authenticate',
          `Bearer resource_metadata="${appConfig.oauthIssuer}/.well-known/oauth-protected-resource/mcp"`,
        );
      }
      next(err);
    },
  );

  return router;
}
