import { expect } from 'chai';
import {
  isSandboxAlertType,
  parseSandboxCapacityAlert,
  storedAlertFields,
} from '../../../src/modules/notification/utils/sandbox-capacity-alert';

const PROTOTYPE_NAMES = ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf'];

const event = (payload: Record<string, unknown> = {}) => ({
  audience: 'platformAdmins',
  type: 'SANDBOX_CAPACITY_CRITICAL',
  payload: {
    v: 1,
    alertId: 'a1',
    level: 'critical',
    firedAt: new Date().toISOString(),
    ...payload,
  },
});

describe('sandbox load alert parsing ignores prototype names', () => {
  for (const name of PROTOTYPE_NAMES) {
    it(`refuses the type ${name} as unknown`, () => {
      expect(isSandboxAlertType(name)).to.be.false;
      const result = parseSandboxCapacityAlert({ ...event(), type: name });
      expect(result).to.deep.equal({ ok: false, reason: 'unknown_type', type: name.replace(/[^A-Za-z0-9_.-]/g, '?') });
    });
  }

  it('reads no field the event only inherits', () => {
    const inherited = Object.create({
      type: 'SANDBOX_CAPACITY_CRITICAL',
      payload: event().payload,
    });
    expect(parseSandboxCapacityAlert(inherited)).to.include({ ok: false, reason: 'unknown_type' });

    const payload = Object.create({ v: 1, alertId: 'a1', level: 'critical' });
    const result = parseSandboxCapacityAlert({ type: 'SANDBOX_CAPACITY_CRITICAL', payload });
    expect(result).to.include({ ok: false, reason: 'bad_payload' });
  });

  it('ignores an inherited email flag, limit and reason counts', () => {
    const raw = Object.assign(Object.create({ email: false }), event({
      limit: Object.create({ ceiling: 7 }),
      reasonCounts: Object.create({ slots: 9 }),
    }));
    const result = parseSandboxCapacityAlert(raw);
    if (!result.ok) throw new Error(result.reason);
    expect(result.alert.email).to.be.true;
    expect(result.alert.limit.ceiling).to.be.null;
    expect(result.alert.reasonCounts).to.deep.equal({ slots: 0, memory: 0, cpu: 0 });
  });

  it('treats __proto__, constructor and toString keys from JSON as plain data', () => {
    const raw = JSON.parse(JSON.stringify(event()).replace(
      '"v":1,',
      '"v":1,"__proto__":{"host":"evil"},"constructor":{"x":1},"toString":"x",' +
        '"limit":{"__proto__":{"ceiling":7},"constructor":3,"toString":4},' +
        '"reasonCounts":{"__proto__":{"slots":9},"constructor":1},',
    ).replace('{"audience"', '{"__proto__":{"email":false},"constructor":"x","toString":"y","audience"'));
    const result = parseSandboxCapacityAlert(raw);
    if (!result.ok) throw new Error(result.reason);
    expect(result.alert.host).to.be.null;
    expect(result.alert.email).to.be.true;
    expect(result.alert.limit.ceiling).to.be.null;
    expect(result.alert.reasonCounts).to.deep.equal({ slots: 0, memory: 0, cpu: 0 });
    const stored = JSON.stringify(storedAlertFields(result.alert));
    expect(stored).to.not.contain('evil');
    expect(stored).to.not.contain('constructor');
    expect(stored).to.not.contain('toString');
    expect(({} as Record<string, unknown>).host).to.be.undefined;
  });
});
