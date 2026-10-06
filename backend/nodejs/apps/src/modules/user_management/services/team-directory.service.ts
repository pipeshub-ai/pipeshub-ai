import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { Logger } from '../../../libs/services/logger.service';
import { CallerIdentity } from '../../../libs/types/caller-identity';
import { AppConfig } from '../../tokens_manager/config/config';
import { CachedTeamDirectory, getTeamIdsCache } from './cached-team-directory';

const TEAM_IDS_TIMEOUT_MS = 2000;
const TEAM_MEMBERS_TIMEOUT_MS = 2000;
const TEAM_MEMBERS_PAGE_MAX = 100;

export type TeamIdsResult =
  | { readonly status: 'ok'; readonly teamIds: readonly string[] }
  | { readonly status: 'unresolved' };

export type TeamMembersResult =
  | { readonly status: 'ok'; readonly userIds: readonly string[] }
  | { readonly status: 'unresolved' };

export interface ITeamDirectory {
  /** Never throws. */
  callerTeamIds(identity: CallerIdentity): Promise<TeamIdsResult>;
  /** Never throws; 0 when no shared cache holds a version. Part of the decision-cache key. */
  teamsVersion(orgId: string): Promise<number>;
  /** Never throws; false on a non-200 or a transport error. */
  exists(teamId: string, identity: CallerIdentity): Promise<boolean>;
  /**
   * Active members of a team, as the caller. Never throws and never caches. A team with more than
   * `limit` members is `unresolved`, not truncated: notifying part of a team is worse than none.
   */
  memberUserIds(
    teamId: string,
    identity: CallerIdentity,
    opts: { limit: number },
  ): Promise<TeamMembersResult>;
}

interface TeamIdsResponseShape {
  teamIds?: unknown;
}

interface TeamMembersResponseShape {
  team?: { members?: unknown; memberCount?: unknown };
}

export class ConnectorTeamDirectory implements ITeamDirectory {
  constructor(
    private readonly appConfig: AppConfig,
    private readonly logger: Logger,
  ) {}

  async callerTeamIds(identity: CallerIdentity): Promise<TeamIdsResult> {
    try {
      const response = await new AIServiceCommand<TeamIdsResponseShape>({
        uri: `${this.appConfig.connectorBackend}/api/v1/entity/user/team-ids`,
        method: HttpMethod.GET,
        headers: {
          ...identity.authHeaders,
          'Content-Type': 'application/json',
        },
        timeoutMs: TEAM_IDS_TIMEOUT_MS,
      }).execute();
      if (response.statusCode !== HTTP_STATUS.OK) {
        this.logger.warn('Caller team-id lookup returned a non-200 response', {
          statusCode: response.statusCode,
        });
        return { status: 'unresolved' };
      }
      const teamIds = response.data?.teamIds;
      if (!Array.isArray(teamIds)) {
        this.logger.warn('Caller team-id lookup response has no teamIds', {
          statusCode: response.statusCode,
        });
        return { status: 'unresolved' };
      }
      return {
        status: 'ok',
        teamIds: teamIds.filter(
          (id): id is string => typeof id === 'string' && id.length > 0,
        ),
      };
    } catch (error) {
      this.logger.warn(
        'Failed to resolve caller team memberships for project access',
        { error: error instanceof Error ? error.message : String(error) },
      );
      return { status: 'unresolved' };
    }
  }

  teamsVersion(): Promise<number> {
    return Promise.resolve(0);
  }

  async exists(teamId: string, identity: CallerIdentity): Promise<boolean> {
    try {
      const response = await new AIServiceCommand<unknown>({
        uri: `${this.appConfig.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}`,
        method: HttpMethod.GET,
        headers: identity.authHeaders as Record<string, string>,
      }).execute();
      return response.statusCode === HTTP_STATUS.OK;
    } catch {
      return false;
    }
  }

  async memberUserIds(
    teamId: string,
    identity: CallerIdentity,
    opts: { limit: number },
  ): Promise<TeamMembersResult> {
    const limit = Math.min(opts.limit, TEAM_MEMBERS_PAGE_MAX);
    try {
      // One more than the cap is enough to tell "exactly at the cap" from "over it".
      const pageSize = Math.min(limit + 1, TEAM_MEMBERS_PAGE_MAX);
      const response = await new AIServiceCommand<TeamMembersResponseShape>({
        uri: `${this.appConfig.connectorBackend}/api/v1/entity/team/${encodeURIComponent(teamId)}/users?page=1&limit=${String(pageSize)}`,
        method: HttpMethod.GET,
        headers: {
          ...identity.authHeaders,
          'Content-Type': 'application/json',
        },
        timeoutMs: TEAM_MEMBERS_TIMEOUT_MS,
      }).execute();
      const team = response.data?.team;
      if (
        response.statusCode !== HTTP_STATUS.OK ||
        !Array.isArray(team?.members)
      ) {
        this.logger.warn('Team member lookup returned an unusable response', {
          statusCode: response.statusCode,
        });
        return { status: 'unresolved' };
      }
      const userIds = (team.members as Array<{ userId?: unknown }>).flatMap(
        (member) =>
          typeof member.userId === 'string' && member.userId !== ''
            ? [member.userId]
            : [],
      );
      const total =
        typeof team.memberCount === 'number'
          ? team.memberCount
          : userIds.length;
      return total > limit
        ? { status: 'unresolved' }
        : { status: 'ok', userIds };
    } catch (error) {
      this.logger.warn('Team member lookup failed', {
        error: error instanceof Error ? error.message : String(error),
      });
      return { status: 'unresolved' };
    }
  }
}

/** Memoizes the in-flight lookup per request so concurrent and repeated calls share one HTTP call. */
export class RequestScopedTeamDirectory implements ITeamDirectory {
  private readonly cache = new WeakMap<object, Promise<TeamIdsResult>>();

  constructor(private readonly inner: ITeamDirectory) {}

  callerTeamIds(identity: CallerIdentity): Promise<TeamIdsResult> {
    const cached = this.cache.get(identity.requestKey);
    if (cached) return cached;
    const pending = this.inner.callerTeamIds(identity);
    this.cache.set(identity.requestKey, pending);
    return pending;
  }

  teamsVersion(orgId: string): Promise<number> {
    return this.inner.teamsVersion(orgId);
  }

  exists(teamId: string, identity: CallerIdentity): Promise<boolean> {
    return this.inner.exists(teamId, identity);
  }

  memberUserIds(
    teamId: string,
    identity: CallerIdentity,
    opts: { limit: number },
  ): Promise<TeamMembersResult> {
    return this.inner.memberUserIds(teamId, identity, opts);
  }
}

const requestScopedDirectories = new WeakMap<AppConfig, ITeamDirectory>();

// Logger is a process-wide singleton whose getInstance(config) rewrites the
// shared `service` meta, so it must not run as a per-call default.
const defaultLogger = Logger.getInstance({ service: 'TeamDirectory' });

/** One shared request-scoped directory per AppConfig; `exists` is never memoised. */
export function teamDirectoryFor(
  appConfig: AppConfig,
  logger: Logger = defaultLogger,
): ITeamDirectory {
  let directory = requestScopedDirectories.get(appConfig);
  if (!directory) {
    directory = new RequestScopedTeamDirectory(
      new CachedTeamDirectory(
        new ConnectorTeamDirectory(appConfig, logger),
        getTeamIdsCache,
        logger,
      ),
    );
    requestScopedDirectories.set(appConfig, directory);
  }
  return directory;
}
