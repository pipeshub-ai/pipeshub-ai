/**
 * CPD-6 on CPD-5b's child pin: a child of a pinned parent is pinned from its
 * parent's pin and its own request alone (release ⊓ parent ⊓ request). What
 * the worker reads from a pin must survive that fold: the approvals with
 * their flow rules (none, one, two), the Labs stages and the model. Through
 * the production pinner, snapshot cache and loader.
 */
import { expect } from 'chai';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import { configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import {
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type PinSubject,
  type ResolvedRunConfig,
} from '../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';

const ORG = 'org-child';
const deployment = (approvals: PolicyLayer['approvals']): PolicyLayer => ({ schemaVersion: 1, scope: { kind: 'deployment' }, approvals });
const request = (sections: Partial<PolicyLayer> = {}): PolicyLayer => ({
  schemaVersion: 1,
  scope: { kind: 'request' },
  budgets: { activeTimeS: 600 },
  ...sections,
});
const STAGES = {
  ENABLE_SKILLS: false,
  ENABLE_USER_CONTEXT: true,
  ENABLE_KNOWLEDGE_PUBLICATIONS: false,
};
const MODELS = { llm: [{ modelKey: 'mk-1', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-x' } }] };

describe('CPD-6 a child’s pin holds what its worker reads (release ⊓ parent ⊓ request)', () => {
  let heads: InMemoryHeadRevisions;
  let layers: Map<string, PolicyLayer>;
  let aiModels: unknown;
  let pinner: PolicyRunConfigPinner<unknown>;

  const subject = (overrides: Partial<PinSubject> = {}): PinSubject => ({
    orgId: ORG,
    actorId: 'u3',
    agentKey: 'a7',
    projectId: null,
    chatMode: 'auto',
    surface: 'web',
    request: request(),
    ...overrides,
  });
  const pinned = async (overrides: Partial<PinSubject> = {}): Promise<ResolvedRunConfig> => {
    const pin = await pinner.pin(subject(overrides));
    if ('code' in pin) return expect.fail(`${pin.code} ${pin.dimension ?? ''} ${pin.message}`);
    return pin;
  };
  const child = (parent: ResolvedRunConfig, sections: Partial<PolicyLayer> = {}) =>
    pinner.pin(subject({ surface: 'child', parent, request: request({ budgets: { maxTurns: 3 }, ...sections }) }));
  const value = (pin: ResolvedRunConfig, path: string): unknown => pin.effectiveConfig.dimensions[path]?.value;

  beforeEach(() => {
    heads = new InMemoryHeadRevisions();
    layers = new Map();
    aiModels = MODELS;
    pinner = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({
        load: policySnapshotLoader({
          heads,
          layers: async () => layers,
          rollout: { read: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }) },
          stages: { read: async () => STAGES },
          models: { read: async () => configuredModels(aiModels) },
        }),
        heads,
      }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources({ modelAliases: ['pipeshub:org-default'] }),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    }) as PolicyRunConfigPinner<unknown>;
  });

  const FLOWS: Array<[string, () => void, unknown]> = [
    ['no flow rule', () => layers.set('deployment', deployment({ writes: 'ask' })), undefined],
    [
      'one flow rule',
      () => layers.set('deployment:flow', deployment({ flow: { level: 'ask', above: 'public' } })),
      [{ level: 'ask', above: 'public' }],
    ],
    [
      'two flow rules',
      () => {
        layers.set('deployment:flow', deployment({ flow: { level: 'ask', above: 'public' } }));
        layers.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, approvals: { writes: 'ask', flow: { level: 'deny', above: 'private' } } });
      },
      [
        { level: 'ask', above: 'public' },
        { level: 'deny', above: 'private' },
      ],
    ],
  ];

  for (const [name, arrange, flow] of FLOWS) {
    it(`a child of a parent with ${name} is pinned with its parent’s approvals`, async () => {
      arrange();
      const parent = await pinned();
      expect((value(parent, 'approvals') as { flow?: unknown }).flow).to.deep.equal(flow);
      const pin = await child(parent);
      if ('code' in pin) return expect.fail(`${pin.code} ${pin.dimension ?? ''} ${pin.message}`);
      expect(value(pin, 'approvals')).to.deep.equal(value(parent, 'approvals'));
    });
  }

  it('a child may narrow its parent’s approvals and never loosen them', async () => {
    layers.set('deployment:flow', deployment({ flow: { level: 'ask', above: 'public' } }));
    layers.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, approvals: { writes: 'ask' } });
    const parent = await pinned();
    const narrowed = await child(parent, { approvals: { writes: 'deny' } });
    if ('code' in narrowed) return expect.fail(narrowed.code);
    expect(value(narrowed, 'approvals')).to.deep.include({
      classes: { read: 'allow', workspace: 'allow', write: 'deny', execute: 'deny' },
      flow: [{ level: 'ask', above: 'public' }],
    });
    const looser = await child(parent, { approvals: { writes: 'allow' } });
    if ('code' in looser) return expect.fail(looser.code);
    expect(value(looser, 'approvals'), 'approvals meet: the looser request changes nothing').to.deep.equal(value(parent, 'approvals'));
  });

  it('a child holds its parent’s Labs stages, whatever the settings say now', async () => {
    const parent = await pinned();
    STAGES.ENABLE_SKILLS = true;
    heads.commit(ORG);
    try {
      const pin = await child(parent);
      if ('code' in pin) return expect.fail(`${pin.code} ${pin.dimension ?? ''}`);
      for (const path of Object.keys(parent.effectiveConfig.dimensions).filter((p) => p.startsWith('rollout.'))) {
        expect(value(pin, path), path).to.deep.equal(value(parent, path));
      }
      expect(value(pin, 'rollout.ENABLE_SKILLS')).to.equal(false);
    } finally {
      STAGES.ENABLE_SKILLS = false;
    }
  });

  it('a child holds its parent’s model, not the deployment’s default of the moment', async () => {
    const parent = await pinned();
    expect(parent.llm?.model).to.equal('gpt-x');
    aiModels = { llm: [{ modelKey: 'mk-2', provider: 'anthropic', isDefault: true, configuration: { model: 'claude-z' } }] };
    heads.commit(ORG);
    const pin = await child(parent);
    if ('code' in pin) return expect.fail(`${pin.code} ${pin.dimension ?? ''}`);
    expect(pin.llm).to.deep.equal(parent.llm);
    expect(value(pin, 'models.routes')).to.deep.equal(value(parent, 'models.routes'));
  });

  it('a child that names a model of its own is looked up among the configured models, as a root is', async () => {
    aiModels = { llm: [...MODELS.llm, { modelKey: 'mk-2', provider: 'anthropic', configuration: { model: 'claude-z' } }] };
    const parent = await pinned();
    const own = await pinner.pin(subject({ surface: 'child', parent, request: request({ budgets: { maxTurns: 3 } }), model: { modelKey: 'mk-2' } }));
    if ('code' in own) return expect.fail(`${own.code} ${own.dimension ?? ''}`);
    expect(own.llm).to.deep.include({ modelKey: 'mk-2', provider: 'anthropic', model: 'claude-z' });
    const gone = await pinner.pin(subject({ surface: 'child', parent, request: request({ budgets: { maxTurns: 3 } }), model: { modelKey: 'mk-gone' } }));
    expect(gone).to.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary' });
  });
});
