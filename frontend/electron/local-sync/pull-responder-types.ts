import type { WatchEvent } from './watcher/replay-event-expander';

export type SyncMode = 'FULL' | 'INCREMENTAL';

/** Server -> desktop, one page of a run. */
export interface ServePullRequest {
  connectorId: string;
  runId: string;
  batchIndex: number;
  mode: SyncMode;
  cursor?: string | null;
  maxEvents: number;
  timeoutMs?: number;
}

export interface ServePullPage {
  ok: true;
  connectorId: string;
  runId: string;
  batchIndex: number;
  cursor: string | null;
  hasMore: boolean;
  events: WatchEvent[];
  rootPath: string | null;
}

/** Codes the connector already understands; anything else lands as INTERNAL. */
export type PullErrorCode =
  | 'ROOT_UNREADABLE'
  | 'CURSOR_UNKNOWN'
  | 'STALE_RUN'
  | 'BUSY'
  | 'CONFIG_MISMATCH'
  | 'INTERNAL';

export interface ServePullFailure {
  ok: false;
  connectorId?: string;
  runId: string;
  batchIndex: number;
  error: { code: PullErrorCode; message: string; retryable: boolean };
}

export type ServePullResponse = ServePullPage | ServePullFailure;

/**
 * Opaque-to-the-server position token.
 *
 * A sequence, never a timestamp: two files written in the same millisecond
 * straddle a time boundary and get silently lost or duplicated. The mode is
 * part of the token because a FULL position (a disk-walk path) and an
 * INCREMENTAL one (a journal batch) are not interchangeable, and `v` lets a
 * future desktop reject tokens it no longer understands with CURSOR_UNKNOWN.
 */
export type CursorToken =
  | { v: 1; mode: 'INCREMENTAL'; afterBatchId: string }
  | { v: 1; mode: 'FULL'; afterPath: string };

const CURSOR_VERSION = 1;

export function encodeCursor(token: CursorToken): string {
  return Buffer.from(JSON.stringify(token), 'utf8').toString('base64');
}

/** Returns null for anything unparseable, versioned differently, or in the wrong mode. */
export function decodeCursor(
  raw: string | null | undefined,
  expectedMode: SyncMode,
): CursorToken | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(String(raw), 'base64').toString('utf8'));
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const token = parsed as Partial<CursorToken>;
  if (token.v !== CURSOR_VERSION || token.mode !== expectedMode) return null;
  if (token.mode === 'INCREMENTAL') {
    return typeof token.afterBatchId === 'string' && token.afterBatchId
      ? { v: 1, mode: 'INCREMENTAL', afterBatchId: token.afterBatchId }
      : null;
  }
  return typeof token.afterPath === 'string'
    ? { v: 1, mode: 'FULL', afterPath: token.afterPath }
    : null;
}

export function pullFailure(
  request: Pick<ServePullRequest, 'connectorId' | 'runId' | 'batchIndex'>,
  code: PullErrorCode,
  message: string,
  retryable: boolean,
): ServePullFailure {
  return {
    ok: false,
    connectorId: request.connectorId,
    runId: request.runId,
    batchIndex: request.batchIndex,
    error: { code, message, retryable },
  };
}
