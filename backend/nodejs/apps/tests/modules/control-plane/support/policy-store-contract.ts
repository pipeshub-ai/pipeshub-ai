/**
 * PS: what every `PolicyStore` does (03-interfaces §2.5); CPD-4 runs it on the
 * Mongo adapter and the `KvDocumentPolicyReadThrough`. PS-7..PS-12 are CPD-4's:
 * concurrency, the audited transaction, isolation and the scope listing;
 * PS-12 the one cross-org read (the retention sweep in `on`).
 */
import { expect } from 'chai';
import { ControlPlaneUnavailableError } from '../../../../src/modules/control-plane';
import type { PolicyLayer } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { PolicyLayers, PolicyStorePurge } from '../../../../src/modules/control-plane/ports/policy-store';
import { directTx, ORG_A, ORG_B, rejection } from './cp-objects';
import { committed, expectConflict, rolledBack, type StoreWorld } from './store-world';

export interface PolicyStoreWorld extends StoreWorld<PolicyLayers> {
  /** Leaves data in the scope's row that is not a policy layer (a hand edit, an old writer). */
  corrupt(orgId: string, scope: string): Promise<void>;
}

const layer = (maxUsd: number, kind: PolicyLayer['scope']['kind'] = 'org'): PolicyLayer => ({
  schemaVersion: 1,
  scope: { kind, id: 'x' },
  engineProfile: { limits: { maxUsd } },
});

export function describePolicyStoreContract(name: string, make: () => Promise<PolicyStoreWorld>): void {
  describe(`PolicyStore contract (PS): ${name}`, () => {
    let world: PolicyStoreWorld;
    let store: PolicyLayers;

    beforeEach(async () => {
      world = await make();
      store = world.store;
    });

    it('PS-1: a scope never written reads as no layer at revision 0', async () => {
      expect(await store.read(ORG_A, `org:${ORG_A}`)).to.deep.equal({ layer: null, revision: 0 });
    });

    it('PS-2: write is a compare-and-set on the revision', async () => {
      expect(await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 0)).to.equal(1);
      await expectConflict(store.write(directTx(ORG_A), `org:${ORG_A}`, layer(9), 0), 1);
      expect(await store.read(ORG_A, `org:${ORG_A}`)).to.deep.equal({ layer: layer(5), revision: 1 });
    });

    it('PS-3: writing null deletes the layer and records a history row', async () => {
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      expect(await store.write(directTx(ORG_A), 'agent:a7', null, 1)).to.equal(2);
      expect(await store.read(ORG_A, 'agent:a7')).to.deep.equal({ layer: null, revision: 2 });
      expect((await store.history(ORG_A, 'agent:a7', 10)).map((row) => [row.revision, row.layer])).to.deep.equal([
        [2, null],
        [1, layer(2, 'agent')],
      ]);
    });

    it('PS-4: a corrupt layer is CP_UNAVAILABLE for that scope only', async () => {
      await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 0);
      await store.write(directTx(ORG_A), 'team:t9', layer(3, 'team'), 0);
      await store.write(directTx(ORG_B), 'team:t9', layer(4, 'team'), 0);
      await world.corrupt(ORG_A, 'team:t9');
      const failed = await rejection(store.read(ORG_A, 'team:t9'));
      expect(failed).to.be.instanceOf(ControlPlaneUnavailableError);
      expect((failed as ControlPlaneUnavailableError).code).to.equal('CP_UNAVAILABLE');
      const many = await store.readMany(ORG_A, [`org:${ORG_A}`, 'team:t9']);
      expect(many.get('team:t9')).to.deep.equal({ status: 'unavailable', code: 'CP_UNAVAILABLE' });
      expect(many.get(`org:${ORG_A}`)).to.deep.equal({ status: 'ok', layer: layer(5), revision: 1 });
      expect(await store.read(ORG_B, 'team:t9')).to.deep.equal({ layer: layer(4, 'team'), revision: 1 });
    });

    it('PS-5: history is newest first and a rollback is a new revision that keeps every row', async () => {
      await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 0);
      await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(3), 1);
      expect(await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 2)).to.equal(3);
      const history = await store.history(ORG_A, `org:${ORG_A}`, 10);
      expect(history.map((row) => row.revision)).to.deep.equal([3, 2, 1]);
      expect(history[0]!.layer).to.deep.equal(history[2]!.layer);
      expect((await store.history(ORG_A, `org:${ORG_A}`, 1)).map((row) => row.revision)).to.deep.equal([3]);
    });

    it('PS-6: readMany is one round trip and scopes are per org', async () => {
      await store.write(directTx(ORG_A), 'team:t9', layer(3, 'team'), 0);
      await store.write(directTx(ORG_B), 'team:t9', layer(4, 'team'), 0);
      const before = world.roundTrips();
      const many = await store.readMany(ORG_A, ['team:t9', 'agent:a7', 'user:u3']);
      expect(world.roundTrips() - before).to.equal(1);
      expect(many.get('team:t9')).to.deep.equal({ status: 'ok', layer: layer(3, 'team'), revision: 1 });
      expect(many.get('agent:a7')).to.deep.equal({ status: 'ok', layer: null, revision: 0 });
      expect((await store.read(ORG_B, 'team:t9')).layer).to.deep.equal(layer(4, 'team'));
    });

    it('PS-7: two writes racing from one revision: exactly one commits, the other names its revision', async () => {
      await committed(world, (tx) => store.write(tx, `org:${ORG_A}`, layer(9), 0));
      const outcomes = await Promise.allSettled([
        committed(world, (tx) => store.write(tx, `org:${ORG_A}`, layer(5), 1)),
        committed(world, (tx) => store.write(tx, `org:${ORG_A}`, layer(3), 1)),
      ]);
      const failed = outcomes.filter((outcome) => outcome.status === 'rejected');
      expect(failed).to.have.length(1);
      await expectConflict(Promise.reject((failed[0] as PromiseRejectedResult).reason as Error), 2);
      const history = await store.history(ORG_A, `org:${ORG_A}`, 10);
      expect(history.map((row) => row.revision)).to.deep.equal([2, 1]);
      expect((await store.read(ORG_A, `org:${ORG_A}`)).layer).to.deep.equal(history[0]!.layer);
    });

    it('PS-8: a write joins the control-plane transaction: rolled back, nothing; committed, one config.changed', async () => {
      await rolledBack(world, (tx) => store.write(tx, `org:${ORG_A}`, layer(5), 0));
      expect(await store.read(ORG_A, `org:${ORG_A}`)).to.deep.equal({ layer: null, revision: 0 });
      expect(await store.history(ORG_A, `org:${ORG_A}`, 10)).to.deep.equal([]);
      expect(await world.configEntries(ORG_A)).to.equal(0);
      await committed(world, (tx) => store.write(tx, `org:${ORG_A}`, layer(5), 0));
      expect(await store.read(ORG_A, `org:${ORG_A}`)).to.deep.equal({ layer: layer(5), revision: 1 });
      expect(await world.configEntries(ORG_A)).to.equal(1);
    });

    it("PS-9: org B never reads org A's layer, history or scopes", async () => {
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      expect(await store.read(ORG_B, 'agent:a7')).to.deep.equal({ layer: null, revision: 0 });
      expect(await store.history(ORG_B, 'agent:a7', 10)).to.deep.equal([]);
      expect(await store.scopesOf(ORG_B, 'agent')).to.deep.equal([]);
      await store.write(directTx(ORG_B), 'agent:a7', layer(4, 'agent'), 0);
      expect((await store.read(ORG_A, 'agent:a7')).layer).to.deep.equal(layer(2, 'agent'));
    });

    it("PS-10: scopesOf lists the org's written scopes of one kind, sorted, without deleted ones", async () => {
      await store.write(directTx(ORG_A), 'agent:b2', layer(2, 'agent'), 0);
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      await store.write(directTx(ORG_A), 'agent:c3', layer(2, 'agent'), 0);
      await store.write(directTx(ORG_A), 'agent:c3', null, 1);
      await store.write(directTx(ORG_A), 'team:t9', layer(3, 'team'), 0);
      expect(await store.scopesOf(ORG_A, 'agent')).to.deep.equal(['agent:a7', 'agent:b2']);
      expect(await store.scopesOf(ORG_A, 'team')).to.deep.equal(['team:t9']);
    });

    it('PS-12: orgLayers yields every org layer across orgs, in org order, without deleted ones', async () => {
      const seen = async () => {
        const rows: Array<[string, PolicyLayer]> = [];
        for await (const row of store.orgLayers()) rows.push([row.orgId, row.layer]);
        return rows;
      };
      await store.write(directTx(ORG_B), `org:${ORG_B}`, layer(4), 0);
      await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 0);
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      expect(await seen()).to.deep.equal([
        [ORG_A, layer(5)],
        [ORG_B, layer(4)],
      ]);
      await store.write(directTx(ORG_B), `org:${ORG_B}`, null, 1);
      expect(await seen()).to.deep.equal([[ORG_A, layer(5)]]);
    });

    it('PS-13: writeMany writes several scopes of the org in one write and answers each new revision', async () => {
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      // A read-through has marked the org held with an empty org row by now: start from what is stored.
      const org = (await store.read(ORG_A, `org:${ORG_A}`)).revision;
      const revisions = await store.writeMany(directTx(ORG_A), [
        { scope: `org:${ORG_A}`, layer: layer(5), expectedRevision: org },
        { scope: 'agent:a7', layer: null, expectedRevision: 1 },
      ]);
      expect(Object.fromEntries(revisions)).to.deep.equal({ [`org:${ORG_A}`]: org + 1, 'agent:a7': 2 });
      expect((await store.read(ORG_A, 'agent:a7')).layer).to.equal(null);
      await expectConflict(
        store.writeMany(directTx(ORG_A), [{ scope: `org:${ORG_A}`, layer: layer(3), expectedRevision: org }]),
        org + 1,
      );
    });
  });
}

export interface PolicyStorePurgeWorld {
  readonly store: PolicyLayers & PolicyStorePurge;
}

/** PP: the migration's revert removes one org's layers and history, and nothing of another org. */
export function describePolicyStorePurgeContract(name: string, make: () => Promise<PolicyStorePurgeWorld>): void {
  describe(`PolicyStorePurge contract (PP): ${name}`, () => {
    it("PP-1: purge removes the org's layers and history; another org keeps its own", async () => {
      const { store } = await make();
      await store.write(directTx(ORG_A), `org:${ORG_A}`, layer(5), 0);
      await store.write(directTx(ORG_A), 'agent:a7', layer(2, 'agent'), 0);
      await store.write(directTx(ORG_B), `org:${ORG_B}`, layer(4), 0);
      await store.purge(directTx(ORG_A), ORG_A);
      expect(await store.read(ORG_A, `org:${ORG_A}`)).to.deep.equal({ layer: null, revision: 0 });
      expect(await store.history(ORG_A, 'agent:a7', 10)).to.deep.equal([]);
      expect(await store.read(ORG_B, `org:${ORG_B}`)).to.deep.equal({ layer: layer(4), revision: 1 });
    });
  });
}
