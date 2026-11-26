/**
 * MA1 (QA37-U4, U5, K3): the role key of a spawn, the model a role sets and
 * the model fields of a child's spec, each held to the shared vector
 * `contracts/harness/vectors/child-model.json` (Python reads the same file).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { childModelOf, roleCostBound, roleKeysOf, roleRoutesOf } from '../../../src/modules/agent-harness/domain/child-model';
import { childGrants, childRunSpec, pipeshub, type ChildSpecModel, type SubAgentDefinition } from '../../../src/modules/agent-harness/domain/child-runs';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { minMapBound } from '../../../src/modules/agent-harness/domain/policy/orders';
import { pinRunSpec, type PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { agentDefinition } from './support/agent-definition';
import { goldenSpecSubject } from './support/golden-run-spec';

type Route = RunSpec['model']['policy'][number];
type ExtModel = { modelKey: string; modelName: string } | null;

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/child-model.json'), 'utf8'),
) as {
  roleKeys: Array<{ subAgent: SubAgentDefinition | null; keys: string[] }>;
  cases: Array<{
    id: string;
    behaviour: string;
    roleModels: Record<string, string>;
    subAgent: SubAgentDefinition | null;
    expect: { inherit?: true; model?: { provider: string; model: string; role: string } };
  }>;
  specs: Array<{
    id: string;
    behaviour: string;
    parent: { policy: Route[]; extModel: ExtModel };
    model: ChildSpecModel | null;
    expect: { policy: Route[]; extModel: ExtModel };
  }>;
};

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

/** A pinned parent whose model fields are the vector's. */
const parentWith = (policy: Route[], extModel: ExtModel): PinnedRunSpec => {
  const pinned = pinRunSpec(goldenSpecSubject(), DURABLE);
  const extension = { ...pipeshub(pinned.spec)! };
  if (extModel === null) delete extension.model;
  else extension.model = extModel;
  return {
    ...pinned,
    spec: {
      ...pinned.spec,
      budgets: { ...pinned.spec.budgets, maxChildren: 4 },
      model: { ...pinned.spec.model, policy },
      extensions: { ...pinned.spec.extensions, 'x.pipeshub': extension },
    },
  };
};

describe('MA1 the role key of a spawn (QA37-U4)', () => {
  for (const [index, row] of VECTOR.roleKeys.entries()) {
    it(`QA37-U4 #${index + 1}: ${JSON.stringify(row.subAgent?.domainAgent ?? null)} / ${JSON.stringify(row.subAgent?.role ?? null)} → ${row.keys.join(', ')}`, () => {
      expect(roleKeysOf(row.subAgent)).to.deep.equal(row.keys);
    });
  }

  it('QA37-U4: a spawn with no sub-agent definition is the `default` role', () => {
    expect(roleKeysOf(undefined)).to.deep.equal(['default']);
  });
});

describe('MA1 inherit unless a role sets a model (QA37-U5)', () => {
  for (const row of VECTOR.cases) {
    it(`QA37-U5 ${row.id}: ${row.behaviour}`, () => {
      const asked = childModelOf(row.roleModels, row.subAgent);
      if (row.expect.inherit === true) expect(asked).to.equal(null);
      else expect(asked).to.deep.equal(row.expect.model);
    });
  }

  it('QA37-U5: a definition’s role models are routes named as the deployment configures the provider', () => {
    const definition = agentDefinition({
      roleModels: {
        researcher: { modelKey: 'model-2', modelName: 'claude-x', provider: 'Anthropic' },
        default: { modelKey: 'model-1', modelName: 'gpt-x', provider: 'openAI' },
      },
    });
    expect(roleRoutesOf(definition)).to.deep.equal({ researcher: 'anthropic:claude-x', default: 'openai:gpt-x' });
    expect(childModelOf(roleRoutesOf(definition), { role: 'Researcher', toolNames: [] })).to.deep.equal({
      provider: 'anthropic',
      model: 'claude-x',
      role: 'researcher',
    });
  });

  it('QA37-U5: no definition, an older authority’s, or one without role models: every child inherits', () => {
    expect(roleRoutesOf(null)).to.deep.equal({});
    expect(roleRoutesOf(agentDefinition())).to.deep.equal({});
    expect(childModelOf(roleRoutesOf(agentDefinition()), { role: 'researcher', toolNames: [] })).to.equal(null);
  });

  it('QA37-U5: a role named like an object member (`constructor`) finds no model of its own', () => {
    expect(childModelOf({}, { role: 'constructor', toolNames: [] })).to.equal(null);
  });
});

describe('MA1 the model fields of a child’s spec (QA37-K3)', () => {
  for (const row of VECTOR.specs) {
    it(`QA37-K3 ${row.id}: ${row.behaviour}`, () => {
      const parent = parentWith(row.parent.policy, row.parent.extModel);
      const decision = childGrants(parent.spec, {}, 1);
      if (decision.grants === null) return expect.fail('expected a grant');
      const child = childRunSpec(
        parent,
        decision.grants,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        row.model ?? undefined,
      );
      expect(child.spec.model.policy).to.deep.equal(row.expect.policy);
      expect(pipeshub(child.spec)?.model ?? null).to.deep.equal(row.expect.extModel);
      // Everything but the model is the parent's, whatever the role set.
      expect({ ...child.spec.model, policy: null }).to.deep.equal({ ...parent.spec.model, policy: null });
    });
  }
});

describe('MA1 the cost ceiling of a spawn’s roles (QA37-U7)', () => {
  const ceilings = { default: 20, researcher: 5, jira_agent: 2 };
  const cases: Array<[string, unknown, string[], number | null]> = [
    ['no ceilings: none', null, ['researcher', 'default'], null],
    ['the role’s own bound', ceilings, ['researcher', 'default'], 5],
    ['a role without a bound takes `default`', ceilings, ['writer', 'default'], 20],
    ['the least of the domain agent’s and the role’s', ceilings, ['jira_agent', 'researcher', 'default'], 2],
    ['`default` bounds a spawn that names no role key', ceilings, [], 20],
    ['no `default` and no bound for the role: none', { researcher: 5 }, ['writer', 'default'], null],
  ];
  for (const [name, value, keys, expected] of cases) {
    it(`QA37-U7: ${name}`, () => {
      expect(roleCostBound(value, keys)).to.equal(expected);
    });
  }

  it('QA37-U7: the bound of one key is the order’s own (`minMapBound`), so there is one rule', () => {
    for (const key of ['researcher', 'writer', 'default', 'jira_agent']) {
      expect(roleCostBound(ceilings, [key])).to.equal(minMapBound(ceilings, key));
    }
  });
});
