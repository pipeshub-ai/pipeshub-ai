import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { TtlCache } from '../../../../../libs/utils/ttl-cache';

export const AGENT_DIRECTORY_TTL_MS = 60_000;
/** Short, so an agent made or shared elsewhere (the builder page calls Python directly) shows in the picker soon. */
export const AGENT_LIST_TTL_MS = 10_000;
export const AGENT_DIRECTORY_TIMEOUT_MS = 3_000;
const MAX_ENTRIES = 5_000;

export type AgentAccess =
  | {
      readonly status: 'allowed';
      readonly isServiceAccount: boolean;
      readonly name?: string;
      readonly handle?: string;
    }
  | { readonly status: 'denied' }
  | { readonly status: 'unavailable' };

export interface IAgentDirectory {
  /** Whether `identity` may execute the agent; `'unavailable'` when the AI backend cannot say. Never throws. */
  canExecute(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<boolean | 'unavailable'>;
  /** Service-account agents answer from their creator's access, so they are not mentionable in shared chats. */
  isServiceAccount(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<boolean | 'unavailable'>;
}

export interface AgentProfile {
  readonly name: string;
  readonly handle?: string;
}

export interface IAgentProfiles {
  /** How the picker labels an agent the caller may run; undefined when it has no usable name. Never throws. */
  describe(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<AgentProfile | undefined>;
}

export interface ListedAgent extends AgentProfile {
  readonly agentKey: string;
  readonly isServiceAccount: boolean;
}

export interface IAgentListing {
  /** Agents the caller may execute (the query service's own list, which applies `check_agent_permission`); `'unavailable'` when it cannot say. Never throws. */
  listExecutable(
    identity: CallerIdentity,
  ): Promise<readonly ListedAgent[] | 'unavailable'>;
}

const LIST_PAGE_SIZE = 100;
const LIST_MAX_PAGES = 3;

const defaultLogger = Logger.getInstance({ service: 'AgentDirectory' });

interface AgentListShape {
  agents?: Array<{
    _key?: unknown;
    id?: unknown;
    name?: unknown;
    handle?: unknown;
    isServiceAccount?: unknown;
  }>;
  pagination?: { hasNext?: unknown };
}

interface AgentResponseShape {
  agent?: { isServiceAccount?: unknown; name?: unknown; handle?: unknown };
}

/**
 * Asks the query service `GET /agent/{key}` as the caller, which applies `check_agent_permission`
 * (404 without it). A 403 is a missing token scope, not a verdict, so it reads as unavailable.
 * Answers are cached per caller for 60 s.
 */
export class HttpAgentDirectory implements IAgentDirectory, IAgentProfiles, IAgentListing
{
  private readonly cache: TtlCache<AgentAccess>;
  private readonly lists: TtlCache<readonly ListedAgent[]>;

  constructor(
    private readonly aiBackendUrl: () => string,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
    clock: IClock = systemClock,
  ) {
    this.cache = new TtlCache(AGENT_DIRECTORY_TTL_MS, MAX_ENTRIES, clock);
    this.lists = new TtlCache(AGENT_LIST_TTL_MS, MAX_ENTRIES, clock);
  }

  /**
   * After Node creates, updates or deletes an agent: the caller's list is dropped, and so is every
   * cached answer about that key (a cached "not found" must not outlive a create of it).
   */
  invalidate(identity: CallerIdentity, agentKey?: string): void {
    this.lists.delete(`${identity.orgId}:${identity.userId}`);
    if (agentKey !== undefined) {
      this.cache.deleteWhere(
        (k) =>
          k.startsWith(`${identity.orgId}:`) && k.endsWith(`:${agentKey}`),
      );
    }
  }

  async listExecutable(
    identity: CallerIdentity,
  ): Promise<readonly ListedAgent[] | 'unavailable'> {
    const key = `${identity.orgId}:${identity.userId}`;
    const hit = this.lists.get(key);
    if (hit) return hit;
    const listed = await this.fetchList(identity);
    if (listed === 'unavailable') return listed;
    this.lists.set(key, listed);
    for (const a of listed) {
      this.cache.set(`${key}:${a.agentKey}`, {
        status: 'allowed',
        isServiceAccount: a.isServiceAccount,
        name: a.name,
        ...(a.handle !== undefined && { handle: a.handle }),
      });
    }
    return listed;
  }

  private async fetchList(
    identity: CallerIdentity,
  ): Promise<readonly ListedAgent[] | 'unavailable'> {
    const out: ListedAgent[] = [];
    try {
      for (let page = 1; page <= LIST_MAX_PAGES; page += 1) {
        const response = await new AIServiceCommand<AgentListShape>({
          uri: `${this.aiBackendUrl()}/api/v1/agent/?page=${String(page)}&limit=${String(LIST_PAGE_SIZE)}`,
          method: HttpMethod.GET,
          headers: {
            Authorization: identity.authHeaders.authorization ?? '',
            'Content-Type': 'application/json',
          },
          timeoutMs: AGENT_DIRECTORY_TIMEOUT_MS,
          maxAttempts: 1,
        }).execute();
        if (response.statusCode !== 200) {
          this.logger.warn('Agent list returned an unusable answer', {
            statusCode: response.statusCode,
          });
          return 'unavailable';
        }
        for (const a of response.data?.agents ?? []) {
          const raw = a._key ?? a.id;
          if (typeof raw !== 'string' || typeof a.name !== 'string') continue;
          out.push({
            agentKey: raw,
            name: a.name,
            isServiceAccount: a.isServiceAccount === true,
            ...(typeof a.handle === 'string' && { handle: a.handle }),
          });
        }
        if (response.data?.pagination?.hasNext !== true) break;
      }
      return out;
    } catch (error) {
      this.logger.warn('Agent list failed', {
        error: error instanceof Error ? error.message : String(error),
      });
      return 'unavailable';
    }
  }

  async canExecute(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<boolean | 'unavailable'> {
    const access = await this.access(identity, agentKey);
    return access.status === 'unavailable'
      ? 'unavailable'
      : access.status === 'allowed';
  }

  async isServiceAccount(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<boolean | 'unavailable'> {
    const access = await this.access(identity, agentKey);
    return access.status === 'allowed' ? access.isServiceAccount : false;
  }

  async describe(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<AgentProfile | undefined> {
    const access = await this.access(identity, agentKey);
    if (access.status !== 'allowed' || access.name === undefined) {
      return undefined;
    }
    return {
      name: access.name,
      ...(access.handle !== undefined && { handle: access.handle }),
    };
  }

  private async access(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<AgentAccess> {
    const key = `${identity.orgId}:${identity.userId}:${agentKey}`;
    const hit = this.cache.get(key);
    if (hit) return hit;
    const access = await this.fetch(identity, agentKey);
    if (access.status !== 'unavailable') this.cache.set(key, access);
    return access;
  }

  private async fetch(
    identity: CallerIdentity,
    agentKey: string,
  ): Promise<AgentAccess> {
    try {
      const response = await new AIServiceCommand<AgentResponseShape>({
        uri: `${this.aiBackendUrl()}/api/v1/agent/${encodeURIComponent(agentKey)}`,
        method: HttpMethod.GET,
        headers: {
          // Only the credential: the inbound request may be a POST whose length and host must not follow.
          Authorization: identity.authHeaders.authorization ?? '',
          'Content-Type': 'application/json',
        },
        timeoutMs: AGENT_DIRECTORY_TIMEOUT_MS,
        maxAttempts: 1,
      }).execute();
      if (response.statusCode === 404) return { status: 'denied' };
      if (response.statusCode !== 200) {
        this.logger.warn('Agent lookup returned an unusable answer', {
          statusCode: response.statusCode,
        });
        return { status: 'unavailable' };
      }
      const agent = response.data?.agent;
      return {
        status: 'allowed',
        isServiceAccount: agent?.isServiceAccount === true,
        ...(typeof agent?.name === 'string' && { name: agent.name }),
        ...(typeof agent?.handle === 'string' && { handle: agent.handle }),
      };
    } catch (error) {
      this.logger.warn('Agent lookup failed', {
        error: error instanceof Error ? error.message : String(error),
      });
      return { status: 'unavailable' };
    }
  }
}

export interface IAgentCacheInvalidator {
  invalidate(identity: CallerIdentity, agentKey?: string): void;
}

let invalidator: IAgentCacheInvalidator | undefined;

export function useAgentCacheInvalidator(
  next: IAgentCacheInvalidator | undefined,
): void {
  invalidator = next;
}

/** Called by the routes that proxy agent create, update and delete. */
export const invalidateAgentCaches = (
  identity: CallerIdentity,
  agentKey?: string,
): void => invalidator?.invalidate(identity, agentKey);

let profiles: IAgentProfiles | undefined;

/** Wired once at startup; the conversation detail handlers read it, as they read the event producers. */
export function useAgentProfiles(next: IAgentProfiles | undefined): void {
  profiles = next;
}

export const agentProfiles = (): IAgentProfiles | undefined => profiles;
