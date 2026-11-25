/**
 * Tracing for the harness (P9), Node's side: the run's admission span, the root
 * of the run's trace. Every process that knows the run id derives the same
 * trace context (`contracts/harness/vectors/trace-ids.json`; Python's
 * `harness_core.telemetry.ids`), so the worker, the gateways and this span join
 * one trace without a header or a run-document field. Off (`HARNESS_OTEL`
 * unset): the no-op tracer, nothing recorded. No span carries content.
 */
import { createHash } from 'node:crypto';

export type HarnessSpanKind = 'internal' | 'client' | 'server';
export type HarnessSpanAttribute = string | number | boolean;

export interface HarnessSpan {
  name: string;
  kind: HarnessSpanKind;
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  startMs: number;
  endMs: number;
  attributes: Record<string, HarnessSpanAttribute>;
  /** `error.type`: the reason code the operation ended with. */
  errorType?: string;
}

export interface HarnessTracer {
  readonly enabled: boolean;
  record(span: HarnessSpan): void;
  /** Exports what is buffered (a stopping process calls it). */
  flush(): Promise<void>;
}

export const noopHarnessTracer: HarnessTracer = {
  enabled: false,
  record: () => undefined,
  flush: () => Promise.resolve(),
};

export class InMemoryHarnessTracer implements HarnessTracer {
  readonly enabled = true;
  readonly spans: HarnessSpan[] = [];

  record(span: HarnessSpan): void {
    this.spans.push(span);
  }

  flush(): Promise<void> {
    return Promise.resolve();
  }
}

const RUN_PREFIX = 'pipeshub.run/';

const hexOf = (text: string, size: number): string => {
  const digest = createHash('sha256').update(text).digest('hex').slice(0, size);
  // An all-zero id is invalid in OpenTelemetry (astronomically rare for a sha256 prefix, but defined).
  return /^0+$/.test(digest) ? '1'.padStart(size, '0') : digest;
};

export const runTraceId = (runId: string): string =>
  hexOf(RUN_PREFIX + runId, 32);

export const runRootSpanId = (runId: string): string =>
  hexOf(`${RUN_PREFIX}${runId}/admit`, 16);

export interface AdmissionSpanInput {
  orgId: string;
  sessionId: string;
  runId: string;
  startMs: number;
  endMs: number;
  profile?: string;
  harnessMajor?: number;
  replayed?: boolean;
  agentKey?: string;
}

/** The run's root: what Node did to admit it (ids, enums and timings only). */
export function admissionSpan(input: AdmissionSpanInput): HarnessSpan {
  const attributes: Record<string, HarnessSpanAttribute> = {
    'pipeshub.org_id': input.orgId,
    'pipeshub.session_id': input.sessionId,
    'pipeshub.run_id': input.runId,
    'gen_ai.conversation.id': input.sessionId,
  };
  if (input.profile !== undefined)
    attributes['pipeshub.profile'] = input.profile;
  if (input.harnessMajor !== undefined)
    attributes['pipeshub.harness_major'] = input.harnessMajor;
  if (input.replayed !== undefined)
    attributes['pipeshub.replayed'] = input.replayed;
  if (input.agentKey !== undefined)
    attributes['gen_ai.agent.id'] = input.agentKey;
  return {
    name: 'pipeshub.run.admit',
    kind: 'server',
    traceId: runTraceId(input.runId),
    spanId: runRootSpanId(input.runId),
    startMs: input.startMs,
    endMs: input.endMs,
    attributes,
  };
}
