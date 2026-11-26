import { AuditEntry, auditProjection } from '../domain/audit';
import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../domain/journal';
import { AuditChainStore } from '../ports/audit';
import { JournalStore } from '../ports/journal-store';

/**
 * Chains the audit-relevant events of every append into the org's audit
 * stream in the same transaction (REPORT 6.1.8, 6.16), so an audit record
 * exists exactly when its journal event does. Sits outside the sealing store:
 * it projects metadata from the plaintext before the content is sealed.
 */
export class AuditedJournalStore<Tx> implements JournalStore<Tx> {
  constructor(
    private readonly inner: JournalStore<Tx>,
    private readonly audit: AuditChainStore<Tx>,
  ) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const projections = batch.events.map((event) =>
      event.data !== undefined
        ? auditProjection(event.type, event.data, event.auditFields)
        : (event.audit ?? null),
    );
    const appended = await this.inner.append(batch, tx);
    const entries = projections.flatMap((projection, index): AuditEntry[] => {
      const event = batch.events[index];
      if (projection === null || event === undefined) return [];
      return [
        {
          ...projection,
          kind: event.type,
          sessionId: batch.sessionId,
          streamId: batch.streamId,
          runId: batch.runId ?? null,
          journalSeq: appended.firstSeq + index,
          writer: batch.writer,
          attemptId: batch.attemptId ?? null,
          fence: batch.fence ?? null,
        },
      ];
    });
    if (entries.length > 0) {
      await this.audit.append(batch.orgId, entries, appended.ts, tx);
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
