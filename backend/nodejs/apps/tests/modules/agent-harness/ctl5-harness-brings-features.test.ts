/**
 * CTL-5: turning on "Agent harness" brings the default chat's Agent mode on the harness, collaborator turns and
 * the activity drawer with it; none has a switch of its own. The kill switch is the operator's env setting
 * AGENT_HARNESS_KILL_SWITCH, for the whole instance, and a Labs value of it is never read.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as configurationUtil from '../../../src/modules/configuration_manager/utils/util';
import {
  PlatformActivityPanel,
  PlatformCollaboratorTurns,
  PlatformHarnessRolloutSettings,
} from '../../../src/modules/agent-harness/adapters/configuration/platform-rollout-settings';

const store = {} as never;
const READY = { REPLICA_SET_AVAILABLE: 'true', AGENT_HARNESS_RELAY_ENABLED: 'true', AGENT_HARNESS_WORKER_ENABLED: 'true' };

const stored = (featureFlags: Record<string, boolean>) =>
  sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
    fileUploadMaxSizeBytes: 1,
    featureFlags,
    featureFlagOverrides: {},
    notices: {},
    agentHarness: { modelFamilies: null },
  });

describe('CTL-5: what "Agent harness" brings with it', () => {
  afterEach(() => sinon.restore());

  it('the default chat\'s Agent mode runs on the harness whenever the harness is on', async () => {
    stored({ ENABLE_AGENT_HARNESS: true });
    const settings = await new PlatformHarnessRolloutSettings(store, READY).read('org-1');
    expect(settings.assistantModes).to.include('agent');
  });

  it('collaborator turns and the activity drawer are on, whatever an older Labs page stored', async () => {
    stored({ ENABLE_COLLABORATOR_TURNS: false, ENABLE_AGENT_ACTIVITY_PANEL: false });
    expect(await new PlatformCollaboratorTurns(store).enabled('org-1')).to.equal(true);
    expect(await new PlatformActivityPanel(store).enabled('org-1')).to.equal(true);
  });

  it('the kill switch is the env setting: on stops the harness for every org; a Labs value is not read', async () => {
    stored({ ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: true });
    const off = await new PlatformHarnessRolloutSettings(store, READY).read('org-1');
    expect(off.killSwitch).to.equal(false);
    const killed = await new PlatformHarnessRolloutSettings(store, { ...READY, AGENT_HARNESS_KILL_SWITCH: 'true' }).read('org-1');
    expect(killed.killSwitch).to.equal(true);
  });
});
