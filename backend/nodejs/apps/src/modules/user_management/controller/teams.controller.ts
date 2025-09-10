import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import { inject, injectable } from 'inversify';

@injectable()
export class TeamsController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}

  async createTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    this.logger.info('Creating team', {
      requestId: req.context?.requestId,
      userId: req.user?.userId,
      orgId: req.user?.orgId,
    });
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to create team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.CREATED).json(team);
    } catch (error: any) {
      this.logger.error('Error creating team', {
        requestId,
        message: 'Error creating team',
        error: error.message,
      });
      next(error);
    }
  }

  async getTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    const { teamId } = req.params;
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    if (!orgId) {
      throw new BadRequestError('Organization ID is required');
    }
    if (!userId) {
      throw new BadRequestError('User ID is required');
    }
    try {
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to get team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error getting team', {
        requestId,
        message: 'Error getting team',
        error: error.message,
      });
      next(error);
    }
  }

  async listTeams(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }

      const { page, limit, search } = req.query;
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/list?${queryString}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to get teams');
      }
      const teams = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(teams);
    } catch (error: any) {
      this.logger.error('Error getting teams', {
        requestId,
        message: 'Error getting teams',
        error: error.message,
      });
      next(error);
    }
  }

  async addUsersToTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}/users`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to add users to team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error adding users to team', {
        requestId,
        message: 'Error adding users to team',
        error: error.message,
      });
      next(error);
    }
  }

  async updateTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}`,
        method: HttpMethod.PUT,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to update team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error updating team', {
        requestId,
        message: 'Error updating team',
        error: error.message,
      });
      next(error);
    }
  }

  async deleteTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}`,
        method: HttpMethod.DELETE,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to delete team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error deleting team', {
        requestId,
        message: 'Error deleting team',
        error: error.message,
      });
      next(error);
    }
  }

  async removeUserFromTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}/users`,
        method: HttpMethod.DELETE,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to remove user from team');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error removing user from team', {
        requestId,
        message: 'Error removing user from team',
        error: error.message,
      });
      next(error);
    }
  }

  async getTeamUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}/users`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to get team users');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error getting team users', {
        requestId,
        message: 'Error getting team users',
        error: error.message,
      });
      next(error);
    }
  }

  async getUserTeams(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/teams`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        res.status(HTTP_STATUS.OK).json([]);
        return;
      }
      const teams = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(teams);
    } catch (error: any) {
      this.logger.error('Error getting user teams', {
        requestId,
        message: 'Error getting user teams',
        error: error.message,
      });
      next(error);
    }
  }

  async updateTeamUsersPermissions(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${teamId}/users/permissions`,
        method: HttpMethod.PUT,
        body: req.body,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw new BadRequestError('Failed to update team users permissions');
      }
      const team = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(team);
    } catch (error: any) {
      this.logger.error('Error updating team users permissions', {
        requestId,
        message: 'Error updating team users permissions',
        error: error.message,
      });
      next(error);
    }
  }
}
