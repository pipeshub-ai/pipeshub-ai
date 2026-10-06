import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { TtlCache } from '../../../../../libs/utils/ttl-cache';

export const AGENT_DIRECTORY_TTL_MS = 60_000;
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

const defaultLogger = Logger.getInstance({ service: 'AgentDirectory' });

interface AgentResponseShape {
  agent?: { isServiceAccount?: unknown; name?: unknown; handle?: unknown };
}

/**
 * Asks the query service `GET /agent/{key}` as the caller, which applies `check_agent_permission`
 * (404 without it). A 403 is a missing token scope, not a verdict, so it reads as unavailable.
 * Answers are cached per caller for 60 s.
 */
export class HttpAgentDirectory implements IAgentDirectory, IAgentProfiles {
  private readonly cache: TtlCache<AgentAccess>;

  constructor(
    private readonly aiBackendUrl: () => string,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
    clock: IClock = systemClock,
  ) {
    this.cache = new TtlCache(AGENT_DIRECTORY_TTL_MS, MAX_ENTRIES, clock);
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
