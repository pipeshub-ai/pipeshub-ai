/** P8a SC: the schedule domain, pure. */
import { expect } from 'chai';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  assertCron,
  assertScheduleBudget,
  coalescedSlot,
  cronSlotKey,
  decideCron,
  eventSlotKey,
  firingBackoffMs,
  firingDisposition,
  firingInput,
  latestCronSlot,
  manualSlotKey,
  MISFIRE_GRACE_MS,
  nextCronSlot,
  scheduleIdempotencyKey,
} from '../../../src/modules/agent-harness/domain/schedules';

const at = (iso: string): number => Date.parse(iso);
const iso = (ms: number | null): string | null =>
  ms === null ? null : new Date(ms).toISOString();

const refusal = (fn: () => void): HarnessScheduleRefusedError => {
  try {
    fn();
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessScheduleRefusedError);
    return error as HarnessScheduleRefusedError;
  }
  throw new Error('expected a refusal');
};

const MONDAY_9 = {
  kind: 'cron' as const,
  cron: '0 9 * * 1',
  timeZone: 'Europe/Berlin',
  catchUp: 'skip' as const,
};

describe('P8a schedules: domain (SC)', () => {
  it('SC-1: next slot of a cron in its time zone, summer and winter', () => {
    expect(iso(nextCronSlot('0 9 * * 1', 'Europe/Berlin', at('2026-07-05T00:00:00Z')))).to.equal(
      '2026-07-06T07:00:00.000Z',
    );
    expect(iso(nextCronSlot('0 9 * * 1', 'Europe/Berlin', at('2026-12-06T00:00:00Z')))).to.equal(
      '2026-12-07T08:00:00.000Z',
    );
    expect(iso(latestCronSlot('0 9 * * 1', 'Europe/Berlin', at('2026-07-08T00:00:00Z')))).to.equal(
      '2026-07-06T07:00:00.000Z',
    );
    // A slot at the instant asked for is the latest, not the next.
    expect(iso(latestCronSlot('0 9 * * 1', 'Europe/Berlin', at('2026-07-06T07:00:00Z')))).to.equal(
      '2026-07-06T07:00:00.000Z',
    );
  });

  it('SC-1: across the DST nights each local slot fires once', () => {
    const slots = (cron: string, from: string, n: number): string[] => {
      const out: string[] = [];
      let cursor = at(from);
      for (let i = 0; i < n; i += 1) {
        cursor = nextCronSlot(cron, 'Europe/Berlin', cursor) as number;
        out.push(iso(cursor) as string);
      }
      return out;
    };
    // 02:30 does not exist on 29 March: it fires once that day, never twice, never skipped a day.
    const spring = slots('30 2 * * *', '2026-03-28T00:00:00Z', 3);
    expect(new Set(spring.map((s) => s.slice(0, 10))).size).to.equal(3);
    // 02:30 happens twice on 25 October: one firing.
    const autumn = slots('30 2 * * *', '2026-10-24T00:00:00Z', 3);
    expect(autumn.filter((s) => s.startsWith('2026-10-25'))).to.have.length(1);
  });

  for (const [name, cron, timeZone, field] of [
    ['a cron that does not parse', 'every monday', 'UTC', 'cron'],
    ['six fields', '0 0 9 * * 1', 'UTC', 'cron'],
    ['an unknown time zone', '0 9 * * 1', 'Mars/Base', 'timeZone'],
    ['every minute', '* * * * *', 'UTC', 'cron'],
    ['every 4 minutes', '*/4 * * * *', 'UTC', 'cron'],
    ['two slots 1 minute apart', '0,1 9 * * *', 'UTC', 'cron'],
    ['never fires (31 February)', '0 9 31 2 *', 'UTC', 'cron'],
  ] as const) {
    it(`SC-2: ${name} is SCHEDULE_INVALID naming ${field}`, () => {
      const error = refusal(() => assertCron(cron, timeZone, at('2026-10-05T00:00:00Z')));
      expect(error.code).to.equal('SCHEDULE_INVALID');
      expect(error.field).to.equal(field);
    });
  }

  it('SC-2: every 5 minutes is allowed', () => {
    assertCron('*/5 * * * *', 'UTC', at('2026-10-05T00:00:00Z'));
    assertCron('0 9 * * 1', 'America/New_York', at('2026-10-05T00:00:00Z'));
  });

  describe('SC-3: catch-up decision table', () => {
    const cursor = at('2026-07-06T07:00:00Z');
    it('not due: wait', () => {
      expect(decideCron(MONDAY_9, cursor, cursor - 1)).to.deep.equal({ kind: 'wait' });
    });
    it('on time (within grace): fire the slot, cursor to next week', () => {
      const decision = decideCron(MONDAY_9, cursor, cursor + 60_000);
      expect(decision).to.deep.include({ kind: 'fire', slotAt: cursor, missed: 0, late: false });
      expect(iso((decision as { nextFireAt: number }).nextFireAt)).to.equal('2026-07-13T07:00:00.000Z');
    });
    it('late beyond the grace with skip: skipped', () => {
      expect(decideCron(MONDAY_9, cursor, cursor + MISFIRE_GRACE_MS + 1)).to.deep.include({
        kind: 'skip',
        slotAt: cursor,
        late: true,
      });
    });
    it('late with run_latest: fire the slot', () => {
      expect(
        decideCron({ ...MONDAY_9, catchUp: 'run_latest' }, cursor, cursor + MISFIRE_GRACE_MS + 1),
      ).to.deep.include({ kind: 'fire', slotAt: cursor, late: true });
    });
    it('three weeks down: only the latest slot is considered; the two before it are missed', () => {
      const now = at('2026-07-20T07:01:00Z');
      const onTime = decideCron(MONDAY_9, cursor, now);
      expect(onTime).to.deep.include({ kind: 'fire', slotAt: at('2026-07-20T07:00:00Z'), missed: 2, late: false });
      const later = at('2026-07-21T12:00:00Z');
      expect(decideCron(MONDAY_9, cursor, later)).to.deep.include({ kind: 'skip', slotAt: at('2026-07-20T07:00:00Z'), missed: 2 });
      const latest = decideCron({ ...MONDAY_9, catchUp: 'run_latest' }, cursor, later);
      expect(latest).to.deep.include({ kind: 'fire', slotAt: at('2026-07-20T07:00:00Z'), missed: 2 });
      expect(iso((latest as { nextFireAt: number }).nextFireAt)).to.equal('2026-07-27T07:00:00.000Z');
    });
  });

  it('SC-4: events of one window share a slot; the next window is a new one', () => {
    const a = coalescedSlot(at('2026-10-05T10:01:00Z'), 300);
    const b = coalescedSlot(at('2026-10-05T10:04:59Z'), 300);
    const c = coalescedSlot(at('2026-10-05T10:05:00Z'), 300);
    expect(a).to.equal(b);
    expect(c).to.equal(a + 300_000);
    expect(eventSlotKey(a)).to.equal('e:2026-10-05T10:00:00.000Z');
  });

  it('SC-5: keys', () => {
    expect(cronSlotKey(at('2026-07-06T07:00:00Z'))).to.equal('c:2026-07-06T07:00:00.000Z');
    expect(manualSlotKey('k-1')).to.equal('manual:k-1');
    expect(scheduleIdempotencyKey('s1', 'c:x')).to.equal('schedule:s1:c:x');
    expect(scheduleIdempotencyKey('s1', 'c:x')).to.not.equal(scheduleIdempotencyKey('s2', 'c:x'));
  });

  it('SC-6: a firing input is the template at the slot time in the schedule zone', () => {
    const input = {
      query: 'Summarise new support tickets',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'auto' },
    };
    expect(firingInput({ input, trigger: MONDAY_9 }, at('2026-07-06T07:00:00Z'))).to.deep.equal({
      ...input,
      timezone: 'Europe/Berlin',
      currentTime: '2026-07-06T07:00:00.000Z',
    });
    expect(firingInput({ input: null, trigger: MONDAY_9 }, 0)).to.equal(null);
  });

  it('SC-6: budget bounds', () => {
    assertScheduleBudget(undefined);
    assertScheduleBudget(0.5);
    assertScheduleBudget(5);
    for (const value of [0, -1, 5.01, 100]) {
      expect(refusal(() => assertScheduleBudget(value)).code).to.equal('SCHEDULE_BUDGET_EXCEEDS_CEILING');
    }
  });

  it('SC-7: refusal classification', () => {
    const table: Record<string, string> = {
      unavailable: 'retry',
      actor_deleted: 'disable',
      template_missing: 'disable',
      actor_disabled: 'pause',
      actor_not_person: 'pause',
      org_inactive: 'pause',
      agent_forbidden: 'pause',
      scheduled_runs_disabled: 'refuse',
      rollout_legacy: 'refuse',
      quota: 'refuse',
      account_not_owned: 'refuse',
      run_active: 'refuse',
    };
    for (const [reason, disposition] of Object.entries(table)) {
      expect(firingDisposition(reason), reason).to.equal(disposition);
    }
    expect(firingBackoffMs(1)).to.equal(60_000);
    expect(firingBackoffMs(2)).to.equal(120_000);
    expect(firingBackoffMs(20)).to.equal(600_000);
  });
});
