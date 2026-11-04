import { expect } from 'chai';
import * as impl from '../../../../src/libs/services/telemetry/telemetry-impl';
import * as stub from '../../../../src/libs/services/telemetry/telemetry-impl.stub';
import * as runtime from '../../../../src/libs/services/telemetry/telemetry-runtime';
import * as runtimeStub from '../../../../src/libs/services/telemetry/telemetry-runtime.stub';

// A build swaps each file for its stub, so the two must export the same names.
describe('telemetry stubs', () => {
  it('telemetry-impl.stub exports the names telemetry-impl does', () => {
    expect(Object.keys(stub).sort()).to.deep.equal(Object.keys(impl).sort());
  });

  it('telemetry-runtime.stub exports the names telemetry-runtime does', () => {
    expect(Object.keys(runtimeStub).sort()).to.deep.equal(
      Object.keys(runtime).sort(),
    );
  });

  it('does nothing and reports telemetry off', async () => {
    expect(stub.isTelemetryEnabled()).to.equal(false);
    expect(stub.recordEvent('x', { a: 1 })).to.equal(undefined);
    stub.metricsBackend.createCounter({}).inc({ a: 1 });
    stub.metricsBackend.createGauge({}).set({ a: 1 }, 1);
    stub.metricsBackend.createHistogram({}).observe({ a: 1 }, 1);
    expect(runtimeStub.TelemetryService.current()).to.equal(undefined);
    let called = false;
    runtimeStub.metricsMiddleware()({} as never, {} as never, () => {
      called = true;
    });
    expect(called).to.equal(true);
  });
});
