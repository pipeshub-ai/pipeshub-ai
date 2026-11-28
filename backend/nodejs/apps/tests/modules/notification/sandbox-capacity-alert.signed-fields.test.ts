import { expect } from 'chai';
import { parseSandboxCapacityAlert } from '../../../src/modules/notification/utils/sandbox-capacity-alert';

// A signature covers only an event's payload: the alert's type and email flag are read from there, so a writer to the
// broker cannot turn a signed warning into a "back to normal" notice or stop its email by changing the top level.
/** A copy of *obj* without *key*. */
function without<T extends object, K extends keyof T>(obj: T, key: K): Omit<T, K> {
  const copy: Partial<T> = { ...obj };
  delete copy[key];
  return copy as Omit<T, K>;
}

describe('sandbox load alert: type and email come from the payload when it carries them', () => {
  const payload = (over: Record<string, unknown> = {}) => ({
    v: 1,
    alertId: '6f2c1c7e-2d5b-4b1a-9c3e-1a2b3c4d5e6f',
    level: 'warning',
    firedAt: new Date().toISOString(),
    type: 'SANDBOX_CAPACITY_WARNING',
    email: true,
    ...over,
  });

  it('ignores a top-level type and email that disagree with the payload', () => {
    const forged = { audience: 'platformAdmins', type: 'SANDBOX_CAPACITY_RESOLVED', email: false, payload: payload() };
    const result = parseSandboxCapacityAlert(forged) as { ok: boolean; alert?: { type: string; kind: string; email: boolean } };
    expect(result.ok).to.equal(true);
    expect(result.alert).to.include({ type: 'SANDBOX_CAPACITY_WARNING', kind: 'warning', email: true });
  });

  it('honours the payload saying in-app only', () => {
    const result = parseSandboxCapacityAlert({ type: 'SANDBOX_CAPACITY_WARNING', payload: payload({ email: false }) }) as {
      alert?: { email: boolean };
    };
    expect(result.alert?.email).to.equal(false);
  });

  it('an older producer (no type in the payload) is read from the top level, as before', () => {
    const older = without(without(payload(), 'type'), 'email');
    const result = parseSandboxCapacityAlert({ type: 'SANDBOX_CAPACITY_WARNING', email: false, payload: older }) as {
      ok: boolean;
      alert?: { type: string; email: boolean };
    };
    expect(result.ok).to.equal(true);
    expect(result.alert).to.include({ type: 'SANDBOX_CAPACITY_WARNING', email: false });
  });
});
