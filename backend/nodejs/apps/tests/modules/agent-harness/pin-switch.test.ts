/**
 * CPD-5 the switch, C4.04: `CONTROL_PLANE_PIN` unset is `on`; `off` binds
 * nothing; `shadow` binds the step and warns at startup that the pin does not
 * decide, naming the gates `on` was switched on after; an unknown value
 * refuses to start.
 */
import { expect } from 'chai';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_POLICY_PINNING,
  bindControlPlanePin,
} from '../../../src/modules/agent-harness/binders/control-plane-pin';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { CONTROL_PLANE_PIN_GATES, parseControlPlanePin } from '../../../src/modules/control-plane';
import { fixedRollout } from './support/rollout';
import type { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import type { HarnessRolloutPolicy } from '../../../src/modules/agent-harness/ports/harness-rollout';

describe('CPD-5 CONTROL_PLANE_PIN: default, off, on', () => {
  const saved = process.env.CONTROL_PLANE_PIN;
  let step: PolicyPinning | null = null;
  afterEach(() => {
    if (saved === undefined) delete process.env.CONTROL_PLANE_PIN;
    else process.env.CONTROL_PLANE_PIN = saved;
  });

  const bind = (value: string | undefined, rollout: HarnessRolloutPolicy = fixedRollout(), documents: Record<string, unknown> = {}) => {
    if (value === undefined) delete process.env.CONTROL_PLANE_PIN;
    else process.env.CONTROL_PLANE_PIN = value;
    const warned: Array<[string, Record<string, unknown>]> = [];
    const container = new Container();
    bindControlPlanePin({
      container,
      dependencies: { rollout, ...documents },
      logger: { warn: (message: string, meta: { metadata?: Record<string, unknown> } & Record<string, unknown>) => void warned.push([message, meta]) },
    } as unknown as HarnessBindingContext);
    step = container.isBound(AGENT_HARNESS_POLICY_PINNING)
      ? container.get<PolicyPinning>(AGENT_HARNESS_POLICY_PINNING)
      : null;
    return { bound: step !== null, warned };
  };

  it('C4.04 unset or blank is on with the harness switched on; any case; an unknown value refuses to start', () => {
    const on = { AGENT_HARNESS_WORKER_ENABLED: 'true' };
    expect(parseControlPlanePin(on)).to.equal('on');
    expect(parseControlPlanePin({ ...on, CONTROL_PLANE_PIN: '  ' })).to.equal('on');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: 'Shadow' })).to.equal('shadow');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: ' ON ' })).to.equal('on');
    expect(() => parseControlPlanePin({ CONTROL_PLANE_PIN: 'yes' })).to.throw(/CONTROL_PLANE_PIN/);
  });

  it('R50-OFF: unset or blank is off while the harness is switched off (ADR-OPS-06); a value set still decides', () => {
    // The switch unset is on since 2026-11-20 (ADR-OPS-06 as amended), so the pin follows it.
    expect(parseControlPlanePin({})).to.equal('on');
    expect(parseControlPlanePin({ AGENT_HARNESS_WORKER_ENABLED: 'false' })).to.equal('off');
    expect(parseControlPlanePin({ AGENT_HARNESS_WORKER_ENABLED: 'false', CONTROL_PLANE_PIN: ' ' })).to.equal('off');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: 'on' })).to.equal('on');
  });

  it('C4.04 on (the default) binds the pinning step without a warning; off binds nothing', () => {
    expect(bind(undefined)).to.deep.equal({ bound: true, warned: [] });
    expect(bind('on')).to.deep.equal({ bound: true, warned: [] });
    expect(bind('off')).to.deep.equal({ bound: false, warned: [] });
  });

  it('shadow binds the step and warns once, naming every gate on was switched on after', () => {
    const { bound, warned } = bind('shadow');
    expect(bound).to.equal(true);
    expect(warned.map(([message]) => message)).to.deep.equal(['cp.pin_shadow']);
    expect(JSON.stringify(warned[0]?.[1])).to.include(CONTROL_PLANE_PIN_GATES[0] as string).and.to.include('CPD-6');
  });

  it('CPD5-43: the bound step compares rollout only when the rollout policy exposes its switches', () => {
    bind('shadow');
    expect(step?.comparesRollout).to.equal(false);
    bind('shadow', {
      ...fixedRollout(),
      settings: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }),
    });
    expect(step?.comparesRollout).to.equal(true);
  });

  it('binds with a stored document handed in its dependencies as without it', () => {
    const doc = { read: async () => null };
    const documents = {
      approvalSettings: { document: doc },
      memory: { policies: doc },
    };
    expect(bind('shadow', fixedRollout(), documents).bound).to.equal(true);
  });
});
