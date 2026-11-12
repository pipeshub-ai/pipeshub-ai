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
import { BaseError } from '../../../libs/errors/base.error';
import { AppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { OAuthTokenService } from '../../oauth_provider/services/oauth_token.service';
import { handleMCPRequest } from '../controller/mcp.controller';
import {
  mcpOriginGuard,
  mcpRateLimiters,
  mcpTokenGuard,
  sendMcpError,
} from '../middlewares/mcp-guard.middleware';

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
  const limits = mcpRateLimiters(
    container.get<Logger>('Logger'),
    appConfig.maxRequestsPerMinute,
  );
  const guard = [
    mcpOriginGuard([appConfig.oauthIssuer, appConfig.frontendUrl]),
    limits.rejectedTokens,
    authMiddleware.authenticate,
    mcpTokenGuard(
      () => container.get<OAuthTokenService>('OAuthTokenService').mcpResource,
    ),
    limits.perAccount,
  ];

  /**
   * POST /
   * Handles all MCP JSON-RPC requests (initialize, tool calls, etc.)
   */
  router.post('/', ...guard, handleMCPRequest(appConfig));

  /**
   * GET /
   * Used by clients for SSE streaming. Each request gets its own server that is
   * gone once it answers, so a GET stream would never carry a message: refuse it
   * with 405, which the MCP spec lets a client take as "no SSE stream here".
   */
  router.get('/', ...guard, (_req: Request, res: Response) => {
    res.set('Allow', 'POST');
    sendMcpError(res, 405, 'Method not allowed.');
  });

  // An MCP client learns where to authorize from this header on a 401 (MCP
  // authorization spec, RFC 9728 section 5.1); without it, one with no token,
  // or with an expired or revoked one, cannot start OAuth again. Every 401
  // counts: a rejected bearer token is an OAuth error class, not
  // UnauthorizedError. The error then goes on to the app's error handler.
  router.use(
    (err: unknown, _req: Request, res: Response, next: NextFunction) => {
      if (err instanceof BaseError && err.statusCode === 401) {
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
