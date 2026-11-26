/**
 * The `EngineSignInStore` contract (SIGNIN, ES-1..ES-6): run against the
 * in-memory store and against MongoDB (engine-signins.mongo.itest.ts).
 */
import { expect } from 'chai';
import type { EngineSignInStore } from '../../../../src/modules/agent-harness/ports/engine-signins';

export interface EngineSignInStoreWorld {
  store: EngineSignInStore;
  orgId: string;
  /** Two people of the org, a service account of the org, and a person of another org. */
  users: [string, string];
  serviceAccount: string;
  stranger: { orgId: string; userId: string };
}

export function describeEngineSignInStoreContract(name: string, world: () => Promise<EngineSignInStoreWorld>): void {
  describe(`EngineSignInStore contract: ${name}`, () => {
    it('ES-1: off by default; nobody outside the org is found; a service account says so', async () => {
      const w = await world();
      expect(await w.store.read(w.orgId, w.users[0])).to.deep.equal({ stored: { enabled: false }, serviceAccount: false });
      expect(await w.store.read(w.orgId, w.stranger.userId)).to.equal(null);
      expect((await w.store.read(w.orgId, w.serviceAccount))?.serviceAccount).to.equal(true);
    });

    it('ES-2: a write replaces the record: what the next state lacks (the email after a disconnect) is gone', async () => {
      const w = await world();
      await w.store.write(w.orgId, w.users[0], { enabled: true, email: 'ada@example.com', plan: 'team', signedInAt: 5 });
      expect((await w.store.read(w.orgId, w.users[0]))?.stored).to.deep.equal({
        enabled: true, email: 'ada@example.com', plan: 'team', signedInAt: 5,
      });
      await w.store.write(w.orgId, w.users[0], { enabled: false, disconnectedAt: 9 });
      expect((await w.store.read(w.orgId, w.users[0]))?.stored).to.deep.equal({ enabled: false, disconnectedAt: 9 });
      expect((await w.store.read(w.orgId, w.users[1]))?.stored).to.deep.equal({ enabled: false });
    });

    it('ES-6 (SIGNIN-5): who is signed in: switch on and signed in since the time asked, in this org only', async () => {
      const w = await world();
      await w.store.write(w.orgId, w.users[0], { enabled: true, email: 'a@x.example', plan: 'team', signedInAt: 100 });
      await w.store.write(w.orgId, w.users[1], { enabled: true, email: 'b@x.example', plan: 'team', signedInAt: 50 });
      await w.store.write(w.stranger.orgId, w.stranger.userId, { enabled: true, email: 'c@x.example', plan: 'team', signedInAt: 100 });
      expect(await w.store.signedInSince(w.orgId, 80)).to.deep.equal([w.users[0]]);
      expect(await w.store.signedInSince(w.orgId, 0)).to.deep.equal([...w.users].sort());
      await w.store.write(w.orgId, w.users[0], { enabled: false, disconnectedAt: 120 });
      expect(await w.store.signedInSince(w.orgId, 80)).to.deep.equal([]);
    });

    it('ES-3: the org switch is off until set, and per org', async () => {
      const w = await world();
      expect(await w.store.orgAllows(w.orgId)).to.equal(false);
      await w.store.setOrgAllows(w.orgId, true);
      expect([await w.store.orgAllows(w.orgId), await w.store.orgAllows(w.stranger.orgId)]).to.deep.equal([true, false]);
      await w.store.setOrgAllows(w.orgId, false);
      expect(await w.store.orgAllows(w.orgId)).to.equal(false);
    });

    it('ES-4: erasing a person leaves everyone else', async () => {
      const w = await world();
      for (const user of w.users) await w.store.write(w.orgId, user, { enabled: true, email: `${user}@x.example`, plan: 'max' });
      await w.store.eraseUser(w.orgId, w.users[0]);
      expect((await w.store.read(w.orgId, w.users[0]))?.stored).to.deep.equal({ enabled: false });
      expect((await w.store.read(w.orgId, w.users[1]))?.stored.enabled).to.equal(true);
    });

    it('ES-5: erasing an org removes its records and its switch, and nothing of another org', async () => {
      const w = await world();
      await w.store.write(w.orgId, w.users[0], { enabled: true, email: 'a@x.example', plan: 'team' });
      await w.store.write(w.stranger.orgId, w.stranger.userId, { enabled: true, email: 'b@x.example', plan: 'team' });
      await w.store.setOrgAllows(w.orgId, true);
      await w.store.setOrgAllows(w.stranger.orgId, true);
      await w.store.eraseOrg(w.orgId);
      expect((await w.store.read(w.orgId, w.users[0]))?.stored).to.deep.equal({ enabled: false });
      expect(await w.store.orgAllows(w.orgId)).to.equal(false);
      expect((await w.store.read(w.stranger.orgId, w.stranger.userId))?.stored.enabled).to.equal(true);
      expect(await w.store.orgAllows(w.stranger.orgId)).to.equal(true);
    });
  });
}
