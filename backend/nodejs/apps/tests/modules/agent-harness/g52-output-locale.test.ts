/**
 * G-52 / C20.53 (G52-N1..N5): the person's locale reaches the run as `input.locale`, from the request's
 * `Accept-Language`, only while a live worker reads it (`output_locale`, guarded), and never reaches a worker that
 * does not: an older worker's input model forbids the field.
 */
import { expect } from 'chai';
import { OutputLocaleStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext } from '../../../src/modules/agent-harness/application/admission/context';
import { KNOWN_WORKER_FEATURES } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import type { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import {
  canonicalLocale,
  OUTPUT_LOCALE_FEATURE,
  requestLocale,
  withOutputLocale,
} from '../../../src/modules/agent-harness/domain/output-locale';
import { workerInput } from '../../../src/modules/agent-harness/domain/worker-input';
import { admissionRequirements, GUARDED_WORKER_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { goldenSpecSubject } from './support/golden-run-spec';

const INPUT: RunInputSnapshot = goldenSpecSubject().run.input!;

describe('G-52: the answer language follows the person\'s locale', () => {
  it('G52-N1: the locale is the header\'s most preferred well-formed tag, in canonical case', () => {
    expect(requestLocale('de-DE,de;q=0.9,en;q=0.8')).to.equal('de-DE');
    expect(requestLocale('en;q=0.5, es-es;q=0.9')).to.equal('es-ES');
    expect(requestLocale('zh-hant-tw')).to.equal('zh-Hant-TW');
    expect(requestLocale(['ko-KR', 'en'])).to.equal('ko-KR');
    expect(requestLocale('*')).to.equal(undefined);
    expect(requestLocale('de DE; drop table')).to.equal(undefined);
    expect(requestLocale(undefined)).to.equal(undefined);
    expect(requestLocale('de-DE,'.repeat(100))).to.equal(undefined);
    expect(requestLocale('fr;q=0')).to.equal(undefined);
    expect(canonicalLocale(' HI-in ')).to.equal('hi-IN');
  });

  it('G52-N2: admission keeps the locale unless a live worker shows it cannot read it (ADR-CAP-01 D2)', async () => {
    for (const live of [true, false]) {
      const withheld: string[] = [];
      const context = policyContext(
        { orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: INPUT, locale: 'de-DE' },
        {},
      );
      await new OutputLocaleStep({
        // A live worker (`chat`) that does or does not advertise the feature.
        live: async (feature) => (feature === OUTPUT_LOCALE_FEATURE ? live : feature === 'chat'),
        metrics: { withheld: (feature: string) => withheld.push(feature) } as never,
      }).run(context);
      expect(context.outputLocale).to.equal(live ? 'de-DE' : undefined);
      expect(withheld).to.deep.equal(live ? [] : [OUTPUT_LOCALE_FEATURE]);
    }
    // No worker seen yet: unknown, kept (the sweeper relaxes it if the worker that comes cannot read it).
    const early = policyContext(
      { orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: INPUT, locale: 'de-DE' },
      {},
    );
    await new OutputLocaleStep({ live: async () => false, metrics: { withheld: () => undefined } as never }).run(early);
    expect(early.outputLocale).to.equal('de-DE');
    const none = policyContext({ orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: INPUT }, {});
    await new OutputLocaleStep({ live: async () => true, metrics: { withheld: () => undefined } as never }).run(none);
    expect(none.outputLocale).to.equal(undefined);
    // A drained run's readmission keeps the locale its input already holds.
    const drained = policyContext(
      { orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: { ...INPUT, locale: 'cs-CZ' } },
      {},
    );
    await new OutputLocaleStep({ live: async () => true, metrics: { withheld: () => undefined } as never }).run(drained);
    expect(drained.outputLocale).to.equal('cs-CZ');
  });

  it('G52-N3: a run whose input names a locale requires output_locale, a guarded contract feature', () => {
    const run = goldenSpecSubject().run;
    expect(admissionRequirements({ ...run, input: withOutputLocale(INPUT, 'de-DE') }).features).to.include(
      OUTPUT_LOCALE_FEATURE,
    );
    expect(admissionRequirements({ ...run, input: withOutputLocale(INPUT, undefined) }).features).to.not.include(
      OUTPUT_LOCALE_FEATURE,
    );
    expect(GUARDED_WORKER_FEATURES).to.include(OUTPUT_LOCALE_FEATURE);
    expect(KNOWN_WORKER_FEATURES).to.include(OUTPUT_LOCALE_FEATURE);
  });

  it('G52-N4: a claim sends the locale only for a run that requires output_locale', () => {
    const input = withOutputLocale(INPUT, 'de-DE');
    expect(workerInput({ input, requires: { foldVersion: 1, features: [OUTPUT_LOCALE_FEATURE] } })?.locale).to.equal(
      'de-DE',
    );
    // Relaxed off the guarded feature (DUR2), or admitted before it: an older worker would refuse the field.
    expect(workerInput({ input, requires: { foldVersion: 1, features: [] } })).to.not.have.property('locale');
    expect(workerInput({ input, requires: undefined })).to.not.have.property('locale');
    expect(withOutputLocale(input, undefined)).to.not.have.property('locale');
  });
});
