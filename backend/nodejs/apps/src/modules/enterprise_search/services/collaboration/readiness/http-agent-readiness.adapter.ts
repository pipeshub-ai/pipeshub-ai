import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../../libs/enums/token-scopes.enum';
import { Logger } from '../../../../../libs/services/logger.service';
import { IServiceTokenIssuer } from '../../../../../libs/services/service-token.issuer';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { TtlCache } from '../../../../../libs/utils/ttl-cache';
import {
  AgentReadinessResult,
  IAgentReadinessPort,
  ReadinessSubject,
} from './agent-readiness.port';

export const AGENT_READINESS_TTL_MS = 60_000;
export const AGENT_READINESS_TIMEOUT_MS = 3_000;
const MAX_ENTRIES = 5_000;

const UNKNOWN: AgentReadinessResult = { status: 'unknown' };
const UNAVAILABLE: AgentReadinessResult = { status: 'unavailable' };

interface ReadinessBody {
  canSend: boolean;
  missingToolsets: string[];
  unauthenticatedToolsets: string[];
}

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

const isReadinessBody = (value: unknown): value is ReadinessBody => {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const body = value as Record<string, unknown>;
  return (
    typeof body.canSend === 'boolean' &&
    isStringArray(body.missingToolsets) &&
    isStringArray(body.unauthenticatedToolsets)
  );
};

/** Calls the query service's `GET /agent/{id}/readiness`; answers are cached per instance for 60 s. */
export class HttpAgentReadinessAdapter implements IAgentReadinessPort {
  private readonly cache: TtlCache<AgentReadinessResult>;

  constructor(
    private readonly aiBackendUrl: () => string,
    private readonly tokens: IServiceTokenIssuer,
    private readonly logger: Pick<Logger, 'warn'>,
    clock: IClock = systemClock,
  ) {
    this.cache = new TtlCache(AGENT_READINESS_TTL_MS, MAX_ENTRIES, clock);
  }

  async check(
    subject: ReadinessSubject,
    agentKey: string,
  ): Promise<AgentReadinessResult> {
    const key = this.keyOf(subject, agentKey);
    const hit = this.cache.get(key);
    if (hit) {
      return hit;
    }
    try {
      const result = await this.fetchReadiness(subject, agentKey);
      if (result.status !== 'unknown') {
        this.cache.set(key, result);
      }
      return result;
    } catch (error) {
      this.logger.warn('Agent readiness lookup failed; skipping the check', {
        error: error instanceof Error ? error.message : String(error),
      });
      return UNKNOWN;
    }
  }

  invalidate(subject: ReadinessSubject, agentKey: string): void {
    this.cache.delete(this.keyOf(subject, agentKey));
  }

  private keyOf({ orgId, userId }: ReadinessSubject, agentKey: string): string {
    return `${orgId}:${userId}:${agentKey}`;
  }

  private async fetchReadiness(
    { orgId, userId }: ReadinessSubject,
    agentKey: string,
  ): Promise<AgentReadinessResult> {
    const token = this.tokens.issue(
      { userId, orgId, scopes: [TokenScopes.CONVERSATION_CREATE] },
      '1m',
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${this.aiBackendUrl()}/api/v1/agent/${encodeURIComponent(agentKey)}/readiness`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: AGENT_READINESS_TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    if (response.statusCode === 404) {
      return UNAVAILABLE;
    }
    if (response.statusCode !== 200 || !isReadinessBody(response.data)) {
      this.logger.warn('Agent readiness lookup returned an unusable answer', {
        statusCode: response.statusCode,
      });
      return UNKNOWN;
    }
    const { canSend, missingToolsets, unauthenticatedToolsets } = response.data;
    return canSend
      ? { status: 'ready' }
      : {
          status: 'blocked',
          toolsets: [...missingToolsets, ...unauthenticatedToolsets],
        };
  }
}
