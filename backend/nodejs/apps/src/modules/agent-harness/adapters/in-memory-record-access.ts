/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous port without I/O. */
import {
  RecordAccessFailure,
  RecordAccessUnavailableError,
  RecordReadAccess,
  SourceReadQuery,
  SourceStatus,
} from '../ports/record-access';
import { CheckedSource, sourceKey } from '../projection/citation-source';

/**
 * Grants per credential over records, the indexed copies of live items and
 * records that do not exist. `queries` records every call, `failWith` makes
 * the next ones reject, `dropAnswers` answers for nothing.
 */
export class InMemoryRecordReadAccess implements RecordReadAccess {
  readonly queries: Array<{ authorization: string; sources: CheckedSource[] }> =
    [];
  failWith: RecordAccessFailure | null = null;
  dropAnswers = false;
  private readonly grants = new Map<string, Set<string>>();
  private readonly absent = new Set<string>();
  private readonly copies = new Map<string, string[]>();
  private readonly contents = new Map<string, string>();

  grant(authorization: string, ...recordIds: string[]): this {
    const granted = this.grants.get(authorization) ?? new Set<string>();
    for (const id of recordIds) granted.add(id);
    this.grants.set(authorization, granted);
    return this;
  }

  revoke(authorization: string, ...recordIds: string[]): this {
    for (const id of recordIds) this.grants.get(authorization)?.delete(id);
    return this;
  }

  /**
   * Records that do not exist. A record reads `denied` either way, as the
   * real authority answers (no existence oracle); a live item none of whose
   * indexed copies exists is `unresolved`.
   */
  missing(...recordIds: string[]): this {
    for (const id of recordIds) this.absent.add(id);
    return this;
  }

  /** An attachment record and the stored content it is read by. */
  content(recordId: string, virtualRecordId: string): this {
    this.contents.set(recordId, virtualRecordId);
    return this;
  }

  /** `recordId` is an indexed copy of the live item (`connector`, `externalRecordId`). */
  indexCopy(
    connector: string,
    externalRecordId: string,
    recordId: string,
  ): this {
    const key = sourceKey({ kind: 'live', connector, externalRecordId });
    this.copies.set(key, [...(this.copies.get(key) ?? []), recordId]);
    return this;
  }

  async decide(
    query: SourceReadQuery,
  ): Promise<ReadonlyMap<string, SourceStatus>> {
    this.queries.push({
      authorization: query.authorization,
      sources: [...query.sources],
    });
    if (this.failWith !== null) {
      throw new RecordAccessUnavailableError(this.failWith, 'injected failure');
    }
    const decided = new Map<string, SourceStatus>();
    if (this.dropAnswers) return decided;
    const granted = this.grants.get(query.authorization) ?? new Set<string>();
    for (const source of query.sources) {
      const ids =
        source.kind === 'live'
          ? (this.copies.get(sourceKey(source)) ?? [])
          : [source.recordId];
      const named =
        source.kind === 'live' || source.virtualRecordId === undefined
          ? null
          : source.virtualRecordId;
      const status: SourceStatus = ids.some(
        (id) =>
          granted.has(id) &&
          (named === null || this.contents.get(id) === named),
      )
        ? 'readable'
        : source.kind !== 'live' || ids.some((id) => !this.absent.has(id))
          ? 'denied'
          : 'unresolved';
      decided.set(sourceKey(source), status);
    }
    return decided;
  }
}
