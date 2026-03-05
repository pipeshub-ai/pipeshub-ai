/**
 * MCP (Model Context Protocol) Routes
 *
 * Exposes the Streamable HTTP transport endpoints for MCP clients.
 * All routes require authentication via bearer token or OAuth.
 *
 * @module mcp/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
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
 
  return router;
}
