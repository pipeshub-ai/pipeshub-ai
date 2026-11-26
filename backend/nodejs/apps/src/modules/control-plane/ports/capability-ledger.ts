import type { LegacyCapabilities } from '../contracts/generated';
import type { StoreTx } from './store-tx';

/** One control-plane object the capability migration or its sync wrote, at the revision it left. */
export interface LedgerEntry {
  readonly object: 'installation' | 'grant';
  /** The installation id, or the grant's subject. */
  readonly id: string;
  readonly revision: number;
}

export type UndoOutcome = 'reverted' | 'drifted' | 'absent';

export const CAPABILITY_MIGRATION_ID = 'control-plane-capabilities-v1';

/**
 * The ledger of CPD-7's capability migration (D2: revert undoes exactly what
 * apply wrote). Entries are written in the same transaction as the object, so
 * neither lands without the other. `undo` removes the object (and its history)
 * only when it is still at the entry's revision; otherwise it is `drifted`
 * and kept, with its entry.
 */
export interface CapabilityLedger {
  entries(orgId: string): Promise<LedgerEntry[]>;
  record(tx: StoreTx, entry: LedgerEntry): Promise<void>;
  undo(tx: StoreTx, entry: LedgerEntry): Promise<UndoOutcome>;
  /**
   * Whether a whole apply recorded the org as migrated (CPD-10 R3): the one
   * fact that makes its effective set the authority for a run's items. An
   * org that was never migrated and one that grants nothing both hold no
   * objects, so nothing else may answer this.
   */
  migrated(orgId: string): Promise<boolean>;
  /** Records the transaction's org as migrated; a second record changes nothing. */
  markMigrated(tx: StoreTx): Promise<void>;
  unmarkMigrated(tx: StoreTx): Promise<void>;
}

export interface LegacyPageOptions {
  /** The cursor a previous page answered as `nextAgent`; null for the first page (which alone carries the instances). */
  readonly after: string | null;
  /** One agent only (its sync); omitted, every agent of the org, a page at a time. */
  readonly agentKey?: string;
  /** False: instances only, no agents. */
  readonly agents?: boolean;
}

/** Where the legacy facts come from (the Python legacy driver, an org's own only). */
export interface LegacyCapabilitySource {
  page(orgId: string, options: LegacyPageOptions): Promise<LegacyCapabilities>;
}
