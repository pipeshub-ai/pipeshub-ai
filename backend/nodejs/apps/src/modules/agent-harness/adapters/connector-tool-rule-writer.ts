import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { RuleGrantTarget, ToolRuleWriter } from '../ports/tool-rules';

const TIMEOUT_MS = 10_000;

const rulesSchema = z.object({
  tools: z.record(z.enum(['allow', 'ask', 'block'])),
});

/**
 * The connectors service's `/api/v1/mcp-servers/instances/{id}/my-tool-rules`
 * (main's person-level MCP tool rules), asked with the person's own
 * Authorization header, so the service decides whose rules they are. The PUT
 * replaces the whole map, so the rule is merged into what is stored, and an
 * Allow never replaces a Block saved since the card was shown (main's
 * `_remember`).
 */
export class ConnectorToolRuleWriter implements ToolRuleWriter {
  constructor(private readonly connectorBackend: () => string) {}

  async remember(
    authorization: string,
    target: RuleGrantTarget,
    rule: 'allow' | 'block',
  ): Promise<boolean> {
    const uri = `${this.connectorBackend()}/api/v1/mcp-servers/instances/${encodeURIComponent(target.instanceId)}/my-tool-rules`;
    const headers = {
      Authorization: authorization,
      'Content-Type': 'application/json',
    };
    const read = await new AIServiceCommand<unknown>({
      uri,
      method: HttpMethod.GET,
      headers,
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 2,
    }).execute();
    const stored =
      read.statusCode === 200 ? rulesSchema.safeParse(read.data) : null;
    if (!stored?.success) return false;
    const tools = stored.data.tools;
    if (rule === 'allow' && tools[target.toolName] === 'block') return false;
    const written = await new AIServiceCommand<unknown>({
      uri,
      method: HttpMethod.PUT,
      headers,
      body: { tools: { ...tools, [target.toolName]: rule } },
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    return written.statusCode >= 200 && written.statusCode < 300;
  }
}
