import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';

// Type for team list API response
interface TeamListResponse {
  teams?: Array<{ name: string; orgId: string }>;
}

// Constants for the Global Reader team
export const GLOBAL_READER_TEAM_NAME = 'Global Reader';
export const GLOBAL_READER_TEAM_DESCRIPTION =
  'System-managed team for global read access across the organization';

@injectable()
export class GlobalReaderTeamService {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}

  /**
   * Ensures the Global Reader team exists for the given organization.
   * This method is idempotent - safe to call multiple times.
   *
   * @param orgId - The organization ID
   * @param userId - The user ID (creator/admin)
   * @param headers - HTTP headers to pass through (for auth context)
   * @returns Promise<void> - Does not throw on failure, only logs
   */
  async ensureGlobalReaderTeamExists(
    orgId: string,
    userId: string,
    headers: Record<string, string>,
  ): Promise<void> {
    try {
      // Step 1: Check if team already exists
      const exists = await this.checkTeamExists(orgId, headers);
      if (exists) {
        this.logger.info('Global Reader team already exists', { orgId });
        return;
      }

      // Step 2: Create the team
      await this.createTeam(orgId, userId, headers);
      this.logger.info('Global Reader team created successfully', { orgId });
    } catch (error) {
      // Log but don't throw - team creation failure should not block org creation
      this.logger.error('Failed to ensure Global Reader team exists', {
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  private async checkTeamExists(
    orgId: string,
    headers: Record<string, string>,
  ): Promise<boolean> {
    const searchParams = new URLSearchParams({
      search: GLOBAL_READER_TEAM_NAME,
      limit: '100',
    });

    const aiCommandOptions: AICommandOptions = {
      uri: `${this.config.connectorBackend}/api/v1/entity/team/list?${searchParams.toString()}`,
      method: HttpMethod.GET,
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
    };

    const aiCommand = new AIServiceCommand<TeamListResponse>(aiCommandOptions);
    const response = await aiCommand.execute();

    if (response.statusCode !== HTTP_STATUS.OK) {
      this.logger.warn('Failed to check for existing Global Reader team', {
        statusCode: response.statusCode,
        orgId,
      });
      return false;
    }

    // Check if exact match exists for this org
    const teams = response.data?.teams || [];
    return teams.some(
      (team: { name: string; orgId: string }) =>
        team.name === GLOBAL_READER_TEAM_NAME && team.orgId === orgId,
    );
  }

  private async createTeam(
    orgId: string,
    userId: string,
    headers: Record<string, string>,
  ): Promise<void> {
    const teamPayload = {
      name: GLOBAL_READER_TEAM_NAME,
      description: GLOBAL_READER_TEAM_DESCRIPTION,
      orgId,
      createdBy: userId,
    };

    const aiCommandOptions: AICommandOptions = {
      uri: `${this.config.connectorBackend}/api/v1/entity/team`,
      method: HttpMethod.POST,
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
      body: teamPayload,
    };

    const aiCommand = new AIServiceCommand(aiCommandOptions);
    const response = await aiCommand.execute();

    if (response.statusCode !== HTTP_STATUS.OK) {
      throw new Error(
        `Failed to create Global Reader team: ${response.msg || 'Unknown error'}`,
      );
    }
  }
}
