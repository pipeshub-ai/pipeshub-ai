import type { CheckedSource } from '../projection/citation-source';

export interface SourceReadQuery {
  /**
   * The viewer's own bearer credential. The authority resolves the org and
   * the user from it; nothing in the query can name whose access is checked.
   */
  authorization: string;
  sources: readonly CheckedSource[];
}

/**
 * `unresolved`: nothing in the viewer's org matches (no such record, or no
 * indexed copy of a live item), so the viewer's access cannot be verified.
 */
export type SourceStatus = 'readable' | 'denied' | 'unresolved';

export type RecordAccessFailure =
  'refused' | 'unavailable' | 'invalid_response';

/** The authority could not say which sources the viewer may read. */
export class RecordAccessUnavailableError extends Error {
  constructor(
    readonly reason: RecordAccessFailure,
    message: string,
  ) {
    super(message);
    this.name = 'RecordAccessUnavailableError';
  }
}

/**
 * Which cited sources a viewer may read now, per the record authorization
 * that serves their content (P0.1's tiered check; a live item through its
 * indexed copy, C-18). One call answers a batch, keyed by `sourceKey`.
 * Rejects when it cannot decide, so callers fail closed.
 */
export interface RecordReadAccess {
  decide(query: SourceReadQuery): Promise<ReadonlyMap<string, SourceStatus>>;
}
