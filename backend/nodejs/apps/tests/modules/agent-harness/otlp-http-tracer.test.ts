import { expect } from 'chai';
import sinon from 'sinon';
import { OtlpHttpTracer } from '../../../src/modules/agent-harness/adapters/telemetry/otlp-http-tracer';
import { admissionSpan } from '../../../src/modules/agent-harness/application/harness-tracing';

describe('agent-harness OTLP tracer: export on its timer', () => {
  let clock: sinon.SinonFakeTimers;

  beforeEach(() => {
    clock = sinon.useFakeTimers();
  });

  afterEach(() => clock.restore());

  it('a recorded span is exported once its flush interval passes, without an explicit flush', async () => {
    const fetch = sinon.stub().resolves(new Response(null, { status: 200 }));
    const tracer = new OtlpHttpTracer({
      endpoint: 'http://collector:4318/',
      serviceName: 'pipeshub-node-test',
      fetch,
      flushIntervalMs: 500,
    });
    tracer.record(admissionSpan({ orgId: 'o', sessionId: 's', runId: 'run-1', startMs: 1, endMs: 2 }));
    tracer.record(admissionSpan({ orgId: 'o', sessionId: 's', runId: 'run-2', startMs: 3, endMs: 4 }));
    await clock.tickAsync(499);
    expect(fetch.called).to.equal(false);
    await clock.tickAsync(1);
    expect(fetch.callCount).to.equal(1);
    expect(fetch.firstCall.args[0]).to.equal('http://collector:4318/v1/traces');
    await clock.tickAsync(5_000);
    expect(fetch.callCount).to.equal(1);
  });
});
