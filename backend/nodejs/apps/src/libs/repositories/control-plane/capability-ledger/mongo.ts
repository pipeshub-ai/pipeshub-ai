import type { ClientSession } from 'mongoose';
import {
  CAPABILITY_MIGRATION_ID,
  type CapabilityLedger,
  type LedgerEntry,
  type UndoOutcome,
} from '../../../../modules/control-plane/ports/capability-ledger';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import {
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
  CpCapabilityLedgerModel,
  type LedgerDocument,
} from '../installations/schema/installations.schema';
import { MigrationLedgerModel } from '../policy/schema/policy.schema';

const sessionOf = (tx: StoreTx): ClientSession | null =>
  (tx.session ?? null) as ClientSession | null;
const ledgerId = (orgId: string, entry: LedgerEntry): string =>
  `${orgId}|${entry.object}|${entry.id}`;
const installationId = (orgId: string, id: string): string => `${orgId}|${id}`;
const grantId = (orgId: string, subject: string): string =>
  `${orgId}:${subject}`;
/** The org's row in `cpMigrationLedger`, keyed as every data migration's (`{id}:{orgId}`). */
const markerId = (orgId: string): string =>
  `${CAPABILITY_MIGRATION_ID}:${orgId}`;

/**
 * `cpCapabilityLedger`: what CPD-7's migration and sync wrote, so revert
 * removes exactly that. Whether the org is migrated is its one row in
 * `cpMigrationLedger`, the per-org ledger of the data migrations.
 */
export class MongoCapabilityLedger implements CapabilityLedger {
  async entries(orgId: string): Promise<LedgerEntry[]> {
    const docs = await CpCapabilityLedgerModel.find({ orgId })
      .sort({ _id: 1 })
      .lean<LedgerDocument[]>()
      .exec();
    return docs.map(({ object, id, revision }) => ({ object, id, revision }));
  }

  async record(tx: StoreTx, entry: LedgerEntry): Promise<void> {
    await tx.enlist(async () => {
      await CpCapabilityLedgerModel.updateOne(
        { _id: ledgerId(tx.orgId, entry) },
        { $set: { orgId: tx.orgId, ...entry } },
        { upsert: true, session: sessionOf(tx) ?? undefined },
      ).exec();
    });
  }

  async undo(tx: StoreTx, entry: LedgerEntry): Promise<UndoOutcome> {
    const session = sessionOf(tx);
    const model =
      entry.object === 'installation' ? CpInstallationModel : CpGrantModel;
    const _id =
      entry.object === 'installation'
        ? installationId(tx.orgId, entry.id)
        : grantId(tx.orgId, entry.id);
    const current = await (model as typeof CpGrantModel)
      .findById(_id, { revision: 1 })
      .session(session)
      .lean<{ revision: number }>()
      .exec();
    if (current !== null && current.revision !== entry.revision)
      return 'drifted';
    await tx.enlist(async () => {
      const options = { session: session ?? undefined };
      await (model as typeof CpGrantModel)
        .deleteOne({ _id, revision: entry.revision }, options)
        .exec();
      if (entry.object === 'installation') {
        await CpInstallationHistoryModel.deleteMany(
          { orgId: tx.orgId, id: entry.id },
          options,
        ).exec();
      }
      await CpCapabilityLedgerModel.deleteOne(
        { _id: ledgerId(tx.orgId, entry) },
        options,
      ).exec();
    });
    return current === null ? 'absent' : 'reverted';
  }

  async migrated(orgId: string): Promise<boolean> {
    const row = await MigrationLedgerModel.findById(markerId(orgId), { _id: 1 })
      .lean<{ _id: string }>()
      .exec();
    return row !== null;
  }

  async markMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(async () => {
      await MigrationLedgerModel.updateOne(
        { _id: markerId(tx.orgId) },
        {
          $set: {
            migrationId: CAPABILITY_MIGRATION_ID,
            orgId: tx.orgId,
            appliedAt: new Date(),
            entry: JSON.stringify({ migrated: true }),
          },
        },
        { upsert: true, session: sessionOf(tx) ?? undefined },
      ).exec();
    });
  }

  async unmarkMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(async () => {
      await MigrationLedgerModel.deleteOne(
        { _id: markerId(tx.orgId) },
        { session: sessionOf(tx) ?? undefined },
      ).exec();
    });
  }
}
