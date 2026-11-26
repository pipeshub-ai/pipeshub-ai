/**
 * C20.53 / G-52 (WP100-LANG-N1..N5): every run answers in the person's language from one source, the run's pinned
 * locale (`input.locale`). A child run inherits its parent's (the child's own run says it, so the TaskSpec does
 * not); a scheduled run has no browser, so its schedule pins its owner's locale when they make it, from the same
 * `Accept-Language` a chat request carries, and each firing's input carries it to admission.
 */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { childInput } from '../../../src/modules/agent-harness/application/child-run-service';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  CreateScheduleRequest,
  ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { OUTPUT_LOCALE_FEATURE, withOutputLocale } from '../../../src/modules/agent-harness/domain/output-locale';
import { firingInput } from '../../../src/modules/agent-harness/domain/schedules';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { scheduleCaller } from '../../../src/modules/agent-harness/transport/schedule-routes';
import { ALICE, FakeActors, FakeAdmission, FakeSetting, ORG, TestClock } from './support/schedules';
import { goldenSpecSubject } from './support/golden-run-spec';
import { testControlPlane } from '../control-plane/support/in-memory';

const CRON: CreateScheduleRequest = {
  name: 'Weekly support digest',
  agentKey: 'support-agent',
  input: { query: 'Summarise new support tickets' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

function service(): { service: ScheduleService; store: InMemoryScheduleStore; admission: FakeAdmission } {
  const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
  const store = new InMemoryScheduleStore(clock);
  const admission = new FakeAdmission();
  const actors = new FakeActors();
  const setting = new FakeSetting();
  const firer = new ScheduleFirer({ store, admission, actors, setting });
  return {
    service: new ScheduleService({ transaction: testControlPlane(), store, firer, actors, setting, newToken: () => 't' }),
    store,
    admission,
  };
}

describe('C20.53 / G-52: engine, child and scheduled runs answer in the person\'s language', () => {
  it('WP100-LANG-N1: a child run inherits its parent\'s pinned locale, and so requires output_locale', () => {
    const parent = goldenSpecSubject().run as HarnessRun;
    const withLocale = { ...parent, input: withOutputLocale(parent.input!, 'de-DE') };

    const child = childInput(withLocale, 'Write the report', null);
    expect(child.locale).to.equal('de-DE');
    expect(child.query).to.equal('Write the report');
    expect(admissionRequirements({ ...parent, input: child }).features).to.include(OUTPUT_LOCALE_FEATURE);
    // A parent with none gives none: the child's model answers in the language the request is written in.
    expect(childInput(parent, 'Write the report', null)).to.not.have.property('locale');
  });

  it('WP100-LANG-N2: a schedule pins its owner\'s locale from the request that makes it; each firing carries it', async () => {
    const { service: schedules, store, admission } = service();
    const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session', locale: 'de-DE' };

    const { schedule } = await schedules.create(alice, CRON);
    const stored = await store.get(ORG, schedule.id);
    expect(stored?.input?.locale).to.equal('de-DE');
    expect(firingInput(stored!, Date.parse('2026-07-06T07:00:00.000Z'))?.locale).to.equal('de-DE');

    await schedules.runNow(alice, schedule.id, 'key-1');
    expect(admission.requests[0]?.input.locale).to.equal('de-DE');
    // The template the owner reads back is their prompt, not a setting: the locale is not shown or editable there.
    expect(schedule.input).to.not.have.property('locale');
  });

  it('WP100-LANG-N3: a schedule made with no locale fires with none', async () => {
    const { service: schedules, store } = service();
    const { schedule } = await schedules.create({ orgId: ORG, userId: ALICE, via: 'session' }, CRON);
    expect((await store.get(ORG, schedule.id))?.input).to.not.have.property('locale');
  });

  it('WP100-LANG-N4: the schedule routes take the caller\'s locale from Accept-Language, canonical or none', () => {
    const req = (header?: string) =>
      ({
        headers: header === undefined ? {} : { 'accept-language': header },
        user: { orgId: ORG, userId: ALICE },
      }) as never;
    expect(scheduleCaller(req('de-de,en;q=0.5'))).to.deep.include({ orgId: ORG, userId: ALICE, locale: 'de-DE' });
    expect(scheduleCaller(req())).to.not.have.property('locale');
    expect(scheduleCaller(req('*'))).to.not.have.property('locale');
  });

  it('WP100-LANG-N5: a schedule made through the API without Accept-Language pins no locale, and no firing carries one', async () => {
    // WP100-FU-4: no org default language. The run then answers in the language its prompt is written in (the
    // worker adds no answer-language section without a pinned locale: test_g52_output_language.py LANG-N5).
    const { service: schedules, store, admission } = service();
    const caller = scheduleCaller({ headers: {}, user: { orgId: ORG, userId: ALICE } } as never);

    const { schedule } = await schedules.create(caller, CRON);
    const stored = await store.get(ORG, schedule.id);
    expect(stored?.input).to.not.have.property('locale');
    expect(firingInput(stored!, Date.parse('2026-07-06T07:00:00.000Z'))).to.not.have.property('locale');
    await schedules.runNow(caller, schedule.id, 'key-1');
    expect(admission.requests[0]?.input).to.not.have.property('locale');
  });
});
