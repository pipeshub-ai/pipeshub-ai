import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';

const logger = Logger.getInstance({ service: 'ProjectTeamMembership' });

/**
 * The connector service caps `limit` at 100 and answers 422 above it, which
 * this module used to read as "no teams", so every team row granted nothing.
 */
const TEAM_MEMBERSHIP_PAGE_SIZE = 100;

/** Bounds the page walk; a caller in more teams than this is under-granted, never over-granted. */
const TEAM_MEMBERSHIP_MAX_PAGES = 20;

interface TeamsListResponseShape {
  teams?: Array<{ id?: string; _id?: string }>;
  pagination?: { pages?: number };
}

/**
 * Per-request memoization for `resolveCallerTeamIds` — `computeRole` runs
 * once per project row in `ProjectService.list()`, and a project route can
 * call `assertAccess` more than once (e.g. read-then-write flows), so
 * without this every one of those would re-issue the same
 * `GET /api/v1/entity/user/teams` call. Keyed by the request object itself
 * (not a custom property on it, since `AuthenticatedUserRequest` doesn't
 * declare one) so entries are released once the request is GC'd — no TTL
 * or manual eviction needed.
 */
const callerTeamIdsCache = new WeakMap<
  AuthenticatedUserRequest,
  Promise<string[]>
>();

/**
 * Resolves the org/team graph ids of every team the requesting user
 * belongs to (Python's `entity/user/teams`, the same graph "team" concept
 * KB sharing and `ShareCommonApi.listUserTeams()` already use) — the
 * membership check `ProjectService.computeRole` needs to grant a project's
 * `team` member rows the same access a matching `user` row would get.
 *
 * Never throws: a team-lookup failure degrades to "caller belongs to no
 * teams" (user-direct and org-visibility access still apply) rather than
 * failing the whole project request over a non-critical enrichment call.
 */
export async function resolveCallerTeamIds(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
): Promise<string[]> {
  const cached = callerTeamIdsCache.get(req);
  if (cached) return cached;

  const promise = fetchCallerTeamIds(req, appConfig);
  callerTeamIdsCache.set(req, promise);
  return promise;
}

async function fetchCallerTeamIds(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
): Promise<string[]> {
  try {
    const teamIds: string[] = [];
    for (let page = 1; page <= TEAM_MEMBERSHIP_MAX_PAGES; page += 1) {
      const commandOptions: AICommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/entity/user/teams?page=${String(page)}&limit=${String(TEAM_MEMBERSHIP_PAGE_SIZE)}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const response = await new AIServiceCommand<TeamsListResponseShape>(
        commandOptions,
      ).execute();
      if (response.statusCode !== HTTP_STATUS.OK || !response.data) {
        logger.warn('Team lookup for project access did not succeed', {
          statusCode: response.statusCode,
          page,
        });
        return teamIds;
      }
      const teams = response.data.teams ?? [];
      for (const team of teams) {
        const id = team.id ?? team._id;
        if (id !== undefined && id !== '') teamIds.push(id);
      }
      const pages = response.data.pagination?.pages;
      const lastPage =
        typeof pages === 'number'
          ? page >= pages
          : teams.length < TEAM_MEMBERSHIP_PAGE_SIZE;
      if (lastPage) break;
    }
    return teamIds;
  } catch (error) {
    logger.warn('Failed to resolve caller team memberships for project access', {
      error: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
}
