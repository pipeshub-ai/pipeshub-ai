import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  AgentRunDefinition,
  agentRunDefinitionSchema,
} from '../../agent-harness/domain/agent-definition';
import {
  AgentAccessPolicy,
  AgentUseRequest,
} from '../../agent-harness/ports/agent-access';

const DENIED = new Set([401, 403, 404]);
const ACCESS_CHECK_TIMEOUT_MS = 10_000;

/**
 * Asks the agent authority (Python `GET /api/v1/agent/{key}/run-definition`,
 * `agent:execute`-scoped for a named agent, `conversation:chat` enough for the
 * universal one as on its chat route (#4220); it runs check_agent_permission for the caller)
 * with the actor's own credential. Node never mints a credential for this.
 */
export class HarnessAgentAccessPolicy implements AgentAccessPolicy {
  constructor(private readonly aiBackend: () => string) {}

  async authorizeRun(
    request: AgentUseRequest,
  ): Promise<AgentRunDefinition | null> {
    const query = new URLSearchParams();
    if (request.agentVersion !== undefined) {
      query.set('version', String(request.agentVersion));
    }
    if (request.mcpFirstUse === true) query.set('mcpFirstUse', 'true');
    const pinned = query.size > 0 ? `?${query.toString()}` : '';
    const response = await new AIServiceCommand({
      uri: `${this.aiBackend()}/api/v1/agent/${encodeURIComponent(request.agentKey)}/run-definition${pinned}`,
      method: HttpMethod.GET,
      headers: {
        Authorization: request.authorization,
        'Content-Type': 'application/json',
      },
      timeoutMs: ACCESS_CHECK_TIMEOUT_MS,
      maxAttempts: 2,
    }).execute();
    if (DENIED.has(response.statusCode)) return null;
    if (response.statusCode !== 200) {
      throw new Error(
        `Agent access check failed with status ${String(response.statusCode)}`,
      );
    }
    const body = response.data as { definition?: unknown } | null;
    const definition = agentRunDefinitionSchema.parse(body?.definition);
    // The authority answered for another agent: never pin it for this one.
    if (definition.agentKey !== request.agentKey) {
      throw new Error('Agent access check returned another agent');
    }
    // A pinned version the authority no longer holds comes back as its latest (OWD-14): the run pins and
    // records the version it is given, so it never runs one it does not name.
    return definition;
  }
}
