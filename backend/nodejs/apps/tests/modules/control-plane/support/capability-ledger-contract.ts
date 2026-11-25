/**
 * LG: what every `CapabilityLedger` does (CPD-7), run on the in-memory ledger
 * (`capability-migration.test.ts`) and the Mongo one (MG-9's itest).
 */
import { expect } from 'chai';
import { aGrant, anInstallation, instId, ORG_A, ORG_B } from './cp-objects';
import type { CapabilityWorld } from './capability-world';
import { committed, rolledBack, type StoreWorld } from './store-world';

export function describeCapabilityLedgerContract(name: string, make: () => Promise<CapabilityWorld>): void {
  describe(`CapabilityLedger contract (LG): ${name}`, () => {
    let world: CapabilityWorld;
    let tx: StoreWorld<null>;

    beforeEach(async () => {
      world = await make();
      tx = { store: null, roundTrips: () => 0, transaction: world.transaction, configEntries: world.configEntries };
    });

    const install = () =>
      committed(tx, async (t) => {
        await world.installations.create(t, anInstallation());
        await world.ledger.record(t, { object: 'installation', id: instId(1), revision: 1 });
      });

    it('LG-1: an entry lands only with its transaction', async () => {
      await rolledBack(tx, (t) => world.ledger.record(t, { object: 'grant', id: 'agent:a7', revision: 1 }));
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([]);
      await install();
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([{ object: 'installation', id: instId(1), revision: 1 }]);
    });

    it('LG-2: undo at the recorded revision removes the object, its history and the entry', async () => {
      await install();
      await committed(tx, async (t) => {
        await world.grants.put(t, aGrant(), 0);
        await world.ledger.record(t, { object: 'grant', id: 'agent:a7', revision: 1 });
      });
      for (const entry of await world.ledger.entries(ORG_A)) {
        await committed(tx, async (t) => expect(await world.ledger.undo(t, entry)).to.equal('reverted'));
      }
      expect(await world.installations.get(ORG_A, instId(1))).to.equal(null);
      expect(await world.installations.history(ORG_A, instId(1), 10)).to.deep.equal([]);
      expect(await world.grants.get(ORG_A, 'agent:a7')).to.equal(null);
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([]);
    });

    it('LG-3: an object past the recorded revision is drifted: object and entry stay', async () => {
      await install();
      await committed(tx, (t) => world.installations.replace(t, anInstallation({ status: 'disabled' }), 1));
      const [entry] = await world.ledger.entries(ORG_A);
      await committed(tx, async (t) => expect(await world.ledger.undo(t, entry!)).to.equal('drifted'));
      expect((await world.installations.get(ORG_A, instId(1)))?.status).to.equal('disabled');
      expect(await world.ledger.entries(ORG_A)).to.have.length(1);
    });

    it("LG-4: org B neither lists nor undoes org A's entries", async () => {
      await install();
      expect(await world.ledger.entries(ORG_B)).to.deep.equal([]);
      await world.transaction.run({ orgId: ORG_B, actor: 'system' }, async (t) => ({
        result: await world.ledger.undo(t, { object: 'installation', id: instId(1), revision: 1 }),
        change: { scope: `org:${ORG_B}`, object: { type: 'installation', id: instId(1) }, revisionFrom: 1, revisionTo: null, changeDigest: `sha256:${'0'.repeat(64)}`, changed: [] },
      }));
      expect(await world.installations.get(ORG_A, instId(1))).to.not.equal(null);
      expect(await world.ledger.entries(ORG_A)).to.have.length(1);
    });

    it('LG-5 (CPD10-56): the migrated row lands only with its transaction, once, for its org alone', async () => {
      expect(await world.ledger.migrated(ORG_A)).to.equal(false);
      await rolledBack(tx, (t) => world.ledger.markMigrated(t));
      expect(await world.ledger.migrated(ORG_A)).to.equal(false);
      await committed(tx, (t) => world.ledger.markMigrated(t));
      await committed(tx, (t) => world.ledger.markMigrated(t));
      expect(await world.ledger.migrated(ORG_A)).to.equal(true);
      expect(await world.ledger.migrated(ORG_B)).to.equal(false);
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([]);
    });

    it('LG-6 (CPD10-56): unmarking removes the row with its transaction; unmarking an unmarked org changes nothing', async () => {
      await committed(tx, (t) => world.ledger.unmarkMigrated(t));
      expect(await world.ledger.migrated(ORG_A)).to.equal(false);
      await committed(tx, (t) => world.ledger.markMigrated(t));
      await rolledBack(tx, (t) => world.ledger.unmarkMigrated(t));
      expect(await world.ledger.migrated(ORG_A)).to.equal(true);
      await committed(tx, (t) => world.ledger.unmarkMigrated(t));
      expect(await world.ledger.migrated(ORG_A)).to.equal(false);
    });
  });
}
