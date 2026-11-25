/**
 * CPD-6 the live layer Node answers (02-lld §8.3, live-layer.schema.json):
 * which stored layers narrow which runs, and what of a layer leaves Node.
 */
import { expect } from 'chai';
import { liveLayerOf, liveLayerSchema, type PolicyLayer } from '../../../src/modules/control-plane';
import { ORG_A } from './support/cp-objects';
import { aLayer } from './support/resolution-fixtures';

const live = (sections: Record<string, unknown>) => ({ schemaVersion: 1, scope: { kind: 'live' }, ...sections });
const SWITCH_OFF = { layer: live({ rollout: { AGENT_HARNESS_KILL_SWITCH: false } }) };

const of = (layers: Record<string, PolicyLayer>, killSwitch = false, revision = 12) =>
  liveLayerOf({ orgId: ORG_A, revision, layers: new Map(Object.entries(layers)), killSwitch });

describe('CPD-6 liveLayerOf', () => {
  it('an org that stored nothing: the kill switch as it stands, at the head, valid against the contract', () => {
    const layer = of({}, false, 7);
    expect(layer).to.deep.equal({ schemaVersion: 1, orgId: ORG_A, revision: 7, layers: [SWITCH_OFF], revokedBindings: [] });
    expect(liveLayerSchema.safeParse(layer).success).to.equal(true);
  });

  it('NTF-11: the kill switch on is in the layer every run of the org reads', () => {
    expect(of({}, true).layers).to.deep.equal([{ layer: live({ rollout: { AGENT_HARNESS_KILL_SWITCH: true } }) }]);
  });

  it("the deployment's and the org's layers narrow every run; the deployment's and the org's layers for an agent narrow that agent's runs", () => {
    const layer = of({
      deployment: aLayer('deployment', { approvals: { writes: 'ask' } }),
      'deployment:agent:a7': aLayer('deployment', { approvals: { tools: { 'jira@i1/create_issue': 'deny' } } }),
      [`org:${ORG_A}`]: aLayer('org', { approvals: { writes: 'deny', flow: { level: 'deny' } } }, ORG_A),
      'agent:a7': aLayer('agent', { approvals: { classes: { execute: 'deny' } } }, 'a7'),
      'agent:b2': aLayer('agent', { budgets: { maxCostUsd: 1 } }, 'b2'),
    });

    expect(layer.layers).to.deep.equal([
      SWITCH_OFF,
      { agentKey: 'a7', layer: live({ approvals: { classes: { execute: 'deny' } } }) },
      { agentKey: 'b2', layer: live({ budgets: { maxCostUsd: 1 } }) },
      { layer: live({ approvals: { writes: 'ask' } }) },
      { agentKey: 'a7', layer: live({ approvals: { tools: { 'jira@i1/create_issue': 'deny' } } }) },
      { layer: live({ approvals: { writes: 'deny', flow: { level: 'deny' } } }) },
    ]);
    expect(liveLayerSchema.safeParse(layer).success).to.equal(true);
  });

  it("the deployment's flow rule (its own layer in the pin) narrows every run, never read as an agent's", () => {
    const layer = of({ 'deployment:flow': aLayer('deployment', { approvals: { flow: { level: 'deny', above: 'private' } } }) });

    expect(layer.layers).to.deep.equal([SWITCH_OFF, { layer: live({ approvals: { flow: { level: 'deny', above: 'private' } } }) }]);
  });

  it('keeps what narrows (approvals, engine profile, budgets, model routes and access rows) and nothing else of a layer', () => {
    const stored = aLayer(
      'org',
      {
        approvals: { writes: 'ask' },
        engineProfile: { mode: 'plan' },
        budgets: { maxTurns: 5 },
        models: { routes: ['openai:gpt-x'], primary: 'openai:gpt-x', roles: { planner: 'openai:gpt-x' }, disabledRules: ['row-1'], consumerPlans: false },
        rollout: { ENABLE_SKILLS: false, AGENT_HARNESS_KILL_SWITCH: true },
        memory: { recall: true },
        retention: { days: 30 },
        lockedBy: { approvals: 'org' },
        updated: { approvals: { at: 1, by: 'p_u1' } },
      },
      ORG_A,
    );

    const [, narrowed] = of({ [`org:${ORG_A}`]: stored }).layers;

    expect(narrowed).to.deep.equal({
      layer: live({
        approvals: { writes: 'ask' },
        engineProfile: { mode: 'plan' },
        budgets: { maxTurns: 5 },
        models: { routes: ['openai:gpt-x'], disabledRules: ['row-1'], consumerPlans: false },
      }),
    });
    expect(JSON.stringify(of({ [`org:${ORG_A}`]: stored }))).to.not.match(/p_u1/);
  });

  for (const [what, scope, layer] of [
    ["a user's layer (choices only)", 'user:u3', aLayer('user', { models: { primary: 'openai:gpt-x' } }, 'u3')],
    ["another org's layer", 'org:org-b', aLayer('org', { approvals: { writes: 'deny' } }, 'org-b')],
    ['a layer holding only what does not narrow', `org:${ORG_A}`, aLayer('org', { models: { primary: 'openai:gpt-x' }, retention: { days: 30 } }, ORG_A)],
  ] as const) {
    it(`${what} is not in the live layer`, () => {
      expect(of({ [scope]: layer }).layers).to.deep.equal([SWITCH_OFF]);
    });
  }

  it('the same layers answer the same bytes whatever order they were read in', () => {
    const a = aLayer('agent', { approvals: { writes: 'ask' } }, 'a7');
    const o = aLayer('org', { approvals: { writes: 'deny' } }, ORG_A);
    expect(JSON.stringify(of({ 'agent:a7': a, [`org:${ORG_A}`]: o }))).to.equal(JSON.stringify(of({ [`org:${ORG_A}`]: o, 'agent:a7': a })));
  });
});
