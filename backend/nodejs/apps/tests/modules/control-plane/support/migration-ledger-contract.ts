/**
 * ML: what every data-migration ledger does (CPD-4): entries per migration,
 * sorted by org; record replaces an org's entry; writes join the transaction.
 */
import { expect } from 'chai';
import type { ControlPlaneTransaction } from '../../../../src/modules/control-plane';
import type { MigrationLedger } from '../../../../src/modules/control-plane/ports/data-migration';
import { directTx, ORG_A, ORG_B, rejection, storeChange } from './cp-objects';

export interface MigrationLedgerWorld {
  readonly ledger: MigrationLedger;
  readonly transaction: ControlPlaneTransaction;
}

export function describeMigrationLedgerContract(name: string, make: () => Promise<MigrationLedgerWorld>): void {
  describe(`MigrationLedger contract (ML): ${name}`, () => {
    it('ML-1: entries are per migration, sorted by org; record replaces; remove deletes', async () => {
      const { ledger } = await make();
      await ledger.record(directTx(ORG_B), 'm1', { orgId: ORG_B, entry: { n: 1 } });
      await ledger.record(directTx(ORG_A), 'm1', { orgId: ORG_A, entry: { n: 2 } });
      await ledger.record(directTx(ORG_A), 'm2', { orgId: ORG_A, entry: { n: 3 } });
      await ledger.record(directTx(ORG_A), 'm1', { orgId: ORG_A, entry: { n: 4 } });
      expect(await ledger.entries('m1')).to.deep.equal([
        { orgId: ORG_A, entry: { n: 4 } },
        { orgId: ORG_B, entry: { n: 1 } },
      ]);
      await ledger.remove(directTx(ORG_A), 'm1', ORG_A);
      expect((await ledger.entries('m1')).map((e) => e.orgId)).to.deep.equal([ORG_B]);
      expect((await ledger.entries('m2')).map((e) => e.orgId)).to.deep.equal([ORG_A]);
    });

    it('ML-2: a record whose control-plane write rolls back leaves no entry', async () => {
      const { ledger, transaction } = await make();
      const boom = new Error('after the ledger write');
      expect(
        await rejection(
          transaction.run({ orgId: ORG_A, actor: 'system' }, async (tx) => {
            await ledger.record(tx, 'm1', { orgId: ORG_A, entry: {} });
            throw boom;
          }),
        ),
      ).to.equal(boom);
      expect(await ledger.entries('m1')).to.deep.equal([]);
      await transaction.run({ orgId: ORG_A, actor: 'system' }, async (tx) => {
        await ledger.record(tx, 'm1', { orgId: ORG_A, entry: {} });
        return { result: null, change: storeChange('policy-migration', 'm1') };
      });
      expect(await ledger.entries('m1')).to.have.length(1);
    });
  });
}
