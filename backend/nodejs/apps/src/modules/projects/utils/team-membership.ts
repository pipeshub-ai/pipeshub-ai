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
 * Generously large so one page covers virtually every org — team
 * membership resolution here is a best-effort access-check input, not a
 * paginated listing UI, so silently missing a team beyond this page only
 * risks under-granting access, never over-granting it.
 */
const TEAM_MEMBERSHIP_FETCH_LIMIT = 500;

interface TeamsListResponseShape {
  teams?: Array<{ id?: string; _id?: string }>;
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
    const commandOptions: AICommandOptions = {
      uri: `${appConfig.connectorBackend}/api/v1/entity/user/teams?limit=${TEAM_MEMBERSHIP_FETCH_LIMIT}`,
      method: HttpMethod.GET,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
    };
    const response =
      await new AIServiceCommand<TeamsListResponseShape>(
        commandOptions,
      ).execute();
    if (response.statusCode !== HTTP_STATUS.OK || !response.data) {
      return [];
    }
    const teams = response.data.teams ?? [];
    return teams
      .map((team) => team.id ?? team._id)
      .filter((id): id is string => Boolean(id));
  } catch (error) {
    logger.warn('Failed to resolve caller team memberships for project access', {
      error: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
}
