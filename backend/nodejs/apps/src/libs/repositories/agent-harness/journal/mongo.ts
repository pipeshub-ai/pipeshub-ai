import { assertIdFilter, eq } from '../../id-filter-guard/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../../../../modules/agent-harness/domain/journal';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import {
  SessionEventDocument,
  SessionEventModel,
  SessionStreamCounterModel,
} from './schema/journal.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';
import { transactionWrites } from '../transaction-writes/mongo';

const toEvent = (
  document: SessionEventDocument & { _id?: unknown },
): JournalEvent => {
  const { _id, ts, ...event } = document;
  return { ...structuredClone(event), ts: ts.toISOString() };
};

export class MongoJournalStore implements JournalStore<Tx> {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async append(
    batch: PreparedJournalAppend,
    sessionHandle: Tx | ClientSession,
  ): Promise<JournalAppendResult> {
    const session = asMongoSession(sessionHandle);
    const count = batch.events.length;
    if (count === 0) throw new Error('Journal append needs at least one event');
    // Allocating inside the commit transaction keeps seq order equal to
    // commit order, so a reader never sees a hole that fills in later.
    const filter = { orgId: batch.orgId, streamId: batch.streamId };
    assertIdFilter(filter);
    const key = `sessionStreamCounters\u0000${batch.orgId}\u0000${batch.streamId}`;
    const allocated = transactionWrites.recall<{ lastSeq: number; ts: Date }>(
      session,
      key,
    );
    // A second append to the stream in this transaction (an ending commit's worker events, then `run.completed`)
    // continues the range the first allocated, at its time; the counter's move joins the batch (PERF-E3).
    const counter =
      allocated !== undefined
        ? { lastSeq: allocated.lastSeq + count, ts: allocated.ts }
        : // The driver directly (PERF-E3): Mongoose's cast of a pipeline update is per-commit CPU and casts nothing here.
          ((await SessionStreamCounterModel.collection.findOneAndUpdate(
            filter,
            [
              {
                $set: {
                  orgId: { $literal: batch.orgId },
                  streamId: { $literal: batch.streamId },
                  lastSeq: { $add: [{ $ifNull: ['$lastSeq', 0] }, count] },
                  ts: { $toDate: { $toLong: this.time.expr() } },
                },
              },
            ],
            { upsert: true, returnDocument: 'after', session },
          )) as { lastSeq: number; ts: Date } | null);
    // An upsert that returns the document after it always returns one.
    if (counter === null)
      throw new Error('Stream counter upsert returned no document');
    if (allocated !== undefined) {
      const moved = { $set: { lastSeq: counter.lastSeq } };
      await transactionWrites.update(
        session,
        SessionStreamCounterModel,
        { name: 'updateOne', filter, update: moved },
        () => SessionStreamCounterModel.updateOne(filter, moved, { session }),
      );
    }
    transactionWrites.keep(session, key, {
      lastSeq: counter.lastSeq,
      ts: counter.ts,
    });
    const firstSeq = counter.lastSeq - count + 1;
    const events = batch.events.map((event, index) => ({
      orgId: batch.orgId,
      sessionId: batch.sessionId,
      streamId: batch.streamId,
      seq: firstSeq + index,
      ...(batch.runId !== undefined ? { runId: batch.runId } : {}),
      ...(batch.attemptId !== undefined ? { attemptId: batch.attemptId } : {}),
      ...(batch.fence !== undefined ? { fence: batch.fence } : {}),
      type: event.type,
      schemaVersion: event.schemaVersion,
      critical: event.critical,
      ts: counter.ts,
      writer: batch.writer,
      ...(event.labels !== undefined ? { labels: event.labels } : {}),
      ...(event.data !== undefined ? { data: event.data } : {}),
      ...(event.payloadRef !== undefined
        ? { payloadRef: event.payloadRef }
        : {}),
      ...(event.sealed !== undefined ? { sealed: event.sealed } : {}),
      visibility: event.visibility,
    }));
    await transactionWrites.insert(session, SessionEventModel, events, () =>
      SessionEventModel.insertMany(events, { session, ordered: true }),
    );
    return {
      streamId: batch.streamId,
      firstSeq,
      lastSeq: counter.lastSeq,
      ts: counter.ts.toISOString(),
    };
  }

  async lastSeq(
    orgId: string,
    streamIds: readonly string[],
    sessionHandle?: Tx | ClientSession,
  ): Promise<Record<string, number>> {
    const session = asMongoSession(sessionHandle);
    const query = SessionStreamCounterModel.find({
      orgId: eq(orgId),
      streamId: { $in: [...streamIds] },
    });
    const counters = await (
      session
        ? query.session(session)
        : query.read('primary').readConcern('majority')
    ).lean();
    const found = new Map(
      counters.map((counter) => [counter.streamId, counter.lastSeq]),
    );
    return Object.fromEntries(
      streamIds.map((streamId) => [streamId, found.get(streamId) ?? 0]),
    );
  }

  async readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    const events = await this.durableRead(
      SessionEventModel.find({ orgId, streamId, seq: { $gt: afterSeq } }),
    )
      .sort({ seq: 1 })
      .limit(limit)
      .lean();
    return events.map(toEvent);
  }

  async readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    const events = await this.durableRead(
      SessionEventModel.find({ orgId, runId, seq: { $gt: afterSeq } }),
    )
      .sort({ seq: 1 })
      .limit(limit)
      .lean();
    return events.map(toEvent);
  }

  /**
   * Primary + majority: a worker folding after a claim must not read a stale
   * secondary or an event a failover could still roll back [R-4].
   */
  durableRead<
    Q extends { read(pref: string): Q; readConcern(level: string): Q },
  >(query: Q): Q {
    return query.read('primary').readConcern('majority');
  }
}
