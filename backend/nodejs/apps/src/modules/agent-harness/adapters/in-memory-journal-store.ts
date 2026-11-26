/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { StoreClock, systemStoreClock } from '../domain/execution';
import {
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
} from '../domain/journal';
import { JournalStore } from '../ports/journal-store';
import {
  PayloadOwner,
  PayloadReferences,
  PayloadStore,
  TransactionalPayloadStore,
} from '../ports/payload-store';

const streamKey = (orgId: string, streamId: string): string =>
  JSON.stringify([orgId, streamId]);

const defined = <T extends object>(value: T): T =>
  Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined),
  ) as T;

/**
 * Single-threaded, so appends are trivially serial. Callers that need
 * all-or-nothing batches (the in-memory execution store) validate before
 * appending, as the Mongo transaction would roll back.
 */
export class InMemoryJournalStore implements JournalStore<unknown> {
  private readonly streams = new Map<string, JournalEvent[]>();

  constructor(private readonly clock: StoreClock = systemStoreClock) {}

  async append(batch: PreparedJournalAppend): Promise<JournalAppendResult> {
    return this.appendNow(batch);
  }

  /** Synchronous, so a caller can append and update other state without yielding. */
  appendNow(batch: PreparedJournalAppend): JournalAppendResult {
    const key = streamKey(batch.orgId, batch.streamId);
    const stored = this.streams.get(key) ?? [];
    const ts = new Date(this.clock.now()).toISOString();
    const firstSeq = stored.length + 1;
    const appended = batch.events.map((event, index): JournalEvent =>
      defined({
        orgId: batch.orgId,
        sessionId: batch.sessionId,
        streamId: batch.streamId,
        seq: firstSeq + index,
        runId: batch.runId,
        attemptId: batch.attemptId,
        fence: batch.fence,
        type: event.type,
        schemaVersion: event.schemaVersion,
        critical: event.critical,
        ts,
        writer: batch.writer,
        labels: structuredClone(event.labels),
        visibility: event.visibility,
        data: structuredClone(event.data),
        payloadRef: structuredClone(event.payloadRef),
        sealed: structuredClone(event.sealed),
      }),
    );
    this.streams.set(key, [...stored, ...appended]);
    return {
      streamId: batch.streamId,
      firstSeq,
      lastSeq: firstSeq + appended.length - 1,
      ts,
    };
  }

  async lastSeq(
    orgId: string,
    streamIds: readonly string[],
  ): Promise<Record<string, number>> {
    return Object.fromEntries(
      streamIds.map((streamId) => [
        streamId,
        this.streams.get(streamKey(orgId, streamId))?.length ?? 0,
      ]),
    );
  }

  async readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    const stored = this.streams.get(streamKey(orgId, streamId)) ?? [];
    return structuredClone(
      stored.filter((event) => event.seq > afterSeq).slice(0, limit),
    );
  }

  async readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]> {
    const matching = [...this.streams.values()]
      .flat()
      .filter(
        (event) =>
          event.orgId === orgId &&
          event.runId === runId &&
          event.seq > afterSeq,
      )
      .sort((left, right) => left.seq - right.seq);
    return structuredClone(matching.slice(0, limit));
  }
}

export class InMemoryPayloadStore
  implements
    PayloadStore,
    PayloadReferences<unknown>,
    TransactionalPayloadStore<unknown>
{
  readonly objects = new Map<string, Buffer>();
  readonly owners = new Map<string, PayloadOwner>();
  readonly referenced = new Set<string>();

  async put(
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    const ref = `memory:${orgId}:${hash}`;
    if (!this.objects.has(ref)) {
      this.objects.set(ref, Buffer.from(bytes));
      if (owner !== undefined) this.owners.set(ref, { ...owner });
    }
    return ref;
  }

  /** No transactions in memory: a write is visible at once. */
  putIn(
    _tx: unknown,
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    return this.put(orgId, hash, bytes, owner);
  }

  async markReferenced(orgId: string, refs: readonly string[]): Promise<void> {
    for (const ref of refs) {
      if (ref.startsWith(`memory:${orgId}:`) && this.objects.has(ref)) {
        this.referenced.add(ref);
      }
    }
  }

  async get(orgId: string, ref: string): Promise<Buffer | null> {
    if (!ref.startsWith(`memory:${orgId}:`)) return null;
    const bytes = this.objects.get(ref);
    return bytes === undefined ? null : Buffer.from(bytes);
  }
}
