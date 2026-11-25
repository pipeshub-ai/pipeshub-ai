import type { StoreTx } from './store-tx';

export type MigrationMode = 'dry-run' | 'apply' | 'revert';

/**
 * A registered D2 data migration (05-migration-plan §1.5): it reports before
 * it writes (`dryRun`), applies idempotently, reverts what its ledger says it
 * applied, and keeps that ledger under its own key.
 */
export interface ControlPlaneDataMigration<R> {
  readonly id: string;
  /** Where its ledger lives (`cpMigrationLedger`, entries `{id}:{orgId}`). */
  readonly ledgerKey: string;
  dryRun(): Promise<R>;
  apply(): Promise<R>;
  revert(): Promise<R>;
  run(mode: MigrationMode): Promise<R>;
}

/** One org a migration applied, and what it recorded for its revert. */
export interface LedgerEntry {
  readonly orgId: string;
  readonly entry: unknown;
}

/** A migration's per-org record; writes join the control-plane transaction. */
export interface MigrationLedger {
  entries(migrationId: string): Promise<LedgerEntry[]>;
  record(tx: StoreTx, migrationId: string, entry: LedgerEntry): Promise<void>;
  remove(tx: StoreTx, migrationId: string, orgId: string): Promise<void>;
}
