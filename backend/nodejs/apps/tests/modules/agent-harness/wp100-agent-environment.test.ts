/**
 * WP-100 (C20.18): an agent's environment on its runs.
 *
 * - WP100-ENV-N1: an agent's environment is pinned as `x.pipeshub.environment` on an engine run that requires
 *   `environment_pin`, never on a loop run, never without the feature; the run requires the feature exactly when
 *   it carries one; an engine child inherits it and needs the feature, a loop child does not get it.
 * - WP100-ENV-N2: a definition the authority reports invalid is refused by the run definition's schema.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import type { PipeshubRunSpecExtension } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/run-spec';
import { agentRunDefinitionSchema } from '../../../src/modules/agent-harness/domain/agent-definition';
import { ENVIRONMENT_PIN_FEATURE } from '../../../src/modules/agent-harness/domain/agent-environment';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { childFeatures, childGrants, childRunSpec } from '../../../src/modules/agent-harness/domain/child-runs';
import { engineExecutor, RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { profileExecutor, runSpecResolver, type SpecSubject } from '../../../src/modules/agent-harness/domain/run-spec';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { goldenSpecSubject } from './support/golden-run-spec';

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const PIN = runSpecResolver(profileExecutor);
const PROFILE = RELEASE_ENGINE_PROFILES[0]!;

const ENVIRONMENT = {
  environmentId: 'python-data',
  level: 'agent' as const,
  setup: 'pip install -r requirements.txt',
  env: { LANG: 'C.UTF-8' },
  cachePaths: ['/root/.cache/pip'],
};

const pipeshub = (spec: RunSpec): PipeshubRunSpecExtension =>
  spec.extensions?.['x.pipeshub'] as PipeshubRunSpecExtension;

const subject = (features: string[], extra: Partial<SpecSubject> = {}, environment: unknown = ENVIRONMENT): SpecSubject => {
  const golden = goldenSpecSubject();
  return {
    ...golden,
    agentDefinition: { ...golden.agentDefinition!, environment: environment as never },
    run: { ...golden.run, requires: { foldVersion: 1, features: features as never } },
    ...extra,
  };
};

describe('WP-100: the agent environment on its runs (C20.18)', () => {
  describe('the environment pin', () => {
    it('WP100-ENV-N1: an engine run that requires environment_pin pins the agent environment; nothing else does', () => {
      const engine = PIN(subject([ENVIRONMENT_PIN_FEATURE], { engineProfile: PROFILE }), DURABLE);
      expect(engine.spec.executor?.kind).to.equal('engine');
      expect(pipeshub(engine.spec).environment).to.deep.equal(ENVIRONMENT);
      const unfeatured = PIN(subject([], { engineProfile: PROFILE }), DURABLE);
      expect(pipeshub(unfeatured.spec)).to.not.have.property('environment');
      const loop = PIN(subject([ENVIRONMENT_PIN_FEATURE]), DURABLE);
      expect(pipeshub(loop.spec)).to.not.have.property('environment');
    });

    it('WP100-ENV-N1: the run requires environment_pin exactly when it hands an agent environment to an engine', () => {
      const golden = goldenSpecSubject();
      const definition = { ...golden.agentDefinition!, environment: ENVIRONMENT };
      const withIt = admissionRequirements(golden.run, [], 'engine', null, null, false, false, false, false, false,
        definition, false, false, false, false, true);
      expect(withIt.features).to.include(ENVIRONMENT_PIN_FEATURE);
      const without = admissionRequirements(golden.run, [], 'engine', null, null, false, false, false, false, false,
        golden.agentDefinition, false, false, false, false, false);
      expect(without.features).to.not.include(ENVIRONMENT_PIN_FEATURE);
    });

    it('WP100-ENV-N1: an engine child inherits its parent\'s environment and needs the feature; a loop child does not', () => {
      const pinned = PIN(subject([ENVIRONMENT_PIN_FEATURE], { engineProfile: PROFILE }), DURABLE);
      const parent = { ...pinned, spec: { ...pinned.spec, budgets: { ...pinned.spec.budgets, maxChildren: 4 } } };
      const decision = childGrants(parent.spec, { maxTurns: 5 }, 1);
      if (decision.grants === null) return expect.fail('expected a grant');
      const engineChild = childRunSpec(parent, decision.grants, undefined, undefined, engineExecutor(PROFILE));
      expect(pipeshub(engineChild.spec).environment).to.deep.equal(ENVIRONMENT);
      expect(childFeatures(engineChild)).to.include(ENVIRONMENT_PIN_FEATURE);
      const loopChild = childRunSpec(parent, decision.grants);
      expect(pipeshub(loopChild.spec)).to.not.have.property('environment');
      expect(childFeatures(loopChild)).to.not.include(ENVIRONMENT_PIN_FEATURE);
    });

    it('WP100-ENV-N2: an environment the authority reports invalid refuses the definition', () => {
      const golden = goldenSpecSubject().agentDefinition!;
      expect(agentRunDefinitionSchema.safeParse({ ...golden, environment: ENVIRONMENT }).success).to.equal(true);
      for (const environment of [
        { invalid: true },
        { ...ENVIRONMENT, level: 'image' },
        { ...ENVIRONMENT, image: 'python:3.12' },
        { ...ENVIRONMENT, editionPart: ['pypi'] },
      ]) {
        expect(agentRunDefinitionSchema.safeParse({ ...golden, environment }).success, JSON.stringify(environment))
          .to.equal(false);
      }
    });
  });
});
