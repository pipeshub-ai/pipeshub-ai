import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import {
  ArtifactEraser,
  AttachmentEraser,
  AttachmentErasurePage,
  ConversationAttachment,
  TraceEraser,
  UnsentAttachment,
  UnsentAttachments,
} from '../../agent-harness/ports/session-erasure';
import { OrgQueryServiceEraser } from '../../agent-harness/ports/principal-erasure';

const ERASE_TIMEOUT_MS = 120_000;
const TOKEN_TTL = '5m';

const erasedSchema = z
  .object({
    artifacts: z.number().int().nonnegative(),
    blobs: z.number().int().nonnegative(),
    remaining: z.boolean(),
  })
  .strip();

// `unrecorded` and `removed` default for a query service older than QA-15G: nothing to mark, nothing counted.
const attachmentsErasedSchema = z
  .object({
    attachments: z.number().int().nonnegative(),
    blobs: z.number().int().nonnegative(),
    refused: z.number().int().nonnegative(),
    unrecorded: z.number().int().nonnegative().default(0),
    removed: z.array(z.string().min(1)).default([]),
  })
  .strip();

export interface ArtifactEraserConfig {
  aiBackend: string;
  jwtSecret: string;
  scopedJwtSecret: string;
}

/** The query service answered something other than an erase report. */
export class ArtifactErasureFailedError extends Error {
  constructor(public readonly statusCode: number) {
    super(`Artifact erasure answered ${String(statusCode)}`);
    this.name = 'ArtifactErasureFailedError';
  }
}

/**
 * The query service's erase routes for a deleted conversation's artifacts
 * (E7) and chat attachments (QA15-F4): a service token scoped
 * `conversation:erase`, its org the erased session's, signed by Node after the
 * conversation's delete committed. The body names only what to erase; the org
 * is never taken from it.
 */
const unsentSchema = z
  .object({
    uploads: z.array(
      z
        .object({
          recordId: z.string().min(1),
          ownerId: z.string().min(1),
          conversationId: z.string().min(1).nullable(),
        })
        .strip(),
    ),
    remaining: z.boolean(),
    next: z.string().min(1).nullable().default(null),
  })
  .strip();

const tracesErasedSchema = z
  .object({
    traces: z.number().int().nonnegative(),
    configured: z.boolean(),
  })
  .strip();

const markedSchema = z.object({ marked: z.array(z.string()) }).strip();

const orgErasedSchema = z
  .object({
    attachments: z.number().int().nonnegative(),
    artifacts: z.number().int().nonnegative(),
    blobs: z.number().int().nonnegative(),
    skillCandidates: z.number().int().nonnegative(),
    remaining: z.boolean(),
  })
  .strip();

export class HarnessArtifactEraser
  implements
    ArtifactEraser,
    AttachmentEraser,
    UnsentAttachments,
    TraceEraser,
    OrgQueryServiceEraser
{
  constructor(private readonly config: () => ArtifactEraserConfig) {}

  eraseConversation(
    orgId: string,
    sessionId: string,
  ): Promise<{ artifacts: number; blobs: number; remaining: boolean }> {
    return this.erase(orgId, { conversationId: sessionId });
  }

  collectDiscarded(
    orgId: string,
    before: number,
  ): Promise<{ artifacts: number; blobs: number; remaining: boolean }> {
    return this.erase(orgId, { discardedBefore: before });
  }

  eraseAttachments(
    orgId: string,
    attachments: readonly ConversationAttachment[],
  ): Promise<AttachmentErasurePage> {
    return this.post(
      orgId,
      'attachments/erase',
      {
        attachments: attachments.map(({ recordId, ownerId }) => ({
          recordId,
          ownerId,
        })),
      },
      attachmentsErasedSchema,
    );
  }

  async eraseTraces(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{ traces: number }> {
    const erased = await this.post(
      orgId,
      'traces/erase',
      { conversationId: sessionId, runIds: [...runIds] },
      tracesErasedSchema,
    );
    return { traces: erased.traces };
  }

  unsent(
    orgId: string,
    before: number,
    limit: number,
    after: string | null,
  ): Promise<{
    uploads: UnsentAttachment[];
    remaining: boolean;
    next: string | null;
  }> {
    return this.post(
      orgId,
      'attachments/unsent',
      { before, limit, ...(after !== null ? { after } : {}) },
      unsentSchema,
    );
  }

  async markSent(
    orgId: string,
    conversationId: string,
    recordIds: readonly string[],
  ): Promise<void> {
    await this.post(
      orgId,
      'attachments/sent',
      { conversationId, recordIds: [...recordIds] },
      markedSchema,
    );
  }

  /** One page of a deleted org's chat attachments, artifacts and skill drafts (CLEANUP-G1). */
  eraseOrg(orgId: string): Promise<{
    attachments: number;
    artifacts: number;
    blobs: number;
    skillCandidates: number;
    remaining: boolean;
  }> {
    return this.post(orgId, 'org/erase', {}, orgErasedSchema);
  }

  private erase(
    orgId: string,
    body: Record<string, unknown>,
  ): Promise<{ artifacts: number; blobs: number; remaining: boolean }> {
    return this.post(orgId, 'artifacts/erase', body, erasedSchema);
  }

  private async post<T>(
    orgId: string,
    route: string,
    body: Record<string, unknown>,
    answer: z.ZodType<T, z.ZodTypeDef, unknown>,
  ): Promise<T> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.CONVERSATION_ERASE] },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}/api/v1/chat/${route}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body,
      timeoutMs: ERASE_TIMEOUT_MS,
    }).execute();
    if (response.statusCode !== 200) {
      throw new ArtifactErasureFailedError(response.statusCode);
    }
    const parsed = answer.safeParse(response.data);
    if (!parsed.success) throw new ArtifactErasureFailedError(200);
    return parsed.data;
  }
}
