import { Response, NextFunction, Request } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { McpClientService } from '../services/mcp-client.service';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({
  service: 'McpClientController',
});

export const startMcpAuth =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      const result = await service.startAuth(orgId, userId, req.body);
      res.status(200).json(result);
    } catch (error: any) {
      logger.error('Error starting MCP OAuth flow', { error: error.message });
      next(error);
    }
  };

export const handleMcpAuthCallback =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { code, state } = req.body;
      const result = await service.handleCallback(code, state);
      res.status(200).json(result);
    } catch (error: any) {
      logger.error('Error handling MCP OAuth callback', { error: error.message });
      next(error);
    }
  };

export const getMcpToken =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      const mcpServerUrl = req.query.mcpServerUrl as string;
      const result = await service.getToken(orgId, userId, mcpServerUrl);
      res.status(200).json(result);
    } catch (error: any) {
      logger.error('Error getting MCP token', { error: error.message });
      next(error);
    }
  };

export const deleteMcpToken =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      const mcpServerUrl = req.query.mcpServerUrl as string;
      await service.deleteToken(orgId, userId, mcpServerUrl);
      res.status(200).json({ success: true });
    } catch (error: any) {
      logger.error('Error deleting MCP token', { error: error.message });
      next(error);
    }
  };

export const listMcpConnections =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      const connections = await service.listConnections(orgId, userId);
      res.status(200).json(connections);
    } catch (error: any) {
      logger.error('Error listing MCP connections', { error: error.message });
      next(error);
    }
  };

export const connectMcpBearer =
  (service: McpClientService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      const result = await service.connectBearer(orgId, userId, req.body);
      res.status(200).json(result);
    } catch (error: any) {
      logger.error('Error connecting MCP bearer token', { error: error.message });
      next(error);
    }
  };

export const getClientMetadata =
  (service: McpClientService) =>
  async (_req: Request, res: Response, next: NextFunction): Promise<void> => {
    try {
      const metadata = service.getClientMetadataDocument();
      res.status(200).json(metadata);
    } catch (error: any) {
      logger.error('Error getting client metadata', { error: error.message });
      next(error);
    }
  };
