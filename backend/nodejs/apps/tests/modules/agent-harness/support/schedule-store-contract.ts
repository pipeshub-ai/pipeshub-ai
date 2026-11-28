/**
 * P8a ST: what every ScheduleStore does, run against the in-memory store
 * (`schedule-store.test.ts`) and Mongo on a replica set
 * (`schedule-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { HarnessScheduleRefusedError } from '../../../../src/modules/agent-harness/domain/errors';
import { WATCH_GRACE_MS } from '../../../../src/modules/agent-harness/domain/schedules';
import type { ScheduleEraser, ScheduleStore } from '../../../../src/modules/agent-harness/ports/schedules';
import { ScheduleFirer } from '../../../../src/modules/agent-harness/application/schedule-firer';
import {
  aFiring,
  aSchedule,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeSetting,
  ORG,
  OTHER_ORG,
  T0,
  TestClock,
} from './schedules';

export interface ScheduleStoreFixture {
  store: ScheduleStore & ScheduleEraser;
  clock: TestClock;
}

const LIMITS = { perActor: 50, perOrg: 500 };

export function describeScheduleStoreContract(
  name: string,
  make: () => Promise<ScheduleStoreFixture>,
): void {
  describe(`ScheduleStore contract (ST): ${name}`, () => {
    let f: ScheduleStoreFixture;
    beforeEach(async () => {
      f = await make();
    });

    it('ST-1: create, get, list by actor and by org; another org sees nothing', async () => {
      const a = await f.store.create(aSchedule({ createdAt: T0 }), LIMITS);
      const b = await f.store.create(aSchedule({ actorId: BOB, createdAt: T0 + 1 }), LIMITS);
      await f.store.create(aSchedule({ orgId: OTHER_ORG, createdAt: T0 + 2 }), LIMITS);
      expect(await f.store.get(ORG, a.id)).to.deep.equal(a);
      expect(await f.store.get(OTHER_ORG, a.id)).to.equal(null);
      expect(await f.store.get(null, a.id)).to.deep.equal(a);
      expect((await f.store.list(ORG, { actorId: ALICE }, 10)).map((s) => s.id)).to.deep.equal([a.id]);
      expect((await f.store.list(ORG, {}, 10)).map((s) => s.id)).to.deep.equal([b.id, a.id]);
    });

    it('ST-1: the per-actor and per-org limits', async () => {
      await f.store.create(aSchedule(), { perActor: 1, perOrg: 2 });
      const refused = await f.store
        .create(aSchedule(), { perActor: 1, perOrg: 2 })
        .catch((e: unknown) => e);
      expect(refused).to.be.instanceOf(HarnessScheduleRefusedError);
      expect((refused as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_LIMIT');
      await f.store.create(aSchedule({ actorId: BOB }), { perActor: 1, perOrg: 2 });
      const org = await f.store
        .create(aSchedule({ actorId: 'carol' }), { perActor: 1, perOrg: 2 })
        .catch((e: unknown) => e);
      expect((org as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_LIMIT');
    });

    it('SK-3 (H2): concurrent creates never pass the per-actor or per-org limit', async () => {
      const limits = { perActor: 3, perOrg: 5 };
      const actor = await Promise.allSettled(
        Array.from({ length: 6 }, () => f.store.create(aSchedule(), limits)),
      );
      expect(actor.filter((r) => r.status === 'fulfilled')).to.have.length(3);
      expect(await f.store.list(ORG, { actorId: ALICE }, 50)).to.have.length(3);
      const org = await Promise.allSettled(
        ['bob', 'carol', 'dave', 'erin'].map((actorId) => f.store.create(aSchedule({ actorId }), limits)),
      );
      expect(org.filter((r) => r.status === 'fulfilled')).to.have.length(2);
      expect(await f.store.list(ORG, {}, 50)).to.have.length(5);
      for (const refused of [...actor, ...org].filter((r) => r.status === 'rejected')) {
        expect(((refused as PromiseRejectedResult).reason as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_LIMIT');
      }
    });

    it('SK-4 (H2): a delete makes room again', async () => {
      const limits = { perActor: 1, perOrg: 2 };
      const first = await f.store.create(aSchedule(), limits);
      expect(await f.store.delete(ORG, first.id)).to.equal(true);
      await f.store.create(aSchedule(), limits);
      expect(await f.store.list(ORG, { actorId: ALICE }, 10)).to.have.length(1);
    });

    it('ST-2: a cron slot is claimed once; the cursor moves with it', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const slot = schedule.nextFireAt as number;
      expect((await f.store.dueCron(slot - 1, 10)).map((s) => s.id)).to.deep.equal([]);
      expect((await f.store.dueCron(slot, 10)).map((s) => s.id)).to.deep.equal([schedule.id]);
      const firing = aFiring(schedule, { kind: 'cron', slotKey: 'c:1', slotAt: slot });
      const claimed = await f.store.claimCronSlot(firing, slot, slot + 7 * 86_400_000);
      expect(claimed).to.deep.equal(firing);
      expect((await f.store.get(ORG, schedule.id))?.nextFireAt).to.equal(slot + 7 * 86_400_000);
      expect(await f.store.dueCron(slot, 10)).to.deep.equal([]);
      // A second scheduler that read the old cursor writes nothing.
      const second = aFiring(schedule, { kind: 'cron', slotKey: 'c:1', slotAt: slot });
      expect(await f.store.claimCronSlot(second, slot, slot + 7 * 86_400_000)).to.equal(null);
      const third = aFiring(schedule, { kind: 'cron', slotKey: 'c:2', slotAt: slot });
      expect(await f.store.claimCronSlot(third, slot, slot + 1)).to.equal(null);
      expect(await f.store.firings(ORG, schedule.id, 10)).to.have.length(1);
    });

    it('SCH-03: a slot its owner skipped after the firer read it is not claimed to run; it is claimed as skipped', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const slot = schedule.nextFireAt as number;
      const week = 7 * 86_400_000;
      // The firer read the schedule with no marker; the owner's skip commits before its claim.
      expect(await f.store.update(ORG, schedule.id, 0, { skipNextAt: slot }, T0 + 1)).to.not.be.oneOf(['changed', null]);
      const run = aFiring(schedule, { kind: 'cron', slotKey: 'c:skip', slotAt: slot });
      expect(await f.store.claimCronSlot(run, slot, slot + week)).to.equal(null);
      expect((await f.store.get(ORG, schedule.id))?.nextFireAt).to.equal(slot);
      expect(await f.store.firings(ORG, schedule.id, 10)).to.deep.equal([]);
      // The next read sees the marker and records the slot skipped, in the same claim.
      const skipped = aFiring(schedule, {
        kind: 'cron', slotKey: 'c:skip', slotAt: slot, status: 'skipped', reason: 'skipped_by_owner', attempts: 0, settledAt: T0,
      });
      expect(await f.store.claimCronSlot(skipped, slot, slot + week)).to.deep.equal(skipped);
      expect((await f.store.get(ORG, schedule.id))?.nextFireAt).to.equal(slot + week);
    });

    it('SCH-02: a firing\'s runner is fixed by its first start; only a harness that refused hands it to classic', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing } = await f.store.claimSlot(aFiring(schedule, { slotKey: 'm:runner' }));
      expect(await f.store.fixRunner(ORG, firing.id, 'classic')).to.equal('classic');
      expect(await f.store.fixRunner(ORG, firing.id, 'harness')).to.equal('classic');
      const { firing: other } = await f.store.claimSlot(aFiring(schedule, { slotKey: 'm:runner-2' }));
      expect(await f.store.fixRunner(ORG, other.id, 'harness')).to.equal('harness');
      expect(await f.store.fixRunner(ORG, other.id, 'classic', 'harness')).to.equal('classic');
      expect(await f.store.fixRunner(ORG, other.id, 'harness', 'classic')).to.equal('harness');
      expect(await f.store.fixRunner(OTHER_ORG, other.id, 'harness')).to.equal(null);
    });

    it('SCH-03: a claim checks the revision the firer read: a person\'s change since makes it miss', async () => {
      // A one-off made a cron after the firer read it: its claim would have disabled it as ran_once.
      const at = T0 + 30 * 60_000;
      const once = await f.store.create(
        aSchedule({ trigger: { kind: 'once', at: new Date(at).toISOString(), timeZone: 'UTC' }, nextFireAt: at }),
        LIMITS,
      );
      const read = once.revision ?? 0;
      const cron = { kind: 'cron' as const, cron: '0 9 * * 1', timeZone: 'UTC', catchUp: 'skip' as const };
      expect(await f.store.update(ORG, once.id, read, { trigger: cron, nextFireAt: at }, T0 + 1)).to.not.be.oneOf(['changed', null]);
      const stale = aFiring(once, { kind: 'once', slotKey: `o:${new Date(at).toISOString()}`, slotAt: at });
      expect(await f.store.claimCronSlot(stale, at, null, 'ran_once', read)).to.equal(null);
      expect(await f.store.get(ORG, once.id)).to.deep.include({ status: 'active', nextFireAt: at, trigger: cron });
      expect(await f.store.firings(ORG, once.id, 10)).to.deep.equal([]);
      // Read again, at the revision it is now, the claim goes through.
      const fresh = aFiring(once, { kind: 'cron', slotKey: 'c:fresh', slotAt: at });
      expect(await f.store.claimCronSlot(fresh, at, at + 7 * 86_400_000, undefined, read + 1)).to.deep.equal(fresh);
    });

    it('SCH-03: a skip of a slot the cursor has left is changed, and marks nothing', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const slot = schedule.nextFireAt as number;
      const week = 7 * 86_400_000;
      // The owner read slot A; the firer claims A and moves the cursor to B before the skip writes.
      expect(await f.store.claimCronSlot(aFiring(schedule, { kind: 'cron', slotKey: 'c:a', slotAt: slot }), slot, slot + week)).to.not.equal(null);
      expect(await f.store.update(ORG, schedule.id, 0, { skipNextAt: slot }, T0 + 1)).to.equal('changed');
      expect(await f.store.get(ORG, schedule.id)).to.not.have.property('skipNextAt');
      // A skip of the slot the cursor is on now is written.
      expect(await f.store.update(ORG, schedule.id, 0, { skipNextAt: slot + week }, T0 + 2)).to.deep.include({ skipNextAt: slot + week });
    });

    it('SCH-01: a one-time slot is due, claimed once, and its claim disables the schedule in the same write', async () => {
      const at = T0 + 30 * 60_000;
      const schedule = await f.store.create(
        aSchedule({ trigger: { kind: 'once', at: new Date(at).toISOString(), timeZone: 'UTC' }, nextFireAt: at }),
        LIMITS,
      );
      expect((await f.store.dueCron(at, 10)).map((s) => s.id)).to.deep.equal([schedule.id]);
      const firing = aFiring(schedule, { kind: 'once', slotKey: `o:${new Date(at).toISOString()}`, slotAt: at });
      expect(await f.store.claimCronSlot(firing, at, null, 'ran_once')).to.deep.equal(firing);
      expect(await f.store.get(ORG, schedule.id)).to.deep.include({
        status: 'disabled',
        statusReason: 'ran_once',
        nextFireAt: null,
        revision: 1,
      });
      // An edit of its time read before it fired is refused: it ran, and a new time would never run.
      expect(await f.store.update(ORG, schedule.id, 0, { name: 'Later' }, T0 + 1)).to.equal('changed');
      expect(await f.store.dueCron(at + 60_000, 10)).to.deep.equal([]);
      const second = aFiring(schedule, { kind: 'once', slotKey: firing.slotKey, slotAt: at });
      expect(await f.store.claimCronSlot(second, at, null, 'ran_once')).to.equal(null);
      expect((await f.store.firings(ORG, schedule.id, 10)).map((x) => x.kind)).to.deep.equal(['once']);
    });

    it('SCH-01: a tick and two runs now racing for a one-time slot make one firing', async () => {
      const at = T0 + 30 * 60_000;
      const schedule = await f.store.create(
        aSchedule({ trigger: { kind: 'once', at: new Date(at).toISOString(), timeZone: 'UTC' }, nextFireAt: at }),
        LIMITS,
      );
      f.clock.value = at;
      const admission = new FakeAdmission();
      const firer = () => new ScheduleFirer({ store: f.store, admission, actors: new FakeActors(), setting: new FakeSetting() });
      await Promise.allSettled([firer().fireDue(), firer().runNow(schedule, 'k1'), firer().runNow(schedule, 'k2')]);
      expect(await f.store.firings(ORG, schedule.id, 10)).to.have.length(1);
      expect(admission.admitted).to.equal(1);
      expect(await f.store.get(ORG, schedule.id)).to.deep.include({ status: 'disabled', statusReason: 'ran_once' });
    });

    const ORIGIN = { conversationId: 'ses-1', runId: 'run-1', interactionId: 'ix.approve.k1' };
    const refusal = (error: unknown): string | undefined => (error as HarnessScheduleRefusedError).code;

    it('SCH-01: a deleted chat-made schedule leaves a tombstone no read answers, and frees its quota', async () => {
      const webhook = { kind: 'webhook' as const, coalesceS: 300, auth: 'signature' as const };
      const due = await f.store.create(aSchedule({ id: 'sch_tomb_cron', origin: ORIGIN }), LIMITS);
      const hook = await f.store.create(aSchedule({ id: 'sch_tomb_hook', origin: ORIGIN, trigger: webhook }), LIMITS);
      for (const schedule of [due, hook]) expect(await f.store.delete(ORG, schedule.id)).to.equal(true);
      for (const id of [due.id, hook.id]) {
        expect(await f.store.get(ORG, id)).to.equal(null);
        expect(await f.store.get(null, id)).to.equal(null);
        expect(await f.store.delete(ORG, id)).to.equal(false);
        expect(
          await f.store.setStatus(ORG, id, { from: ['active', 'paused', 'disabled'], to: 'paused', reason: 'by_actor', nextFireAt: null }, T0),
        ).to.equal(null);
        expect(await f.store.setAgentVersion(ORG, id, 2, T0)).to.equal(null);
      }
      expect(await f.store.list(ORG, { actorId: ALICE }, 10)).to.deep.equal([]);
      expect(await f.store.list(ORG, {}, 10)).to.deep.equal([]);
      expect(await f.store.dueCron(T0 + 365 * 86_400_000, 10)).to.deep.equal([]);
      expect(await f.store.eventSchedules(ORG, 'webhook')).to.deep.equal([]);
      expect(await f.store.hasActive('webhook')).to.equal(false);
      expect(await f.store.firings(ORG, due.id, 10)).to.deep.equal([]);
      await f.store.create(aSchedule(), { perActor: 1, perOrg: 1 });
    });

    it('SCH-01: a tombstone’s id is never inserted again; a schedule made elsewhere leaves none', async () => {
      const made = await f.store.create(aSchedule({ id: 'sch_tomb_used', origin: ORIGIN }), LIMITS);
      await f.store.delete(ORG, made.id);
      const again = await f.store.create(aSchedule({ id: made.id, origin: ORIGIN }), LIMITS).catch((e: unknown) => e);
      expect(refusal(again)).to.equal('SCHEDULE_APPROVAL_USED');
      expect(await f.store.get(ORG, made.id)).to.equal(null);
      const plain = await f.store.create(aSchedule({ id: 'sch_plain_1' }), LIMITS);
      await f.store.delete(ORG, plain.id);
      expect((await f.store.create(aSchedule({ id: plain.id }), LIMITS)).id).to.equal(plain.id);
    });

    it('SCH-01: erasing the person or the org removes the tombstone too', async () => {
      for (const erase of [() => f.store.eraseUser(ORG, ALICE), () => f.store.eraseOrg(ORG)]) {
        const made = await f.store.create(aSchedule({ id: 'sch_tomb_erased', origin: ORIGIN }), LIMITS);
        await f.store.delete(ORG, made.id);
        await erase();
        expect((await f.store.create(aSchedule({ id: made.id, origin: ORIGIN }), LIMITS)).id).to.equal(made.id);
        await f.store.eraseOrg(ORG);
      }
    });

    it('SCH-01: a create, a delete and a create of one approval at once never make it twice', async () => {
      const id = 'sch_tomb_race';
      const [first, deleted, second] = await Promise.allSettled([
        f.store.create(aSchedule({ id, origin: ORIGIN }), LIMITS),
        f.store.delete(ORG, id),
        f.store.create(aSchedule({ id, origin: ORIGIN }), LIMITS),
      ]);
      const live = await f.store.list(ORG, {}, 10);
      expect(live.length).to.be.at.most(1);
      expect([first, second].filter((r) => r.status === 'fulfilled').length).to.be.at.most(1);
      if (deleted?.status === 'fulfilled' && deleted.value) expect(live).to.deep.equal([]);
      await f.store.delete(ORG, id);
      const later = await f.store.create(aSchedule({ id, origin: ORIGIN }), LIMITS).catch((e: unknown) => e);
      expect(refusal(later)).to.equal('SCHEDULE_APPROVAL_USED');
    });

    it('SCH-01: a schedule keeps its chat origin; a second create under its id makes nothing', async () => {
      const origin = { conversationId: 'ses-1', runId: 'run-1', interactionId: 'ix.approve.k1' };
      const schedule = await f.store.create(aSchedule({ id: 'sch_chat_1', origin }), LIMITS);
      expect((await f.store.get(ORG, schedule.id))?.origin).to.deep.equal(origin);
      const again = await f.store.create(aSchedule({ id: 'sch_chat_1', origin, name: 'twice' }), LIMITS).catch((e: unknown) => e);
      expect(again).to.be.instanceOf(Error);
      expect(await f.store.list(ORG, {}, 10)).to.have.length(1);
      expect((await f.store.get(ORG, schedule.id))?.name).to.equal(schedule.name);
    });

    it('ST-2: a paused schedule is not due and cannot be claimed', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const slot = schedule.nextFireAt as number;
      await f.store.setStatus(ORG, schedule.id, { from: ['active'], to: 'paused', reason: 'by_actor', nextFireAt: slot }, T0);
      expect(await f.store.dueCron(slot, 10)).to.deep.equal([]);
      expect(await f.store.claimCronSlot(aFiring(schedule, { slotKey: 'c:1' }), slot, null)).to.equal(null);
    });

    it('ST-3: a manual or event slot is unique per schedule', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const first = await f.store.claimSlot(aFiring(schedule, { slotKey: 'manual:k' }));
      expect(first.created).to.equal(true);
      const again = await f.store.claimSlot(aFiring(schedule, { slotKey: 'manual:k' }));
      expect(again).to.deep.equal({ firing: first.firing, created: false });
      const other = await f.store.create(aSchedule(), LIMITS);
      expect((await f.store.claimSlot(aFiring(other, { slotKey: 'manual:k' }))).created).to.equal(true);
    });

    it('ST-4: settle once; a settled firing is left as it is', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing } = await f.store.claimSlot(aFiring(schedule));
      expect(
        await f.store.settle(ORG, firing.id, { status: 'admitted', runner: 'harness', runRef: 'run-1', runId: 'run-1', sessionId: 'ses-1', watchUntil: T0 + 10 }, T0 + 1),
      ).to.equal(true);
      expect(await f.store.settle(ORG, firing.id, { status: 'refused', reason: 'quota' }, T0 + 2)).to.equal(false);
      expect(await f.store.getFiring(ORG, firing.id)).to.deep.include({
        status: 'admitted',
        runId: 'run-1',
        sessionId: 'ses-1',
        reason: null,
        settledAt: T0 + 1,
        watchUntil: T0 + 10,
      });
      expect(await f.store.getFiring(OTHER_ORG, firing.id)).to.equal(null);
    });

    it('SCH-02: a firing keeps the runner that started it and its reference; a classic turn has no run id', async () => {
      const schedule = await f.store.create(aSchedule({ maxCostUsd: 1 }), LIMITS);
      const { firing: classic } = await f.store.claimSlot(aFiring(schedule, { slotKey: 'm:classic' }));
      expect(
        await f.store.settle(
          ORG,
          classic.id,
          { status: 'admitted', runner: 'classic', runRef: 'conv-1', runId: null, sessionId: 'conv-1', watchUntil: T0 + 60_000, costCapIgnored: true },
          T0,
        ),
      ).to.equal(true);
      expect(await f.store.getFiring(ORG, classic.id)).to.deep.include({
        runner: 'classic',
        runRef: 'conv-1',
        runId: null,
        sessionId: 'conv-1',
        costCapIgnored: true,
      });
      expect((await f.store.watched(T0, 10)).map((w) => w.runRef)).to.include('conv-1');
      const { firing: durable } = await f.store.claimSlot(aFiring(schedule, { slotKey: 'm:durable' }));
      await f.store.settle(ORG, durable.id, { status: 'admitted', runner: 'harness', runRef: 'run-9', runId: 'run-9', sessionId: 'ses-9', watchUntil: T0 + 60_000 }, T0);
      const read = await f.store.getFiring(ORG, durable.id);
      expect(read).to.deep.include({ runner: 'harness', runRef: 'run-9', runId: 'run-9' });
      expect(read).to.not.have.property('costCapIgnored');
      // A refusal writes no runner.
      const { firing: refused } = await f.store.claimSlot(aFiring(schedule, { slotKey: 'm:refused' }));
      await f.store.settle(ORG, refused.id, { status: 'refused', reason: 'quota' }, T0);
      expect(await f.store.getFiring(ORG, refused.id)).to.not.have.property('runner');
    });

    it('SCH-03: the coding engine a chat chose is kept and read back; absent stays absent', async () => {
      const kept = await f.store.create(aSchedule({ codingEngine: 'none' }), LIMITS);
      expect((await f.store.get(ORG, kept.id))?.codingEngine).to.equal('none');
      const plain = await f.store.create(aSchedule(), LIMITS);
      expect(await f.store.get(ORG, plain.id)).to.not.have.property('codingEngine');
    });

    it('SCH-03: an update at the revision read changes the fields and bumps it; a stale one changes nothing', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const from = { conversationId: 'chat-1', agentKey: 'helper', at: T0 + 5, interactionId: 'ix-1' };
      const input = { ...schedule.input!, query: 'An animal joke' };
      const updated = await f.store.update(
        ORG,
        schedule.id,
        0,
        { name: 'Renamed', input, notify: ['in_app', 'email'], nextFireAt: T0 + 90_000, skipNextAt: T0 + 90_000, lastChangedFrom: from },
        T0 + 5,
      );
      expect(updated).to.not.equal('changed');
      const read = await f.store.get(ORG, schedule.id);
      expect(read).to.deep.include({
        name: 'Renamed',
        revision: 1,
        notify: ['in_app', 'email'],
        nextFireAt: T0 + 90_000,
        skipNextAt: T0 + 90_000,
        lastChangedFrom: from,
        updatedAt: T0 + 5,
      });
      expect(read?.input?.query).to.equal('An animal joke');
      expect(await f.store.update(ORG, schedule.id, 0, { name: 'Late' }, T0 + 6)).to.equal('changed');
      expect((await f.store.get(ORG, schedule.id))?.name).to.equal('Renamed');
      await f.store.update(ORG, schedule.id, 1, { skipNextAt: null }, T0 + 7);
      expect(await f.store.get(ORG, schedule.id)).to.not.have.property('skipNextAt');
      expect(await f.store.update(ORG, 'no-such', 0, { name: 'x' }, T0)).to.equal(null);
      expect(await f.store.update(OTHER_ORG, schedule.id, 2, { name: 'x' }, T0)).to.equal(null);
      // A pause, a resume and a version move are changes too.
      const paused = await f.store.setStatus(
        ORG,
        schedule.id,
        { from: ['active'], to: 'paused', reason: 'by_actor', nextFireAt: null, lastChangedFrom: { ...from, interactionId: 'ix-2' } },
        T0 + 8,
      );
      expect(paused).to.deep.include({ revision: 3, lastChangedFrom: { ...from, interactionId: 'ix-2' } });
    });

    it('SCH-03: a delete or a pause at a stale revision is refused SCHEDULE_CHANGED and removes nothing', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      await f.store.claimSlot(aFiring(schedule));
      await f.store.update(ORG, schedule.id, 0, { name: 'Moved on' }, T0 + 1);
      const refused = async (work: () => Promise<unknown>): Promise<string> => {
        try {
          await work();
        } catch (error) {
          return (error as HarnessScheduleRefusedError).code;
        }
        return 'none';
      };
      expect(await refused(() => f.store.delete(ORG, schedule.id, 0))).to.equal('SCHEDULE_CHANGED');
      expect(await f.store.get(ORG, schedule.id)).to.not.equal(null);
      expect(await f.store.firings(ORG, schedule.id, 10)).to.have.length(1);
      const pause = { from: ['active'] as const, to: 'paused' as const, reason: 'by_actor', nextFireAt: null };
      expect(await refused(() => f.store.setStatus(ORG, schedule.id, { ...pause, expectedRevision: 0 }, T0 + 2))).to.equal(
        'SCHEDULE_CHANGED',
      );
      expect((await f.store.get(ORG, schedule.id))?.status).to.equal('active');
      expect(await f.store.setStatus(ORG, schedule.id, { ...pause, expectedRevision: 1 }, T0 + 2)).to.deep.include({
        status: 'paused',
        revision: 2,
      });
      // A pause from a state it cannot leave is not a change of revision: it answers null, as before.
      expect(await f.store.setStatus(ORG, schedule.id, { ...pause, expectedRevision: 2 }, T0 + 3)).to.equal(null);
      expect(await f.store.delete(ORG, schedule.id, 2)).to.equal(true);
      expect(await f.store.delete(ORG, schedule.id, 2)).to.equal(false);
      expect(await f.store.delete(ORG, 'no-such', 0)).to.equal(false);
    });

    it('SCH-03: an edit and a delete approved on the same revision race: exactly one of them happens', async () => {
      for (let round = 0; round < 5; round += 1) {
        const schedule = await f.store.create(aSchedule(), LIMITS);
        const [edited, deleted] = await Promise.allSettled([
          f.store.update(ORG, schedule.id, 0, { name: 'Edited' }, T0 + 1),
          f.store.delete(ORG, schedule.id, 0),
        ]);
        const after = await f.store.get(ORG, schedule.id);
        if (after === null) {
          // The delete won: the edit found nothing to change.
          expect(deleted).to.deep.equal({ status: 'fulfilled', value: true });
          expect(edited).to.deep.equal({ status: 'fulfilled', value: null });
        } else {
          // The edit won: the delete approved on the old revision removed nothing.
          expect(after).to.deep.include({ name: 'Edited', revision: 1 });
          // And the edit says so: a null here reads as not found to the person whose change took effect.
          expect(edited.status).to.equal('fulfilled');
          expect((edited as PromiseFulfilledResult<unknown>).value).to.deep.include({ id: schedule.id, name: 'Edited', revision: 1 });
          expect(deleted.status).to.equal('rejected');
          expect(((deleted as PromiseRejectedResult).reason as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_CHANGED');
        }
      }
    });

    it('SCH-03: a schedule a classic chat made keeps its chat; deleting it leaves no tombstone', async () => {
      const schedule = await f.store.create(aSchedule({ origin: { conversationId: 'chat-1', agentKey: 'helper' } }), LIMITS);
      expect((await f.store.get(ORG, schedule.id))?.origin).to.deep.equal({ conversationId: 'chat-1', agentKey: 'helper' });
      expect(await f.store.delete(ORG, schedule.id)).to.equal(true);
      // No tombstone holds the id: the same id can be made again.
      expect((await f.store.create(aSchedule({ id: schedule.id }), LIMITS)).id).to.equal(schedule.id);
    });

    it('ST-5: a pending firing whose lease passed is retaken once', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing } = await f.store.claimSlot(aFiring(schedule, { leaseUntil: T0 + 60_000, attempts: 1 }));
      expect(await f.store.takeStale(T0 + 59_999, 60_000, 10)).to.deep.equal([]);
      const taken = await f.store.takeStale(T0 + 60_000, 60_000, 10);
      expect(taken.map((t) => [t.id, t.attempts, t.leaseUntil])).to.deep.equal([[firing.id, 2, T0 + 120_000]]);
      expect(await f.store.takeStale(T0 + 60_000, 60_000, 10)).to.deep.equal([]);
      // A release by the attempt that holds it moves the lease; a stale attempt's does not.
      await f.store.release(ORG, firing.id, 1, T0);
      expect((await f.store.getFiring(ORG, firing.id))?.leaseUntil).to.equal(T0 + 120_000);
      await f.store.release(ORG, firing.id, 2, T0 + 500_000);
      expect((await f.store.getFiring(ORG, firing.id))?.leaseUntil).to.equal(T0 + 500_000);
    });

    it('ST-6: pause, resume, delete (its firings too)', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const paused = await f.store.setStatus(ORG, schedule.id, { from: ['active'], to: 'paused', reason: 'by_admin', nextFireAt: null }, T0 + 5);
      expect(paused).to.deep.include({ status: 'paused', statusReason: 'by_admin', nextFireAt: null, updatedAt: T0 + 5 });
      expect(await f.store.setStatus(ORG, schedule.id, { from: ['active'], to: 'paused', reason: null, nextFireAt: null }, T0)).to.equal(null);
      const resumed = await f.store.setStatus(ORG, schedule.id, { from: ['paused'], to: 'active', reason: null, nextFireAt: T0 + 99 }, T0 + 6);
      expect(resumed).to.deep.include({ status: 'active', nextFireAt: T0 + 99 });
      await f.store.claimSlot(aFiring(schedule));
      expect(await f.store.delete(OTHER_ORG, schedule.id)).to.equal(false);
      expect(await f.store.delete(ORG, schedule.id)).to.equal(true);
      expect(await f.store.get(ORG, schedule.id)).to.equal(null);
      expect(await f.store.firings(ORG, schedule.id, 10)).to.deep.equal([]);
      expect(await f.store.delete(ORG, schedule.id)).to.equal(false);
    });

    it("OWD-14: an agent schedule's version pin moves, and reads back; never another org's, a disabled one or the assistant's", async () => {
      const pinned = await f.store.create(aSchedule({ agentVersion: 2 }), LIMITS);
      expect(await f.store.get(ORG, pinned.id)).to.deep.include({ agentVersion: 2 });
      expect(await f.store.setAgentVersion(ORG, pinned.id, 3, T0 + 5)).to.deep.include({ agentVersion: 3, updatedAt: T0 + 5 });
      expect(await f.store.setAgentVersion(ORG, pinned.id, 'latest', T0 + 6)).to.deep.include({ agentVersion: 'latest' });
      expect((await f.store.get(ORG, pinned.id))?.agentVersion).to.equal('latest');
      expect(await f.store.setAgentVersion(OTHER_ORG, pinned.id, 1, T0)).to.equal(null);

      const assistant = await f.store.create(aSchedule({ agentKey: undefined }), LIMITS);
      expect(await f.store.setAgentVersion(ORG, assistant.id, 1, T0)).to.equal(null);
      const gone = await f.store.create(aSchedule({ agentKey: 'agent-9' }), LIMITS);
      await f.store.eraseAgent(ORG, 'agent-9');
      expect(await f.store.setAgentVersion(ORG, gone.id, 1, T0)).to.equal(null);
    });

    it('ST-7, CLEANUP-G2: erasing a user deletes their schedules and firings; erasing the org deletes all of its; nobody else is touched', async () => {
      const mine = await f.store.create(aSchedule({ webhookTokenHash: 'ab'.repeat(32) }), LIMITS);
      await f.store.claimSlot(aFiring(mine));
      const bobs = await f.store.create(aSchedule({ actorId: BOB }), LIMITS);
      const elsewhere = await f.store.create(aSchedule({ orgId: OTHER_ORG }), LIMITS);
      expect(await f.store.eraseUser(ORG, ALICE)).to.equal(1);
      expect(await f.store.get(ORG, mine.id)).to.equal(null);
      expect(await f.store.firings(ORG, mine.id, 10)).to.deep.equal([]);
      expect(await f.store.get(ORG, bobs.id)).to.deep.equal(bobs);
      expect(await f.store.get(OTHER_ORG, elsewhere.id)).to.deep.equal(elsewhere);
      // A re-invited account starts with none, and with its whole limit.
      await f.store.create(aSchedule(), { perActor: 1, perOrg: 500 });
      expect(await f.store.eraseOrg(ORG)).to.be.greaterThanOrEqual(2);
      expect(await f.store.list(ORG, {}, 10)).to.deep.equal([]);
      expect(await f.store.get(OTHER_ORG, elsewhere.id)).to.deep.equal(elsewhere);
    });

    it("DEACT-2 (SEC-AZ-08): pausing a user pauses their active schedules and triggers only; paused, disabled, others' and other orgs' stay", async () => {
      const cron = await f.store.create(aSchedule(), LIMITS);
      const hook = await f.store.create(
        aSchedule({ trigger: { kind: 'webhook', coalesceS: 300 }, nextFireAt: null, webhookTokenHash: 'ef'.repeat(32) }),
        LIMITS,
      );
      const paused = await f.store.create(aSchedule(), LIMITS);
      await f.store.setStatus(ORG, paused.id, { from: ['active'], to: 'paused', reason: 'by_actor', nextFireAt: null }, T0);
      const pausedBefore = await f.store.get(ORG, paused.id);
      const bobs = await f.store.create(aSchedule({ actorId: BOB }), LIMITS);
      const elsewhere = await f.store.create(aSchedule({ orgId: OTHER_ORG }), LIMITS);

      expect(await f.store.pauseUser(ORG, ALICE, 'actor_disabled')).to.equal(2);
      for (const id of [cron.id, hook.id]) {
        expect(await f.store.get(ORG, id)).to.deep.include({ status: 'paused', statusReason: 'actor_disabled', nextFireAt: null, revision: 1 });
      }
      // An edit read before the pause is refused: it is not written over what the system changed.
      expect(await f.store.update(ORG, cron.id, 0, { name: 'Late edit' }, T0 + 1)).to.equal('changed');
      // The trigger keeps its secret: pausing is not erasing, and its owner may resume it.
      expect((await f.store.get(ORG, hook.id))?.webhookTokenHash).to.equal('ef'.repeat(32));
      expect(await f.store.get(ORG, paused.id)).to.deep.equal(pausedBefore);
      expect(await f.store.get(ORG, bobs.id)).to.deep.equal(bobs);
      expect(await f.store.get(OTHER_ORG, elsewhere.id)).to.deep.equal(elsewhere);
      // Again: nothing left to pause.
      expect(await f.store.pauseUser(ORG, ALICE, 'actor_disabled')).to.equal(0);
    });

    it("CLEANUP-G3: erasing an agent disables the agent's schedules and drops their content; other agents' and other orgs' stay", async () => {
      const its = await f.store.create(aSchedule({ agentKey: 'agent-1', webhookTokenHash: 'cd'.repeat(32) }), LIMITS);
      const other = await f.store.create(aSchedule({ agentKey: 'agent-2' }), LIMITS);
      const plain = await f.store.create(aSchedule(), LIMITS);
      const elsewhere = await f.store.create(aSchedule({ orgId: OTHER_ORG, agentKey: 'agent-1' }), LIMITS);
      expect(await f.store.eraseAgent(ORG, 'agent-1')).to.equal(1);
      expect(await f.store.get(ORG, its.id)).to.deep.include({
        status: 'disabled',
        statusReason: 'agent_deleted',
        input: null,
        nextFireAt: null,
        webhookTokenHash: null,
        revision: 1,
      });
      // An edit read before the erasure cannot write the erased ask back.
      expect(await f.store.update(ORG, its.id, 0, { input: aSchedule().input! }, T0 + 1)).to.equal('changed');
      expect((await f.store.get(ORG, its.id))?.input).to.equal(null);
      for (const kept of [other, plain]) expect(await f.store.get(ORG, kept.id)).to.deep.equal(kept);
      expect(await f.store.get(OTHER_ORG, elsewhere.id)).to.deep.equal(elsewhere);
    });

    it('ST-7: event schedules by kind and connector; hasActive', async () => {
      expect(await f.store.hasActive('connector')).to.equal(false);
      const hook = await f.store.create(aSchedule({ trigger: { kind: 'webhook', coalesceS: 300 }, nextFireAt: null }), LIMITS);
      const c1 = await f.store.create(aSchedule({ trigger: { kind: 'connector', connectorId: 'c1', coalesceS: 300 }, nextFireAt: null }), LIMITS);
      await f.store.create(aSchedule({ trigger: { kind: 'connector', connectorId: 'c2', coalesceS: 300 }, nextFireAt: null }), LIMITS);
      expect((await f.store.eventSchedules(ORG, 'webhook')).map((s) => s.id)).to.deep.equal([hook.id]);
      expect((await f.store.eventSchedules(ORG, 'connector', 'c1')).map((s) => s.id)).to.deep.equal([c1.id]);
      expect(await f.store.eventSchedules(OTHER_ORG, 'connector', 'c1')).to.deep.equal([]);
      expect(await f.store.hasActive('connector')).to.equal(true);
      await f.store.setStatus(ORG, c1.id, { from: ['active'], to: 'paused', reason: null, nextFireAt: null }, T0);
      expect(await f.store.eventSchedules(ORG, 'connector', 'c1')).to.deep.equal([]);
    });

    it('ST-8: the approval watch', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing } = await f.store.claimSlot(aFiring(schedule));
      await f.store.settle(ORG, firing.id, { status: 'admitted', runner: 'harness', runRef: 'run-1', runId: 'run-1', sessionId: 'ses-1', watchUntil: T0 + 1_000 }, T0);
      expect((await f.store.watched(T0, 10)).map((w) => w.id)).to.deep.equal([firing.id]);
      // A run expired at its deadline is still seen ending (TR-07): the watch outlives it by the grace.
      expect((await f.store.watched(T0 + 1_000 + WATCH_GRACE_MS - 1, 10)).map((w) => w.id)).to.deep.equal([firing.id]);
      expect(await f.store.watched(T0 + 1_000 + WATCH_GRACE_MS, 10)).to.deep.equal([]);
      expect(await f.store.markNotified(ORG, firing.id, T0 + 1)).to.equal(true);
      expect(await f.store.markNotified(ORG, firing.id, T0 + 2)).to.equal(false);
      expect(await f.store.unwatch(ORG, firing.id, T0 + 3)).to.equal(true);
      // Ended once: the run's end is told by whoever ended the watch, and only them.
      expect(await f.store.unwatch(ORG, firing.id, T0 + 4)).to.equal(false);
      expect(await f.store.watched(T0, 10)).to.deep.equal([]);
      expect((await f.store.getFiring(ORG, firing.id))?.watchEndedAt).to.equal(T0 + 3);
      expect((await f.store.getFiring(ORG, firing.id))?.notifiedAt).to.equal(T0 + 1);
    });

    it('ST-10 (TR-07): the watch goes round, the firing checked longest ago first', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const ids: string[] = [];
      for (let i = 0; i < 3; i += 1) {
        const { firing } = await f.store.claimSlot(aFiring(schedule));
        await f.store.settle(ORG, firing.id, { status: 'admitted', runner: 'harness', runRef: `run-${String(i)}`, runId: `run-${String(i)}`, sessionId: 's', watchUntil: T0 + 60_000 }, T0);
        ids.push(firing.id);
      }
      await f.store.markChecked(ORG, ids[0]!, T0 + 1);
      await f.store.markChecked(ORG, ids[1]!, T0 + 2);
      expect((await f.store.watched(T0, 1)).map((w) => w.id)).to.deep.equal([ids[2]]);
      await f.store.markChecked(ORG, ids[2]!, T0 + 3);
      expect((await f.store.watched(T0, 3)).map((w) => w.id)).to.deep.equal(ids);
    });

    it('ST-11 (TR-06): a slot by its key; the active firings; one queued firing, put back without spending an attempt', async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing: active } = await f.store.claimSlot(aFiring(schedule, { slotAt: T0 }));
      await f.store.settle(ORG, active.id, { status: 'admitted', runner: 'harness', runRef: 'run-1', runId: 'run-1', sessionId: 's', watchUntil: T0 + 60_000 }, T0);
      expect((await f.store.firingBySlot(ORG, schedule.id, active.slotKey))?.id).to.equal(active.id);
      expect(await f.store.firingBySlot(ORG, schedule.id, 'missing')).to.equal(null);
      expect(await f.store.firingBySlot(OTHER_ORG, schedule.id, active.slotKey)).to.equal(null);
      expect((await f.store.activeFirings(ORG, schedule.id, 10)).map((w) => w.id)).to.deep.equal([active.id]);
      const { firing: waiting } = await f.store.claimSlot(aFiring(schedule, { slotAt: T0 + 1 }));
      expect(await f.store.queuedFiring(ORG, schedule.id)).to.equal(null);
      expect(await f.store.requeue(ORG, waiting.id, 1, 'run-1', T0 + 30_000)).to.equal(true);
      expect(await f.store.queuedFiring(ORG, schedule.id)).to.deep.include({ id: waiting.id, queuedBehind: 'run-1', reason: 'overlap_queued', attempts: 1, leaseUntil: T0 + 30_000 });
      const [retaken] = await f.store.takeStale(T0 + 30_000, 60_000, 10);
      expect(retaken).to.deep.include({ id: waiting.id, attempts: 2 });
      expect(await f.store.requeue(ORG, waiting.id, 1, 'run-1', T0 + 90_000)).to.equal(false);
      expect(await f.store.requeue(ORG, waiting.id, 2, 'run-1', T0 + 90_000)).to.equal(true);
      expect(await f.store.getFiring(ORG, waiting.id)).to.deep.include({ attempts: 1, leaseUntil: T0 + 90_000 });
      await f.store.unwatch(ORG, active.id, T0 + 2);
      expect(await f.store.activeFirings(ORG, schedule.id, 10)).to.deep.equal([]);
    });

    it("ST-12 (TR-03): a firing's trigger context is fixed by its first attempt", async () => {
      const schedule = await f.store.create(aSchedule(), LIMITS);
      const { firing } = await f.store.claimSlot(
        aFiring(schedule, { kind: 'event', event: { connectorId: 'c1', recordId: 'rec-1', at: T0 } }),
      );
      expect((await f.store.getFiring(ORG, firing.id))?.event).to.deep.equal({ connectorId: 'c1', recordId: 'rec-1', at: T0 });
      const first = { kind: 'connector' as const, scheduleId: schedule.id, firingId: firing.id, slotAt: new Date(T0).toISOString() };
      expect(await f.store.fixContext(ORG, firing.id, first)).to.deep.equal(first);
      expect(await f.store.fixContext(ORG, firing.id, { ...first, slotAt: 'later' })).to.deep.equal(first);
      expect((await f.store.getFiring(ORG, firing.id))?.context).to.deep.equal(first);
    });

    it("ST-13 (TR-04): a webhook's deliveries are counted per window and schedule", async () => {
      expect(await f.store.countDelivery(ORG, 'sch-a', T0, T0 + 120_000)).to.equal(1);
      expect(await f.store.countDelivery(ORG, 'sch-a', T0, T0 + 120_000)).to.equal(2);
      expect(await f.store.countDelivery(ORG, 'sch-b', T0, T0 + 120_000)).to.equal(1);
      expect(await f.store.countDelivery(ORG, 'sch-a', T0 + 60_000, T0 + 180_000)).to.equal(1);
    });

    it('ST-15 (SEC-C-13): a signed delivery nonce is first once per schedule; a window count does not consume it', async () => {
      expect(await f.store.firstDelivery(ORG, 'sch-a', 'n1', T0, T0 + 240_000)).to.equal(true);
      expect(await f.store.firstDelivery(ORG, 'sch-a', 'n1', T0 + 61_000, T0 + 301_000)).to.equal(false);
      expect(await f.store.firstDelivery(ORG, 'sch-b', 'n1', T0, T0 + 240_000)).to.equal(true);
      expect(await f.store.firstDelivery(ORG, 'sch-a', 'n2', T0, T0 + 240_000)).to.equal(true);
      expect(await f.store.countDelivery(ORG, 'sch-a', T0, T0 + 120_000)).to.equal(1);
      expect(await f.store.firstDelivery(ORG, 'sch-a', 'n1', T0 + 1, T0 + 240_001)).to.equal(false);
    });

    it("ST-14 (TR-04, TR-06, TR-07): a signed webhook's key, the overlap policy and the channels read back as written", async () => {
      const created = await f.store.create(
        aSchedule({
          trigger: { kind: 'webhook', coalesceS: 300, auth: 'signature' },
          nextFireAt: null,
          webhookSigningKey: 'pxs_key',
          overlap: 'queue',
          notify: ['in_app', 'email'],
        }),
        LIMITS,
      );
      expect(await f.store.get(ORG, created.id)).to.deep.equal(created);
      await f.store.eraseAgent(ORG, 'support-agent');
      expect(await f.store.get(ORG, created.id)).to.deep.include({ webhookSigningKey: null, status: 'disabled' });
    });
  });
}

/**
 * SK-1, SK-2, ST-9: separate store instances over one backend racing (two Node replicas). `instances` empties the
 * backend and answers a maker of store instances on `clock`; an in-memory backend answers the one store each time.
 */
export function describeScheduleStoreRaces(
  name: string,
  instances: (clock: TestClock) => Promise<() => ScheduleStore & ScheduleEraser>,
): void {
  describe(`ScheduleStore races (SK, ST-9): ${name}`, function () {
    this.timeout(120_000);

    it('SK-1, SK-2 (H2): two creates at once on two store instances, one below the limit: exactly one passes', async () => {
      for (let round = 0; round < 10; round += 1) {
        const instance = await instances(new TestClock());
        const stores = [instance(), instance()];
        // Per actor: Alice holds limit - 1.
        const perActor = { perActor: 2, perOrg: 500 };
        await stores[0]!.create(aSchedule(), perActor);
        const actor = await Promise.allSettled(stores.map((s) => s.create(aSchedule(), perActor)));
        expect(actor.filter((r) => r.status === 'fulfilled'), `round ${String(round)}`).to.have.length(1);
        expect(await stores[0]!.list(ORG, { actorId: ALICE }, 50)).to.have.length(2);
        // Per org: two actors, one slot left in the org.
        const perOrg = { perActor: 50, perOrg: 3 };
        const org = await Promise.allSettled([
          stores[0]!.create(aSchedule({ actorId: 'carol' }), perOrg),
          stores[1]!.create(aSchedule({ actorId: 'dave' }), perOrg),
        ]);
        expect(org.filter((r) => r.status === 'fulfilled'), `round ${String(round)}`).to.have.length(1);
        expect(await stores[0]!.list(ORG, {}, 50)).to.have.length(3);
      }
    });

    it('ST-16 (SEC-C-13): one signed delivery replayed at once on two store instances is first exactly once', async () => {
      for (let round = 0; round < 10; round += 1) {
        const instance = await instances(new TestClock());
        const stores = [instance(), instance(), instance(), instance()];
        const firsts = await Promise.all(stores.map((s) => s.firstDelivery(ORG, 'sch-a', `n${String(round)}`, 1, 240_001)));
        expect(firsts.filter(Boolean), `round ${String(round)}`).to.have.length(1);
      }
    });

    it('ST-9: two schedulers on their own store instances tick concurrently over one due slot: one firing, one admission', async () => {
      const clock = new TestClock();
      const instance = await instances(clock);
      const setup = instance();
      const schedules = await Promise.all(
        Array.from({ length: 5 }, () => setup.create(aSchedule(), { perActor: 50, perOrg: 500 })),
      );
      clock.value = (schedules[0]?.nextFireAt as number) + 30_000;
      const admission = new FakeAdmission();
      admission.delayMs = 5;
      const firer = () =>
        new ScheduleFirer({
          store: instance(),
          admission,
          actors: new FakeActors(),
          setting: new FakeSetting(),
        });
      const a = firer();
      const b = firer();
      await Promise.all(Array.from({ length: 20 }, (_, i) => (i % 2 === 0 ? a : b).tick()));
      expect(admission.requests).to.have.length(5);
      expect(new Set(admission.requests.map((r) => r.idempotencyKey)).size).to.equal(5);
      for (const schedule of schedules) {
        const firings = await setup.firings(ORG, schedule.id, 10);
        expect(firings.map((f) => f.status)).to.deep.equal(['admitted']);
        expect((await setup.get(ORG, schedule.id))?.nextFireAt).to.be.greaterThan(clock.value);
      }
    });
  });
}
