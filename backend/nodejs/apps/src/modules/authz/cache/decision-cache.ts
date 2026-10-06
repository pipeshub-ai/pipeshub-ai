import { Logger } from '../../../libs/services/logger.service';
import { ICacheService } from '../../../libs/services/cache/cacheService.interface';

export const DECISION_CACHE_TTL_SECONDS = 30;

export interface DecisionKeyParts {
  orgId: string;
  subjectKey: string;
  resourceType: string;
  resourceId: string;
  aclVersion?: number | null;
  /** From ITeamDirectory.teamsVersion; callers without a team directory omit it and it is 0. */
  teamsVersion?: number | null;
}

export interface DecisionCacheOptions {
  /** A resolver is read on every call, so a cache wired after construction is still picked up. */
  cache?: ICacheService | (() => ICacheService | undefined);
  logger?: Pick<Logger, 'warn'>;
  ttlSeconds?: number;
}

export interface DecisionSetOptions {
  /** False when the decision depended on `'unresolved'` teams; such decisions are never cached. */
  teamsResolved?: boolean;
}

export const buildDecisionKey = (p: DecisionKeyParts): string =>
  `authz:v1:${p.orgId}:${p.subjectKey}:${p.resourceType}:${p.resourceId}:${String(p.aclVersion ?? 0)}:${String(p.teamsVersion ?? 0)}`;

/**
 * Versioned authorization decision cache. A per-request `WeakMap` (keyed by the
 * request object) fronts an optional shared cache. A shared-cache error is a
 * miss, never a deny. Invalidation is by version in the key, not by delete.
 */
export class DecisionCache<T = unknown> {
  private readonly perRequest = new WeakMap<object, Map<string, T>>();
  private readonly ttl: number;

  constructor(private readonly opts: DecisionCacheOptions = {}) {
    this.ttl = opts.ttlSeconds ?? DECISION_CACHE_TTL_SECONDS;
  }

  async get(request: object, parts: DecisionKeyParts): Promise<T | undefined> {
    const key = buildDecisionKey(parts);
    const local = this.perRequest.get(request)?.get(key);
    if (local !== undefined) {
      return local;
    }
    const shared = this.sharedCache();
    if (!shared) {
      return undefined;
    }
    try {
      const hit = await shared.get<T>(key);
      if (hit === null || hit === undefined) {
        return undefined;
      }
      this.remember(request, key, hit);
      return hit;
    } catch (error) {
      this.opts.logger?.warn(
        'Authz decision cache read failed; treating as miss',
        {
          error: error instanceof Error ? error.message : String(error),
        },
      );
      return undefined;
    }
  }

  async set(
    request: object,
    parts: DecisionKeyParts,
    decision: T,
    options: DecisionSetOptions = {},
  ): Promise<void> {
    if (options.teamsResolved === false) {
      return;
    }
    const key = buildDecisionKey(parts);
    this.remember(request, key, decision);
    const shared = this.sharedCache();
    if (!shared) {
      return;
    }
    try {
      await shared.set(key, decision, { ttl: this.ttl });
    } catch (error) {
      this.opts.logger?.warn(
        'Authz decision cache write failed; continuing uncached',
        {
          error: error instanceof Error ? error.message : String(error),
        },
      );
    }
  }

  private sharedCache(): ICacheService | undefined {
    const { cache } = this.opts;
    return typeof cache === 'function' ? cache() : cache;
  }

  private remember(request: object, key: string, decision: T): void {
    let bucket = this.perRequest.get(request);
    if (!bucket) {
      bucket = new Map();
      this.perRequest.set(request, bucket);
    }
    bucket.set(key, decision);
  }
}
