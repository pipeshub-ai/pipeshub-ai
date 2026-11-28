import { expect } from 'chai';
import { COLLECTION_NEEDS_CONSENT } from '../../../../src/edition';
import * as flag from '../../../../src/libs/services/telemetry/flag';
import { PrometheusBackend } from '../../../../src/libs/services/telemetry/metrics-backend';

// What may be recorded: never while the flag is off; with it on, whenever the admin consents, and without consent only
// where recording does not wait for it (`COLLECTION_NEEDS_CONSENT`, off by default).
describe('telemetry records by the flag, and by the admin consent where the edition asks for it', () => {
  const saved = process.env.TELEMETRY_COLLECTION_FLAG;
  afterEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = saved;
    flag.setCollectionConsent(true);
  });

  it('records nothing while the flag is off, whatever the consent', () => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'false';
    for (const consent of [true, false]) {
      flag.setCollectionConsent(consent);
      expect(flag.isCollecting(), `consent=${consent}`).to.equal(false);
    }
  });

  it('records with the flag on and the consent given', () => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
    flag.setCollectionConsent(true);
    expect(flag.isCollecting()).to.equal(true);
  });

  it('with the consent withdrawn, records only where recording does not wait for it', async () => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
    flag.setCollectionConsent(false);
    expect(flag.isCollecting()).to.equal(!COLLECTION_NEEDS_CONSENT);

    const backend = new PrometheusBackend();
    backend.createCounter({ name: 'gate_test_total', help: 'h', labelNames: [] }).inc({});
    expect(/^gate_test_total 1$/m.test(await backend.serialize())).to.equal(!COLLECTION_NEEDS_CONSENT);
  });
});
