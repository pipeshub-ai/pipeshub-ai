import { Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { McpClientService } from '../services/mcp-client.service';
import {
  startMcpAuth,
  handleMcpAuthCallback,
  getMcpToken,
  deleteMcpToken,
  listMcpConnections,
  connectMcpBearer,
  getClientMetadata,
} from '../controller/mcp-client.controller';

// ============================================================================
// Validation Schemas
// ============================================================================

const startAuthSchema = z.object({
  body: z.object({
    mcpServerUrl: z.string().url('Invalid MCP server URL'),
    scope: z.string().optional(),
    clientId: z.string().optional(),
    clientSecret: z.string().optional(),
    frontendBaseUrl: z.string().url('Invalid frontend base URL'),
  }),
});

const callbackSchema = z.object({
  body: z.object({
    code: z.string(),
    state: z.string(),
  }),
});

const connectBearerSchema = z.object({
  body: z.object({
    mcpServerUrl: z.string().url('Invalid MCP server URL'),
    bearerToken: z.string().min(1, 'Bearer token is required'),
  }),
});

const mcpServerUrlQuerySchema = z.object({
  query: z.object({
    mcpServerUrl: z.string().url('Invalid MCP server URL'),
  }),
});

// ============================================================================
// Router Factory
// ============================================================================

export function createMcpClientRouter(container: Container): Router {
  const router = Router();
  const service = container.get<McpClientService>('McpClientService');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // GET /client-metadata.json — Public, no auth
  router.get(
    '/client-metadata.json',
    metricsMiddleware(container),
    getClientMetadata(service),
  );

  // POST /auth/start — Authenticated
  router.post(
    '/auth/start',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(startAuthSchema),
    startMcpAuth(service),
  );

  // POST /auth/connect-bearer — Authenticated (bearer token direct connection)
  router.post(
    '/auth/connect-bearer',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectBearerSchema),
    connectMcpBearer(service),
  );

  // POST /auth/callback — Authenticated (frontend calls after IdP redirect)
  router.post(
    '/auth/callback',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(callbackSchema),
    handleMcpAuthCallback(service),
  );

  // GET /auth/token — Authenticated
  router.get(
    '/auth/token',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(mcpServerUrlQuerySchema),
    getMcpToken(service),
  );

  // DELETE /auth/token — Authenticated
  router.delete(
    '/auth/token',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(mcpServerUrlQuerySchema),
    deleteMcpToken(service),
  );

  // GET /auth/connections — Authenticated
  router.get(
    '/auth/connections',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    listMcpConnections(service),
  );

  return router;
}
