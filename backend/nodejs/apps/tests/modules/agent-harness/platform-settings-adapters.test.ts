import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as configurationUtil from '../../../src/modules/configuration_manager/utils/util';
import {
  KNOWLEDGE_PUBLICATIONS_LABS_FLAG,
  PlatformKnowledgePublications,
} from '../../../src/modules/agent-harness/adapters/configuration/platform-rollout-settings';
import { AiModelsConfiguredModels } from '../../../src/modules/agent-harness/adapters/configuration/ai-models-configured-models';
import { InMemoryConfiguredModels } from './support/model-access';
import { singleStoreOrgSettings } from '../../../src/modules/agent-harness/adapters/configuration/single-store-org-settings';

const store = {} as never;

const platformFlags = (featureFlags: Record<string, boolean>) =>
  sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
    fileUploadMaxSizeBytes: 1,
    featureFlags,
    agentHarness: { modelFamilies: null },
  });

describe('agent-harness platform settings adapters', () => {
  afterEach(() => sinon.restore());

  for (const [label, flag, setting] of [
    ['knowledge publications (P2.13)', KNOWLEDGE_PUBLICATIONS_LABS_FLAG, () => new PlatformKnowledgePublications(singleStoreOrgSettings(store))],
  ] as const) {
    describe(label, () => {
      it('is on only when its Labs flag is exactly true', async () => {
        platformFlags({ [flag]: true });
        expect(await setting().enabled('org-1')).to.equal(true);
      });

      it('absent, false, or another flag on: off', async () => {
        const stub = platformFlags({});
        expect(await setting().enabled('org-1')).to.equal(false);
        stub.resolves({ fileUploadMaxSizeBytes: 1, featureFlags: { [flag]: false, ENABLE_AGENT_HARNESS: true }, agentHarness: { modelFamilies: null } });
        expect(await setting().enabled('org-1')).to.equal(false);
      });

      it('an unreadable store is an error, never "off"', async () => {
        sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').rejects(new Error('kv down'));
        let refused = false;
        try {
          await setting().enabled('org-1');
        } catch {
          refused = true;
        }
        expect(refused).to.equal(true);
      });
    });
  }

  describe('AiModelsConfiguredModels (A1): what the tests\' in-memory models stand in for', () => {
    const llm = [
      { provider: 'openAI', modelKey: 'm-default', isDefault: true, configuration: { model: 'gpt-5, gpt-5-mini ,' } },
      { provider: 'anthropic', modelKey: 'model-2', configuration: { model: 'claude-x' } },
      { provider: 'openAI', modelKey: 'no-names' },
      { provider: 7, modelKey: 'bad-provider', configuration: { model: 'x' } },
      'not an entry',
    ];
    const configured = (config: Record<string, unknown> | null) => {
      sinon.stub(configurationUtil, 'readStoredAiModelsConfig').resolves(config);
      return new AiModelsConfiguredModels(singleStoreOrgSettings(store));
    };

    it('finds an entry by key, its comma-separated model names trimmed', async () => {
      expect(await configured({ llm }).find('org-1', 'model-2')).to.deep.equal({
        provider: 'anthropic',
        modelKey: 'model-2',
        modelNames: ['claude-x'],
      });
    });

    it('without a key: the entry marked default, never the first', async () => {
      const reordered = [llm[1], llm[0]];
      expect(await configured({ llm: reordered }).find('org-1', undefined)).to.deep.equal({
        provider: 'openAI',
        modelKey: 'm-default',
        modelNames: ['gpt-5', 'gpt-5-mini'],
      });
    });

    it('MA1: lists every entry as configured (the provider as written, never a machine name), as the fake does', async () => {
      const listed = await configured({ llm }).list('org-1');
      expect(listed).to.deep.equal([
        { provider: 'openAI', modelKey: 'm-default', modelNames: ['gpt-5', 'gpt-5-mini'] },
        { provider: 'anthropic', modelKey: 'model-2', modelNames: ['claude-x'] },
        { provider: 'openAI', modelKey: 'no-names', modelNames: [] },
      ]);
      const fake = new InMemoryConfiguredModels(listed.map((entry) => ({ ...entry })));
      expect(await fake.list()).to.deep.equal(listed);
      sinon.restore();
      expect(await configured(null).list('org-1')).to.deep.equal([]);
    });

    it('MG-15: an entry carries the fields its region is derived from, never its key', async () => {
      const entries = [
        {
          provider: 'azureOpenAI',
          modelKey: 'm-azure',
          configuration: { model: 'gpt-4o', endpoint: 'https://swedencentral.api.cognitive.microsoft.com', apiKey: 'sk-test-not-real' },
        },
        { provider: 'bedrock', modelKey: 'm-eu', configuration: { model: 'claude-x', region: 'eu-west-1', awsAccessSecretKey: 'not-real' } },
      ];
      expect(await configured({ llm: entries }).list('org-1')).to.deep.equal([
        { provider: 'azureOpenAI', modelKey: 'm-azure', modelNames: ['gpt-4o'], location: { endpoint: 'https://swedencentral.api.cognitive.microsoft.com' } },
        { provider: 'bedrock', modelKey: 'm-eu', modelNames: ['claude-x'], location: { region: 'eu-west-1' } },
      ]);
    });

    it('answers as the in-memory fake does for the same entries', async () => {
      const fake = new InMemoryConfiguredModels();
      const real = configured({ llm });
      for (const key of [undefined, 'm-default', 'missing']) {
        const fromFake = await fake.find('org-1', key);
        const fromReal = await real.find('org-1', key);
        expect(fromReal === null, String(key)).to.equal(fromFake === null);
        if (fromReal && fromFake) expect(fromReal.modelKey).to.equal(fromFake.modelKey);
      }
    });

    it('no names configured: an empty list; a malformed entry or none stored: null', async () => {
      const models = configured({ llm });
      expect((await models.find('org-1', 'no-names'))?.modelNames).to.deep.equal([]);
      expect(await models.find('org-1', 'bad-provider')).to.equal(null);
      sinon.restore();
      expect(await configured(null).find('org-1', undefined)).to.equal(null);
      sinon.restore();
      expect(await configured({ llm: 'not a list' }).find('org-1', undefined)).to.equal(null);
    });
  });
});
