import {
  HarnessUnavailableError,
  JournalPayloadMissingError,
} from '../domain/errors';
import type { HarnessRun } from '../domain/execution';
import { parseContentRef } from '../domain/run-content';
import {
  describeHistory,
  HISTORY_PART_BYTES,
  historyBytes,
  historyParts,
  joinHistory,
  RunHistoryRef,
} from '../domain/run-history';
import type { HistoryTurnEntry } from '../domain/session-audience';
import { PayloadReferences, PayloadStore } from '../ports/payload-store';
import { EnvelopeCipher } from './envelope-cipher';
import { payloadsWithin } from './payloads-within';
import { openRunContent, sealRunContent } from './run-content-service';
import { journalContentFailure } from './sealed-journal-store';

type HistoryOwner = Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId'>;

/**
 * A run's history kept beside its document (QA22-F2): sealed as content the
 * admitted run wrote, so the org's key protects it, the session's erasure
 * deletes it by owner, and the reference mark keeps retention off it.
 */
export class RunHistoryContent {
  constructor(
    private readonly cipher: EnvelopeCipher | null,
    private readonly blobs: PayloadStore | null,
    private readonly references: PayloadReferences<unknown> | null = null,
    private readonly partBytes = HISTORY_PART_BYTES,
  ) {}

  /**
   * Stores `turns` in the admission's transaction `tx` and marks them
   * referenced there. Null: there is nothing to keep by reference (no
   * history, or nowhere to seal it), and the caller keeps it inline.
   */
  async store(
    run: HistoryOwner,
    turns: readonly HistoryTurnEntry[],
    tx?: unknown,
  ): Promise<RunHistoryRef | null> {
    if (turns.length === 0 || this.cipher === null || this.blobs === null) {
      return null;
    }
    const bytes = historyBytes(turns);
    const blobs = payloadsWithin(this.blobs, tx);
    const parts: string[] = [];
    // One at a time: a transaction takes one operation at a time.
    for (const part of historyParts(bytes, this.partBytes)) {
      parts.push((await sealRunContent(this.cipher, blobs, run, part)).ref);
    }
    if (this.references !== null) {
      await this.references.markReferenced(
        run.orgId,
        parts.flatMap((part) => parseContentRef(part)?.storeRef ?? []),
        tx,
      );
    }
    return { parts, ...describeHistory(bytes, turns.length) };
  }

  /**
   * The history `run` was admitted with. A part that is gone or does not
   * match is terminal for the run (`JournalPayloadMissingError`); a store
   * that cannot answer is not.
   */
  async read(
    run: HistoryOwner,
    ref: RunHistoryRef,
  ): Promise<HistoryTurnEntry[]> {
    const { cipher, blobs } = this;
    if (cipher === null || blobs === null) {
      throw new HarnessUnavailableError(
        'The run names a stored history and no history store is configured',
      );
    }
    const parts = await Promise.all(
      ref.parts.map(async (part) => {
        const storeRef = parseContentRef(part)?.storeRef;
        let plaintext: Buffer | null;
        try {
          plaintext =
            storeRef === undefined
              ? null
              : await openRunContent(cipher, blobs, run, storeRef);
        } catch (error) {
          throw journalContentFailure(error, part);
        }
        if (plaintext === null) {
          throw new JournalPayloadMissingError(part, 'missing');
        }
        return plaintext;
      }),
    );
    const turns = joinHistory(parts, ref);
    if (turns === null) {
      throw new JournalPayloadMissingError(ref.parts[0] ?? ref.hash, 'corrupt');
    }
    return turns;
  }
}
