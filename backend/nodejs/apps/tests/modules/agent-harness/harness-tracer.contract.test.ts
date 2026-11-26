/**
 * The HarnessTracer contract over the in-memory tracer and over OtlpHttpTracer
 * posting to a collector socket on 127.0.0.1 (no fixed port), which decodes
 * the OTLP JSON it receives back into spans.
 */
import http from 'node:http';
import { AddressInfo } from 'node:net';
import { expect } from 'chai';
import { OtlpHttpTracer } from '../../../src/modules/agent-harness/adapters/telemetry/otlp-http-tracer';
import {
  HarnessSpan,
  HarnessSpanAttribute,
  InMemoryHarnessTracer,
} from '../../../src/modules/agent-harness/application/harness-tracing';
import { describeHarnessTracerContract, spanFixture } from './support/harness-tracer-contract';

interface OtlpValue {
  stringValue?: string;
  intValue?: string;
  doubleValue?: number;
  boolValue?: boolean;
}

interface OtlpSpan {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: number;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes: Array<{ key: string; value: OtlpValue }>;
  status?: { code: number };
}

const KINDS: Record<number, HarnessSpan['kind']> = { 1: 'internal', 2: 'server', 3: 'client' };

const valueOf = (value: OtlpValue): HarnessSpanAttribute =>
  value.stringValue ?? value.boolValue ?? value.doubleValue ?? Number(value.intValue);

const millis = (nanos: string): number => Number(BigInt(nanos) / BigInt(1_000_000));

function decodeSpan(span: OtlpSpan): HarnessSpan {
  const attributes: Record<string, HarnessSpanAttribute> = {};
  for (const { key, value } of span.attributes) attributes[key] = valueOf(value);
  const errorType = attributes['error.type'];
  delete attributes['error.type'];
  expect(span.status?.code === 2, 'status is ERROR exactly when error.type is set').to.equal(errorType !== undefined);
  return {
    name: span.name,
    kind: KINDS[span.kind]!,
    traceId: span.traceId,
    spanId: span.spanId,
    ...(span.parentSpanId !== undefined ? { parentSpanId: span.parentSpanId } : {}),
    startMs: millis(span.startTimeUnixNano),
    endMs: millis(span.endTimeUnixNano),
    attributes,
    ...(errorType !== undefined ? { errorType: String(errorType) } : {}),
  };
}

/** An OTLP/HTTP traces receiver: acknowledged spans, in arrival order. */
async function startCollector() {
  const spans: HarnessSpan[] = [];
  let status = 200;
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      if (req.method !== 'POST' || req.url !== '/v1/traces' || req.headers['content-type'] !== 'application/json') {
        res.writeHead(404).end();
        return;
      }
      if (status === 200) {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8')) as {
          resourceSpans: Array<{ scopeSpans: Array<{ spans: OtlpSpan[] }> }>;
        };
        for (const resource of body.resourceSpans) {
          for (const scope of resource.scopeSpans) spans.push(...scope.spans.map(decodeSpan));
        }
      }
      res.writeHead(status, { 'content-type': 'application/json' }).end('{}');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    endpoint: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`,
    spans,
    refuse: (refusing: boolean) => {
      status = refusing ? 503 : 200;
    },
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

describeHarnessTracerContract('in-memory', async () => {
  const tracer = new InMemoryHarnessTracer();
  return { tracer, exported: () => tracer.spans };
});

describe('OTLP/HTTP to a local collector', () => {
  const collectors: Array<Awaited<ReturnType<typeof startCollector>>> = [];
  const otlp = async (refusing = false) => {
    const collector = await startCollector();
    collectors.push(collector);
    collector.refuse(refusing);
    return {
      tracer: new OtlpHttpTracer({ endpoint: collector.endpoint, serviceName: 'pxh-contract' }),
      exported: () => collector.spans,
      recover: () => collector.refuse(false),
    };
  };

  afterEach(async () => {
    for (const collector of collectors.splice(0)) await collector.close();
  });

  describeHarnessTracerContract('OtlpHttpTracer', () => otlp(), { refusing: () => otlp(true) });

  it('TR-8: flush waits for an export a full batch already started, so a stopping process loses no span', async () => {
    let releaseFirst: () => void = () => undefined;
    let started = 0;
    const exports: string[] = [];
    const fetch = async (_url: string, init: { body: string }) => {
      const count = (JSON.parse(init.body) as { resourceSpans: Array<{ scopeSpans: Array<{ spans: unknown[] }> }> })
        .resourceSpans[0]!.scopeSpans[0]!.spans.length;
      started += 1;
      if (started === 1) await new Promise<void>((resolve) => (releaseFirst = resolve));
      exports.push(`${String(count)} spans`);
      return { ok: true, status: 200 };
    };
    const tracer = new OtlpHttpTracer({ endpoint: 'http://collector.invalid', serviceName: 'pxh-contract', fetch, maxBatch: 2 });
    tracer.record(spanFixture());
    tracer.record(spanFixture());
    tracer.record(spanFixture());
    let flushed = false;
    const flushing = tracer.flush().then(() => {
      flushed = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(exports).to.deep.equal(['1 spans']);
    expect(flushed).to.equal(false);
    releaseFirst();
    await flushing;
    expect(exports).to.deep.equal(['1 spans', '2 spans']);
  });
});
