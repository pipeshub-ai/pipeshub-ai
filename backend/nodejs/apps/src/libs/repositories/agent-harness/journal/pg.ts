import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../../../../modules/agent-harness/domain/journal';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import type { Tx } from '../../uow';
import {
  decodeDoc,
  docParam,
  orgUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import type { SessionEventDocument } from './document';

const toEvent = (document: SessionEventDocument): JournalEvent => {
  const { ts, ...event } = document;
  return { ...event, ts: ts.toISOString() };
};

/**
 * The typed session journal on PostgreSQL (`harness.session_events`). An append takes the stream's counter row
 * (`INSERT ... ON CONFLICT DO UPDATE`), whose lock is held to commit: a second writer of the stream waits and then
 * continues after the first one's range, so seq is gap-free and in commit order, and a reader (READ COMMITTED) never
 * sees a hole that fills in later.
 */
export class PgJournalStore implements JournalStore<Tx> {
  /** The time each stream's first append in a unit was stamped with: later appends of that unit keep it, as on Mongo. */
  private readonly stamped = new WeakMap<PgKernelTx, Map<string, Date>>();

  constructor(private readonly time: PgTime = pgServerTime) {}

  async append(
    batch: PreparedJournalAppend,
    tx: Tx,
  ): Promise<JournalAppendResult> {
    const count = batch.events.length;
    if (count === 0) throw new Error('Journal append needs at least one event');
    return orgUnit(tx, batch.orgId, { write: true }, async (k) => {
      const stamps = this.stamped.get(k) ?? new Map<string, Date>();
      this.stamped.set(k, stamps);
      const ts = stamps.get(batch.streamId) ?? new Date(await this.time.now(k));
      stamps.set(batch.streamId, ts);
      const { rows } = await k.query<{ last_seq: string }>(
        `/* pipeshub:harness.stream_counter_allocate */ INSERT INTO harness.stream_counters AS c
           (org_id, stream_id, last_seq, ts, region) VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (org_id, stream_id) DO UPDATE SET last_seq = c.last_seq + excluded.last_seq, ts = excluded.ts
         RETURNING last_seq::text`,
        [batch.orgId, batch.streamId, count, ts, rowRegion()],
      );
      const lastSeq = Number(rows[0]?.last_seq);
      const firstSeq = lastSeq - count + 1;
      const events: SessionEventDocument[] = batch.events.map(
        (event, index) => ({
          orgId: batch.orgId,
          sessionId: batch.sessionId,
          streamId: batch.streamId,
          seq: firstSeq + index,
          ...(batch.runId !== undefined ? { runId: batch.runId } : {}),
          ...(batch.attemptId !== undefined
            ? { attemptId: batch.attemptId }
            : {}),
          ...(batch.fence !== undefined ? { fence: batch.fence } : {}),
          type: event.type,
          schemaVersion: event.schemaVersion,
          critical: event.critical,
          ts,
          writer: batch.writer,
          ...(event.labels !== undefined ? { labels: event.labels } : {}),
          ...(event.data !== undefined ? { data: event.data } : {}),
          ...(event.payloadRef !== undefined
            ? { payloadRef: event.payloadRef }
            : {}),
          ...(event.sealed !== undefined ? { sealed: event.sealed } : {}),
          visibility: event.visibility,
        }),
      );
      await k.query(
        `/* pipeshub:harness.session_events_append */ INSERT INTO harness.session_events
           (org_id, stream_id, seq, session_id, run_id, ts, doc, region)
         SELECT $1, $2, e.seq, $3, $4, $5, e.doc::jsonb, $6
           FROM unnest($7::bigint[], $8::text[]) AS e(seq, doc)`,
        [
          batch.orgId,
          batch.streamId,
          batch.sessionId,
          batch.runId ?? null,
          ts,
          rowRegion(),
          events.map((event) => event.seq),
          events.map((event) => docParam(event)),
        ],
      );
      return {
        streamId: batch.streamId,
        firstSeq,
        lastSeq,
        ts: ts.toISOString(),
      };
    });
  }

  async lastSeq(
    orgId: string,
    streamIds: readonly string[],
    tx?: Tx,
  ): Promise<Record<string, number>> {
    const found = await orgUnit(tx, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ stream_id: string; last_seq: string }>(
        `/* pipeshub:harness.stream_counters_read */ SELECT stream_id, last_seq::text FROM harness.stream_counters
          WHERE org_id = $1 AND stream_id = ANY($2::text[])`,
        [orgId, [...streamIds]],
      );
      return new Map(rows.map((row) => [row.stream_id, Number(row.last_seq)]));
    });
    return Object.fromEntries(
      streamIds.map((streamId) => [streamId, found.get(streamId) ?? 0]),
    );
  }

  readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.read(
      orgId,
      `/* pipeshub:harness.session_events_stream */ SELECT doc FROM harness.session_events
        WHERE org_id = $1 AND stream_id = $2 AND seq > $3 ORDER BY seq LIMIT $4`,
      [orgId, streamId, afterSeq, limit],
    );
  }

  readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    return this.read(
      orgId,
      `/* pipeshub:harness.session_events_run */ SELECT doc FROM harness.session_events
        WHERE org_id = $1 AND run_id = $2 AND seq > $3 ORDER BY seq LIMIT $4`,
      [orgId, runId, afterSeq, limit],
    );
  }

  private read(
    orgId: string,
    text: string,
    values: unknown[],
  ): Promise<JournalEvent[]> {
    return orgUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(text, values);
      return rows.map((row) =>
        toEvent(decodeDoc<SessionEventDocument>(row.doc)),
      );
    });
  }
}
