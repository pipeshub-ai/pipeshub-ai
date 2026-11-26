import { canonicalJsonBytes } from '../contracts/canonical-json';
import {
  JournalContentUnavailableError,
  JournalPayloadMissingError,
  SealedContentError,
  SessionErasedError,
} from '../domain/errors';
import {
  JournalAppendResult,
  JournalEvent,
  journalSealContext,
  PreparedJournalAppend,
  PreparedJournalEvent,
} from '../domain/journal';
import { JournalStore } from '../ports/journal-store';
import { EnvelopeCipher } from './envelope-cipher';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** Where an unreadable inline event is reported (it has no payload ref). */
export const eventContentRef = (
  event: Pick<JournalEvent, 'streamId' | 'seq'>,
): string => `event:${event.streamId}:${String(event.seq)}`;

/**
 * A sealed content failure as the journal reports it: gone or tampered is
 * terminal for the run (`journal_payload_missing`); a format or KEK this
 * replica cannot use is retryable.
 */
export function journalContentFailure(error: unknown, ref: string): unknown {
  if (!(error instanceof SealedContentError)) return error;
  switch (error.reason) {
    case 'corrupt':
      return new JournalPayloadMissingError(ref, 'corrupt');
    case 'shredded':
      return new JournalPayloadMissingError(ref, 'shredded');
    case 'unsupported_version':
    case 'unwrap_failed':
      return new JournalContentUnavailableError(error.reason);
  }
}

/**
 * The journal at rest (REPORT 6.16): every event's `data` is sealed with the
 * org's data key at append, inside the caller's transaction, and opened on
 * read, so projections, transcripts and workers see plain events. The
 * envelope stays plain and queryable. A stored event with plaintext `data`
 * is refused: nothing may downgrade the journal by writing around this store.
 */
export class SealedJournalStore<Tx> implements JournalStore<Tx> {
  constructor(
    private readonly inner: JournalStore<Tx>,
    private readonly cipher: EnvelopeCipher,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const events = await Promise.all(
      batch.events.map((event) => this.seal(batch, event)),
    );
    return this.inner.append({ ...batch, events }, tx);
  }

  lastSeq(
    orgId: string,
    streamIds: readonly string[],
    tx?: Tx,
  ): Promise<Record<string, number>> {
    return this.inner.lastSeq(orgId, streamIds, tx);
  }

  async readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.openAll(
      await this.inner.readStream(orgId, streamId, afterSeq, limit),
    );
  }

  async readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.openAll(
      await this.inner.readRun(orgId, runId, afterSeq, limit),
    );
  }

  private async seal(
    batch: PreparedJournalAppend,
    event: PreparedJournalEvent,
  ): Promise<PreparedJournalEvent> {
    if (event.data === undefined) return event;
    const { data, ...envelope } = event;
    const sealed = await this.cipher.seal(
      batch.orgId,
      'journal.data',
      journalSealContext(batch, event.type),
      canonicalJsonBytes(data),
    );
    return { ...envelope, sealed };
  }

  private openAll(events: JournalEvent[]): Promise<JournalEvent[]> {
    return Promise.all(events.map((event) => this.open(event)));
  }

  private async open(event: JournalEvent): Promise<JournalEvent> {
    if (event.erased === true) throw new SessionErasedError();
    const { sealed, data, ...envelope } = event;
    if (data !== undefined) {
      this.unreadable(event, 'plaintext');
      throw new JournalPayloadMissingError(eventContentRef(event), 'plaintext');
    }
    if (sealed === undefined) {
      if (event.payloadRef !== undefined) return event;
      this.unreadable(event, 'missing');
      throw new JournalPayloadMissingError(eventContentRef(event), 'missing');
    }
    let bytes: Buffer;
    try {
      bytes = await this.cipher.open(
        event.orgId,
        'journal.data',
        journalSealContext(event, event.type),
        sealed,
      );
    } catch (error) {
      if (error instanceof SealedContentError) {
        this.unreadable(event, error.reason);
      }
      throw journalContentFailure(error, eventContentRef(event));
    }
    return {
      ...envelope,
      data: JSON.parse(bytes.toString('utf8')) as Record<string, unknown>,
    };
  }

  private unreadable(event: JournalEvent, reason: string): void {
    this.logger.error(
      'journal.content_unreadable',
      harnessLogMeta('journal.content_unreadable', {
        orgId: event.orgId,
        sessionId: event.sessionId,
        runId: event.runId,
        seq: event.seq,
        type: event.type,
        reason,
      }),
    );
  }
}
