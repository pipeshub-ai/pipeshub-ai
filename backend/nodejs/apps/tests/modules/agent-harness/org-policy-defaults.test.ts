/**
 * What every org gets when no extension binds an org policy: each member reaches every configured model and the
 * release's engines, may use their own Claude account, and runs under the release's quotas and engine profiles.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { groupAccessOf, chatModelGroupsOf, withGroupAccess } from '../../../src/modules/agent-harness/binders/group-access';
import { bindEngineProfiles } from '../../../src/modules/agent-harness/binders/policy-settings';
import {
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_MODEL_ACCESS_POLICIES,
  AGENT_HARNESS_ORG_SIGNIN_SWITCH,
} from '../../../src/modules/agent-harness/binders/tokens';
import { boundOrNull } from '../../../src/modules/agent-harness/binders/context';
import { noEngineCeilings, type EngineProfileCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import { everyOrgAllowsSignIns } from '../../../src/modules/agent-harness/ports/engine-signins';
import type { RoutingDecider } from '../../../src/modules/agent-harness/ports/routing-decider';
import { DEFAULT_ORG_ACCESS_POLICY } from '../../../src/modules/agent-harness/domain/model-access';
import { testModelAccess } from './support/model-access';

describe('org policy defaults: nothing an org admin sets', () => {
  it('no group access is bound: routing is the decider as given, and no turn reads a membership', () => {
    const container = new Container();
    const decider = { decide: () => Promise.reject(new Error('not asked')) } as unknown as RoutingDecider;
    expect(groupAccessOf(container)).to.equal(undefined);
    expect(chatModelGroupsOf(container)).to.equal(undefined);
    expect(withGroupAccess(container, decider)).to.equal(decider);
  });

  it('the engine profile ceilings are none: every engine run gets the release defaults', async () => {
    const container = new Container();
    bindEngineProfiles({ container } as never);
    const ceilings = container.get<EngineProfileCeilings>(AGENT_HARNESS_ENGINE_PROFILES);
    expect(ceilings).to.equal(noEngineCeilings);
    expect(await ceilings.read('org-1')).to.equal(null);
  });

  it('an extension bound first keeps its own ceilings', () => {
    const container = new Container();
    const own: EngineProfileCeilings = { read: () => Promise.resolve({ limits: { maxSteps: 5 } }) };
    container.bind(AGENT_HARNESS_ENGINE_PROFILES).toConstantValue(own);
    bindEngineProfiles({ container } as never);
    expect(container.get(AGENT_HARNESS_ENGINE_PROFILES)).to.equal(own);
  });

  it('every org lets its people use their own Claude account', async () => {
    expect(boundOrNull(new Container(), AGENT_HARNESS_ORG_SIGNIN_SWITCH)).to.equal(null);
    expect(await everyOrgAllowsSignIns.allows('org-1')).to.equal(true);
  });

  it('no vendor-rule narrowing is bound: nothing is switched off', async () => {
    expect(boundOrNull(new Container(), AGENT_HARNESS_MODEL_ACCESS_POLICIES)).to.equal(null);
    const access = testModelAccess({ policies: undefined });
    expect(await access.policy('org-1')).to.deep.equal({ ...DEFAULT_ORG_ACCESS_POLICY, disabledRules: [] });
    expect(await access.policyRevision('org-1')).to.equal(null);
  });
});
