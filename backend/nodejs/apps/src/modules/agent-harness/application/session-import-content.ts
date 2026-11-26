import { createHash } from 'node:crypto';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import { HarnessRun } from '../domain/execution';
import { HarnessConflictError } from '../domain/errors';
import { CONTENT_MAX_BYTES, inlineContentRef } from '../domain/run-content';
import {
  IMPORT_INLINE_MAX_BYTES,
  ImportContentRef,
  ImportPlan,
} from '../domain/session-import';
import { PayloadStore } from '../ports/payload-store';
import { payloadsWithin } from './payloads-within';
import { EnvelopeCipher } from './envelope-cipher';
import { sealRunContent } from './run-content-service';

/**
 * Where an imported transcript lives (P1b T3): in its event when it is small,
 * else sealed as content the admitted run wrote, which the worker reads
 * through the content route like any of the session's content.
 */
export class SessionImportContent {
  constructor(
    private readonly cipher: EnvelopeCipher | null,
    private readonly blobs: PayloadStore | null,
    private readonly inlineMaxBytes = IMPORT_INLINE_MAX_BYTES,
  ) {}

  /** `tx`: the admission transaction that cites the content; it is stored in it when the store can (R3 M8). */
  async store(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId'>,
    plan: ImportPlan,
    tx?: unknown,
  ): Promise<{ ref: ImportContentRef; sealed: boolean }> {
    const bytes = canonicalJsonBytes(plan.turns);
    if (bytes.length <= this.inlineMaxBytes) {
      return { ref: inlineContentRef(bytes), sealed: false };
    }
    if (
      bytes.length > CONTENT_MAX_BYTES ||
      this.cipher === null ||
      this.blobs === null
    ) {
      throw new HarnessConflictError(
        'SESSION_HISTORY_TOO_LARGE',
        'The conversation history is too large to continue durably',
      );
    }
    const { ref } = await sealRunContent(
      this.cipher,
      payloadsWithin(this.blobs, tx),
      run,
      bytes,
    );
    const hash = `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
    return { ref: { ref, hash, size: bytes.length }, sealed: true };
  }
}
