import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { Logger } from '../../../libs/services/logger.service';
import { CallerIdentity } from '../../../libs/types/caller-identity';
import { AppConfig } from '../../tokens_manager/config/config';

const TEAM_LOOKUP_TIMEOUT_MS = 2000;
const TEAM_LOOKUP_CONCURRENCY = 8;

export type TeamInfo =
  | { status: 'ok'; name: string }
  | { status: 'missing' }
  | { status: 'unavailable' };

export interface ITeamLookup {
  /** Never throws; a team that cannot be read is `unavailable`, not `missing`. */
  describeMany(
    teamIds: readonly string[],
    identity: CallerIdentity,
  ): Promise<ReadonlyMap<string, TeamInfo>>;
}

interface TeamResponseShape {
  team?: { name?: unknown };
}

const defaultLogger = Logger.getInstance({ service: 'TeamLookup' });

/** Reads team names from the connector service as the caller; bounded parallelism, no cross-request cache. */
export class ConnectorTeamLookup implements ITeamLookup {
  constructor(
    private readonly appConfig: AppConfig,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
  ) {}

  async describeMany(
    teamIds: readonly string[],
    identity: CallerIdentity,
  ): Promise<ReadonlyMap<string, TeamInfo>> {
    const unique = [...new Set(teamIds)];
    const result = new Map<string, TeamInfo>();
    for (let i = 0; i < unique.length; i += TEAM_LOOKUP_CONCURRENCY) {
      const batch = unique.slice(i, i + TEAM_LOOKUP_CONCURRENCY);
      const infos = await Promise.all(
        batch.map((id) => this.describe(id, identity)),
      );
      batch.forEach((id, index) => {
        result.set(id, infos[index] ?? { status: 'unavailable' });
      });
    }
    return result;
  }

  private async describe(
    teamId: string,
    identity: CallerIdentity,
  ): Promise<TeamInfo> {
    try {
      const response = await new AIServiceCommand<TeamResponseShape>({
        uri: `${this.appConfig.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}`,
        method: HttpMethod.GET,
        headers: identity.authHeaders as Record<string, string>,
        timeoutMs: TEAM_LOOKUP_TIMEOUT_MS,
      }).execute();
      if (response.statusCode === HTTP_STATUS.NOT_FOUND) {
        return { status: 'missing' };
      }
      const name = response.data?.team?.name;
      return response.statusCode === HTTP_STATUS.OK && typeof name === 'string'
        ? { status: 'ok', name }
        : { status: 'unavailable' };
    } catch (error) {
      this.logger.warn('Team lookup failed', {
        error: error instanceof Error ? error.message : String(error),
      });
      return { status: 'unavailable' };
    }
  }
}
