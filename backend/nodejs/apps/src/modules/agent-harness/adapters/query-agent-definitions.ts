import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { BadGatewayError } from '../../../libs/errors/http.errors';
import { handleBackendError } from '../../../libs/errors/backend-error';
import type {
  AgentDefinitions,
  ExportedDefinition,
  ImportedDefinition,
} from '../application/agent-config-transfer';
import {
  agentConfigDocumentSchema,
  portableAgentSchema,
  type PortableAgent,
} from '../contracts/generated/ext/pipeshub';

const TIMEOUT_MS = 30_000;

const exportSchema = z
  .object({
    agent: portableAgentSchema,
    source: agentConfigDocumentSchema.shape.source,
    // Every kind the document may name: a kind the query service adds (gates, WP-51) is taken, not refused.
    excluded: agentConfigDocumentSchema.shape.excluded,
  })
  .strict();

const item = z.object({ kind: z.string(), name: z.string() }).strict();

const importSchema = z
  .object({
    agent: z.object({ _key: z.string().min(1), name: z.string() }).strict(),
    bound: z.array(item),
    rebind: z.array(
      item.extend({ reason: z.string(), detail: z.string() }).strict(),
    ),
    warnings: z.unknown(),
  })
  .strict();

/**
 * The query service's agents API (`/api/v1/agent`), asked with the caller's
 * own Authorization header and nothing else: it decides the org, the caller
 * and what they may read from that credential. A create is sent once (no
 * retry: a retried create could make a second agent).
 */
export class QueryAgentDefinitions implements AgentDefinitions<string> {
  constructor(private readonly aiBackend: () => string) {}

  async export(
    authorization: string,
    agentKey: string,
    version: string | undefined,
  ): Promise<ExportedDefinition> {
    const query =
      version !== undefined ? `?version=${encodeURIComponent(version)}` : '';
    return this.ask(
      authorization,
      HttpMethod.GET,
      `/${encodeURIComponent(agentKey)}/export${query}`,
      undefined,
      2,
      'export this agent',
      exportSchema,
    );
  }

  async import(
    authorization: string,
    agent: PortableAgent,
    options: { bind?: 'named' | 'sole_instance' } = {},
  ): Promise<ImportedDefinition> {
    const answer = await this.ask(
      authorization,
      HttpMethod.POST,
      '/import',
      options.bind === 'sole_instance'
        ? { agent, bind: 'sole_instance' }
        : { agent },
      1,
      'import this agent',
      importSchema,
    );
    return {
      agentKey: answer.agent._key,
      name: answer.agent.name,
      bound: answer.bound,
      rebind: answer.rebind,
      warnings: answer.warnings,
    };
  }

  async remove(authorization: string, agentKey: string): Promise<void> {
    await this.ask(
      authorization,
      HttpMethod.DELETE,
      `/${encodeURIComponent(agentKey)}`,
      undefined,
      2,
      'remove the imported agent',
      z.unknown(),
    );
  }

  private async ask<T>(
    authorization: string,
    method: HttpMethod,
    suffix: string,
    body: unknown,
    maxAttempts: number,
    operation: string,
    schema: z.ZodType<T>,
  ): Promise<T> {
    let response;
    try {
      response = await new AIServiceCommand<unknown>({
        uri: `${this.aiBackend()}/api/v1/agent${suffix}`,
        method,
        headers: {
          Authorization: authorization,
          'Content-Type': 'application/json',
        },
        ...(body !== undefined ? { body } : {}),
        timeoutMs: TIMEOUT_MS,
        maxAttempts,
      }).execute();
    } catch (error) {
      throw handleBackendError(error, operation);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw handleBackendError(response, operation);
    }
    const parsed = schema.safeParse(response.data);
    if (!parsed.success) {
      throw new BadGatewayError(
        'The agents service answered with an unexpected body',
      );
    }
    return parsed.data;
  }
}
