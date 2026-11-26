/**
 * What every `InboxStore` must do (G2, ADR-INBOX-01): a person's Inbox settings, the once-only claim of a run's
 * finished notice, and the watch leases every Node replica reads. `InMemoryInboxStore` (`inbox-store.test.ts`),
 * `MongoInboxStore` (`inbox-store.mongo.itest.ts`) and `PgInboxStore` (`inbox-store.pg.itest.ts`). `orgs()` names
 * two orgs in the shape the store takes; `users()` two people.
 */
import { expect } from 'chai';
import { DEFAULT_INBOX_PREFERENCES, MAX_MUTED_SCHEDULES } from '../../../../src/modules/agent-harness/domain/inbox';
import type { InboxStore } from '../../../../src/modules/agent-harness/ports/inbox';

export interface InboxStoreBackend {
  store: InboxStore;
  /** Another Node replica over the same database. */
  replica(): InboxStore;
  orgs: [string, string];
  users: [string, string];
}

const T0 = 1_700_000_000_000;

export function describeInboxStoreContract(name: string, create: () => Promise<InboxStoreBackend>): void {
  describe(`InboxStore contract: ${name}`, () => {
    let b: InboxStoreBackend;

    beforeEach(async () => {
      b = await create();
    });

    it('IBX-S1: a person with no record reads the defaults, and a write is read back by another replica', async () => {
      const [org] = b.orgs;
      const [ana] = b.users;
      expect(await b.store.preferences(org, ana)).to.deep.equal(DEFAULT_INBOX_PREFERENCES);
      await b.store.setPause(org, ana, { until: T0 + 3_600_000 }, T0);
      await b.store.setRunNotices(org, ana, { email: true }, T0);
      expect(await b.replica().preferences(org, ana)).to.deep.equal({
        mutedSchedules: [],
        pause: { until: T0 + 3_600_000 },
        runNotices: { email: true, slack: false },
      });
    });

    it('IBX-S2: muting is per person and per org, each schedule once, newest last; unmuting removes it', async () => {
      const [org, other] = b.orgs;
      const [ana, ben] = b.users;
      await b.store.setMuted(org, ana, 'sch-1', true, T0);
      await b.store.setMuted(org, ana, 'sch-2', true, T0 + 1);
      const again = await b.store.setMuted(org, ana, 'sch-1', true, T0 + 2);
      expect(again.mutedSchedules).to.deep.equal(['sch-2', 'sch-1']);
      expect((await b.store.preferences(org, ben)).mutedSchedules).to.deep.equal([]);
      expect((await b.store.preferences(other, ana)).mutedSchedules).to.deep.equal([]);
      const after = await b.store.setMuted(org, ana, 'sch-2', false, T0 + 3);
      expect(after.mutedSchedules).to.deep.equal(['sch-1']);
      expect(await b.store.setMuted(org, ana, 'never-muted', false, T0 + 4)).to.deep.include({ mutedSchedules: ['sch-1'] });
    });

    it('IBX-S3: at most MAX_MUTED_SCHEDULES stay muted; the oldest go first', async () => {
      const [org] = b.orgs;
      const [ana] = b.users;
      for (let i = 0; i <= MAX_MUTED_SCHEDULES; i += 1) await b.store.setMuted(org, ana, `s-${i}`, true, T0 + i);
      const muted = (await b.store.preferences(org, ana)).mutedSchedules;
      expect(muted).to.have.length(MAX_MUTED_SCHEDULES);
      expect(muted[0]).to.equal('s-1');
      expect(muted[muted.length - 1]).to.equal(`s-${MAX_MUTED_SCHEDULES}`);
    });

    it('IBX-S4: a pause until resumed and no pause are both kept; the other settings are untouched', async () => {
      const [org] = b.orgs;
      const [ana] = b.users;
      await b.store.setMuted(org, ana, 'sch-1', true, T0);
      await b.store.setPause(org, ana, { untilResumed: true }, T0);
      expect((await b.replica().preferences(org, ana)).pause).to.deep.equal({ untilResumed: true });
      await b.store.setPause(org, ana, null, T0 + 1);
      expect(await b.replica().preferences(org, ana)).to.deep.equal({
        ...DEFAULT_INBOX_PREFERENCES,
        mutedSchedules: ['sch-1'],
      });
    });

    it('IBX-S5: a run notice is claimed once across replicas, per org', async () => {
      const [org, other] = b.orgs;
      const [ana] = b.users;
      const results = await Promise.all([
        b.store.claimRunNotice(org, 'run-1', ana, T0),
        b.replica().claimRunNotice(org, 'run-1', ana, T0),
        b.replica().claimRunNotice(org, 'run-1', ana, T0 + 5),
      ]);
      expect(results.filter(Boolean)).to.have.length(1);
      expect(await b.store.claimRunNotice(other, 'run-1', ana, T0)).to.equal(true);
      expect(await b.store.claimRunNotice(org, 'run-2', ana, T0)).to.equal(true);
    });

    it('IBX-S6: a watch lease is seen by every replica while it lasts, for that person and conversation only', async () => {
      const [org, other] = b.orgs;
      const [ana, ben] = b.users;
      await b.store.watch({ orgId: org, userId: ana, sessionId: 'c-1', watcherId: 'w-1', until: T0 + 60_000 });
      const replica = b.replica();
      expect(await replica.watching(org, ana, 'c-1', T0)).to.equal(true);
      expect(await replica.watching(org, ana, 'c-1', T0 + 60_000)).to.equal(false);
      expect(await replica.watching(org, ben, 'c-1', T0)).to.equal(false);
      expect(await replica.watching(org, ana, 'c-2', T0)).to.equal(false);
      expect(await replica.watching(other, ana, 'c-1', T0)).to.equal(false);
    });

    it('IBX-S7: renewing a lease moves its end; unwatching ends it at once and leaves the others', async () => {
      const [org] = b.orgs;
      const [ana] = b.users;
      await b.store.watch({ orgId: org, userId: ana, sessionId: 'c-1', watcherId: 'w-1', until: T0 + 1_000 });
      await b.store.watch({ orgId: org, userId: ana, sessionId: 'c-1', watcherId: 'w-1', until: T0 + 90_000 });
      expect(await b.replica().watching(org, ana, 'c-1', T0 + 60_000)).to.equal(true);
      await b.store.watch({ orgId: org, userId: ana, sessionId: 'c-1', watcherId: 'w-2', until: T0 + 90_000 });
      await b.replica().unwatch(org, 'w-1');
      expect(await b.store.watching(org, ana, 'c-1', T0)).to.equal(true);
      await b.store.unwatch(org, 'w-2');
      expect(await b.store.watching(org, ana, 'c-1', T0)).to.equal(false);
      await b.store.unwatch(org, 'never-watched');
    });

    it("IBX-S8: erasing a person removes their settings, claims and leases and leaves everyone else's", async () => {
      const [org, other] = b.orgs;
      const [ana, ben] = b.users;
      for (const [o, u] of [[org, ana], [org, ben], [other, ana]] as const) {
        await b.store.setMuted(o, u, 'sch-1', true, T0);
        await b.store.watch({ orgId: o, userId: u, sessionId: 'c-1', watcherId: `w-${o}-${u}`, until: T0 + 60_000 });
      }
      await b.store.claimRunNotice(org, 'run-1', ana, T0);
      await b.store.eraseUser(org, ana);
      expect(await b.store.preferences(org, ana)).to.deep.equal(DEFAULT_INBOX_PREFERENCES);
      expect(await b.store.watching(org, ana, 'c-1', T0)).to.equal(false);
      expect(await b.store.claimRunNotice(org, 'run-1', ana, T0)).to.equal(true);
      expect((await b.store.preferences(org, ben)).mutedSchedules).to.deep.equal(['sch-1']);
      expect(await b.store.watching(other, ana, 'c-1', T0)).to.equal(true);
    });

    it("IBX-S9: erasing an org removes all of its Inbox records and leaves another org's", async () => {
      const [org, other] = b.orgs;
      const [ana] = b.users;
      for (const o of [org, other]) {
        await b.store.setMuted(o, ana, 'sch-1', true, T0);
        await b.store.claimRunNotice(o, 'run-1', ana, T0);
        await b.store.watch({ orgId: o, userId: ana, sessionId: 'c-1', watcherId: `w-${o}`, until: T0 + 60_000 });
      }
      await b.store.eraseOrg(org);
      expect(await b.store.preferences(org, ana)).to.deep.equal(DEFAULT_INBOX_PREFERENCES);
      expect(await b.store.claimRunNotice(org, 'run-1', ana, T0)).to.equal(true);
      expect(await b.store.watching(org, ana, 'c-1', T0)).to.equal(false);
      expect((await b.store.preferences(other, ana)).mutedSchedules).to.deep.equal(['sch-1']);
      expect(await b.store.claimRunNotice(other, 'run-1', ana, T0)).to.equal(false);
      expect(await b.store.watching(other, ana, 'c-1', T0)).to.equal(true);
    });
  });
}
