/**
 * Mirror of Python `app/utils/sse_contract.py` — PipesHub SSE v2 protocol types.
 * Used by enterprise_search proxy and frontend when streamProtocolVersion >= 2.
 */

export const PROTOCOL_NAME_V2 = 'pipeshub.sse.v2' as const;
export const PROTOCOL_V1 = 1;
export const PROTOCOL_V2 = 2;

export interface SSEProtocolAnnouncement {
  protocol: typeof PROTOCOL_NAME_V2;
  v: typeof PROTOCOL_V2;
}

export interface StreamErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
  transient: boolean;
  details?: Record<string, unknown>;
}

/** Full envelope on v2 events (JSON body of SSE `data:`) */
export interface SSEEnvelopeV2 {
  v: 2;
  type: string;
  id: string;
  runId: string;
  conversationId: string | null | undefined;
  messageId: string | null | undefined;
  parentId: string | null | undefined;
  ts: number;
  seq: number;
  data: Record<string, unknown>;
}

export interface StreamTraceHit {
  virtualRecordId?: string;
  recordId?: string;
  title?: string;
  snippet?: string;
  score?: number;
  connector?: string;
  mimeType?: string;
  url?: string;
}

export interface StreamTraceRetrieval {
  query: string;
  source: string;
  hits: StreamTraceHit[];
}

export interface StreamTraceToolCall {
  callId: string;
  name: string;
  args: unknown;
  observation: string;
  latencyMs: number;
  error?: string | null;
}

export interface StreamTraceStep {
  stepId: string;
  name: string;
  summary?: string;
  error?: string;
}

/** Persisted on assistant messages when v2 stream completes */
export interface StreamTrace {
  reasoningSummary?: string;
  retrieval?: StreamTraceRetrieval[];
  toolCalls?: StreamTraceToolCall[];
  steps?: StreamTraceStep[];
}

/** Terminal payload from Python (mirrors IAIResponse + streamTrace) */
export interface AssistantMessageEndPayload {
  messageId: string;
  answer: string;
  citations: unknown[];
  confidence?: unknown;
  reason?: unknown;
  referenceData?: unknown[];
  streamTrace?: StreamTrace;
}

/** Parse SSE `assistant_message_end` JSON (v2 envelope or flat) for streamTrace merge */
export function extractStreamTraceFromAssistantMessageEndWire(
  raw: unknown,
): StreamTrace | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const top = raw as Record<string, unknown>;
  const inner =
    top.data !== undefined && typeof top.data === 'object' && top.data !== null
      ? (top.data as Record<string, unknown>)
      : top;
  const st = inner.streamTrace;
  if (st && typeof st === 'object') {
    return st as StreamTrace;
  }
  return undefined;
}
