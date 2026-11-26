/**
 * UX1 LB-1..LB-10: Labs records only the flags an admin changed. A flag with
 * no override follows its release default; settings saved before this release
 * are read without a write and keep every effective value; a stored durable
 * harness `false` from before is kept and asks the admin once.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../src/libs/encryptor/encryptor';
import {
  PLATFORM_FEATURE_FLAGS,
  PlatformFeatureFlagDef,
} from '../../../src/modules/configuration_manager/constants/constants';
import {
  applyFlagChanges,
  DURABLE_AGENT_FLAG,
  effectiveFlags,
  FEATURE_FLAGS_VERSION,
  readFlagState,
} from '../../../src/modules/configuration_manager/utils/flag-overrides';
import { platformSettingsOf } from '../../../src/modules/configuration_manager/utils/util';
import {
  getAvailablePlatformFeatureFlags,
  setPlatformSettings,
} from '../../../src/modules/configuration_manager/controller/cm_controller';
import { platformSettingsSchema } from '../../../src/modules/configuration_manager/validator/validators';
import { testControlPlane } from '../control-plane/support/in-memory';
import { inRequestOrg } from '../../helpers/request-org-context';

const REGISTRY: PlatformFeatureFlagDef[] = [
  { key: 'ON_FLAG', label: 'On', defaultEnabled: true },
  { key: 'OFF_FLAG', label: 'Off', defaultEnabled: false },
  { key: DURABLE_AGENT_FLAG, label: 'Durable', defaultEnabled: true, previousDefault: false },
];

describe('Labs flag overrides (UX1)', () => {
  it('LB-1: nothing stored is every release default and no override', () => {
    const state = readFlagState({}, REGISTRY);
    expect(state.overrides).to.deep.equal({});
    expect(effectiveFlags(state.overrides, REGISTRY)).to.deep.equal({
      ON_FLAG: true,
      OFF_FLAG: false,
      [DURABLE_AGENT_FLAG]: true,
    });
    expect(state.notices).to.deep.equal({});
  });

  it('LB-2: a stored override wins; a flag without one follows the default, even one that changes', () => {
    const stored = { featureFlagsVersion: FEATURE_FLAGS_VERSION, featureFlags: { OFF_FLAG: true } };
    const state = readFlagState(stored, REGISTRY);
    expect(effectiveFlags(state.overrides, REGISTRY)).to.include({ OFF_FLAG: true, ON_FLAG: true });
    const nextRelease = REGISTRY.map((f) => (f.key === 'ON_FLAG' ? { ...f, defaultEnabled: false } : f));
    expect(effectiveFlags(readFlagState(stored, nextRelease).overrides, nextRelease).ON_FLAG).to.equal(false);
  });

  it('LB-3: a document from before keeps every effective value, for every flag and value', () => {
    for (const def of PLATFORM_FEATURE_FLAGS) {
      for (const value of [true, false]) {
        const stored = { featureFlags: { [def.key]: value } };
        const state = readFlagState(stored, PLATFORM_FEATURE_FLAGS);
        expect(state.legacy, def.key).to.equal(true);
        expect(effectiveFlags(state.overrides, PLATFORM_FEATURE_FLAGS)[def.key], def.key).to.equal(value);
        // A value equal to today's default is not reported as a change.
        expect(def.key in state.overrides, def.key).to.equal(value !== !!def.defaultEnabled);
      }
    }
  });

  it('LB-3 (CTL-5): a flag this release no longer lists is ignored, and the next save drops it', () => {
    const state = readFlagState({ featureFlags: { RETIRED_FLAG: true } }, REGISTRY);
    expect(state.overrides).to.deep.equal({});
  });

  it('LB-4: a stored durable "off" from before stays off and asks the admin once', () => {
    const state = readFlagState({ featureFlags: { [DURABLE_AGENT_FLAG]: false, ON_FLAG: true } }, REGISTRY);
    expect(state.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: false });
    expect(effectiveFlags(state.overrides, REGISTRY)[DURABLE_AGENT_FLAG]).to.equal(false);
    expect(state.notices).to.deep.equal({ durableAgentDefault: 'pending' });
  });

  it('LB-5: a stored durable "on", or none, asks nothing; a deliberate "off" saved after this release asks nothing', () => {
    expect(readFlagState({ featureFlags: { [DURABLE_AGENT_FLAG]: true } }, REGISTRY).notices).to.deep.equal({});
    expect(readFlagState({ featureFlags: { ON_FLAG: true } }, REGISTRY).notices).to.deep.equal({});
    const chosen = { featureFlagsVersion: 2, featureFlags: { [DURABLE_AGENT_FLAG]: false } };
    expect(readFlagState(chosen, REGISTRY).notices).to.deep.equal({});
  });

  it('LB-6: a save records only changes; a flag set to its default drops its override; the pending notice is carried', () => {
    const before = readFlagState(
      { featureFlags: { [DURABLE_AGENT_FLAG]: false, OFF_FLAG: true, ON_FLAG: true } },
      REGISTRY,
    );
    const after = applyFlagChanges(before, { featureFlags: { OFF_FLAG: false, ON_FLAG: false } }, REGISTRY);
    expect(after.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: false, ON_FLAG: false });
    expect(after.notices).to.deep.equal({ durableAgentDefault: 'pending' });
    expect(after.legacy).to.equal(false);
  });

  describe('LB-7: the notice’s three answers', () => {
    const pending = readFlagState({ featureFlags: { [DURABLE_AGENT_FLAG]: false } }, REGISTRY);
    it('"Use the new default" removes the override: durable chat is on', () => {
      const after = applyFlagChanges(pending, { notices: { durableAgentDefault: 'adopted' } }, REGISTRY);
      expect(after.overrides).to.deep.equal({});
      expect(effectiveFlags(after.overrides, REGISTRY)[DURABLE_AGENT_FLAG]).to.equal(true);
      expect(after.notices).to.deep.equal({ durableAgentDefault: 'adopted' });
    });
    it('"Keep it off" records the choice', () => {
      const after = applyFlagChanges(pending, { notices: { durableAgentDefault: 'kept' } }, REGISTRY);
      expect(after.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: false });
      expect(after.notices).to.deep.equal({ durableAgentDefault: 'kept' });
    });
    it('dismissing changes nothing but the notice, which never shows again', () => {
      const after = applyFlagChanges(pending, { notices: { durableAgentDefault: 'dismissed' } }, REGISTRY);
      expect(after.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: false });
      const stored = { featureFlagsVersion: 2, featureFlags: after.overrides, notices: after.notices };
      expect(readFlagState(stored, REGISTRY).notices.durableAgentDefault).to.equal('dismissed');
      const again = applyFlagChanges(readFlagState(stored, REGISTRY), { notices: { durableAgentDefault: 'adopted' } }, REGISTRY);
      expect(again.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: false });
    });
  });

  it('LB-8: turning the durable flag on or off in Labs answers the notice', () => {
    const pending = readFlagState({ featureFlags: { [DURABLE_AGENT_FLAG]: false } }, REGISTRY);
    expect(applyFlagChanges(pending, { featureFlags: { [DURABLE_AGENT_FLAG]: true } }, REGISTRY).notices).to.deep.equal({
      durableAgentDefault: 'adopted',
    });
    expect(applyFlagChanges(pending, { featureFlags: { [DURABLE_AGENT_FLAG]: false } }, REGISTRY).notices).to.deep.equal({
      durableAgentDefault: 'kept',
    });
  });

  it('every reader sees effective values and the overrides beside them', () => {
    // BETA: a stored "on" from before overrides is the previous default (on since DUR3): kept, and marked pending.
    const settings = platformSettingsOf({ featureFlags: { [DURABLE_AGENT_FLAG]: true } });
    expect(settings.featureFlags[DURABLE_AGENT_FLAG]).to.equal(true);
    expect(settings.featureFlagOverrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: true });
    expect(settings.notices).to.deep.equal({ durableAgentDefault: 'pending' });
  });

  it('BETA: the agent harness is off by default, and an org that never saved it follows the new default', () => {
    const none = platformSettingsOf({});
    expect(none.featureFlags[DURABLE_AGENT_FLAG]).to.equal(false);
    expect(none.featureFlagOverrides).to.deep.equal({});
    const saved = platformSettingsOf({ featureFlagsVersion: FEATURE_FLAGS_VERSION, featureFlags: { [DURABLE_AGENT_FLAG]: true } });
    expect(saved.featureFlags[DURABLE_AGENT_FLAG]).to.equal(true);
  });

  it('BETA: an unanswered notice (absent) takes an answer; adopted and kept store the value, whatever the default', () => {
    const fresh = readFlagState({ featureFlagsVersion: FEATURE_FLAGS_VERSION }, PLATFORM_FEATURE_FLAGS);
    const adopted = applyFlagChanges(fresh, { notices: { durableAgentDefault: 'adopted' } }, PLATFORM_FEATURE_FLAGS);
    expect(adopted.overrides).to.deep.equal({ [DURABLE_AGENT_FLAG]: true });
    expect(adopted.notices).to.deep.equal({ durableAgentDefault: 'adopted' });
    const kept = applyFlagChanges(fresh, { notices: { durableAgentDefault: 'kept' } }, PLATFORM_FEATURE_FLAGS);
    expect(kept.overrides).to.deep.equal({});
    const dismissed = applyFlagChanges(fresh, { notices: { durableAgentDefault: 'dismissed' } }, PLATFORM_FEATURE_FLAGS);
    expect([dismissed.overrides, dismissed.notices]).to.deep.equal([{}, { durableAgentDefault: 'dismissed' }]);
    const turnedOn = applyFlagChanges(fresh, { featureFlags: { [DURABLE_AGENT_FLAG]: true } }, PLATFORM_FEATURE_FLAGS);
    expect(turnedOn.notices).to.deep.equal({ durableAgentDefault: 'adopted' });
  });

  it('LB-10: the body takes known notice answers only; the upload limit may be left out', () => {
    const parse = (body: unknown) => platformSettingsSchema.safeParse({ body }).success;
    expect(parse({ featureFlags: { ENABLE_MCP: true } })).to.equal(true);
    expect(parse({ notices: { durableAgentDefault: 'kept' } })).to.equal(true);
    expect(parse({ notices: { durableAgentDefault: 'pending' } })).to.equal(false);
    expect(parse({ notices: { somethingElse: 'kept' } })).to.equal(false);
  });

  it('the available flags never expose previousDefault', async () => {
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    await getAvailablePlatformFeatureFlags()({} as never, res as never, sinon.stub<unknown[]>());
    const flags = res.json.firstCall.args[0].flags as Array<Record<string, unknown>>;
    expect(flags.some((flag) => 'previousDefault' in flag)).to.equal(false);
  });
});

describe('Labs save (UX1 LB-6, LB-9)', () => {
  let encrypt: sinon.SinonStub;
  beforeEach(() => {
    encrypt = sinon.stub().callsFake((value: string) => `encrypted:${value}`);
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt,
      decrypt: (value: string) => value.replace('encrypted:', ''),
    } as never);
  });
  afterEach(() => sinon.restore());

  const save = async (stored: Record<string, unknown> | null, body: Record<string, unknown>) => {
    const kvs = {
      get: sinon.stub().resolves(stored === null ? null : `encrypted:${JSON.stringify(stored)}`),
      set: sinon.stub().resolves(),
      compareAndSet: sinon.stub().resolves(true),
    };
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    const next = sinon.stub();
    await inRequestOrg(setPlatformSettings(kvs as never, testControlPlane()))({ body } as never, res as never, next);
    expect(next.called, String(next.firstCall?.args[0])).to.equal(false);
    return JSON.parse(encrypt.firstCall.args[0]);
  };

  it('LB-6: a save from the old page (every flag) stores only the ones off their default', async () => {
    const everyFlag = Object.fromEntries(PLATFORM_FEATURE_FLAGS.map((f) => [f.key, !!f.defaultEnabled]));
    const saved = await save(null, { fileUploadMaxSizeBytes: 1024, featureFlags: { ...everyFlag, ENABLE_MCP: !everyFlag['ENABLE_MCP'] } });
    expect(saved.featureFlagsVersion).to.equal(FEATURE_FLAGS_VERSION);
    expect(saved.featureFlags).to.deep.equal({ ENABLE_MCP: !everyFlag['ENABLE_MCP'] });
  });

  it('LB-9: what the request does not send is kept: operator keys, rollout settings, the upload limit', async () => {
    const stored = {
      fileUploadMaxSizeBytes: 2048,
      featureFlags: { ENABLE_AGENT_HARNESS: false },
      agentHarness: { modelFamilies: ['anthropic'] },
      softDeletePurge: { enabled: false },
    };
    const saved = await save(stored, { notices: { durableAgentDefault: 'kept' } });
    expect(saved.fileUploadMaxSizeBytes).to.equal(2048);
    expect(saved.agentHarness).to.deep.equal({ modelFamilies: ['anthropic'] });
    expect(saved.softDeletePurge).to.deep.equal({ enabled: false });
    // Off is the release default since BETA, so "kept" stores no override.
    expect(saved.featureFlags).to.deep.equal({});
    expect(saved.notices).to.deep.equal({ durableAgentDefault: 'kept' });
  });

  it('LB-7: "adopted" through the Labs API turns durable chat on and stores it, the default being off (BETA)', async () => {
    const saved = await save({ featureFlags: { ENABLE_AGENT_HARNESS: false } }, { notices: { durableAgentDefault: 'adopted' } });
    expect(saved.featureFlags).to.deep.equal({ ENABLE_AGENT_HARNESS: true });
    expect(platformSettingsOf(saved).featureFlags['ENABLE_AGENT_HARNESS']).to.equal(true);
    expect(platformSettingsOf(saved).notices).to.deep.equal({ durableAgentDefault: 'adopted' });
  });
});
