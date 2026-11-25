/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import {
  AuditAnchor,
  AuditEntry,
  AuditHead,
  AuditRecord,
  AuditUnlinked,
  chainAuditRecords,
} from '../domain/audit';
import { AuditAnchorConflictError } from '../domain/errors';
import { AuditAnchorSink, AuditChainStore } from '../ports/audit';
import {
  AuditAnchorLookup,
  AuditChainQueries,
  AuditKindQuery,
} from '../ports/audit-queries';

const holds = (
  record: AuditRecord,
  name: string,
  values: readonly unknown[],
): boolean =>
  Object.prototype.hasOwnProperty.call(record.fields, name) &&
  values.includes(record.fields[name]);

const matches = (record: AuditRecord, query: AuditKindQuery): boolean =>
  query.kinds.includes(record.kind) &&
  (query.beforeSeq === null || record.seq < query.beforeSeq) &&
  (query.since === undefined || record.ts >= query.since) &&
  (query.until === undefined || record.ts <= query.until) &&
  (query.sessionIds === undefined ||
    (record.sessionId !== null &&
      query.sessionIds.includes(record.sessionId))) &&
  (query.runIds === undefined ||
    (record.runId !== null && query.runIds.includes(record.runId))) &&
  Object.entries(query.fields ?? {}).every(([name, values]) =>
    holds(record, name, values),
  ) &&
  (query.anyOf === undefined ||
    (record.runId !== null &&
      (query.anyOf.runIds ?? []).includes(record.runId)) ||
    Object.entries(query.anyOf.fields ?? {}).some(([name, values]) =>
      holds(record, name, values),
    ));

/** Single-threaded, so an append is trivially serial per org. */
export class InMemoryAuditChainStore
  implements AuditChainStore<unknown>, AuditChainQueries
{
  readonly records = new Map<string, AuditRecord[]>();
  private readonly heads = new Map<string, AuditHead>();

  async append(
    orgId: string,
    entries: readonly AuditEntry[],
    ts: string,
  ): Promise<void> {
    const head = this.heads.get(orgId) ?? null;
    const records = chainAuditRecords(orgId, head, entries, ts);
    const last = records.at(-1);
    if (last === undefined) return;
    this.records.set(orgId, [
      ...(this.records.get(orgId) ?? []),
      ...structuredClone(records),
    ]);
    this.heads.set(orgId, {
      orgId,
      seq: last.seq,
      hash: last.hash,
      anchoredSeq: head?.anchoredSeq ?? 0,
    });
  }

  async read(
    orgId: string,
    afterSeq: number,
    limit: number,
  ): Promise<AuditRecord[]> {
    return structuredClone(
      (this.records.get(orgId) ?? [])
        .filter((record) => record.seq > afterSeq)
        .sort((left, right) => left.seq - right.seq)
        .slice(0, limit),
    );
  }

  async head(orgId: string): Promise<AuditHead | null> {
    const head = this.heads.get(orgId);
    return head === undefined ? null : { ...head };
  }

  async unanchored(limit: number): Promise<AuditHead[]> {
    return [...this.heads.values()]
      .filter((head) => head.seq > head.anchoredSeq)
      .sort((left, right) => left.anchoredSeq - right.anchoredSeq)
      .slice(0, limit)
      .map((head) => ({ ...head }));
  }

  async markAnchored(orgId: string, seq: number): Promise<void> {
    const head = this.heads.get(orgId);
    if (head !== undefined && head.anchoredSeq < seq) head.anchoredSeq = seq;
  }

  /** An append is linked as it is made: none is ever left. */
  async unlinked(): Promise<AuditUnlinked[]> {
    return [];
  }

  async readKind(orgId: string, query: AuditKindQuery): Promise<AuditRecord[]> {
    return structuredClone(
      (this.records.get(orgId) ?? [])
        .filter((record) => matches(record, query))
        .sort((left, right) => right.seq - left.seq)
        .slice(0, query.limit),
    );
  }

  async readAt(orgId: string, seqs: readonly number[]): Promise<AuditRecord[]> {
    const wanted = new Set(seqs);
    return structuredClone(
      (this.records.get(orgId) ?? [])
        .filter((record) => wanted.has(record.seq))
        .sort((left, right) => left.seq - right.seq),
    );
  }

  async sessionsOf(
    orgId: string,
    query: Pick<AuditKindQuery, 'kinds' | 'since' | 'until' | 'limit'>,
  ): Promise<string[]> {
    const sessions: string[] = [];
    for (const record of await this.readKind(orgId, {
      ...query,
      beforeSeq: null,
      limit: Number.MAX_SAFE_INTEGER,
    })) {
      if (record.sessionId !== null && !sessions.includes(record.sessionId)) {
        sessions.push(record.sessionId);
      }
    }
    return sessions.slice(0, query.limit);
  }
}

export class InMemoryAuditAnchorSink
  implements AuditAnchorSink, AuditAnchorLookup
{
  readonly name = 'memory';
  private readonly anchors = new Map<string, AuditAnchor>();

  async write(anchor: AuditAnchor): Promise<void> {
    const key = `${anchor.orgId}:${String(anchor.seq)}`;
    const existing = this.anchors.get(key);
    if (existing !== undefined) {
      if (existing.hash !== anchor.hash) {
        throw new AuditAnchorConflictError(anchor.orgId, anchor.seq);
      }
      return;
    }
    this.anchors.set(key, { ...anchor });
  }

  async list(orgId: string): Promise<AuditAnchor[]> {
    return [...this.anchors.values()]
      .filter((anchor) => anchor.orgId === orgId)
      .sort((left, right) => left.seq - right.seq)
      .map((anchor) => ({ ...anchor }));
  }

  async atOrAfter(orgId: string, seq: number): Promise<AuditAnchor | null> {
    return (await this.list(orgId)).find((anchor) => anchor.seq >= seq) ?? null;
  }
}
