import type {
  LedgerEntry,
  MigrationLedger,
} from '../../../../modules/control-plane/ports/data-migration';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import { activeUnit } from '../../../db';
import {
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../../agent-harness/pg-documents/pg';
import { cpUnit } from '../pg-units';

/** The data migrations' ledger in `harness.cp_migration_ledger`, written in the migration's unit. */
export class PgMigrationLedger implements MigrationLedger {
  /**
   * Every org the migration applied, as the jobs role (a revert reads them all). Inside an org's write the read joins
   * that unit and so sees that org's entry only: what a migration reads there about the org it is writing.
   */
  entries(migrationId: string): Promise<LedgerEntry[]> {
    const read = async (k: PgKernelTx): Promise<LedgerEntry[]> => {
      const { rows } = await k.query<{ org_id: string; entry: string }>(
        `/* pipeshub:harness.cp_migration_ledger_entries */ SELECT org_id, entry FROM harness.cp_migration_ledger
          WHERE migration_id = $1 ORDER BY org_id`,
        [migrationId],
      );
      return rows.map((row) => ({
        orgId: row.org_id,
        entry: JSON.parse(row.entry) as unknown,
      }));
    };
    const open = activeUnit();
    return open
      ? read(open)
      : jobsUnit('control-plane-migration-ledger', { write: false }, read);
  }

  async record(
    tx: StoreTx,
    migrationId: string,
    entry: LedgerEntry,
  ): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, entry.orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_migration_ledger_record */ INSERT INTO harness.cp_migration_ledger
             (org_id, migration_id, applied_at, entry, region) VALUES ($1, $2, now(), $3, $4)
           ON CONFLICT (org_id, migration_id) DO UPDATE SET applied_at = excluded.applied_at, entry = excluded.entry`,
          [entry.orgId, migrationId, JSON.stringify(entry.entry), rowRegion()],
        );
      }),
    );
  }

  async remove(tx: StoreTx, migrationId: string, orgId: string): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_migration_ledger_remove */ DELETE FROM harness.cp_migration_ledger
            WHERE org_id = $1 AND migration_id = $2`,
          [orgId, migrationId],
        );
      }),
    );
  }
}
