import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../domain/journal';
import type { JournalAppendRecorder } from '../ports/journal-append-recorder';
import { JournalStore } from '../ports/journal-store';

/**
 * Hands each append's events to the recorders (`JournalAppendRecorder`) in
 * the append's transaction. Sits outside the sealing store, where event data
 * is still plain; a claim-checked event carries what `JournalService.prepare`
 * took while its data was at hand (`taken`).
 */
export class RecordingJournalStore<Tx> implements JournalStore<Tx> {
  constructor(
    private readonly inner: JournalStore<Tx>,
    private readonly recorders: readonly JournalAppendRecorder<Tx>[],
  ) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const taken = this.recorders.map((recorder) =>
      batch.events.map((event) =>
        event.data !== undefined
          ? recorder.take(event.type, event.data)
          : (event.taken?.[recorder.name] ?? null),
      ),
    );
    const appended = await this.inner.append(batch, tx);
    const at = Date.parse(appended.ts);
    for (const [index, recorder] of this.recorders.entries()) {
      const items = taken[index] ?? [];
      if (items.every((item) => item === null || item === undefined)) continue;
      await recorder.record(
        tx,
        {
          orgId: batch.orgId,
          sessionId: batch.sessionId,
          ...(batch.runId !== undefined ? { runId: batch.runId } : {}),
          streamId: batch.streamId,
          firstSeq: appended.firstSeq,
          at,
        },
        items.map((item) => item ?? null),
      );
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
