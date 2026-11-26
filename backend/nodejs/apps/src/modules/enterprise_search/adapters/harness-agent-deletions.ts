import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import {
  AGENT_DELETION_STATES,
  AgentDeletionLookup,
  AgentDeletionState,
} from '../../agent-harness/ports/agent-erasures';
import { ArtifactEraserConfig } from './harness-artifact-eraser';

const LOOKUP_TIMEOUT_MS = 30_000;
const TOKEN_TTL = '5m';

const answerSchema = z
  .object({
    state: z.enum(
      AGENT_DELETION_STATES as [AgentDeletionState, ...AgentDeletionState[]],
    ),
  })
  .strip();

/** The query service did not answer an agent's state. */
export class AgentDeletionLookupFailedError extends Error {
  constructor(public readonly statusCode: number) {
    super(`Agent deletion lookup answered ${String(statusCode)}`);
    this.name = 'AgentDeletionLookupFailedError';
  }
}

/**
 * The query service's agent tombstone (H5), for the agent-erasure sweeper: a
 * service token scoped `agent:erase` whose org is the erasure entry's, so the
 * query service answers only about that org's agents.
 */
export class HarnessAgentDeletionLookup implements AgentDeletionLookup {
  constructor(private readonly config: () => ArtifactEraserConfig) {}

  async state(orgId: string, agentKey: string): Promise<AgentDeletionState> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.AGENT_ERASE] },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}/internal/deletion`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: LOOKUP_TIMEOUT_MS,
    }).execute();
    if (response.statusCode !== 200) {
      throw new AgentDeletionLookupFailedError(response.statusCode);
    }
    const parsed = answerSchema.safeParse(response.data);
    if (!parsed.success) throw new AgentDeletionLookupFailedError(200);
    return parsed.data.state;
  }
}
