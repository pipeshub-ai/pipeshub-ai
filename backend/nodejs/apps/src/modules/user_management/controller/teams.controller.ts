import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import { inject, injectable } from 'inversify';
import { validateNoFormatSpecifiers, validateNoXSS } from '../../../utils/xss-sanitization';

const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

/**
 * Handle backend errors from AI service responses
 * Extracts error messages from response data and creates appropriate HTTP errors
 */
const handleBackendError = (error: any, operation: string): Error => {
  if (error) {
    if (
      (error?.cause && error.cause.code === 'ECONNREFUSED') ||
      (typeof error?.message === 'string' &&
        error.message.includes('fetch failed'))
    ) {
      return new ServiceUnavailableError(
        AI_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    const { statusCode, data, message } = error;
    const errorDetail =
      data?.detail ||
      data?.reason ||
      data?.message ||
      message ||
      'Unknown error';

    if (errorDetail === 'ECONNREFUSED') {
      return new ServiceUnavailableError(
        AI_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (statusCode) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 409:
        return new ConflictError(errorDetail);
      case 500:
        return new InternalServerError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

/**
 * Handle AI service response
 * Checks status code and extracts error messages if needed
 */
const handleAIServiceResponse = (
  aiResponse: any,
  res: Response,
  operation: string,
  failureMessage: string,
  successStatus: number = HTTP_STATUS.OK,
) => {
  if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK && aiResponse.statusCode !== HTTP_STATUS.CREATED) {
    throw handleBackendError(aiResponse, operation);
  }
  const responseData = aiResponse.data;
  if (!responseData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  res.status(successStatus).json(responseData);
};

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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Creating team',
        'Team creation failed',
        HTTP_STATUS.CREATED,
      );
    } catch (error: any) {
      this.logger.error('Error creating team', {
        requestId,
        message: 'Error creating team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'create team');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Getting team',
        'Team not found',
      );
    } catch (error: any) {
      this.logger.error('Error getting team', {
        requestId,
        message: 'Error getting team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'get team');
      next(handledError);
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
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(String(search), 'search parameter');
          validateNoFormatSpecifiers(String(search), 'search parameter');
          
          if (String(search).length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Getting teams',
        'Teams not found',
      );
    } catch (error: any) {
      this.logger.error('Error getting teams', {
        requestId,
        message: 'Error getting teams',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'get teams');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Adding users to team',
        'Failed to add users to team',
      );
    } catch (error: any) {
      this.logger.error('Error adding users to team', {
        requestId,
        message: 'Error adding users to team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'add users to team');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Updating team',
        'Failed to update team',
      );
    } catch (error: any) {
      this.logger.error('Error updating team', {
        requestId,
        message: 'Error updating team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'update team');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Deleting team',
        'Failed to delete team',
      );
    } catch (error: any) {
      this.logger.error('Error deleting team', {
        requestId,
        message: 'Error deleting team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'delete team');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Removing user from team',
        'Failed to remove user from team',
      );
    } catch (error: any) {
      this.logger.error('Error removing user from team', {
        requestId,
        message: 'Error removing user from team',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'remove user from team');
      next(handledError);
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Getting team users',
        'Team users not found',
      );
    } catch (error: any) {
      this.logger.error('Error getting team users', {
        requestId,
        message: 'Error getting team users',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'get team users');
      next(handledError);
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

      const { page, limit, search } = req.query;
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(String(search), 'search parameter');
          validateNoFormatSpecifiers(String(search), 'search parameter');
          
          if (String(search).length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/teams${queryString ? `?${queryString}` : ''}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== HTTP_STATUS.OK) {
        res.status(HTTP_STATUS.OK).json({ teams: [], pagination: { page: 1, limit: 10, total: 0, pages: 0 } });
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
      handleAIServiceResponse(
        aiResponse,
        res,
        'Updating team users permissions',
        'Failed to update team users permissions',
      );
    } catch (error: any) {
      this.logger.error('Error updating team users permissions', {
        requestId,
        message: 'Error updating team users permissions',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'update team users permissions');
      next(handledError);
    }
  }

  async getUserCreatedTeams(
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
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(String(search), 'search parameter');
          validateNoFormatSpecifiers(String(search), 'search parameter');
          
          if (String(search).length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/teams/created?${queryString}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      handleAIServiceResponse(
        aiResponse,
        res,
        'Getting user created teams',
        'User created teams not found',
      );
    } catch (error: any) {
      this.logger.error('Error getting user created teams', {
        requestId,
        message: 'Error getting user created teams',
        error: error.message,
      });
      const handledError = handleBackendError(error, 'get user created teams');
      next(handledError);
    }
  }
}
