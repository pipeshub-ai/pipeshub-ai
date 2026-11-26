/**
 * MA1 (QA37-X2's source, design D8): `GET /api/v1/agent-harness/model-accounts/access`
 * lists what an agent's editor may choose for the agent and its sub-agent
 * roles: every configured model as its `models[]` entries name it, its release
 * price, whether the org's stored routes allow it, and the org's cost ceilings
 * per role. The real router; every answer is held to the `ModelAccessView` contract.
 */
import { expect } from 'chai';
import { AGENT_HARNESS_PIN_SNAPSHOTS } from '../../../src/modules/agent-harness/binders/control-plane-pin';
import { AGENT_HARNESS_MODEL_ACCESS } from '../../../src/modules/agent-harness/composition';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import type { SnapshotSource } from '../../../src/modules/control-plane';
import { aLayer, aSnapshot } from '../control-plane/support/resolution-fixtures';
import { InMemoryConfiguredModels } from './support/model-access';
import { Harness, startHarness } from './support/model-accounts';

const ACCESS = '/api/v1/agent-harness/model-accounts/access';
const ORG = 'org-1';

describe('MA1 model choices in the model-access view (QA37-X2 source)', () => {
  let h: Harness;
  let asked: Array<[string, string | null, string]>;

  beforeEach(async () => {
    h = await startHarness();
    asked = [];
    h.models.entries = [
      { provider: 'openAI', modelKey: 'mk-a', modelNames: ['gpt-5.4', 'gpt-5.4-mini'], isDefault: true },
      { provider: 'Anthropic', modelKey: 'mk-b', modelNames: ['claude-haiku-4-5'] },
      { provider: 'ollama', modelKey: 'mk-d', modelNames: ['house-model'] },
      { provider: 'azureOpenAI', modelKey: 'mk-e', modelNames: [] },
    ];
  });
  afterEach(() => h.close());

  /** The pin's snapshot source, as `CONTROL_PLANE_PIN=shadow|on` binds it. */
  const pinSource = (models: Record<string, unknown> | null, fail = false): void => {
    const source: Pick<SnapshotSource, 'snapshot'> = {
      snapshot: async (orgId, agentKey, actorId) => {
        asked.push([orgId, agentKey, actorId]);
        if (fail) throw new Error('store away');
        return aSnapshot({ layers: models === null ? {} : { [`org:${orgId}`]: aLayer('org', { models }) } });
      },
    };
    h.container.bind(AGENT_HARNESS_PIN_SNAPSHOTS).toConstantValue(source);
  };

  // With no region pin source, a choice says nothing of where it is served.
  const ALL = [
    { modelKey: 'mk-a', modelName: 'gpt-5.4', provider: 'openAI', usdPerMTok: 17.5, allowed: true },
    { modelKey: 'mk-a', modelName: 'gpt-5.4-mini', provider: 'openAI', usdPerMTok: 5.25, allowed: true },
    { modelKey: 'mk-b', modelName: 'claude-haiku-4-5', provider: 'Anthropic', usdPerMTok: 6, allowed: true },
    { modelKey: 'mk-d', modelName: 'house-model', provider: 'ollama', usdPerMTok: null, allowed: true },
  ];

  it('no pin source (`off`): every configured model, as configured, allowed, with its price or null; no ceilings', async () => {
    const read = await h.call('GET', ACCESS);
    expect(read.status, read.text).to.equal(200);
    expect(read.json.modelChoices).to.deep.equal(ALL);
    expect(read.json.roleCostCeilings).to.deep.equal({});
    expect(read.json.rules).to.have.length.greaterThan(0);
  });

  it('a pin source and an org that stored nothing: the same, read for the caller’s org at org level', async () => {
    pinSource(null);
    const read = await h.call('GET', ACCESS);
    expect(read.json.modelChoices).to.deep.equal(ALL);
    expect(read.json.roleCostCeilings).to.deep.equal({});
    expect(asked).to.deep.equal([[ORG, null, 'u1']]);
  });

  it('the org’s stored routes mark what they leave out as not allowed (by route: the provider’s machine name)', async () => {
    pinSource({ routes: ['openai:gpt-5.4', 'anthropic:claude-haiku-4-5'], roleCostCeilings: { default: 20, researcher: 6 } });
    const read = await h.call('GET', ACCESS);
    expect(read.json.modelChoices.map((choice: { modelName: string; allowed: boolean }) => [choice.modelName, choice.allowed])).to.deep.equal([
      ['gpt-5.4', true],
      ['gpt-5.4-mini', false],
      ['claude-haiku-4-5', true],
      ['house-model', false],
    ]);
    // A choice is still what the editor saves into `models[]` or `roleModels`: provider and name as configured.
    expect(read.json.modelChoices[2]).to.deep.include({ modelKey: 'mk-b', provider: 'Anthropic' });
    expect(read.json.roleCostCeilings).to.deep.equal({ default: 20, researcher: 6 });
  });

  it('a policy that cannot be read leaves both members out; the vendor rules still answer', async () => {
    pinSource(null, true);
    const read = await h.call('GET', ACCESS);
    expect(read.status).to.equal(200);
    expect('modelChoices' in read.json).to.equal(false);
    expect('roleCostCeilings' in read.json).to.equal(false);
    expect(read.json.rules).to.have.length.greaterThan(0);
  });

  it('a deployment whose configured models cannot be listed leaves both members out', async () => {
    const withoutList = { find: (orgId: string, key: string | undefined) => new InMemoryConfiguredModels().find(orgId, key) };
    h.container.rebind(AGENT_HARNESS_MODEL_ACCESS).toConstantValue(new ModelAccessService({ offering: 'oss', models: withoutList }));
    const read = await h.call('GET', ACCESS);
    expect(read.status).to.equal(200);
    expect('modelChoices' in read.json).to.equal(false);
    expect('roleCostCeilings' in read.json).to.equal(false);
  });
});
