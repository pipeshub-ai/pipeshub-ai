import type { ClientSession } from 'mongoose';
import type {
  LedgerEntry,
  MigrationLedger,
} from '../../../../modules/control-plane/ports/data-migration';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import {
  MigrationLedgerDocument,
  MigrationLedgerModel,
} from '../policy/schema/policy.schema';

const sessionOf = (tx: StoreTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;

/** The data migrations' ledger in `cpMigrationLedger`, written in the migration's transaction. */
export class MongoMigrationLedger implements MigrationLedger {
  async entries(migrationId: string): Promise<LedgerEntry[]> {
    const docs = await MigrationLedgerModel.find({ migrationId })
      .sort({ orgId: 1 })
      .lean<MigrationLedgerDocument[]>()
      .exec();
    return docs.map((doc) => ({
      orgId: doc.orgId,
      entry: JSON.parse(doc.entry) as unknown,
    }));
  }

  async record(
    tx: StoreTx,
    migrationId: string,
    entry: LedgerEntry,
  ): Promise<void> {
    await tx.enlist(async () => {
      await MigrationLedgerModel.replaceOne(
        { _id: `${migrationId}:${entry.orgId}` },
        {
          migrationId,
          orgId: entry.orgId,
          appliedAt: new Date(),
          entry: JSON.stringify(entry.entry),
        },
        { upsert: true, session: sessionOf(tx) },
      ).exec();
    });
  }

  async remove(tx: StoreTx, migrationId: string, orgId: string): Promise<void> {
    await tx.enlist(async () => {
      await MigrationLedgerModel.deleteOne(
        { _id: `${migrationId}:${orgId}` },
        { session: sessionOf(tx) },
      ).exec();
    });
  }
}
