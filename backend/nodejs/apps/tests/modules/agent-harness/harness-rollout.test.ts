import { expect } from 'chai';
import sinon from 'sinon';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  assistantAgentLoopMode,
  ASSISTANT_AGENT_DEFINITION_KEY,
  assistantModesWith,
  CURRENT_HARNESS_MAJOR,
  DURABLE_ASSISTANT_CHAT_MODES,
  DURABLE_CHAT_MODES,
  HarnessRolloutSettings,
} from '../../../src/modules/agent-harness/domain/rollout';
import { PlatformHarnessRolloutSettings } from '../../../src/modules/agent-harness/adapters/configuration/platform-rollout-settings';
import { AiModelsFamilyResolver } from '../../../src/modules/agent-harness/adapters/configuration/ai-models-family-resolver';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import * as platformSettings from '../../../src/modules/configuration_manager/utils/util';
import { singleStoreOrgSettings } from '../../../src/modules/agent-harness/adapters/configuration/single-store-org-settings';

const READY = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

const rolloutWith = (
  settings: HarnessRolloutSettings,
  family: string | null = 'anthropic',
) => {
  const resolve = sinon.stub().resolves(family);
  return {
    resolve,
    rollout: new HarnessRollout(
      { read: sinon.stub().resolves(settings) },
      { resolve },
    ),
  };
};

const request = {
  orgId: 'org-1',
  model: { chatMode: 'auto', modelKey: 'model-1' },
};

describe('HarnessRollout', () => {
  describe('F1 flag matrix: org × model family allowlist × kill switch', () => {
    const cases = [true, false].flatMap((orgEnabled) =>
      [true, false].flatMap((familyAllowed) =>
        [false, true].map((killSwitch) => ({
          orgEnabled,
          familyAllowed,
          killSwitch,
        })),
      ),
    );

    for (const { orgEnabled, familyAllowed, killSwitch } of cases) {
      const expected = killSwitch
        ? 'kill_switch'
        : !orgEnabled
          ? 'org_disabled'
          : !familyAllowed
            ? 'model_family_not_allowed'
            : 'enabled';
      it(`org ${orgEnabled ? 'on' : 'off'}, family ${
        familyAllowed ? 'allowed' : 'not allowed'
      }, kill switch ${killSwitch ? 'on' : 'off'} -> ${expected}`, async () => {
        const { rollout } = rolloutWith(
          {
            infrastructureReady: true,
            killSwitch,
            orgEnabled,
            modelFamilies: familyAllowed ? ['Anthropic'] : ['openai'],
          },
          'anthropic',
        );

        expect(await rollout.decide(request)).to.deep.equal({
          useHarness: expected === 'enabled',
          harnessMajor: CURRENT_HARNESS_MAJOR,
          reason: expected,
        });
      });
    }
  });

  it('never admits without durable infrastructure', async () => {
    const { rollout } = rolloutWith({
      infrastructureReady: false,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: null,
    });
    expect((await rollout.decide(request)).reason).to.equal(
      'infrastructure_unavailable',
    );
  });

  describe('chat modes the durable loop runs', () => {
    const enabled = {
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: ['anthropic'],
    };
    for (const chatMode of ['auto', 'quick', 'react', ' React ', 'deep', 'DEEP']) {
      it(`admits ${JSON.stringify(chatMode)}`, async () => {
        const { rollout } = rolloutWith(enabled);
        const decided = await rollout.decide({
          ...request,
          model: { ...request.model, chatMode },
        });
        expect(decided.useHarness).to.equal(true);
      });
    }
    // planExecute never delegates: its phase loop has no durable director yet (K-L).
    for (const chatMode of ['planExecute', 'verification', 'something-new']) {
      it(`keeps ${JSON.stringify(chatMode)} on legacy without resolving the model`, async () => {
        const { rollout, resolve } = rolloutWith(enabled);
        const decided = await rollout.decide({
          ...request,
          model: { ...request.model, chatMode },
        });
        expect(decided).to.include({
          useHarness: false,
          reason: 'chat_mode_not_supported',
        });
        expect(resolve.called).to.equal(false);
      });
    }
    it('keeps the org-level reason when the org is off', async () => {
      const { rollout } = rolloutWith({ ...enabled, orgEnabled: false });
      const decided = await rollout.decide({
        ...request,
        model: { ...request.model, chatMode: 'deep' },
      });
      expect(decided.reason).to.equal('org_disabled');
    });
    it('matches the cross-language vector Python derives from MODE_CATALOG', () => {
      const vector = JSON.parse(
        readFileSync(
          join(
            __dirname,
            '../../../../../../contracts/harness/vectors/durable-chat-modes.json',
          ),
          'utf8',
        ),
      ) as { durableChatModes: string[]; assistantChatModes: string[] };
      expect([...DURABLE_CHAT_MODES].sort()).to.deep.equal(
        [...vector.durableChatModes].sort(),
      );
      expect([...DURABLE_ASSISTANT_CHAT_MODES].sort()).to.deep.equal(
        [...vector.assistantChatModes].sort(),
      );
    });
    it('CPD10-8: the modes are the release layer\'s, the one source a layer narrows', () => {
      expect(DURABLE_CHAT_MODES).to.equal(RELEASE_DEFAULTS.rollout.durable.modes);
      expect(DURABLE_ASSISTANT_CHAT_MODES).to.equal(
        RELEASE_DEFAULTS.rollout.durable.assistantModes,
      );
      expect(RELEASE_DEFAULTS.rollout.durable.modelFamilies).to.equal(null);
    });
  });

  describe('PC-2: the built-in assistant is admitted in its own chat modes', () => {
    const enabled = {
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: ['anthropic'],
    };
    for (const chatMode of ['internal_search', 'web_search', ' Web_Search ']) {
      it(`admits the assistant in ${JSON.stringify(chatMode)}; an agent run in it stays legacy`, async () => {
        const { rollout } = rolloutWith(enabled);
        const model = { ...request.model, chatMode };
        expect((await rollout.decide({ ...request, model, assistant: true })).useHarness).to.equal(true);
        expect(await rollout.decide({ ...request, model })).to.include({
          useHarness: false,
          reason: 'chat_mode_not_supported',
        });
      });
    }
    // `agent` and `agent:*` are Universal Agent Mode (legacy's agent route);
    // the others fall back onto a mode the pin would not record.
    for (const chatMode of ['agent', 'agent:react', 'quick', 'auto', 'react', 'standard', 'image', '']) {
      it(`keeps the assistant in ${JSON.stringify(chatMode)} on legacy`, async () => {
        const { rollout, resolve } = rolloutWith(enabled);
        const decided = await rollout.decide({
          ...request,
          model: { ...request.model, chatMode },
          assistant: true,
        });
        expect(decided).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
        expect(resolve.called).to.equal(false);
      });
    }
  });

  describe('AS1-2: the assistant\'s Agent mode runs durably only when the turn asks and the org allows it', () => {
    const enabled = {
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: null,
    };
    const orgOn = { ...enabled, assistantModes: assistantModesWith(true) };
    const agentTurn = { ...request, model: { ...request.model, chatMode: 'agent' }, assistant: true };
    const cases: Array<[string, HarnessRolloutSettings, boolean, boolean]> = [
      ['org off, not asked', enabled, false, false],
      ['org off, asked', enabled, true, false],
      ['org on, not asked', orgOn, false, false],
      ['org on, asked', orgOn, true, true],
    ];
    for (const [name, settings, durableRequested, durable] of cases) {
      it(`${name}: ${durable ? 'durable' : 'legacy chat_mode_not_supported'}`, async () => {
        const { rollout } = rolloutWith(settings);
        const decided = await rollout.decide({ ...agentTurn, durableRequested });
        expect(decided).to.include(
          durable ? { useHarness: true, reason: 'enabled' } : { useHarness: false, reason: 'chat_mode_not_supported' },
        );
      });
    }
    it('keeps a mode the durable loop does not run on legacy even when asked and allowed', async () => {
      const { rollout } = rolloutWith(orgOn);
      const decided = await rollout.decide({
        ...agentTurn,
        model: { ...request.model, chatMode: 'agent:planExecute' },
        durableRequested: true,
      });
      expect(decided).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
    });
    it('changes nothing for the search modes or for an agent route', async () => {
      const { rollout } = rolloutWith(orgOn);
      for (const durableRequested of [false, true]) {
        const search = await rollout.decide({
          ...request,
          model: { ...request.model, chatMode: 'internal_search' },
          assistant: true,
          durableRequested,
        });
        expect(search.useHarness).to.equal(true);
        const agentRoute = await rollout.decide({
          ...request,
          model: { ...request.model, chatMode: 'agent' },
          durableRequested,
        });
        expect(agentRoute).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
      }
    });
    it('the org setting adds agent to the release assistant modes and nothing else', () => {
      expect(assistantModesWith(false)).to.deep.equal(DURABLE_ASSISTANT_CHAT_MODES);
      expect(assistantModesWith(true)).to.deep.equal([...DURABLE_ASSISTANT_CHAT_MODES, 'agent']);
    });
  });

  describe('AS1-3: the loop mode legacy runs Universal Agent Mode in (parseChatMode)', () => {
    const cases: Array<[string, string | null]> = [
      ['agent', 'quick'],
      [' Agent ', 'quick'],
      ['agent:deep', 'deep'],
      ['agent:', 'quick'],
      ['agent:planExecute', 'planExecute'],
      ['internal_search', null],
      ['web_search', null],
      ['quick', null],
    ];
    for (const [chatMode, loop] of cases) {
      it(`${JSON.stringify(chatMode)} -> ${JSON.stringify(loop)}`, () => {
        expect(assistantAgentLoopMode(chatMode)).to.equal(loop);
      });
    }
    it('names the system assistant definition as legacy names the universal agent', () => {
      expect(ASSISTANT_AGENT_DEFINITION_KEY).to.equal('agentIdPlaceholder');
    });
  });

  it('allows every family without an allowlist and skips resolving it', async () => {
    const { rollout, resolve } = rolloutWith({
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: null,
    });
    expect((await rollout.decide(request)).useHarness).to.equal(true);
    expect(resolve.called).to.equal(false);
  });

  it('keeps an unresolvable model on legacy when an allowlist is set', async () => {
    const { rollout, resolve } = rolloutWith(
      {
        infrastructureReady: true,
        killSwitch: false,
        orgEnabled: true,
        modelFamilies: ['anthropic'],
      },
      null,
    );
    expect(await rollout.decide(request)).to.deep.include({
      useHarness: false,
      reason: 'model_family_unknown',
    });
    expect(resolve.calledOnceWithExactly('org-1', request.model)).to.equal(
      true,
    );
  });

  it('reports org availability for the UI and keeps in-flight runs reachable under the kill switch', async () => {
    const { rollout } = rolloutWith({
      infrastructureReady: true,
      killSwitch: true,
      orgEnabled: true,
      modelFamilies: null,
    });
    expect(await rollout.decideForOrg('org-1')).to.deep.equal({
      useHarness: false,
      harnessMajor: CURRENT_HARNESS_MAJOR,
      reason: 'kill_switch',
      harnessOn: true,
      resumeInFlightRuns: true,
    });
  });

  // ADR-OPS-08: the org predicate is the Labs switch alone; readiness, the kill switch and the allowlist stop new
  // runs, never what the org sees. Reattaching needs the infrastructure as well.
  for (const [name, settings, harnessOn, resume] of [
    ['Labs off', { infrastructureReady: true, killSwitch: false, orgEnabled: false, modelFamilies: null }, false, false],
    ['Labs on', { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }, true, true],
    ['Labs on, infrastructure not ready', { infrastructureReady: false, killSwitch: false, orgEnabled: true, modelFamilies: null }, true, false],
    ['Labs on, an empty allowlist', { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: [] }, true, true],
    ['Labs off, kill switch', { infrastructureReady: true, killSwitch: true, orgEnabled: false, modelFamilies: null }, false, false],
  ] as const) {
    it(`ADR-OPS-08: ${name}: harnessOn ${String(harnessOn)}, reattach ${String(resume)}`, async () => {
      const { rollout } = rolloutWith(settings);
      const decided = await rollout.decideForOrg('org-1');
      expect(decided.harnessOn).to.equal(harnessOn);
      expect(decided.resumeInFlightRuns).to.equal(resume);
    });
  }

  it('does not advertise an org whose allowlist is empty', async () => {
    const { rollout } = rolloutWith({
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: [],
    });
    expect((await rollout.decideForOrg('org-1')).useHarness).to.equal(false);
  });
});

describe('PlatformHarnessRolloutSettings', () => {
  afterEach(() => sinon.restore());

  it('reads the Labs flag and allowlist on every call; the kill switch from env (CTL-5)', async () => {
    const read = sinon.stub(platformSettings, 'getPlatformSettingsFromStore');
    read.resolves({
      fileUploadMaxSizeBytes: 1,
      featureFlags: { ENABLE_AGENT_HARNESS: true },
      featureFlagOverrides: {},
      notices: {},
      agentHarness: { modelFamilies: ['anthropic'] },
    });
    const source = new PlatformHarnessRolloutSettings({} as never, { ...READY, AGENT_HARNESS_KILL_SWITCH: 'true' });

    expect(await source.read('org-1')).to.deep.equal({
      infrastructureReady: true,
      killSwitch: true,
      orgEnabled: true,
      modelFamilies: ['anthropic'],
      assistantModes: [...DURABLE_ASSISTANT_CHAT_MODES, 'agent'],
    });
    await source.read('org-1');
    expect(read.calledTwice).to.equal(true);
  });

  it('treats missing flags as off and missing readiness as unavailable', async () => {
    sinon.stub(platformSettings, 'getPlatformSettingsFromStore').resolves({
      fileUploadMaxSizeBytes: 1,
      featureFlags: {},
      featureFlagOverrides: {},
      notices: {},
      agentHarness: { modelFamilies: null },
    });
    expect(
      await new PlatformHarnessRolloutSettings({} as never, {}).read('org-1'),
    ).to.deep.equal({
      infrastructureReady: false,
      killSwitch: false,
      orgEnabled: false,
      modelFamilies: null,
      assistantModes: [...DURABLE_ASSISTANT_CHAT_MODES, 'agent'],
    });
  });
});

describe('AiModelsFamilyResolver', () => {
  afterEach(() => sinon.restore());

  const config = {
    llm: [
      { provider: 'openAI', modelKey: 'gpt', isDefault: false },
      { provider: 'anthropic', modelKey: 'claude', isDefault: true },
    ],
    reasoning: [{ provider: 'gemini', modelKey: 'thinker' }],
  };

  it('resolves the configured provider of the requested model key', async () => {
    sinon.stub(platformSettings, 'readStoredAiModelsConfig').resolves(config);
    const resolver = new AiModelsFamilyResolver(singleStoreOrgSettings({} as never));
    expect(
      await resolver.resolve('org-1', { chatMode: 'auto', modelKey: 'gpt' }),
    ).to.equal('openai');
    // Only an llm entry names a chat model (#4261, SYNC4 S13): another kind's key routes as an unknown model,
    // and the browser-sent provider is never read.
    expect(
      await resolver.resolve('org-1', {
        chatMode: 'auto',
        modelKey: 'thinker',
        modelProvider: 'anthropic',
      }),
    ).to.equal(null);
  });

  it('uses the default LLM when no model key is sent', async () => {
    sinon.stub(platformSettings, 'readStoredAiModelsConfig').resolves(config);
    expect(
      await new AiModelsFamilyResolver(singleStoreOrgSettings({} as never)).resolve('org-1', {
        chatMode: 'auto',
      }),
    ).to.equal('anthropic');
  });

  it('returns null for an unknown key or missing config', async () => {
    const read = sinon.stub(platformSettings, 'readStoredAiModelsConfig');
    read.resolves(config);
    const resolver = new AiModelsFamilyResolver(singleStoreOrgSettings({} as never));
    expect(
      await resolver.resolve('org-1', { chatMode: 'auto', modelKey: 'gone' }),
    ).to.equal(null);
    read.resolves(null);
    expect(await resolver.resolve('org-1', { chatMode: 'auto' })).to.equal(
      null,
    );
  });
});
