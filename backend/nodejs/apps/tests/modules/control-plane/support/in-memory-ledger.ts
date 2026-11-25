/* eslint-disable @typescript-eslint/require-await -- an in-memory adapter of an asynchronous port. */
import type { LedgerEntry, MigrationLedger } from '../../../../src/modules/control-plane/ports/data-migration';
import type { StoreTx } from '../../../../src/modules/control-plane/ports/store-tx';

/** The data migrations' ledger in memory; writes land when the transaction commits. */
export class InMemoryMigrationLedger implements MigrationLedger {
  private readonly rows = new Map<string, LedgerEntry>();

  async entries(migrationId: string): Promise<LedgerEntry[]> {
    return [...this.rows.entries()]
      .filter(([id]) => id.startsWith(`${migrationId}:`))
      .map(([, entry]) => structuredClone(entry))
      .sort((a, b) => (a.orgId < b.orgId ? -1 : a.orgId > b.orgId ? 1 : 0));
  }

  async record(tx: StoreTx, migrationId: string, entry: LedgerEntry): Promise<void> {
    await tx.enlist(async () => {
      this.rows.set(`${migrationId}:${entry.orgId}`, structuredClone(entry));
    });
  }

  async remove(tx: StoreTx, migrationId: string, orgId: string): Promise<void> {
    await tx.enlist(async () => {
      this.rows.delete(`${migrationId}:${orgId}`);
    });
  }
}
