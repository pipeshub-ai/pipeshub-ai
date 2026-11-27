import {
  AuditAnchor,
  AuditEntry,
  AuditHead,
  AuditRecord,
  AuditUnlinked,
} from '../domain/audit';

/**
 * The per-org audit chain. An append commits with the journal's transaction;
 * a read answers with the appends committed before it linked (`unlinked`
 * names any that could not be), each append's records contiguous and every
 * stream's in journal order.
 */
export interface AuditChainStore<Tx> {
  /** `ts` is the journal append's DB time. */
  append(
    orgId: string,
    entries: readonly AuditEntry[],
    ts: string,
    tx: Tx,
  ): Promise<void>;
  read(orgId: string, afterSeq: number, limit: number): Promise<AuditRecord[]>;
  head(orgId: string): Promise<AuditHead | null>;
  /** Heads that grew since their last anchor, oldest anchor first. */
  unanchored(limit: number): Promise<AuditHead[]>;
  /** Records that the head up to `seq` is anchored (never moves backwards). */
  markAnchored(orgId: string, seq: number): Promise<void>;
  /**
   * Appends committed and not on the chain, per org (one org when given). A
   * read links first, so anything here after a read is an append whose link
   * could not run.
   */
  unlinked(orgId?: string): Promise<AuditUnlinked[]>;
}

/**
 * Where chain heads are anchored. Implementations are append-only: an anchor
 * is written once and never replaced (WORM in production).
 */
export interface AuditAnchorSink {
  readonly name: string;
  /** Idempotent for the same anchor; refuses a different hash at an anchored seq. */
  write(anchor: AuditAnchor): Promise<void>;
  list(orgId: string): Promise<AuditAnchor[]>;
}
