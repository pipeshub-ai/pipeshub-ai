import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import { inject, injectable } from 'inversify';
import { getPictureRepository } from '../../../libs/repositories/picture';
import type {
  TeamCreatedByUser,
  TeamResponse,
  TeamUsersResponse,
  TeamsListResponse,
} from '../types/user_management.types';


import { handleBackendError } from '../../../libs/errors/backend-error';
import { audit } from '../../../libs/services/audit';
import {
  auditTeamMembers,
  auditUserRefusal,
  userRefusal,
  UserAuditReason,
} from '../utils/user-audit';

/** What the team service's refusal status means (the 400s are input errors and are not audited). */
const TEAM_REFUSALS: Readonly<Partial<Record<number, UserAuditReason>>> = {
  [HTTP_STATUS.FORBIDDEN]: 'not_owner',
  [HTTP_STATUS.NOT_FOUND]: 'not_found',
  [HTTP_STATUS.CONFLICT]: 'name_taken',
};

/**
 * The team service's refusal as an error, tagged with why when the status
 * says so. It forbids deleting the org's All team before it checks ownership.
 */
const teamRefusal = (
  aiResponse: { statusCode: number },
  operation: string,
  protectedTeam = false,
): Error => {
  const error = handleBackendError(aiResponse, operation);
  const reason =
    protectedTeam && aiResponse.statusCode === HTTP_STATUS.FORBIDDEN
      ? 'protected_team'
      : TEAM_REFUSALS[aiResponse.statusCode];
  return reason === undefined ? error : userRefusal(error, reason);
};

const asArray = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];

/**
 * The `userId` of each `{ userId, role }` entry. The route schema has already
 * dropped entries without both, as the team service would.
 */
const userIdsOf = (entries: unknown): unknown[] =>
  asArray(entries).map((entry) => (entry as { userId?: unknown } | null)?.userId);

/**
 * Handle AI service response
 * Checks status code and extracts error messages if needed
 */
const handleAIServiceResponse = (
  aiResponse: { statusCode: number; data?: unknown } | undefined,
  res: Response,
  operation: string,
  failureMessage: string,
  successStatus: number = HTTP_STATUS.OK,
  protectedTeam = false,
) => {
  if (!aiResponse) {
    throw new InternalServerError('No response from AI service');
  }
  if (aiResponse.statusCode !== HTTP_STATUS.OK && aiResponse.statusCode !== HTTP_STATUS.CREATED) {
    throw teamRefusal(aiResponse, operation, protectedTeam);
  }
  const responseData = aiResponse.data;
  if (!responseData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  res.status(successStatus).json(responseData);
};

// The team service wraps the team it returns: `{ data: team }` from create,
// `{ team }` from get, update and the member list.
const teamIn = (body: unknown): TeamResponse | undefined => {
  if (typeof body !== 'object' || body === null) return undefined;
  const { team, data } = body as { team?: unknown; data?: unknown };
  const inner: unknown = team ?? data ?? body;
  return typeof inner === 'object' && inner !== null
    ? (inner as TeamResponse)
    : undefined;
};

// Pictures are decoration: a failed lookup must not turn a create the team
// service already committed into an error the client would retry.
async function enrichTeamsProfilePictures(
  orgId: string,
  teams: TeamResponse[],
  logger: Logger,
): Promise<void> {
  const userIds: string[] = [];
  for (const team of teams) {
    if (team.members) {
      for (const member of team.members) {
        if (member?.userId) userIds.push(member.userId);
      }
    }
    const createdByUser = team.createdByUser as TeamCreatedByUser | null | undefined;
    if (createdByUser?.userId) {
      userIds.push(createdByUser.userId);
    }
  }
  if (userIds.length === 0) return;

  const uniqueIds = [...new Set(userIds)];
  let dpDocs;
  try {
    dpDocs = await getPictureRepository().listSet(orgId, uniqueIds);
  } catch (error) {
    logger.warn('Could not look up team profile pictures', { error });
    return;
  }

  const dpMap = new Map<string, string>();
  for (const dp of dpDocs) {
    if (dp.userId && dp.pic) {
      const mime = dp.mimeType || 'image/jpeg';
      dpMap.set(dp.userId, `data:${mime};base64,${dp.pic}`);
    }
  }

  for (const team of teams) {
    if (team.members) {
      for (const member of team.members) {
        if (member?.userId && dpMap.has(member.userId)) {
          member.profilePicture = dpMap.get(member.userId);
        }
      }
    }
    const createdByUser = team.createdByUser as TeamCreatedByUser | null | undefined;
    if (createdByUser?.userId && dpMap.has(createdByUser.userId)) {
      createdByUser.profilePicture = dpMap.get(createdByUser.userId);
    }
  }
}

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
      if (!aiResponse) {
        throw new InternalServerError('No response from AI service');
      }
      if (
        aiResponse.statusCode !== HTTP_STATUS.CREATED &&
        aiResponse.statusCode !== HTTP_STATUS.OK
      ) {
        throw teamRefusal(aiResponse, 'Creating team');
      }
      const teamData = aiResponse.data as TeamResponse | undefined;
      if (!teamData) {
        throw new NotFoundError('Creating team failed: Team not found');
      }
      const created = teamIn(teamData);
      if (created?.id === undefined) {
        // The team exists, but its answer did not say which one.
        audit('team.create', { outcome: 'success', reason: 'no_team_id' });
      } else {
        audit('team.create', {
          target: created.id,
          targetIsTeam: true,
          outcome: 'success',
        });
        const body = (req.body ?? {}) as Record<string, unknown>;
        // The creator joins as owner; the team service skips them in the list.
        const members = userIdsOf(body.userRoles).filter((id) => id !== userId);
        auditTeamMembers('team.member.add', created.id, members);
      }
      if (created !== undefined) {
        await enrichTeamsProfilePictures(orgId, [created], this.logger);
      }
      res.status(HTTP_STATUS.CREATED).json(teamData);
    } catch (error: any) {
      auditUserRefusal('team.create', error);
      const handledError = handleBackendError(error, 'create team');
      next(handledError);
    }
  }

  async getTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const { teamId } = req.params as { teamId: string };
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
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('No response from AI service');
      }
      if (aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw handleBackendError(aiResponse, 'get team');
      }
      const teamData = aiResponse.data as TeamResponse | undefined;
      if (!teamData) {
        throw new NotFoundError('Getting team failed: Team not found');
      }
      const found = teamIn(teamData);
      // Defence in depth: the service scopes by org, but never relay a team
      // from another org even if that scoping regresses. A team the caller
      // may not see is reported as missing, not forbidden.
      if (found === undefined || found.orgId !== orgId) {
        throw new NotFoundError('Team not found');
      }
      await enrichTeamsProfilePictures(orgId, [found], this.logger);
      res.status(HTTP_STATUS.OK).json(teamData);
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get team');
      next(handledError);
    }
  }

  async updateTeam(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const teamId = req.params.teamId as string;
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
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}`,
        method: HttpMethod.PUT,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('No response from AI service');
      }
      if (aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw teamRefusal(aiResponse, 'Updating team');
      }
      this.auditTeamUpdate(teamId, (req.body ?? {}) as Record<string, unknown>);
      const teamData = aiResponse.data as TeamResponse | undefined;
      if (!teamData) {
        throw new NotFoundError('Updating team failed: Team not found');
      }
      const updated = teamIn(teamData);
      if (updated !== undefined) {
        await enrichTeamsProfilePictures(orgId, [updated], this.logger);
      }
      res.status(HTTP_STATUS.OK).json(teamData);
    } catch (error: any) {
      auditUserRefusal('team.update', error, {
        target: teamId,
        targetIsTeam: true,
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
    const teamId = req.params.teamId as string;
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
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}`,
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
        HTTP_STATUS.OK,
        teamId === `all_${orgId}`,
      );
      audit('team.delete', {
        target: teamId,
        targetIsTeam: true,
        outcome: 'success',
      });
    } catch (error: any) {
      auditUserRefusal('team.delete', error, {
        target: teamId,
        targetIsTeam: true,
      });
      const handledError = handleBackendError(error, 'delete team');
      next(handledError);
    }
  }

  async getTeamUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const teamId = req.params.teamId as string;
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
      const qs = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}/users${qs ? `?${qs}` : ''}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand<TeamUsersResponse>(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('No response from AI service');
      }
      if (aiResponse.statusCode !== HTTP_STATUS.OK) {
        throw handleBackendError(aiResponse, 'get team users');
      }

      const data = aiResponse.data;
      const teamData = teamIn(data);

      if (teamData !== undefined) {
        await enrichTeamsProfilePictures(orgId, [teamData], this.logger);
      }

      res.status(HTTP_STATUS.OK).json(data);
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get team users');
      next(handledError);
    }
  }

  async getUserTeams(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }

      const { page, limit, search, created_by, created_after, created_before } = req.query;

      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      if (created_by) queryParams.append('created_by', String(created_by));
      if (created_after) queryParams.append('created_after', String(created_after));
      if (created_before) queryParams.append('created_before', String(created_before));
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/teams${queryString ? `?${queryString}` : ''}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand<TeamsListResponse>(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('No response from AI service');
      }
      // An unreadable database must not look like a person with no teams.
      if (aiResponse.statusCode === HTTP_STATUS.SERVICE_UNAVAILABLE) {
        throw handleBackendError(aiResponse, 'get user teams');
      }
      if (aiResponse.statusCode !== HTTP_STATUS.OK) {
        res.status(HTTP_STATUS.OK).json({
          teams: [],
          pagination: { page: 1, limit: 10, total: 0, pages: 0 },
        });
        return;
      }
      const teamsData = aiResponse.data as TeamsListResponse | undefined;
      if (!teamsData) {
        throw new NotFoundError('Getting user created teams failed: Teams not found');
      }

      const teams = teamsData.teams ?? [];
      if (teams.length > 0) {
        await enrichTeamsProfilePictures(orgId, teams, this.logger);
      }

      res.status(HTTP_STATUS.OK).json(teamsData);
    } catch (error: any) {
      next(error);
    }
  }

  /**
   * The success lines of a team PUT, in the order the team service writes:
   * name and description, removals, role changes, additions.
   */
  private auditTeamUpdate(teamId: string, body: Record<string, unknown>): void {
    // An empty description is a change too.
    if (body.name !== undefined || body.description !== undefined) {
      audit('team.update', {
        target: teamId,
        targetIsTeam: true,
        outcome: 'success',
      });
    }
    auditTeamMembers('team.member.remove', teamId, asArray(body.removeUserIds));
    auditTeamMembers(
      'team.member.role.change',
      teamId,
      userIdsOf(body.updateUserRoles),
    );
    auditTeamMembers('team.member.add', teamId, userIdsOf(body.addUserRoles));
  }
}
