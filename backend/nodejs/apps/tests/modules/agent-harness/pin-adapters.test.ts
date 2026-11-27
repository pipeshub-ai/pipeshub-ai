/**
 * CPD-5 the pin's small adapters: where the snapshot's layers come from in
 * each policy-store mode, the head read on a closed connection, the metric,
 * and the Labs cache when the head cannot be read.
 */
import { expect } from 'chai';
import { HeadCachedRolloutSettings } from '../../../src/modules/agent-harness/adapters/configuration/head-cached-rollout-settings';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { prometheusPinShadowMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/pin-shadow-metrics';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import { configuredFamilies, configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { MongoHeadRevisions } from '../../../src/modules/control-plane';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';

const ORG = 'org-adapters';
const layer = (kind: PolicyLayer['scope']['kind'], maxTurns: number): PolicyLayer => ({
  schemaVersion: 1,
  scope: { kind },
  budgets: { maxTurns },
});
const LABS: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };

describe('CPD-5 pin adapters', () => {
  it('the loader keeps the subject’s scopes of the org’s layers and folds the Labs switches', async () => {
    const heads = new InMemoryHeadRevisions();
    heads.commit(ORG);
    const load = policySnapshotLoader({
      heads,
      layers: async () =>
        new Map([
          [`org:${ORG}`, layer('org', 9)],
          ['agent:a7', layer('agent', 5)],
          ['agent:other', layer('agent', 1)],
        ]),
      rollout: { read: async () => LABS },
    });
    const snapshot = await load(ORG, 'a7', 'u3');
    expect(snapshot.headRevision).to.equal(1);
    expect([...snapshot.layers.keys()]).to.deep.equal([`org:${ORG}`, 'agent:a7']);
    expect(snapshot.layers.get(`org:${ORG}`)).to.deep.include({
      budgets: { maxTurns: 9 },
      rollout: { ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false },
    });
    const bare = await policySnapshotLoader({ heads })(ORG, null, 'u3');
    expect(bare.layers.size).to.equal(0);
  });

  describe('CPD-10: what routing reads of a snapshot', () => {
    const loaded = async (modelFamilies: string[] | null, stored: Record<string, unknown> = {}, aiModels: unknown = null) => {
      const heads = new InMemoryHeadRevisions();
      const snapshot = await policySnapshotLoader({
        heads,
        layers: async () => new Map([[`org:${ORG}`, { ...layer('org', 9), ...stored } as PolicyLayer]]),
        rollout: { read: async () => ({ ...LABS, modelFamilies }) },
        models: { read: async () => configuredModels(aiModels) },
      })(ORG, 'a7', 'u3');
      return { snapshot, rollout: snapshot.layers.get(`org:${ORG}`)?.rollout };
    };

    it('the org’s model families are its rollout.durable.modelFamilies, lower-case and once each; null lists none', async () => {
      expect((await loaded([' OpenAI', 'anthropic', 'openai'])).rollout?.durable).to.deep.equal({ modelFamilies: ['openai', 'anthropic'] });
      expect((await loaded([])).rollout?.durable).to.deep.equal({ modelFamilies: [] });
      expect((await loaded(null)).rollout).to.deep.equal({ ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false });
    });

    it('a family no provider could be named by is left out: it could match none', async () => {
      expect((await loaded(['azure openai', 'openai'])).rollout?.durable).to.deep.equal({ modelFamilies: ['openai'] });
    });

    it('the families sit beside the routing lists the org stores, and take none of them', async () => {
      const { rollout } = await loaded(['openai'], { rollout: { durable: { modes: ['quick'], modelFamilies: ['stored'] }, ENABLE_SKILLS: false } });
      expect(rollout).to.deep.equal({
        ENABLE_SKILLS: false,
        ENABLE_AGENT_HARNESS: true,
        AGENT_HARNESS_KILL_SWITCH: false,
        durable: { modes: ['quick'], modelFamilies: ['openai'] },
      });
      expect((await loaded(null, { rollout: { durable: { modes: ['quick'] } } })).rollout?.durable).to.deep.equal({ modes: ['quick'] });
    });

    it('the snapshot keeps each configured model key’s family, of every kind, and the default LLM’s; nothing that reaches a model', async () => {
      const aiModels = {
        llm: [
          { modelKey: 'mk-1', provider: 'openAI', configuration: { model: 'gpt-x', apiKey: 'sk-secret' } },
          { modelKey: 'mk-2', provider: ' Anthropic ', isDefault: true, configuration: { model: 'claude-x' } },
          { modelKey: 'mk-bare', configuration: { model: 'm' } },
        ],
        embedding: [{ modelKey: 'emb-1', provider: 'cohere' }, { modelKey: 'mk-1', provider: 'shadowed' }, 'junk', null],
        ocr: 'none',
      };
      const { snapshot } = await loaded(null, {}, aiModels);
      expect(snapshot.families).to.deep.equal({
        // Only the llm bucket names chat models (#4261): an embedding key routes as an unknown model.
        byKey: new Map([['mk-1', 'openai'], ['mk-2', 'anthropic'], ['mk-bare', null]]),
        ofDefault: 'anthropic',
      });
      expect(JSON.stringify([...snapshot.families.byKey])).to.not.contain('sk-secret');
      expect(configuredFamilies({ llm: [{ modelKey: 'first', provider: 'gemini' }] }).ofDefault).to.equal('gemini');
      expect(configuredFamilies(null)).to.deep.equal({ byKey: new Map(), ofDefault: null });
      expect((await policySnapshotLoader({ heads: new InMemoryHeadRevisions() })(ORG, null, 'u3')).families).to.deep.equal({ byKey: new Map(), ofDefault: null });
    });
  });

  it('MongoHeadRevisions fails at once on a closed connection (no buffered wait)', async () => {
    const error = await new MongoHeadRevisions().head(ORG).catch((e: unknown) => e);
    expect((error as Error).message).to.match(/not connected/);
    const both = await new MongoHeadRevisions().heads(ORG).catch((e: unknown) => e);
    expect((both as Error).message).to.match(/not connected/);
  });

  it('the Labs cache reads the document when the head cannot be read, as before', async () => {
    const heads = new InMemoryHeadRevisions();
    heads.unreadable = true;
    let reads = 0;
    const labs = new HeadCachedRolloutSettings({ read: async () => ((reads += 1), LABS) }, heads);
    await labs.read(ORG);
    await labs.read(ORG);
    expect(reads).to.equal(2);
  });

  it('the Labs cache expires after its TTL even when the head did not move', async () => {
    const heads = new InMemoryHeadRevisions();
    let now = 0;
    let reads = 0;
    const labs = new HeadCachedRolloutSettings({ read: async () => ((reads += 1), LABS) }, heads, 30_000, () => now);
    await labs.read(ORG);
    now = 30_000;
    await labs.read(ORG);
    expect(reads).to.equal(2);
  });

  it('a mismatch is counted under its dimension in pipeshub_cp_shadow_mismatch_total', async () => {
    prometheusPinShadowMetrics.mismatch('budgets');
    expect(await metricsBackend.serialize()).to.match(/pipeshub_cp_shadow_mismatch_total\{dimension="budgets"\} [1-9]/);
  });
});
