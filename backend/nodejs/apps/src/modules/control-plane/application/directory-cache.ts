import type { TeamMembership } from '../domain/resolution';
import type { AuthzActor } from '../ports/authorizer';
import type {
  AgentDirectory,
  AgentRunAccess,
  MembershipPort,
} from '../ports/directory';

/** 02-lld §8.5: events drop entries at once; the TTL is the backstop when an event is lost. */
export const DIRECTORY_TTL_MS = 30_000;
const DEFAULT_BOUND = 50_000;

export interface DirectoryCacheOptions {
  readonly now: () => number;
  readonly ttlMs?: number;
  /** Most entries held; the least recently used goes first. */
  readonly bound?: number;
}

/** A bounded least-recently-used map whose entries expire; it holds values only, never a failure. */
class ExpiringCache<V> {
  private readonly entries = new Map<string, { value: V; until: number }>();
  // A load that an invalidation overtook answers its caller but is not kept: it may hold what the event retracted.
  private epoch = 0;

  constructor(private readonly options: DirectoryCacheOptions) {}

  async get(key: string, load: () => Promise<V>): Promise<V> {
    const now = this.options.now();
    const hit = this.entries.get(key);
    if (hit !== undefined && hit.until > now) {
      this.entries.delete(key);
      this.entries.set(key, hit);
      return hit.value;
    }
    this.entries.delete(key);
    const started = this.epoch;
    const value = await load();
    if (started !== this.epoch) return value;
    this.entries.set(key, {
      value,
      until: now + (this.options.ttlMs ?? DIRECTORY_TTL_MS),
    });
    const bound = this.options.bound ?? DEFAULT_BOUND;
    while (this.entries.size > bound) {
      this.entries.delete(this.entries.keys().next().value as string);
    }
    return value;
  }

  dropWhere(drop: (key: string, value: V) => boolean): void {
    this.epoch += 1;
    for (const [key, entry] of [...this.entries]) {
      if (drop(key, entry.value)) this.entries.delete(key);
    }
  }

  get size(): number {
    return this.entries.size;
  }
}

const key = (...parts: string[]): string =>
  parts.map(encodeURIComponent).join('|');

/**
 * Team membership read once per `(org, user)` (02-lld §8.5, principal tier).
 * `teamMembershipChanged` drops the named users and every entry that lists
 * the team, which covers people removed and a team deleted.
 */
export class CachingMembershipPort implements MembershipPort {
  private readonly cache: ExpiringCache<readonly TeamMembership[]>;
  private readonly owners: ExpiringCache<number>;
  private readonly ownerIds: ExpiringCache<readonly string[]>;

  constructor(
    private readonly inner: MembershipPort,
    options: DirectoryCacheOptions,
  ) {
    this.cache = new ExpiringCache(options);
    this.owners = new ExpiringCache(options);
    this.ownerIds = new ExpiringCache(options);
  }

  teamsOf(orgId: string, userId: string): Promise<readonly TeamMembership[]> {
    return this.cache.get(key(orgId, userId), () =>
      this.inner.teamsOf(orgId, userId),
    );
  }

  /** Read once per `(org, team)` like membership; a directory that cannot count owners fails the read. */
  teamOwnerCount(orgId: string, teamId: string): Promise<number> {
    const { inner } = this;
    if (inner.teamOwnerCount === undefined) {
      return Promise.reject(
        new Error('The team directory cannot count a team’s owners'),
      );
    }
    return this.owners.get(key(orgId, teamId), () =>
      (inner.teamOwnerCount as NonNullable<MembershipPort['teamOwnerCount']>)(
        orgId,
        teamId,
      ),
    );
  }

  /** AP-08: cached and dropped with the owner count, so a membership change reaches both. */
  teamOwnerIds(orgId: string, teamId: string): Promise<readonly string[]> {
    const { inner } = this;
    if (inner.teamOwnerIds === undefined) {
      return Promise.reject(
        new Error('The team directory cannot name a team’s owners'),
      );
    }
    return this.ownerIds.get(key(orgId, teamId), () =>
      (inner.teamOwnerIds as NonNullable<MembershipPort['teamOwnerIds']>)(
        orgId,
        teamId,
      ),
    );
  }

  teamMembershipChanged(
    orgId: string,
    teamId: string,
    userIds: readonly string[],
  ): void {
    const named = new Set(userIds.map((user) => key(orgId, user)));
    const prefix = `${encodeURIComponent(orgId)}|`;
    this.cache.dropWhere(
      (entry, teams) =>
        named.has(entry) ||
        (entry.startsWith(prefix) && teams.some((t) => t.teamId === teamId)),
    );
    const team = key(orgId, teamId);
    this.owners.dropWhere((entry) => entry === team);
    this.ownerIds.dropWhere((entry) => entry === team);
  }

  get size(): number {
    return this.cache.size;
  }
}

/**
 * Agent access, edit rights and owner team, cached per agent (02-lld §8.5,
 * agent tier). `agentChanged` drops every entry of that agent.
 */
export class CachingAgentDirectory implements AgentDirectory {
  private readonly runs: ExpiringCache<AgentRunAccess | null>;
  private readonly edits: ExpiringCache<boolean>;
  private readonly owners: ExpiringCache<string | null>;

  constructor(
    private readonly inner: AgentDirectory,
    options: DirectoryCacheOptions,
  ) {
    this.runs = new ExpiringCache(options);
    this.edits = new ExpiringCache(options);
    this.owners = new ExpiringCache(options);
  }

  authorizeRun(
    orgId: string,
    agentKey: string,
    actor: AuthzActor,
  ): Promise<AgentRunAccess | null> {
    return this.runs.get(key(orgId, agentKey, actor.orgId, actor.userId), () =>
      this.inner.authorizeRun(orgId, agentKey, actor),
    );
  }

  canEdit(
    orgId: string,
    agentKey: string,
    actor: AuthzActor,
  ): Promise<boolean> {
    return this.edits.get(key(orgId, agentKey, actor.orgId, actor.userId), () =>
      this.inner.canEdit(orgId, agentKey, actor),
    );
  }

  ownerTeam(orgId: string, agentKey: string): Promise<string | null> {
    return this.owners.get(key(orgId, agentKey), () =>
      this.inner.ownerTeam(orgId, agentKey),
    );
  }

  agentChanged(orgId: string, agentKey: string): void {
    const agent = key(orgId, agentKey);
    const ofAgent = (entry: string): boolean =>
      entry === agent || entry.startsWith(`${agent}|`);
    this.runs.dropWhere(ofAgent);
    this.edits.dropWhere(ofAgent);
    this.owners.dropWhere(ofAgent);
  }
}
