/**
 * CTL-5: Labs keeps three agent-harness flags ("Agent harness", scheduled runs, knowledge publications). What the
 * harness brings with it is no longer a switch; the kill switch is an operator's env setting. Stored keys of flags
 * this release no longer lists are ignored, never shown, and dropped by the next save; a stored Labs kill switch
 * that was on reads as "Agent harness" off for that org, so retiring the switch never turns the harness back on.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PLATFORM_FEATURE_FLAGS } from '../../../src/modules/configuration_manager/constants/constants';
import {
  applyFlagChanges,
  effectiveFlags,
  FEATURE_FLAGS_VERSION,
  readFlagState,
} from '../../../src/modules/configuration_manager/utils/flag-overrides';
import { platformSettingsOf } from '../../../src/modules/configuration_manager/utils/util';

const RETIRED = [
  'AGENT_HARNESS_KILL_SWITCH',
  'ENABLE_COLLABORATOR_TURNS',
  'ENABLE_AGENT_ACTIVITY_PANEL',
  'ENABLE_FOLLOWUP_SUGGESTIONS',
  'ENABLE_DURABLE_ASSISTANT_AGENT',
  'ENABLE_WORK_UI',
  'ENABLE_CODE_TASK',
];

describe('CTL-5: Labs offers three agent-harness flags', () => {
  it('lists "Agent harness", scheduled runs and knowledge publications, and none of the retired ones', () => {
    const keys = PLATFORM_FEATURE_FLAGS.map((flag) => flag.key);
    for (const key of ['ENABLE_AGENT_HARNESS', 'ENABLE_SCHEDULED_RUNS', 'ENABLE_KNOWLEDGE_PUBLICATIONS']) {
      expect(keys).to.include(key);
    }
    for (const key of RETIRED) expect(keys, key).to.not.include(key);
    expect(PLATFORM_FEATURE_FLAGS.find((flag) => flag.key === 'ENABLE_AGENT_HARNESS')?.label).to.equal('Agent harness');
  });

  for (const version of [FEATURE_FLAGS_VERSION, undefined]) {
    describe(`a settings document ${version === undefined ? 'saved before overrides' : 'in the override form'}`, () => {
      it('ignores stored keys of flags this release does not list: never read, never shown', () => {
        const stored = {
          featureFlagsVersion: version,
          featureFlags: Object.fromEntries(RETIRED.filter((k) => k !== 'AGENT_HARNESS_KILL_SWITCH').map((k) => [k, true])),
        };
        const settings = platformSettingsOf(stored);
        for (const key of RETIRED) {
          expect(settings.featureFlags, key).to.not.have.property(key);
          expect(settings.featureFlagOverrides, key).to.not.have.property(key);
        }
        // The release default (off since BETA, 2026-11-20).
        expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(false);
      });

      it('a stored Labs kill switch that was on reads as "Agent harness" off for the org', () => {
        const settings = platformSettingsOf({ featureFlagsVersion: version, featureFlags: { AGENT_HARNESS_KILL_SWITCH: true } });
        expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(false);
        expect(settings.featureFlagOverrides).to.deep.equal({ ENABLE_AGENT_HARNESS: false });
      });

      it('a stored kill switch that was off changes nothing', () => {
        const settings = platformSettingsOf({ featureFlagsVersion: version, featureFlags: { AGENT_HARNESS_KILL_SWITCH: false } });
        expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(false);
        expect(settings.featureFlagOverrides).to.deep.equal({});
      });
    });
  }

  it('the next save drops retired keys and keeps the migrated choice until the admin changes it', () => {
    const state = readFlagState(
      { featureFlagsVersion: FEATURE_FLAGS_VERSION, featureFlags: { AGENT_HARNESS_KILL_SWITCH: true, ENABLE_WORK_UI: true } },
      PLATFORM_FEATURE_FLAGS,
    );
    const saved = applyFlagChanges(state, { featureFlags: { ENABLE_SCHEDULED_RUNS: true } }, PLATFORM_FEATURE_FLAGS);
    expect(saved.overrides).to.deep.equal({ ENABLE_AGENT_HARNESS: false, ENABLE_SCHEDULED_RUNS: true });
    const turnedOn = applyFlagChanges(saved, { featureFlags: { ENABLE_AGENT_HARNESS: true } }, PLATFORM_FEATURE_FLAGS);
    expect(effectiveFlags(turnedOn.overrides, PLATFORM_FEATURE_FLAGS)['ENABLE_AGENT_HARNESS']).to.equal(true);
  });

  it('a save naming a retired flag records nothing for it', () => {
    const state = readFlagState({ featureFlagsVersion: FEATURE_FLAGS_VERSION, featureFlags: {} }, PLATFORM_FEATURE_FLAGS);
    const saved = applyFlagChanges(state, { featureFlags: { ENABLE_WORK_UI: true, AGENT_HARNESS_KILL_SWITCH: true } }, PLATFORM_FEATURE_FLAGS);
    expect(saved.overrides).to.deep.equal({});
  });
});
