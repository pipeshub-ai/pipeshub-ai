/**
 * RUN01-D1-PIN (ADR-RUN-01 D1): admission resolves what a run owes once, from its request, and its claim pins it as
 * `x.pipeshub.deliverable` while a live worker reads it (`deliverable_pin`); the worker never parses the request
 * again. The engine child the work is handed to owes what its parent owes. Node and Python resolve alike: both are
 * held to `contracts/harness/vectors/deliverable.json`. RUN01-D1-LEDGER: Node takes the ledger's events
 * (`outcome_ledger`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { pipeshubRunSpecExtensionSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/run-spec';
import { DeliverablePinStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext } from '../../../src/modules/agent-harness/application/admission/context';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { childFeatures, childGrants, childRunSpec } from '../../../src/modules/agent-harness/domain/child-runs';
import { resolveDeliverable } from '../../../src/modules/agent-harness/domain/deliverable';
import { engineExecutor, RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { NODE_CAPABILITIES, OUTCOME_LEDGER_FEATURE } from '../../../src/modules/agent-harness/domain/node-capabilities';
import {
  DELIVERABLE_PIN_FEATURE,
  profileExecutor,
  runSpecResolver,
  SpecSubject,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { admissionRequirements, GUARDED_WORKER_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { goldenSpecSubject } from './support/golden-run-spec';

const VECTOR = join(__dirname, '../../../../../../contracts/harness/vectors/deliverable.json');
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const PIN = runSpecResolver(profileExecutor);
const REQUEST = 'Research Onyx. Write the final report as one Markdown file named report.md in out/';

interface Case {
  id: string;
  request: string;
  deliverable: unknown;
}

const subject = (features: string[], query = REQUEST): SpecSubject => {
  const golden = goldenSpecSubject();
  return {
    ...golden,
    run: {
      ...golden.run,
      input: { ...golden.run.input!, query },
      requires: { foldVersion: 1, features: features as never },
    },
  };
};

const deliverableOf = (spec: RunSpec): unknown =>
  (spec.extensions?.['x.pipeshub'] as { deliverable?: unknown } | undefined)?.deliverable;

describe('RUN01-D1: the deliverable is resolved once and pinned', () => {
  const cases = (JSON.parse(readFileSync(VECTOR, 'utf8')) as { cases: Case[] }).cases;

  for (const c of cases) {
    it(`RUN01-D1-PIN-1 ${c.id}: Node resolves the request as the shared vector says`, () => {
      expect(resolveDeliverable(c.request)).to.deep.equal(c.deliverable);
    });
  }

  it('RUN01-D1-PIN-2: a run that requires deliverable_pin pins it, valid, and one that does not pins nothing', () => {
    const { spec } = PIN(subject([DELIVERABLE_PIN_FEATURE]), DURABLE);
    expect(deliverableOf(spec)).to.deep.equal({ kind: 'file', name: 'report.md' });
    expect(pipeshubRunSpecExtensionSchema.safeParse(spec.extensions?.['x.pipeshub']).success).to.equal(true);
    expect(deliverableOf(PIN(subject([]), DURABLE).spec)).to.equal(undefined);
    expect(deliverableOf(PIN(subject([DELIVERABLE_PIN_FEATURE], 'which bugs are security bugs?'), DURABLE).spec)).to.deep.equal({
      kind: 'answer',
    });
  });

  it('RUN01-D1-PIN-3: admission requires the feature unless a live worker shows it cannot read it, and it is guarded', async () => {
    for (const live of [true, false]) {
      const withheld: string[] = [];
      const context = policyContext(
        { orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: goldenSpecSubject().run.input! },
        {},
      );
      await new DeliverablePinStep({
        // A live worker (`chat`) with or without the feature; before any worker is seen it is required (ADR-CAP-01 D2).
        live: async (feature) => (feature === DELIVERABLE_PIN_FEATURE ? live : feature === 'chat'),
        metrics: { withheld: (feature: string) => withheld.push(feature) } as never,
      }).run(context);
      expect(context.deliverablePin).to.equal(live);
      expect(withheld).to.deep.equal(live ? [] : [DELIVERABLE_PIN_FEATURE]);
    }
    const run = goldenSpecSubject().run;
    const args = [run, [], 'loop', null, null, false, false, false, false, false, undefined, false, false, false] as const;
    expect(admissionRequirements(...args, true).features).to.include(DELIVERABLE_PIN_FEATURE);
    expect(admissionRequirements(...args, false).features).to.not.include(DELIVERABLE_PIN_FEATURE);
    expect(GUARDED_WORKER_FEATURES).to.include(DELIVERABLE_PIN_FEATURE);
  });

  it('RUN01-D1-PIN-4: the engine child the work is handed to owes its parent\'s deliverable; a helper does not', () => {
    const pinned = PIN(subject([DELIVERABLE_PIN_FEATURE]), DURABLE);
    const parent = { ...pinned, spec: { ...pinned.spec, budgets: { ...pinned.spec.budgets, maxChildren: 4 } } };
    const decision = childGrants(parent.spec, { maxTurns: 5 }, 1);
    if (decision.grants === null) return expect.fail('expected a grant');
    const engine = childRunSpec(parent, decision.grants, undefined, undefined, engineExecutor(RELEASE_ENGINE_PROFILES[0]!));
    expect(deliverableOf(engine.spec)).to.deep.equal({ kind: 'file', name: 'report.md' });
    expect(childFeatures(engine)).to.include(DELIVERABLE_PIN_FEATURE);
    const helper = childRunSpec(parent, decision.grants);
    expect(deliverableOf(helper.spec)).to.equal(undefined);
    expect(childFeatures(helper)).to.not.include(DELIVERABLE_PIN_FEATURE);
  });

  it('RUN01-D1-LEDGER-5: Node names outcome_ledger, so a worker journals the ledger\'s events', () => {
    expect(NODE_CAPABILITIES.features).to.include(OUTCOME_LEDGER_FEATURE);
  });
});
