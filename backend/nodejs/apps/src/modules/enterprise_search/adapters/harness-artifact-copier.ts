import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import {
  ArtifactCopier,
  ArtifactCopyOutcome,
  ArtifactCopyRequest,
} from '../../agent-harness/ports/session-forks';
import { ArtifactEraserConfig } from './harness-artifact-eraser';

const COPY_TIMEOUT_MS = 120_000;
const TOKEN_TTL = '5m';

const copiesSchema = z
  .object({
    copies: z.array(
      z
        .object({
          artifactId: z.string().min(1),
          version: z.number().int().positive().nullable(),
          copy: z
            .object({
              artifactId: z.string().min(1),
              documentId: z.string(),
              version: z.number().int().positive(),
            })
            .strip()
            .nullable(),
          reason: z.string().optional(),
        })
        .strip(),
    ),
  })
  .strip();

/** The query service answered something other than a copy report. */
export class ArtifactCopyFailedError extends Error {
  constructor(public readonly statusCode: number) {
    super(`Artifact copy answered ${String(statusCode)}`);
    this.name = 'ArtifactCopyFailedError';
  }
}

/**
 * The artifact registry's copy route on the query service (SS3): a service
 * token scoped `conversation:fork` carrying the forker's org and user, so the
 * registry copies only what that user may read. The body names only the
 * conversations and the versions.
 */
export class HarnessArtifactCopier implements ArtifactCopier {
  constructor(private readonly config: () => ArtifactEraserConfig) {}

  async copy(request: ArtifactCopyRequest): Promise<ArtifactCopyOutcome[]> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      {
        orgId: request.orgId,
        userId: request.actorId,
        scopes: [TokenScopes.CONVERSATION_FORK],
      },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}/api/v1/chat/artifacts/copy`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: {
        fromConversationId: request.fromConversationId,
        toConversationId: request.toConversationId,
        artifacts: request.artifacts.map((ref) => ({
          artifactId: ref.artifactId,
          ...(ref.version !== null ? { version: ref.version } : {}),
        })),
      },
      timeoutMs: COPY_TIMEOUT_MS,
    }).execute();
    if (response.statusCode !== 200) {
      throw new ArtifactCopyFailedError(response.statusCode);
    }
    const parsed = copiesSchema.safeParse(response.data);
    if (!parsed.success) throw new ArtifactCopyFailedError(200);
    return parsed.data.copies.map((copy) => ({
      artifactId: copy.artifactId,
      version: copy.version,
      copy: copy.copy,
      ...(copy.reason !== undefined ? { reason: copy.reason } : {}),
    }));
  }
}
