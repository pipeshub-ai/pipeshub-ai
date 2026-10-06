import { ICacheService } from '../../../libs/services/cache/cacheService.interface';
import { Logger } from '../../../libs/services/logger.service';
import {
  recordTeamIdsCache,
  recordTeamIdsUpstream,
} from '../../../libs/services/telemetry/modules/team-ids-cache-metrics';
import { CallerIdentity } from '../../../libs/types/caller-identity';
import {
  ITeamDirectory,
  TeamIdsResult,
  TeamMembersResult,
} from './team-directory.service';

export const TEAM_IDS_CACHE_TTL_SECONDS = 30;

export const teamIdsKey = (
  orgId: string,
  userId: string,
  teamsVersion: number,
): string => `teamids:v1:${orgId}:${userId}:${String(teamsVersion)}`;

export const teamsVersionKey = (orgId: string): string =>
  `teamids:ver:${orgId}`;

let sharedCache: ICacheService | undefined;

/** Set once at boot; until then lookups pass straight through to the connector. */
export function useTeamIdsCache(cache: ICacheService | undefined): void {
  sharedCache = cache;
}

export const getTeamIdsCache = (): ICacheService | undefined => sharedCache;

const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

async function fetchTeamsVersion(
  cache: ICacheService,
  orgId: string,
): Promise<number> {
  const value = await cache.get<number>(teamsVersionKey(orgId));
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** Never throws; 0 when there is no cache or it cannot be read. */
export async function readTeamsVersion(
  cache: ICacheService | undefined,
  orgId: string,
): Promise<number> {
  if (!cache) return 0;
  try {
    return await fetchTeamsVersion(cache, orgId);
  } catch {
    return 0;
  }
}

/** Never throws. A failed bump leaves entries live for at most the TTL. */
export async function bumpTeamsVersion(
  orgId: string,
  logger: Pick<Logger, 'warn'>,
  cache: ICacheService | undefined = sharedCache,
): Promise<void> {
  if (!cache || orgId === '') return;
  try {
    await cache.increment(teamsVersionKey(orgId));
  } catch (error) {
    logger.warn('Failed to bump teams version; team-id cache may be stale', {
      error: errorMessage(error),
    });
  }
}

const isTeamIdList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((id) => typeof id === 'string');

/**
 * Caches resolved team ids per org, user and teams version. A cache error is a
 * miss, never a deny, and `'unresolved'` is never stored.
 */
export class CachedTeamDirectory implements ITeamDirectory {
  private readonly inFlight = new Map<string, Promise<TeamIdsResult>>();

  constructor(
    private readonly inner: ITeamDirectory,
    private readonly getCache: () => ICacheService | undefined,
    private readonly logger: Pick<Logger, 'warn'>,
    private readonly ttlSeconds: number = TEAM_IDS_CACHE_TTL_SECONDS,
  ) {}

  async callerTeamIds(identity: CallerIdentity): Promise<TeamIdsResult> {
    const cache = this.getCache();
    if (!cache) {
      return this.upstream(identity);
    }
    let key: string;
    try {
      const version = await fetchTeamsVersion(cache, identity.orgId);
      key = teamIdsKey(identity.orgId, identity.userId, version);
      const hit = await cache.get<unknown>(key);
      if (isTeamIdList(hit)) {
        recordTeamIdsCache('hit');
        return { status: 'ok', teamIds: hit };
      }
    } catch (error) {
      recordTeamIdsCache('error');
      this.logger.warn('Team-id cache unavailable; passing through', {
        error: errorMessage(error),
      });
      return this.upstream(identity);
    }
    return this.load(cache, key, identity);
  }

  private async upstream(identity: CallerIdentity): Promise<TeamIdsResult> {
    const started = process.hrtime.bigint();
    let outcome: 'ok' | 'error' = 'error';
    try {
      const result = await this.inner.callerTeamIds(identity);
      if (result.status === 'ok') outcome = 'ok';
      return result;
    } finally {
      recordTeamIdsUpstream(
        outcome,
        Number(process.hrtime.bigint() - started) / 1e9,
      );
    }
  }

  private load(
    cache: ICacheService,
    key: string,
    identity: CallerIdentity,
  ): Promise<TeamIdsResult> {
    const pending = this.inFlight.get(key);
    if (pending) {
      recordTeamIdsCache('coalesced');
      return pending;
    }
    recordTeamIdsCache('miss');
    const fetched = this.fetchAndStore(cache, key, identity).finally(() => {
      this.inFlight.delete(key);
    });
    this.inFlight.set(key, fetched);
    return fetched;
  }

  private async fetchAndStore(
    cache: ICacheService,
    key: string,
    identity: CallerIdentity,
  ): Promise<TeamIdsResult> {
    const result = await this.upstream(identity);
    if (result.status === 'ok') {
      try {
        await cache.set(key, result.teamIds, { ttl: this.ttlSeconds });
      } catch (error) {
        this.logger.warn('Team-id cache write failed; continuing uncached', {
          error: errorMessage(error),
        });
      }
    }
    return result;
  }

  teamsVersion(orgId: string): Promise<number> {
    return readTeamsVersion(this.getCache(), orgId);
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
