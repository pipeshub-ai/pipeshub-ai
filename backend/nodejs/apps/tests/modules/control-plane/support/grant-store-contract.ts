/**
 * GS: what every `GrantStore` does (03-interfaces §2.4); CPD-7 runs it on the
 * Mongo adapter (and the `GraphEdgeGrantReadThrough`).
 */
import { expect } from 'chai';
import { MAX_GRANT_ENTRIES } from '../../../../src/modules/control-plane/domain/objects';
import { ObjectTooLarge, WrongOrg } from '../../../../src/modules/control-plane/domain/store-errors';
import type { GrantStore } from '../../../../src/modules/control-plane/ports/grant-store';
import { aGrant, anEntry, directTx, instId, ORG_A, ORG_B } from './cp-objects';
import { committed, expectConflict, expectRefused, rolledBack, type StoreWorld } from './store-world';

export function describeGrantStoreContract(name: string, make: () => Promise<StoreWorld<GrantStore>>): void {
  describe(`GrantStore contract (GS): ${name}`, () => {
    let world: StoreWorld<GrantStore>;
    let store: GrantStore;

    beforeEach(async () => {
      world = await make();
      store = world.store;
    });

    it('GS-1: a grant put at revision 0 reads back at revision 1; an unknown subject is null', async () => {
      expect(await store.put(directTx(ORG_A), aGrant({ revision: 9 }), 0)).to.equal(1);
      expect(await store.get(ORG_A, 'agent:a7')).to.deep.equal(aGrant({ revision: 1 }));
      expect(await store.get(ORG_A, 'agent:a8')).to.equal(null);
    });

    it('GS-2: put is a compare-and-set on the revision', async () => {
      await store.put(directTx(ORG_A), aGrant(), 0);
      await expectConflict(store.put(directTx(ORG_A), aGrant({ entries: [] }), 0), 1);
      expect(await store.put(directTx(ORG_A), aGrant({ entries: [] }), 1)).to.equal(2);
      await expectConflict(store.put(directTx(ORG_A), aGrant(), 1), 2);
      expect((await store.get(ORG_A, 'agent:a7'))?.entries).to.deep.equal([]);
    });

    it('GS-3: getMany is one round trip for any number of subjects', async () => {
      await store.put(directTx(ORG_A), aGrant({ subject: 'org' }), 0);
      await store.put(directTx(ORG_A), aGrant({ subject: 'user:u3' }), 0);
      const before = world.roundTrips();
      const found = await store.getMany(ORG_A, ['org', 'user:u3', 'agent:missing', 'team:t9']);
      expect(world.roundTrips() - before).to.equal(1);
      expect([...found.keys()].sort()).to.deep.equal(['org', 'user:u3']);
      expect(found.get('user:u3')?.subject).to.equal('user:u3');
    });

    it('GS-4: usersOf follows committed puts and ignores rolled-back ones', async () => {
      await committed(world, (tx) => store.put(tx, aGrant({ entries: [anEntry(instId(1)), anEntry(instId(2))] }), 0));
      await committed(world, (tx) => store.put(tx, aGrant({ subject: 'org', entries: [anEntry(instId(1))] }), 0));
      expect(await store.usersOf(ORG_A, instId(1))).to.deep.equal(['agent:a7', 'org']);
      await committed(world, (tx) => store.put(tx, aGrant({ entries: [anEntry(instId(2))] }), 1));
      expect(await store.usersOf(ORG_A, instId(1))).to.deep.equal(['org']);
      expect(await store.usersOf(ORG_A, instId(2))).to.deep.equal(['agent:a7']);
      await rolledBack(world, (tx) => store.put(tx, aGrant({ subject: 'user:u3', entries: [anEntry(instId(2))] }), 0));
      expect(await store.usersOf(ORG_A, instId(2))).to.deep.equal(['agent:a7']);
    });

    it(`GS-5: more than ${MAX_GRANT_ENTRIES} entries is ObjectTooLarge and stores nothing`, async () => {
      const entries = Array.from({ length: MAX_GRANT_ENTRIES + 1 }, (_, n) => anEntry(instId(n + 1)));
      await expectRefused(store.put(directTx(ORG_A), aGrant({ entries }), 0), ObjectTooLarge);
      expect(await store.get(ORG_A, 'agent:a7')).to.equal(null);
      const full = Array.from({ length: MAX_GRANT_ENTRIES }, (_, n) => anEntry(instId(n + 1)));
      expect(await store.put(directTx(ORG_A), aGrant({ entries: full }), 0)).to.equal(1);
    });

    it('GS-6: org B never sees org A grants, and a put cannot name another org than its transaction', async () => {
      await store.put(directTx(ORG_A), aGrant(), 0);
      expect(await store.get(ORG_B, 'agent:a7')).to.equal(null);
      expect((await store.getMany(ORG_B, ['agent:a7'])).size).to.equal(0);
      expect(await store.usersOf(ORG_B, instId(1))).to.deep.equal([]);
      await expectRefused(store.put(directTx(ORG_B), aGrant(), 0), WrongOrg);
    });
  });
}
