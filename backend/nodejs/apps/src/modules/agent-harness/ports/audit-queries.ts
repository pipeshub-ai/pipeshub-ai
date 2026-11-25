import type {
  AuditAnchor,
  AuditFieldValue,
  AuditRecord,
} from '../domain/audit';

/**
 * Records of some kinds, newest first, as an admin page reads them. Every
 * condition is ANDed; a list matches any of its values. `fields` names
 * entry fields the kinds carry (`by`, `pathDigest`), never a nested path.
 */
export interface AuditKindQuery {
  kinds: readonly string[];
  /** Only records with a smaller seq (the page cursor); null: from the head. */
  beforeSeq: number | null;
  limit: number;
  /** Bounds on the record's `ts`, inclusive (ISO 8601, as stored). */
  since?: string;
  until?: string;
  sessionIds?: readonly string[];
  /** Only records of these runs (J-18: an agent's runs). */
  runIds?: readonly string[];
  fields?: Readonly<Record<string, readonly AuditFieldValue[]>>;
  /**
   * One more condition, met when any of its parts is: the record is of one of
   * `runIds`, or one of `fields` holds one of its values. A person's activity
   * is their runs' records or the records that name them acting (J-18).
   */
  anyOf?: {
    runIds?: readonly string[];
    fields?: Readonly<Record<string, readonly AuditFieldValue[]>>;
  };
}

/**
 * Reads of an org's chain by kind (AUDITUI). Like `AuditChainStore.read`,
 * each links what is pending first, so a record committed before the call is
 * on the page. The org is always the caller's own: no read crosses orgs.
 */
export interface AuditChainQueries {
  readKind(orgId: string, query: AuditKindQuery): Promise<AuditRecord[]>;
  /** The stored records at these seqs (those that exist), for the per-record checks. */
  readAt(orgId: string, seqs: readonly number[]): Promise<AuditRecord[]>;
  /** The distinct sessions of records of `kinds` in the window, the most recent first. */
  sessionsOf(
    orgId: string,
    query: Pick<AuditKindQuery, 'kinds' | 'since' | 'until' | 'limit'>,
  ): Promise<string[]>;
}

/** The first anchor at or after a seq, without listing every anchor of the org. */
export interface AuditAnchorLookup {
  atOrAfter(orgId: string, seq: number): Promise<AuditAnchor | null>;
}
