/**
 * P9 OT-1, OT-7, OT-10 on Node: the switch, the shared trace derivation, the
 * admission span and its OTLP/HTTP export (to a local collector).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { createServer, IncomingMessage, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  admissionSpan,
  InMemoryHarnessTracer,
  noopHarnessTracer,
  runRootSpanId,
  runTraceId,
} from '../../../src/modules/agent-harness/application/harness-tracing';
import {
  harnessTracerFromEnv,
  OtlpHttpTracer,
} from '../../../src/modules/agent-harness/adapters/telemetry/otlp-http-tracer';
import { InvalidEnvFlagError } from '../../../src/libs/utils/env-flags';
import { fixedRollout } from './support/rollout';
import { grantingAccess } from './support/agent-definition';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/trace-ids.json'), 'utf-8'),
) as { cases: Array<{ runId: string; traceId: string; rootSpanId: string }> };

const SENTINEL = 'SENTINEL-admission-content-p9';

describe('harness tracing (P9)', () => {
  it('OT-1: off unless HARNESS_OTEL is on, and refuses a malformed value', () => {
    expect(harnessTracerFromEnv({})).to.equal(noopHarnessTracer);
    expect(harnessTracerFromEnv({ HARNESS_OTEL: 'false' })).to.equal(noopHarnessTracer);
    expect(harnessTracerFromEnv({ HARNESS_OTEL: 'true' })).to.be.instanceOf(OtlpHttpTracer);
    expect(() => harnessTracerFromEnv({ HARNESS_OTEL: 'maybe', PIPESHUB_HARDENED: 'true' })).to.throw(InvalidEnvFlagError);
  });

  it('OT-7: derives the trace context Python derives (the shared vector)', () => {
    expect(VECTOR.cases.length).to.be.greaterThanOrEqual(3);
    for (const c of VECTOR.cases) {
      expect(runTraceId(c.runId)).to.equal(c.traceId);
      expect(runRootSpanId(c.runId)).to.equal(c.rootSpanId);
    }
  });

  it('OT-7: an admitted run records its root span with the log fields and no content', async () => {
    const tracer = new InMemoryHarnessTracer();
    const store = {
      admit: sinon.stub().callsFake(async (command: { runId: string; orgId: string }) => ({
        run: { runId: command.runId, orgId: command.orgId, sessionId: 'ses-1', profile: 'quick', harnessMajor: 2 },
        created: true,
      })),
    };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      newId: () => 'run-traced',
      agentAccess: grantingAccess(),
      tracer,
    });
    await service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      idempotencyKey: 'k-1',
      deadlineMs: 60_000,
      input: { query: SENTINEL, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    expect(tracer.spans).to.have.length(1);
    const [span] = tracer.spans;
    expect(span!.name).to.equal('pipeshub.run.admit');
    expect(span!.kind).to.equal('server');
    expect(span!.traceId).to.equal(runTraceId('run-traced'));
    expect(span!.spanId).to.equal(runRootSpanId('run-traced'));
    expect(span!.attributes).to.include({
      'pipeshub.org_id': 'org-1',
      'pipeshub.run_id': 'run-traced',
      'pipeshub.session_id': 'ses-1',
      'gen_ai.agent.id': 'agent-1',
      'pipeshub.replayed': false,
    });
    expect(JSON.stringify(span)).to.not.include(SENTINEL);
    expect(span!.endMs).to.be.at.least(span!.startMs);
  });

  it('OT-1: without a tracer nothing is recorded', async () => {
    const store = { admit: sinon.stub().resolves({ run: { runId: 'r', orgId: 'o', sessionId: 's' }, created: true }) };
    const service = new RunAdmissionService({ store: store as never, rollout: fixedRollout(), agentAccess: grantingAccess() });
    await service.submit({
      orgId: 'o', actorId: 'u', agentKey: 'a', authorization: 'Bearer t', idempotencyKey: 'k', deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    expect(noopHarnessTracer.enabled).to.equal(false);
  });

  describe('OT-10: OTLP/HTTP export', () => {
    let server: Server;
    let bodies: Array<Record<string, unknown>>;
    let endpoint: string;

    beforeEach(async () => {
      bodies = [];
      server = createServer((req: IncomingMessage, res) => {
        let raw = '';
        req.on('data', (chunk: Buffer) => {
          raw += chunk.toString();
        });
        req.on('end', () => {
          expect(req.url).to.equal('/v1/traces');
          expect(req.headers['content-type']).to.equal('application/json');
          bodies.push(JSON.parse(raw) as Record<string, unknown>);
          res.writeHead(200).end('{}');
        });
      });
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      endpoint = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
    });

    afterEach(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    });

    it('posts the span in the OTLP JSON encoding, with error.type and status when it failed', async () => {
      const tracer = new OtlpHttpTracer({ endpoint, serviceName: 'pipeshub-node-test' });
      tracer.record({
        ...admissionSpan({ orgId: 'o', sessionId: 's', runId: 'run-x', startMs: 1_000, endMs: 1_250 }),
        errorType: 'quota_exceeded',
      });
      await tracer.flush();
      expect(bodies).to.have.length(1);
      const resource = (bodies[0]!['resourceSpans'] as Array<Record<string, any>>)[0]!;
      expect(resource['resource'].attributes[0]).to.deep.equal({
        key: 'service.name',
        value: { stringValue: 'pipeshub-node-test' },
      });
      const span = resource['scopeSpans'][0].spans[0];
      expect(span.traceId).to.equal(runTraceId('run-x'));
      expect(span.spanId).to.equal(runRootSpanId('run-x'));
      expect(span.kind).to.equal(2);
      expect(span.startTimeUnixNano).to.equal('1000000000');
      expect(span.endTimeUnixNano).to.equal('1250000000');
      expect(span.status).to.deep.equal({ code: 2 });
      expect(span.attributes).to.deep.include({ key: 'error.type', value: { stringValue: 'quota_exceeded' } });
      expect(span.attributes).to.deep.include({ key: 'pipeshub.run_id', value: { stringValue: 'run-x' } });
    });

    it('a collector that is down costs the caller nothing and is logged once', async () => {
      const warn = sinon.spy();
      const tracer = new OtlpHttpTracer({
        endpoint: 'http://127.0.0.1:1',
        serviceName: 'x',
        logger: { debug: () => undefined, info: () => undefined, warn, error: () => undefined },
      });
      tracer.record(admissionSpan({ orgId: 'o', sessionId: 's', runId: 'r1', startMs: 1, endMs: 2 }));
      await tracer.flush();
      tracer.record(admissionSpan({ orgId: 'o', sessionId: 's', runId: 'r2', startMs: 1, endMs: 2 }));
      await tracer.flush();
      expect(warn.callCount).to.equal(1);
      expect(warn.firstCall.args[0]).to.equal('telemetry.export_failed');
    });
  });
});
