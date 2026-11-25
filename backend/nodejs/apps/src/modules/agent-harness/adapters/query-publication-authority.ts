import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  AuthorityAnswer,
  ContainerKind,
  CreatePublicationBody,
  PublicationAuthority,
  PublicationView,
} from '../ports/knowledge-publications';

const TIMEOUT_MS = 15_000;

const audience = z
  .object({
    userIds: z.array(z.string()),
    teamIds: z.array(z.string()),
    org: z.boolean(),
  })
  .strict();

export const publicationViewSchema = z
  .object({
    id: z.string().min(1),
    containerKind: z.enum(['kb', 'recordGroup']),
    containerId: z.string().min(1),
    audience,
    mode: z.enum(['answer_only', 'full']),
    createdBy: z.string(),
    createdAt: z.number(),
    expiresAt: z.number().nullable(),
    revokedAt: z.number().nullable(),
    revokedBy: z.string().nullable(),
  })
  .strict();

const listSchema = z
  .object({ publications: z.array(publicationViewSchema) })
  .strict();

const refusalSchema = z.object({
  detail: z.object({ code: z.string().min(1), message: z.string() }),
});

/**
 * The query service's `/api/v1/knowledge/publications` (P2.13), asked with
 * the caller's own Authorization header and nothing else: it decides the org,
 * the caller and their role from that credential. A create is sent once (no
 * retry: it is not idempotent); reads retry once.
 */
export class QueryPublicationAuthority implements PublicationAuthority {
  constructor(private readonly aiBackend: () => string) {}

  create(
    authorization: string,
    body: CreatePublicationBody,
  ): Promise<AuthorityAnswer<PublicationView>> {
    return this.ask(
      authorization,
      HttpMethod.POST,
      '',
      publicationViewSchema,
      body,
      1,
    );
  }

  async list(
    authorization: string,
    containerKind: ContainerKind,
    containerId: string,
  ): Promise<AuthorityAnswer<PublicationView[]>> {
    const answer = await this.ask(
      authorization,
      HttpMethod.GET,
      `?containerKind=${encodeURIComponent(containerKind)}&containerId=${encodeURIComponent(containerId)}`,
      listSchema,
      undefined,
      2,
    );
    return answer.ok ? { ok: true, value: answer.value.publications } : answer;
  }

  revoke(
    authorization: string,
    publicationId: string,
  ): Promise<AuthorityAnswer<PublicationView>> {
    return this.ask(
      authorization,
      HttpMethod.DELETE,
      `/${encodeURIComponent(publicationId)}`,
      publicationViewSchema,
      undefined,
      2,
    );
  }

  private async ask<T>(
    authorization: string,
    method: HttpMethod,
    suffix: string,
    schema: z.ZodType<T>,
    body: unknown,
    maxAttempts: number,
  ): Promise<AuthorityAnswer<T>> {
    let response;
    try {
      response = await new AIServiceCommand<unknown>({
        uri: `${this.aiBackend()}/api/v1/knowledge/publications${suffix}`,
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
      return {
        ok: false,
        status: 503,
        code: 'publications_unavailable',
        message: error instanceof Error ? error.name : 'request failed',
      };
    }
    if (response.statusCode >= 200 && response.statusCode < 300) {
      const parsed = schema.safeParse(response.data);
      if (parsed.success) return { ok: true, value: parsed.data };
      return {
        ok: false,
        status: 502,
        code: 'publications_invalid_response',
        message: 'The knowledge service answered with an unexpected body',
      };
    }
    const refusal = refusalSchema.safeParse(response.data);
    return {
      ok: false,
      status: response.statusCode,
      code: refusal.success ? refusal.data.detail.code : 'publication_refused',
      message: refusal.success
        ? refusal.data.detail.message
        : `status ${String(response.statusCode)}`,
    };
  }
}
