import type { ChangeEvent, ConfigChanged } from '../domain/config-change';
import type { CpTx } from './transaction';

/** Appends `config.changed` to the org's audit chain inside the write's transaction (03-interfaces §5.3). */
export interface ConfigAuditLog {
  record(tx: CpTx, change: ConfigChanged): Promise<void>;
}

/**
 * Writes the change event in the write's transaction, for the dispatcher to
 * publish. `committed` is called once that transaction has committed: what it
 * enqueued is announced to this process's holders at once (read-your-writes)
 * and the dispatcher is woken; it never throws, and a `tx` that never commits
 * announces nothing.
 */
export interface ChangeOutbox {
  enqueue(tx: CpTx, event: ChangeEvent): Promise<void>;
  committed(tx: CpTx): Promise<void>;
}

/** Whether the org's chain already holds the entry for a head revision (ordered-steps repair). */
export interface ConfigAuditReader {
  recorded(orgId: string, headRevision: number): Promise<boolean>;
}
