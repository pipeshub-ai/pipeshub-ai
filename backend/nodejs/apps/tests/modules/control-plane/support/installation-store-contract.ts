/**
 * IS: what every `InstallationStore` does (03-interfaces §2.3), run on the
 * in-memory adapter (`skeleton-stores.test.ts`); CPD-7 runs it on the Mongo
 * adapter and adds the index case (02-lld §4.2).
 */
import { expect } from 'chai';
import { ObjectExists, WrongOrg } from '../../../../src/modules/control-plane/domain/store-errors';
import type { InstallationStore } from '../../../../src/modules/control-plane/ports/installation-store';
import { anInstallation, directTx, instId, ORG_A, ORG_B } from './cp-objects';
import { committed, expectConflict, expectRefused, rolledBack, type StoreWorld } from './store-world';

export function describeInstallationStoreContract(
  name: string,
  make: () => Promise<StoreWorld<InstallationStore>>,
): void {
  describe(`InstallationStore contract (IS): ${name}`, () => {
    let world: StoreWorld<InstallationStore>;
    let store: InstallationStore;

    beforeEach(async () => {
      world = await make();
      store = world.store;
    });

    it('IS-1: a created installation reads back at revision 1; an unknown id is null', async () => {
      await store.create(directTx(ORG_A), anInstallation({ revision: 7 }));
      expect(await store.get(ORG_A, instId(1))).to.deep.equal(anInstallation({ revision: 1 }));
      expect(await store.get(ORG_A, instId(2))).to.equal(null);
    });

    it('IS-2: creating an existing id is ObjectExists and keeps the stored one', async () => {
      await store.create(directTx(ORG_A), anInstallation());
      await expectRefused(store.create(directTx(ORG_A), anInstallation({ visibility: 'team' })), ObjectExists);
      expect((await store.get(ORG_A, instId(1)))?.visibility).to.equal('org');
    });

    it('IS-3: replace is a compare-and-set on the revision', async () => {
      await store.create(directTx(ORG_A), anInstallation());
      expect(await store.replace(directTx(ORG_A), anInstallation({ status: 'disabled' }), 1)).to.equal(2);
      await expectConflict(store.replace(directTx(ORG_A), anInstallation({ status: 'enabled' }), 1), 2);
      const stored = await store.get(ORG_A, instId(1));
      expect(stored).to.include({ status: 'disabled', revision: 2 });
    });

    it('IS-4: history has one row per revision, newest first, at most limit', async () => {
      await store.create(directTx(ORG_A), anInstallation());
      await store.replace(directTx(ORG_A), anInstallation({ status: 'disabled' }), 1);
      await store.replace(directTx(ORG_A), anInstallation({ status: 'enabled' }), 2);
      const history = await store.history(ORG_A, instId(1), 10);
      expect(history.map((row) => [row.revision, row.installation.status])).to.deep.equal([
        [3, 'enabled'],
        [2, 'disabled'],
        [1, 'enabled'],
      ]);
      expect((await store.history(ORG_A, instId(1), 2)).map((row) => row.revision)).to.deep.equal([3, 2]);
    });

    it('IS-5: org B never sees org A rows, and a write cannot name another org than its transaction', async () => {
      await store.create(directTx(ORG_A), anInstallation());
      await store.create(directTx(ORG_A), anInstallation({ id: instId(2), installedVia: instId(1) }));
      expect(await store.get(ORG_B, instId(1))).to.equal(null);
      expect(await store.listForOrg(ORG_B, {})).to.deep.equal([]);
      expect(await store.history(ORG_B, instId(1), 10)).to.deep.equal([]);
      expect(await store.children(ORG_B, instId(1))).to.deep.equal([]);
      await expectRefused(store.create(directTx(ORG_B), anInstallation({ id: instId(3) })), WrongOrg);
      await expectRefused(store.replace(directTx(ORG_B), anInstallation(), 1), WrongOrg);
      expect(await store.get(ORG_B, instId(3))).to.equal(null);
    });

    it('IS-6: listForOrg filters by status, kind and owner, ordered by id', async () => {
      await store.create(directTx(ORG_A), anInstallation({ id: instId(3), kind: 'toolset', item: 'toolset:pipeshub/jira@1' }));
      await store.create(directTx(ORG_A), anInstallation({ id: instId(1) }));
      await store.create(directTx(ORG_A), anInstallation({ id: instId(2), status: 'disabled', owner: 'team:t9' }));
      const ids = async (filter: Parameters<InstallationStore['listForOrg']>[1]) =>
        (await store.listForOrg(ORG_A, filter)).map((inst) => inst.id);
      expect(await ids({})).to.deep.equal([instId(1), instId(2), instId(3)]);
      expect(await ids({ status: ['enabled'] })).to.deep.equal([instId(1), instId(3)]);
      expect(await ids({ kind: 'toolset' })).to.deep.equal([instId(3)]);
      expect(await ids({ owner: 'team:t9' })).to.deep.equal([instId(2)]);
    });

    it('IS-7: children are only the installations installed via the parent', async () => {
      await store.create(directTx(ORG_A), anInstallation({ id: instId(1), kind: 'plugin', item: 'plugin:acme/kit@1' }));
      await store.create(directTx(ORG_A), anInstallation({ id: instId(2), installedVia: instId(1) }));
      await store.create(directTx(ORG_A), anInstallation({ id: instId(3) }));
      expect((await store.children(ORG_A, instId(1))).map((inst) => inst.id)).to.deep.equal([instId(2)]);
    });

    it('IS-8: a rolled-back write leaves nothing; a committed one lands with one audit entry', async () => {
      await rolledBack(world, (tx) => store.create(tx, anInstallation()));
      expect(await store.get(ORG_A, instId(1))).to.equal(null);
      expect(await store.history(ORG_A, instId(1), 10)).to.deep.equal([]);
      expect(await world.configEntries(ORG_A)).to.equal(0);
      await committed(world, (tx) => store.create(tx, anInstallation()));
      expect(await store.get(ORG_A, instId(1))).to.not.equal(null);
      expect(await world.configEntries(ORG_A)).to.equal(1);
    });
  });
}
