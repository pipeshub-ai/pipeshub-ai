/**
 * OTLP over HTTP (JSON encoding, `POST <endpoint>/v1/traces`) for the harness
 * tracer (P9). Node ships no OpenTelemetry SDK, and the harness records a few
 * spans with ids it derives itself, so this adapter encodes them directly.
 * Spans are batched and posted in the background; an export failure is logged
 * once per burst and never reaches the request that recorded the span.
 */
import {
  HarnessSpan,
  HarnessSpanAttribute,
  HarnessTracer,
} from '../../application/harness-tracing';
import {
  HarnessLogger,
  silentHarnessLogger,
} from '../../application/harness-log';
import { envFlag, EnvFlagEnvironment } from '../../../../libs/utils/env-flags';
import { noopHarnessTracer } from '../../application/harness-tracing';

const SCOPE = 'pipeshub.agent_harness';
const KIND = { internal: 1, server: 2, client: 3 } as const;
const STATUS_ERROR = 2;
const DEFAULT_ENDPOINT = 'http://localhost:4318';

type Fetch = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ ok: boolean; status: number }>;

export interface OtlpHttpTracerOptions {
  endpoint: string;
  serviceName: string;
  fetch?: Fetch;
  logger?: HarnessLogger;
  flushIntervalMs?: number;
  maxBatch?: number;
}

const otlpValue = (value: HarnessSpanAttribute): Record<string, unknown> => {
  if (typeof value === 'boolean') return { boolValue: value };
  if (typeof value === 'number') {
    return Number.isInteger(value)
      ? { intValue: String(value) }
      : { doubleValue: value };
  }
  return { stringValue: value };
};

const nanos = (ms: number): string => `${String(Math.round(ms))}000000`;

/** One OTLP `ExportTraceServiceRequest` in its JSON encoding. */
export function otlpRequest(
  serviceName: string,
  spans: readonly HarnessSpan[],
): Record<string, unknown> {
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [
            { key: 'service.name', value: { stringValue: serviceName } },
          ],
        },
        scopeSpans: [
          {
            scope: { name: SCOPE },
            spans: spans.map((span) => {
              const attributes = { ...span.attributes };
              if (span.errorType !== undefined)
                attributes['error.type'] = span.errorType;
              return {
                traceId: span.traceId,
                spanId: span.spanId,
                ...(span.parentSpanId !== undefined
                  ? { parentSpanId: span.parentSpanId }
                  : {}),
                name: span.name,
                kind: KIND[span.kind],
                startTimeUnixNano: nanos(span.startMs),
                endTimeUnixNano: nanos(span.endMs),
                attributes: Object.entries(attributes).map(([key, value]) => ({
                  key,
                  value: otlpValue(value),
                })),
                ...(span.errorType !== undefined
                  ? { status: { code: STATUS_ERROR } }
                  : {}),
              };
            }),
          },
        ],
      },
    ],
  };
}

export class OtlpHttpTracer implements HarnessTracer {
  readonly enabled = true;
  private readonly buffer: HarnessSpan[] = [];
  private readonly fetch: Fetch;
  private readonly logger: HarnessLogger;
  private readonly maxBatch: number;
  private readonly url: string;
  private timer: NodeJS.Timeout | null = null;
  private failing = false;
  private readonly inflight = new Set<Promise<void>>();

  constructor(private readonly options: OtlpHttpTracerOptions) {
    this.fetch = options.fetch ?? ((url, init) => fetch(url, init));
    this.logger = options.logger ?? silentHarnessLogger;
    this.maxBatch = options.maxBatch ?? 256;
    this.url = `${options.endpoint.replace(/\/+$/, '')}/v1/traces`;
  }

  record(span: HarnessSpan): void {
    this.buffer.push(span);
    if (this.buffer.length >= this.maxBatch) {
      void this.flush();
      return;
    }
    if (this.timer === null) {
      this.timer = setTimeout(() => {
        this.timer = null;
        void this.flush();
      }, this.options.flushIntervalMs ?? 2_000);
      this.timer.unref();
    }
  }

  /** Also waits for exports a full batch started, so a stopping process loses none. */
  async flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const pending = [...this.inflight];
    const spans = this.buffer.splice(0, this.buffer.length);
    if (spans.length > 0) {
      const exporting = this.export(spans);
      this.inflight.add(exporting);
      void exporting.finally(() => this.inflight.delete(exporting));
      pending.push(exporting);
    }
    await Promise.all(pending);
  }

  private async export(spans: HarnessSpan[]): Promise<void> {
    try {
      const response = await this.fetch(this.url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(otlpRequest(this.options.serviceName, spans)),
      });
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      this.failing = false;
    } catch (error) {
      if (!this.failing) {
        this.logger.warn('telemetry.export_failed', {
          event: 'telemetry.export_failed',
          spans: spans.length,
          errorType: error instanceof Error ? error.name : typeof error,
        });
      }
      this.failing = true;
    }
  }
}

/** `HARNESS_OTEL` on: OTLP to `OTEL_EXPORTER_OTLP_ENDPOINT` as `OTEL_SERVICE_NAME`; off: the no-op tracer. */
export function harnessTracerFromEnv(
  env: EnvFlagEnvironment = process.env,
  logger?: HarnessLogger,
): HarnessTracer {
  if (!envFlag('HARNESS_OTEL', env)) return noopHarnessTracer;
  return new OtlpHttpTracer({
    endpoint:
      (env['OTEL_EXPORTER_OTLP_ENDPOINT'] ?? '').trim() || DEFAULT_ENDPOINT,
    serviceName: (env['OTEL_SERVICE_NAME'] ?? '').trim() || 'pipeshub-node',
    ...(logger !== undefined ? { logger } : {}),
  });
}
