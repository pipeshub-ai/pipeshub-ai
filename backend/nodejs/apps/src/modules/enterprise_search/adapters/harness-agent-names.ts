import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import type {
  AgentNameReader,
  AgentSummary,
  AgentSummaryReader,
} from '../../agent-harness/ports/agent-names';
import type { ArtifactEraserConfig } from './harness-artifact-eraser';

const LOOKUP_TIMEOUT_MS = 10_000;
const TOKEN_TTL = '5m';
/** Keys one request names at most: the query service's own bound. */
export const AGENT_NAMES_PER_REQUEST = 200;

const answerSchema = z
  .object({
    names: z.record(z.string().min(1).max(512)),
    // Absent from a query service older than ADR-UI-03: the versions read as unknown.
    latestVersions: z.record(z.number().int().min(1)).optional(),
  })
  .strip();

/**
 * The query service's agent names (WP-51), for the usage report, the
 * budget notices and the admin lists (ADR-UI-03, with each agent's latest
 * published version): a service token scoped `agent:names` whose org is the
 * page's, so the query service names only that org's agents. A failed
 * answer throws; the caller shows the agents unnamed, never by key.
 */
export class HarnessAgentNames implements AgentNameReader, AgentSummaryReader {
  constructor(private readonly config: () => ArtifactEraserConfig) {}

  async names(
    orgId: string,
    agentKeys: readonly string[],
  ): Promise<Map<string, string>> {
    const found = new Map<string, string>();
    for (const [key, summary] of await this.lookup(orgId, agentKeys, false)) {
      found.set(key, summary.name);
    }
    return found;
  }

  summaries(
    orgId: string,
    agentKeys: readonly string[],
  ): Promise<Map<string, AgentSummary>> {
    return this.lookup(orgId, agentKeys, true);
  }

  private async lookup(
    orgId: string,
    agentKeys: readonly string[],
    withVersions: boolean,
  ): Promise<Map<string, AgentSummary>> {
    const found = new Map<string, AgentSummary>();
    const keys = [...new Set(agentKeys)];
    if (keys.length === 0) return found;
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.AGENT_NAMES] },
      TOKEN_TTL,
    );
    for (let at = 0; at < keys.length; at += AGENT_NAMES_PER_REQUEST) {
      const batch = keys.slice(at, at + AGENT_NAMES_PER_REQUEST);
      const response = await new AIServiceCommand<unknown>({
        uri: `${config.aiBackend}/api/v1/agent/internal/names`,
        method: HttpMethod.POST,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: withVersions
          ? { agentKeys: batch, include: ['latestVersion'] }
          : { agentKeys: batch },
        timeoutMs: LOOKUP_TIMEOUT_MS,
      }).execute();
      if (response.statusCode !== 200) {
        throw new Error(
          `Agent names lookup answered ${String(response.statusCode)}`,
        );
      }
      const parsed = answerSchema.safeParse(response.data);
      if (!parsed.success) throw new Error('Agent names lookup was malformed');
      for (const key of batch) {
        const name = parsed.data.names[key];
        if (name !== undefined) {
          found.set(key, {
            name,
            latestVersion: parsed.data.latestVersions?.[key] ?? null,
          });
        }
      }
    }
    return found;
  }
}
