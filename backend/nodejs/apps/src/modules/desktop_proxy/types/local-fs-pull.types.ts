import { z } from 'zod';

/**
 * Contract for the server-driven Local FS sync. The connector service asks
 * for one page at a time; this route relays the ask to the user's desktop and
 * returns its answer.
 *
 * `orgId`/`userId` are deliberately absent from the body — they come from the
 * scoped token, so a caller cannot address another tenant's desktop.
 */
export const LocalFsPullEventsSchema = z.object({
  body: z.object({
    connectorId: z.string().min(1),
    runId: z.string().min(1),
    batchIndex: z.number().int().min(0),
    mode: z.enum(['FULL', 'INCREMENTAL']),
    cursor: z.string().nullable().optional(),
    maxEvents: z.number().int().min(1).max(1000),
    timeoutMs: z.number().int().min(1000).max(300_000),
  }),
});

export const LocalFsFetchContentSchema = z.object({
  body: z.object({
    connectorId: z.string().min(1),
    relPath: z.string().min(1),
    externalRecordId: z.string().min(1),
    sha256: z.string().nullable().optional(),
    timeoutMs: z.number().int().min(1000).max(300_000),
  }),
});

export interface LocalFsPullRequestPayload {
  connectorId: string;
  runId: string;
  batchIndex: number;
  mode: 'FULL' | 'INCREMENTAL';
  cursor?: string | null;
  maxEvents: number;
  timeoutMs: number;
}

export interface LocalFsFetchContentPayload {
  connectorId: string;
  relPath: string;
  externalRecordId: string;
  sha256?: string | null;
  timeoutMs: number;
}

export interface LocalFsFileEvent {
  type: string;
  path: string;
  oldPath?: string | null;
  timestamp: number;
  size?: number | null;
  isDirectory: boolean;
  sha256?: string | null;
  mimeType?: string | null;
}

export interface LocalFsPullResult {
  connectorId: string;
  runId: string;
  batchIndex: number;
  /**
   * Which machine answered. `run_sync` pins the first device it sees onto the
   * sync point and refuses later pages from a different one — two laptops
   * signed into one account would otherwise take turns pruning each other's
   * records.
   */
  deviceId?: string | null;
  cursor?: string | null;
  hasMore: boolean;
  events: LocalFsFileEvent[];
  rootPath?: string | null;
}

/** Desktop ack: either a page, or a failure it wants the server to see. */
export type LocalFsPullAck =
  | ({ ok: true } & LocalFsPullResult)
  | {
      ok: false;
      runId?: string;
      batchIndex?: number;
      error: { code: string; message: string; retryable: boolean };
    };

/** Desktop -> server: claim the connectors this machine holds the folder for. */
export interface DesktopRegisterPayload {
  connectorIds: string[];
  deviceId?: string | null;
}

export interface DesktopRegisterAck {
  accepted: string[];
  rejected: Array<{ connectorId: string; reason: string }>;
}

export interface DesktopUnregisterPayload {
  connectorIds: string[];
}

/** Server -> desktop: metadata ack for a content fetch. Bytes follow. */
export type LocalFsContentAck =
  | { ok: true; requestId: string; size: number; mimeType?: string | null }
  | {
      ok: false;
      requestId?: string;
      error: { code: string; message: string; retryable: boolean };
    };

export interface LocalFsContentChunkPayload {
  requestId: string;
  seq: number;
  data: ArrayBuffer | Buffer | Uint8Array;
  final?: boolean;
}

export interface LocalFsContentAbortPayload {
  requestId: string;
  error?: { code?: string; message?: string; retryable?: boolean };
}

/** Raised when no desktop socket is registered for the target connector. */
export class DesktopOfflineError extends Error {}

/** Raised when the desktop did not ack within its budget. */
export class DesktopTimeoutError extends Error {}

/**
 * Raised when the desktop answered but reported a failure. Distinct from the
 * two above because the desktop *is* reachable — the controller maps this to
 * 502 and passes `retryable` through so the connector can decide whether to
 * back off or give up.
 */
export class DesktopRemoteError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
  ) {
    super(message);
  }
}
