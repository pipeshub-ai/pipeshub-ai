/**
 * What every HarnessTracer that records must do (P9), TR-1..TR-7: the
 * in-memory tracer and OTLP/HTTP to a real collector socket
 * (`harness-tracer.contract.test.ts`). `exported` reads back what reached the
 * tracer's destination, decoded to spans, so both are held to one shape.
 */
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import {
  HarnessSpan,
  HarnessTracer,
  runRootSpanId,
  runTraceId,
} from '../../../../src/modules/agent-harness/application/harness-tracing';

export interface HarnessTracerFixture {
  tracer: HarnessTracer;
  exported(): HarnessSpan[];
}

export interface HarnessTracerContractOptions {
  /** A tracer of the same kind whose destination refuses every export until `recover`. */
  refusing?: () => Promise<HarnessTracerFixture & { recover(): void }>;
}

export const spanFixture = (overrides: Partial<HarnessSpan> = {}): HarnessSpan => {
  const runId = `run-${randomUUID()}`;
  return {
    name: 'pipeshub.run.admit',
    kind: 'server',
    traceId: runTraceId(runId),
    spanId: runRootSpanId(runId),
    startMs: 1_760_000_000_000,
    endMs: 1_760_000_000_125,
    attributes: {
      'pipeshub.org_id': 'org-1',
      'pipeshub.run_id': runId,
      'pipeshub.harness_major': 2,
      'pipeshub.replayed': false,
      'pipeshub.ratio': 0.25,
    },
    ...overrides,
  };
};

export function describeHarnessTracerContract(
  name: string,
  create: () => Promise<HarnessTracerFixture>,
  options: HarnessTracerContractOptions = {},
): void {
  describe(`HarnessTracer contract: ${name}`, function () {
    this.timeout(20_000);
    let f: HarnessTracerFixture;

    beforeEach(async () => {
      f = await create();
    });

    afterEach(async () => {
      await f.tracer.flush();
    });

    it('TR-1: a recording tracer says it is enabled', () => {
      expect(f.tracer.enabled).to.equal(true);
    });

    it('TR-2: a recorded span is exported by flush with its ids, name, kind, timings and attributes', async () => {
      const span = spanFixture();
      f.tracer.record(span);
      await f.tracer.flush();
      expect(f.exported()).to.deep.equal([span]);
    });

    it('TR-3: a child keeps its parent and its error type; a root has neither', async () => {
      const root = spanFixture();
      const child = spanFixture({
        name: 'pipeshub.run.dispatch',
        kind: 'client',
        traceId: root.traceId,
        spanId: 'a1b2c3d4e5f60718',
        parentSpanId: root.spanId,
        errorType: 'BROKER_UNAVAILABLE',
      });
      const internal = spanFixture({ kind: 'internal', attributes: {} });
      for (const span of [root, child, internal]) f.tracer.record(span);
      await f.tracer.flush();
      expect(f.exported()).to.deep.equal([root, child, internal]);
    });

    it('TR-4: flushing with nothing recorded exports nothing', async () => {
      await f.tracer.flush();
      await f.tracer.flush();
      expect(f.exported()).to.deep.equal([]);
    });

    it('TR-5: every span recorded before flush is exported once, past a batch', async () => {
      const spans = Array.from({ length: 300 }, () => spanFixture());
      for (const span of spans) f.tracer.record(span);
      await f.tracer.flush();
      expect(f.exported().map((s) => s.spanId + s.traceId)).to.have.members(spans.map((s) => s.spanId + s.traceId));
      expect(f.exported()).to.have.length(spans.length);
    });

    it('TR-6: concurrent recorders and flushes lose and repeat nothing', async () => {
      const spans = Array.from({ length: 40 }, () => spanFixture());
      await Promise.all(
        spans.map(async (span, i) => {
          await Promise.resolve();
          f.tracer.record(span);
          if (i % 10 === 9) await f.tracer.flush();
        }),
      );
      await f.tracer.flush();
      expect(f.exported()).to.have.deep.members(spans);
      expect(f.exported()).to.have.length(spans.length);
    });

    const { refusing } = options;
    if (refusing !== undefined) {
      it('TR-7: a destination that refuses costs the recorder nothing; spans after it recovers are exported', async () => {
        const down = await refusing();
        expect(() => down.tracer.record(spanFixture())).to.not.throw();
        await down.tracer.flush();
        expect(down.exported()).to.deep.equal([]);
        down.recover();
        const after = spanFixture();
        down.tracer.record(after);
        await down.tracer.flush();
        expect(down.exported()).to.deep.equal([after]);
      });
    }
  });
}
