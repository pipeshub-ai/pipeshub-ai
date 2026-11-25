import { AIServiceCommand } from '../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../libs/enums/http-methods.enum';
import { InlineClaimHint, InlineClaimTarget } from '../../ports/inline-claim';

/** Short: the admission response must not wait on the claimer. */
export const INLINE_CLAIM_HINT_TIMEOUT_MS = 2_000;

export interface InlineClaimLogger {
  warn(message: string, metadata?: Record<string, unknown>): void;
}

/**
 * `POST {query}/api/v1/harness/runs/{runId}/claim` with the worker credential,
 * the same one the query service presents when it then claims from Node.
 */
export class HttpInlineClaimHint implements InlineClaimHint {
  constructor(
    private readonly queryService: () => string,
    private readonly credential: () => Promise<string | null>,
    private readonly logger: InlineClaimLogger,
    private readonly timeoutMs = INLINE_CLAIM_HINT_TIMEOUT_MS,
  ) {}

  async hint(target: InlineClaimTarget): Promise<void> {
    try {
      const credential = await this.credential();
      if (credential === null) {
        this.logger.warn('Inline claim hint skipped: no worker credential', {
          runId: target.runId,
        });
        return;
      }
      const response = await new AIServiceCommand({
        uri: `${this.queryService()}/api/v1/harness/runs/${encodeURIComponent(target.runId)}/claim`,
        method: HttpMethod.POST,
        headers: {
          Authorization: `Bearer ${credential}`,
          'Content-Type': 'application/json',
        },
        body: { orgId: target.orgId },
        timeoutMs: this.timeoutMs,
        maxAttempts: 1,
      }).execute();
      if (response.statusCode >= 300) {
        this.logger.warn('Inline claim hint refused', {
          runId: target.runId,
          status: response.statusCode,
        });
      }
    } catch (error) {
      this.logger.warn('Inline claim hint failed', {
        runId: target.runId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}
