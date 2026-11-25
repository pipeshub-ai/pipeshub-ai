import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../domain/journal';
import { referencedPayloads } from '../domain/payload-references';
import { JournalStore } from '../ports/journal-store';
import { PayloadReferences } from '../ports/payload-store';

/**
 * Marks the payloads an append's events point at as referenced, in the
 * append's transaction (T5). Sits outside the sealing store, where event data
 * is still plain; a payload stored for an append that then aborts stays
 * unreferenced, and GC may collect it once its writer run has ended.
 */
export class PayloadReferencingJournalStore<Tx> implements JournalStore<Tx> {
  constructor(
    private readonly inner: JournalStore<Tx>,
    private readonly references: PayloadReferences<Tx>,
  ) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const appended = await this.inner.append(batch, tx);
    const refs = referencedPayloads(batch.events);
    if (refs.length > 0) {
      await this.references.markReferenced(batch.orgId, refs, tx);
    }
    return appended;
  }

  lastSeq(
    orgId: string,
    streamIds: readonly string[],
    tx?: Tx,
  ): Promise<Record<string, number>> {
    return this.inner.lastSeq(orgId, streamIds, tx);
  }

  readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.inner.readStream(orgId, streamId, afterSeq, limit);
  }

  readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.inner.readRun(orgId, runId, afterSeq, limit);
  }
}
