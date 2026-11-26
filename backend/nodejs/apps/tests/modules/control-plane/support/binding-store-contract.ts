/**
 * BS: what every `BindingStore` does (03-interfaces §2.6); CPD-8 runs it on the
 * Mongo adapter.
 */
import { expect } from 'chai';
import type { Binding } from '../../../../src/modules/control-plane/domain/objects';
import { InvalidObject, ObjectExists, WrongOrg } from '../../../../src/modules/control-plane/domain/store-errors';
import type { BindingStore } from '../../../../src/modules/control-plane/ports/binding-store';
import { assertNoCanary, VECTOR_EXAMPLES } from '../../agent-harness/support/canary-scan';
import { aBinding, bindingId, directTx, instId, ORG_A, ORG_B } from './cp-objects';
import { expectConflict, expectRefused, type StoreWorld } from './store-world';

export function describeBindingStoreContract(name: string, make: () => Promise<StoreWorld<BindingStore>>): void {
  describe(`BindingStore contract (BS): ${name}`, () => {
    let store: BindingStore;

    beforeEach(async () => {
      store = (await make()).store;
    });

    it('BS-1: a created binding reads back by id and by (installation, owner) at revision 1', async () => {
      expect(await store.upsert(directTx(ORG_A), aBinding({ revision: 4 }), null)).to.equal(1);
      expect(await store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding({ revision: 1 }));
      expect(await store.find(ORG_A, instId(1), 'user:u3')).to.deep.equal(aBinding({ revision: 1 }));
      expect(await store.find(ORG_A, instId(1), 'user:u4')).to.equal(null);
    });

    it('BS-2: one binding that is not revoked per (org, installation, owner)', async () => {
      await store.upsert(directTx(ORG_A), aBinding(), null);
      await expectRefused(store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null), ObjectExists);
      expect(await store.get(ORG_A, bindingId(2))).to.equal(null);
      expect(await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(3), owner: 'agent:a7' }), null)).to.equal(1);
      for (const status of ['pending', 'expiring', 'expired', 'error'] as const) {
        await store.upsert(directTx(ORG_A), aBinding({ status }), (await store.get(ORG_A, bindingId(1)))!.revision);
        await expectRefused(store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null), ObjectExists);
      }
    });

    it('BS-14: a revoked binding does not block a new one for the same owner; it stays, and find reads the new one', async () => {
      await store.upsert(directTx(ORG_A), aBinding(), null);
      await store.upsert(directTx(ORG_A), aBinding({ status: 'revoked', secret: null }), 1);
      expect((await store.find(ORG_A, instId(1), 'user:u3'))?.status).to.equal('revoked');
      expect(await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null)).to.equal(1);
      expect(await store.find(ORG_A, instId(1), 'user:u3')).to.deep.include({ id: bindingId(2), status: 'active' });
      expect((await store.get(ORG_A, bindingId(1)))?.status).to.equal('revoked');
      // Revoked again and bound a third time: the latest revoked one is what find reads when none is live.
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2), status: 'revoked', secret: null }), 1);
      expect(await store.find(ORG_A, instId(1), 'user:u3')).to.deep.include({ id: bindingId(2), status: 'revoked' });
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(3) }), null);
      expect((await store.listForOwner(ORG_A, 'user:u3')).map((b) => [b.id, b.status])).to.deep.equal([
        [bindingId(1), 'revoked'],
        [bindingId(2), 'revoked'],
        [bindingId(3), 'active'],
      ]);
    });

    it('BS-15: a revoked binding cannot come back beside a newer one for the same owner', async () => {
      await store.upsert(directTx(ORG_A), aBinding(), null);
      await store.upsert(directTx(ORG_A), aBinding({ status: 'revoked', secret: null }), 1);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null);
      await expectRefused(store.upsert(directTx(ORG_A), aBinding({ status: 'active' }), 2), ObjectExists);
      expect((await store.get(ORG_A, bindingId(1)))).to.deep.include({ status: 'revoked', revision: 2 });
    });

    it('BS-3: an update is a compare-and-set on the revision', async () => {
      await store.upsert(directTx(ORG_A), aBinding(), null);
      expect(await store.upsert(directTx(ORG_A), aBinding({ status: 'expiring' }), 1)).to.equal(2);
      await expectConflict(store.upsert(directTx(ORG_A), aBinding({ status: 'revoked' }), 1), 2);
      expect((await store.get(ORG_A, bindingId(1)))?.status).to.equal('expiring');
    });

    it('BS-4: a binding never holds a secret: no field for one, and no token shape fits its code', async () => {
      for (const example of VECTOR_EXAMPLES) {
        const smuggled = { ...aBinding(), accessToken: example.secret } as Binding;
        await expectRefused(store.upsert(directTx(ORG_A), smuggled, null), InvalidObject);
        const asCode = aBinding({ status: 'error', lastError: { code: example.secret, at: 1 } });
        await expectRefused(store.upsert(directTx(ORG_A), asCode, null), InvalidObject);
      }
      expect(await store.get(ORG_A, bindingId(1))).to.equal(null);
      await store.upsert(directTx(ORG_A), aBinding({ status: 'error', lastError: { code: 'invalid_grant', at: 1 } }), null);
      assertNoCanary('stored binding', await store.get(ORG_A, bindingId(1)), { shapes: 'vendor' });
    });

    it('BS-5: dueForRefresh lists active and expiring bindings of every org, soonest first', async () => {
      const due = (n: number, orgId: string, status: Binding['status'], expiresAt: number | null) =>
        store.upsert(
          directTx(orgId),
          aBinding({ id: bindingId(n), orgId, owner: `user:u${n}`, status, expiresAt }),
          null,
        );
      await due(1, ORG_A, 'active', 300);
      await due(2, ORG_B, 'expiring', 100);
      await due(3, ORG_A, 'revoked', 50);
      await due(4, ORG_A, 'expired', 60);
      await due(5, ORG_B, 'active', 900);
      await due(6, ORG_A, 'active', null);
      await due(7, ORG_A, 'active', 200);
      expect((await store.dueForRefresh(500, 10)).map((b) => b.id)).to.deep.equal([bindingId(2), bindingId(7), bindingId(1)]);
      expect((await store.dueForRefresh(500, 2)).map((b) => b.id)).to.deep.equal([bindingId(2), bindingId(7)]);
    });

    it('BS-19 (QA23-F2): a refresh claim is one holder\'s until it lapses or the binding is written; it is not a revision', async () => {
      await store.upsert(directTx(ORG_A), aBinding({ expiresAt: 100 }), null);
      const a = { holder: 'replica-a', now: 1_000 };
      const b = { holder: 'replica-b', now: 1_000 };
      expect(await store.claimRefresh(ORG_A, bindingId(1), 2, { ...a, until: 2_000 })).to.equal(false, 'stale revision');
      expect(await store.claimRefresh(ORG_B, bindingId(1), 1, { ...a, until: 2_000 })).to.equal(false, 'another org');
      expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { ...a, until: 2_000 })).to.equal(true);
      expect(await store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding({ expiresAt: 100 }));
      expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { ...b, until: 2_000 })).to.equal(false);
      expect((await store.dueForRefresh(500, 10, b)).map((x) => x.id)).to.deep.equal([]);
      expect((await store.dueForRefresh(500, 10, a)).map((x) => x.id)).to.deep.equal([bindingId(1)]);
      expect((await store.dueForRefresh(500, 10)).map((x) => x.id)).to.deep.equal([bindingId(1)]);
      expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { ...a, until: 3_000 })).to.equal(true, 'the holder renews');
      expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { holder: 'replica-b', now: 3_000, until: 4_000 })).to.equal(true, 'lapsed');
      expect((await store.dueForRefresh(500, 10, { holder: 'replica-a', now: 3_500 })).map((x) => x.id)).to.deep.equal([]);
      await store.upsert(directTx(ORG_A), aBinding({ expiresAt: 100 }), 1);
      expect((await store.dueForRefresh(500, 10, { holder: 'replica-a', now: 3_500 })).map((x) => x.id)).to.deep.equal([bindingId(1)]);
      expect(await store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding({ expiresAt: 100, revision: 2 }));
    });

    it('BS-6: listForOwner is the owner\'s bindings in that org only; writes stay in the transaction\'s org', async () => {
      await store.upsert(directTx(ORG_A), aBinding(), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2), installation: instId(2) }), null);
      await store.upsert(directTx(ORG_B), aBinding({ id: bindingId(3), orgId: ORG_B }), null);
      expect((await store.listForOwner(ORG_A, 'user:u3')).map((b) => b.id)).to.deep.equal([bindingId(1), bindingId(2)]);
      expect(await store.get(ORG_B, bindingId(1))).to.equal(null);
      await expectRefused(store.upsert(directTx(ORG_B), aBinding({ id: bindingId(4) }), null), WrongOrg);
    });

    it('BS-6b (PERF-E): listForOwners is listForOwner of each owner in turn, in one read', async () => {
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(1), owner: 'user:u3' }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2), owner: 'org', installation: instId(2) }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(3), owner: 'user:u3', installation: instId(3) }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(4), owner: 'user:u4', installation: instId(4) }), null);
      await store.upsert(directTx(ORG_B), aBinding({ id: bindingId(5), orgId: ORG_B, owner: 'org' }), null);
      const owners = ['org', 'agent:a1', 'user:u3'] as const;
      const each = await Promise.all(owners.map((owner) => store.listForOwner(ORG_A, owner)));
      const all = await store.listForOwners(ORG_A, owners);
      expect(all).to.deep.equal(each.flat());
      expect(all.map((b) => b.id)).to.deep.equal([bindingId(2), bindingId(1), bindingId(3)]);
      expect(await store.listForOwners(ORG_A, [])).to.deep.equal([]);
    });

    it("BS-11: listByInstallation is that installation's bindings in that org only, revoked ones included, by owner", async () => {
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(1), owner: 'user:u4' }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2), owner: 'user:u3', status: 'revoked', secret: null }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(3), owner: 'org' }), null);
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(4), installation: instId(2) }), null);
      await store.upsert(directTx(ORG_B), aBinding({ id: bindingId(5), orgId: ORG_B }), null);
      const all = await store.listByInstallation(ORG_A, instId(1), { after: null, limit: 10 });
      expect(all.items.map((b) => [b.id, b.owner, b.status])).to.deep.equal([
        [bindingId(3), 'org', 'active'],
        [bindingId(2), 'user:u3', 'revoked'],
        [bindingId(1), 'user:u4', 'active'],
      ]);
      expect(all.next).to.equal(null);
      expect((await store.listByInstallation(ORG_B, instId(1), { after: null, limit: 10 })).items.map((b) => b.id)).to.deep.equal([bindingId(5)]);
      expect(await store.listByInstallation(ORG_A, instId(9), { after: null, limit: 10 })).to.deep.equal({ items: [], next: null });
    });

    it('BS-12: listByInstallation pages by its cursor: every binding once, the last page has no cursor', async () => {
      const owners = ['user:u1', 'user:u2', 'user:u3', 'user:u4', 'user:u5'] as const;
      for (const [index, owner] of owners.entries()) {
        await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(index + 1), owner }), null);
      }
      const seen: string[] = [];
      const cursors: Array<string | null> = [];
      let after: string | null = null;
      do {
        const page = await store.listByInstallation(ORG_A, instId(1), { after, limit: 2 });
        seen.push(...page.items.map((b) => b.owner));
        cursors.push(page.next);
        after = page.next;
      } while (after !== null);
      expect(seen).to.deep.equal([...owners]);
      expect(cursors.map((cursor) => cursor === null)).to.deep.equal([false, false, true]);
      expect((await store.listByInstallation(ORG_A, instId(1), { after: null, limit: 5 })).next).to.equal(null);
    });

    it("BS-16: listByInstallation pages an owner's superseded bindings too: each row once, oldest first within the owner", async () => {
      for (const id of [1, 2, 3]) {
        if (id > 1) await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(id - 1), status: 'revoked', secret: null }), 1);
        await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(id) }), null);
      }
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(4), owner: 'user:u4' }), null);
      const seen: string[] = [];
      let after: string | null = null;
      do {
        const page = await store.listByInstallation(ORG_A, instId(1), { after, limit: 1 });
        seen.push(...page.items.map((b) => `${b.id}:${b.status}`));
        after = page.next;
      } while (after !== null);
      expect(seen).to.deep.equal([`${bindingId(1)}:revoked`, `${bindingId(2)}:revoked`, `${bindingId(3)}:active`, `${bindingId(4)}:active`]);
    });
  });
}
