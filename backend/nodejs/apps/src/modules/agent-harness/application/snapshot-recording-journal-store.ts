import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../domain/journal';
import { JournalStore } from '../ports/journal-store';
import type {
  SandboxSnapshotIndex,
  StoredSandboxSnapshot,
} from '../ports/sandbox-snapshots';

const SNAPSHOTTED = 'engine.snapshotted';

/** The storage document an `engine.snapshotted` event's data names, or null. */
export function snapshotObjectId(
  data: Record<string, unknown> | undefined,
): string | null {
  const ref = data?.['snapshot'];
  const id =
    typeof ref === 'object' && ref !== null
      ? (ref as { objectId?: unknown }).objectId
      : undefined;
  return typeof id === 'string' && id.length > 0 ? id : null;
}

/**
 * Records each sandbox snapshot an append names (OWD-12), in the append's
 * transaction, so the retention sweeper can delete it once it is past its
 * org's TTL. Sits outside the sealing store, where event data is still plain.
 */
export class SnapshotRecordingJournalStore<Tx> implements JournalStore<Tx> {
  constructor(
    private readonly inner: JournalStore<Tx>,
    private readonly snapshots: SandboxSnapshotIndex<Tx>,
    private readonly region: string,
  ) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const appended = await this.inner.append(batch, tx);
    const takenAt = Date.parse(appended.ts);
    const named: StoredSandboxSnapshot[] = batch.events.flatMap((event) => {
      if (event.type !== SNAPSHOTTED) return [];
      const objectId = snapshotObjectId(event.data);
      return objectId === null
        ? []
        : [
            {
              orgId: batch.orgId,
              sessionId: batch.sessionId,
              runId: batch.runId ?? null,
              objectId,
              takenAt,
              region: this.region,
            },
          ];
    });
    if (named.length > 0) await this.snapshots.record(named, tx);
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
