/**
 * QA2-11 (MAC-13, R2 F20): the account kind an engine run is admitted on
 * narrows its profile's `accountKinds`, and the decision keeps that profile.
 * `ModelAccessService` over the real vault (in-memory key-value store) and
 * the shipped signed vendor rules.
 */
import { expect } from 'chai';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import {
  AccessSubject,
  ModelAccessService,
} from '../../../src/modules/agent-harness/application/model-access-service';
import {
  PinnedEngineProfile,
  profileDigest,
  RELEASE_ENGINE_PROFILES,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessEngineProfileRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { agentDefinition } from './support/agent-definition';
import { InMemoryConfiguredModels, testModelAccess } from './support/model-access';
import { KEYS, MemoryKv, platformCipher } from './support/model-accounts';

const RELEASE = RELEASE_ENGINE_PROFILES[0]!;
const OWN_KEY = { accountId: 'users/user-1/openAI', modelName: 'gpt-5' };
const CLOUD = { accountId: 'cloud/acc-1', modelKey: 'm-azure', modelName: 'gpt-5' };
const CLOUD_AGENT = agentDefinition({
  models: [{ modelKey: 'm-azure', modelName: 'gpt-5', provider: 'azureOpenAI', isReasoning: false }],
  modelAccountId: CLOUD.accountId,
});

describe('QA-2 MAC-13: the account kind a run is admitted on narrows its profile', () => {
  let service: ModelAccessService;

  const subject = (
    model: Partial<AccessSubject['model']> = {},
    engineProfile: PinnedEngineProfile | null = RELEASE,
  ): AccessSubject => ({
    orgId: 'org-1',
    actorId: 'user-1',
    // SEC-C-02: an org cloud account is reached only as the agent's editor pinned it.
    definition: model.accountId === CLOUD.accountId ? CLOUD_AGENT : null,
    model: { chatMode: 'auto', ...model },
    engineProfile,
  });

  beforeEach(async () => {
    const vault = new ModelAccountVault(new MemoryKv(), platformCipher);
    await vault.put(
      'org-1',
      { kind: 'user', userId: 'user-1' },
      'openAI',
      {
        kind: 'user_api_key',
        provider: 'openAI',
        label: null,
        material: { provider: 'openAI', apiKey: KEYS.openAI },
        hint: KEYS.openAI,
      },
      'user-1',
    );
    await vault.put(
      'org-1',
      { kind: 'org' },
      'acc-1',
      {
        kind: 'org_cloud',
        provider: 'azureOpenAI',
        label: 'EU',
        material: {
          provider: 'azureOpenAI',
          apiKey: KEYS.azure,
          endpoint: 'https://x.openai.azure.com',
          deploymentName: 'gpt-5',
        },
        hint: KEYS.azure,
      },
      'admin',
    );
    service = testModelAccess({
      accounts: vault,
      models: new InMemoryConfiguredModels([
        ...new InMemoryConfiguredModels().entries,
        { provider: 'azureOpenAI', modelKey: 'm-azure', modelNames: ['gpt-5'] },
      ]),
    });
  });

  for (const [kind, model] of [
    ['org_api_key', {}],
    ['org_cloud', CLOUD],
  ] as const) {
    it(`QA2-11 a run admitted on ${kind} holds its profile narrowed to that kind, under another digest`, async () => {
      expect(RELEASE.accountKinds).to.include.members(['org_api_key', 'user_api_key', 'org_cloud']);
      const decision = await service.decide(subject(model));
      expect(decision?.access.access.accountKind).to.equal(kind);
      expect(decision?.engineProfile).to.deep.equal({ ...RELEASE, accountKinds: [kind] });
      expect(profileDigest(decision!.engineProfile)).to.not.equal(profileDigest(RELEASE));
      expect(await service.admit(subject(model))).to.deep.equal(decision?.access);
    });
  }

  it('QA2-11 a profile that admits the kind alone is decided unchanged', async () => {
    const narrowed = { ...RELEASE, accountKinds: ['org_api_key' as const] };
    const decision = await service.decide(subject({}, narrowed));
    expect(decision?.engineProfile).to.deep.equal(narrowed);
    expect(profileDigest(decision!.engineProfile)).to.equal(profileDigest(narrowed));
  });

  it('QA2-11 a kind the profile does not admit is refused naming accountKinds and the request', async () => {
    let refused: unknown = null;
    try {
      await service.decide(subject({}, { ...RELEASE, accountKinds: ['user_api_key'] }));
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessEngineProfileRefusedError);
    expect(refused).to.include({ code: 'ENGINE_PROFILE_WIDENING', dimension: 'accountKinds', layer: 'request' });
  });

  it("QA2-11 a person's key named by the request is no account here, though one is stored: refused account_missing", async () => {
    let refused: unknown = null;
    try {
      await service.decide(subject(OWN_KEY));
    } catch (error) {
      refused = error;
    }
    expect(refused).to.include({ code: 'account_missing' });
  });

  it('QA2-11 a loop run has no profile to narrow: nothing is decided', async () => {
    expect(await service.decide(subject({}, null))).to.equal(null);
  });
});
