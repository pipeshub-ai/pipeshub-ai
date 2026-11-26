import {
  CAPABILITY_MIGRATION_ID,
  type CapabilityLedger,
  type LedgerEntry,
  type UndoOutcome,
} from '../../../../modules/control-plane/ports/capability-ledger';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import { rowRegion } from '../../agent-harness/pg-documents/pg';
import { cpUnit } from '../pg-units';

/**
 * `harness.cp_capability_ledger`: what CPD-7's migration and sync wrote, so revert removes exactly that. Whether the
 * org is migrated is its row in `cp_migration_ledger`, the per-org ledger of the data migrations.
 */
export class PgCapabilityLedger implements CapabilityLedger {
  entries(orgId: string): Promise<LedgerEntry[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{
        object: LedgerEntry['object'];
        id: string;
        revision: string;
      }>(
        `/* pipeshub:harness.cp_capability_ledger_entries */ SELECT object, id, revision::text FROM harness.cp_capability_ledger
          WHERE org_id = $1 ORDER BY object, id`,
        [orgId],
      );
      return rows.map(({ object, id, revision }) => ({
        object,
        id,
        revision: Number(revision),
      }));
    });
  }

  async record(tx: StoreTx, entry: LedgerEntry): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_capability_ledger_record */ INSERT INTO harness.cp_capability_ledger
             (org_id, object, id, revision, region) VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (org_id, object, id) DO UPDATE SET revision = excluded.revision`,
          [tx.orgId, entry.object, entry.id, entry.revision, rowRegion()],
        );
      }),
    );
  }

  /** The object is removed only at the revision the ledger holds; one that moved since is `drifted` and kept. */
  undo(tx: StoreTx, entry: LedgerEntry): Promise<UndoOutcome> {
    return cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
      const current = await k.query<{ revision: string }>(
        entry.object === 'installation'
          ? `/* pipeshub:harness.cp_capability_undo_read_installation */ SELECT revision::text FROM harness.cp_installations
              WHERE org_id = $1 AND id = $2 FOR UPDATE`
          : `/* pipeshub:harness.cp_capability_undo_read_grant */ SELECT revision::text FROM harness.cp_grants
              WHERE org_id = $1 AND subject = $2 FOR UPDATE`,
        [tx.orgId, entry.id],
      );
      const found = current.rows[0];
      if (found !== undefined && Number(found.revision) !== entry.revision)
        return 'drifted';
      await tx.enlist(async () => {
        if (entry.object === 'installation') {
          await k.query(
            `/* pipeshub:harness.cp_capability_undo_installation */ DELETE FROM harness.cp_installations
              WHERE org_id = $1 AND id = $2 AND revision = $3`,
            [tx.orgId, entry.id, entry.revision],
          );
          await k.query(
            `/* pipeshub:harness.cp_capability_undo_installation_history */ DELETE FROM harness.cp_installation_history
              WHERE org_id = $1 AND id = $2`,
            [tx.orgId, entry.id],
          );
        } else {
          await k.query(
            `/* pipeshub:harness.cp_capability_undo_grant */ DELETE FROM harness.cp_grants
              WHERE org_id = $1 AND subject = $2 AND revision = $3`,
            [tx.orgId, entry.id, entry.revision],
          );
        }
        await k.query(
          `/* pipeshub:harness.cp_capability_ledger_remove */ DELETE FROM harness.cp_capability_ledger
            WHERE org_id = $1 AND object = $2 AND id = $3`,
          [tx.orgId, entry.object, entry.id],
        );
      });
      return found === undefined ? 'absent' : 'reverted';
    });
  }

  migrated(orgId: string): Promise<boolean> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query(
        `/* pipeshub:harness.cp_capability_migrated */ SELECT 1 FROM harness.cp_migration_ledger
          WHERE org_id = $1 AND migration_id = $2`,
        [orgId, CAPABILITY_MIGRATION_ID],
      );
      return rows.length > 0;
    });
  }

  async markMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_capability_mark_migrated */ INSERT INTO harness.cp_migration_ledger
             (org_id, migration_id, applied_at, entry, region) VALUES ($1, $2, now(), $3, $4)
           ON CONFLICT (org_id, migration_id) DO UPDATE SET applied_at = excluded.applied_at, entry = excluded.entry`,
          [
            tx.orgId,
            CAPABILITY_MIGRATION_ID,
            JSON.stringify({ migrated: true }),
            rowRegion(),
          ],
        );
      }),
    );
  }

  async unmarkMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_capability_unmark_migrated */ DELETE FROM harness.cp_migration_ledger
            WHERE org_id = $1 AND migration_id = $2`,
          [tx.orgId, CAPABILITY_MIGRATION_ID],
        );
      }),
    );
  }
}
