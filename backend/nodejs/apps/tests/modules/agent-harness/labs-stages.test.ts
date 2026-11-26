/**
 * CPD-6 CPD6-15: the Labs stages a pin holds. The four the durable worker
 * reads follow the shared vector (`labs-stages.json`, read by Python's
 * `read_platform_feature_flag` too); the three Node reads follow Node's own
 * effective flags.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import sinon from 'sinon';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import {
  AiModelsConfiguredLlms,
  PLATFORM_LABS_REGISTRY,
  PlatformLabsStages,
} from '../../../src/modules/agent-harness/adapters/configuration/platform-pin-sources';
import { labsStages, WORKER_STAGES } from '../../../src/modules/agent-harness/domain/labs-stages';
import { policyLayerSchema } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import { singleStoreOrgSettings } from '../../../src/modules/agent-harness/adapters/configuration/single-store-org-settings';
import { without } from './support/without';

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/labs-stages.json'), 'utf8'),
) as { stages: string[]; cases: Array<{ id: string; settings: unknown; worker: Record<string, boolean> }> };

describe('CPD-6 Labs stages in the pin (CPD6-15)', () => {
  it('the vector names exactly the stages the worker reads', () => {
    expect(VECTORS.stages).to.deep.equal([...WORKER_STAGES]);
  });

  for (const vector of VECTORS.cases) {
    it(`${vector.id}: the worker's stages of ${JSON.stringify(vector.settings)}`, () => {
      const stages = labsStages(vector.settings, PLATFORM_LABS_REGISTRY) as Record<string, boolean>;
      expect(Object.fromEntries(WORKER_STAGES.map((key) => [key, stages[key]]))).to.deep.equal(vector.worker);
    });
  }

  it('the stages Node reads are Node’s effective flags; retired flags are no stage (CTL-5); the result is a rollout section of a policy layer', () => {
    const stages = labsStages(
      { featureFlagsVersion: 2, featureFlags: { ENABLE_SCHEDULED_RUNS: true, ENABLE_COLLABORATOR_TURNS: true, ENABLE_AGENT_ACTIVITY_PANEL: false } },
      PLATFORM_LABS_REGISTRY,
    );
    expect(stages).to.include({ ENABLE_SCHEDULED_RUNS: true });
    for (const retired of ['ENABLE_COLLABORATOR_TURNS', 'ENABLE_AGENT_ACTIVITY_PANEL', 'ENABLE_FOLLOWUP_SUGGESTIONS', 'ENABLE_DURABLE_ASSISTANT_AGENT']) {
      expect(stages).to.not.have.property(retired);
    }
    expect(() => policyLayerSchema.parse({ schemaVersion: 1, scope: { kind: 'org' }, rollout: stages })).not.to.throw();
  });

  describe('the stores behind a pin', () => {
    beforeEach(() => {
      sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
      sinon.stub(EncryptionService, 'getInstance').returns({ decrypt: (value: string) => value } as never);
    });
    afterEach(() => sinon.restore());

    it('PlatformLabsStages reads the stored settings document', async () => {
      const kv = { get: async () => JSON.stringify({ featureFlags: { ENABLE_SKILLS: false } }) };
      expect((await new PlatformLabsStages(singleStoreOrgSettings(kv as never)).read('org-1')).ENABLE_SKILLS).to.equal(false);
    });

    it('AiModelsConfiguredLlms reads the stored models without what reaches them; none stored: none', async () => {
      const stored = { llm: [{ modelKey: 'mk-1', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-x', apiKey: 'k' } }] };
      const read = await new AiModelsConfiguredLlms(singleStoreOrgSettings({ get: async () => JSON.stringify(stored) } as never)).read('org-1');
      expect(read.llms.map((model) => without(model, 'configDigest'))).to.deep.equal([
        { modelKey: 'mk-1', provider: 'openai', modelNames: ['gpt-x'], isDefault: true },
      ]);
      expect(read.families).to.deep.equal({ byKey: new Map([['mk-1', 'openai']]), ofDefault: 'openai' });
      expect(await new AiModelsConfiguredLlms(singleStoreOrgSettings({ get: async () => null } as never)).read('org-1')).to.deep.equal({
        llms: [],
        families: { byKey: new Map(), ofDefault: null },
      });
    });
  });
});
