/**
 * Contract for the server-driven Local FS sync. The connector service asks
 * for one page at a time; this route relays the ask to the user's desktop and
 * returns its answer.
 *
 * `orgId`/`userId` are deliberately absent from the body — they come from the
 * scoped token, so a caller cannot address another tenant's desktop.
 */

export interface LocalFsPullRequestPayload {
  connectorId: string;
  /** The connector's owner device; the pull is routed to its socket only. */
  deviceId: string;
  runId: string;
  batchIndex: number;
  mode: 'FULL' | 'INCREMENTAL';
  cursor?: string | null;
  maxEvents: number;
  timeoutMs: number;
}

export interface LocalFsFetchContentPayload {
  connectorId: string;
  deviceId: string;
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
  /** File modification time. Absent on deletions and pre-upgrade desktops. */
  mtimeMs?: number | null;
  /** Inode birth time. Absent on deletions and when the platform/filesystem doesn't report one. */
  birthtimeMs?: number | null;
}

export interface LocalFsPullResult {
  connectorId: string;
  runId: string;
  batchIndex: number;
  /**
   * Which machine answered. `run_sync` refuses a page from any device other
   * than the connector's owner — two laptops signed into one account would
   * otherwise take turns pruning each other's records.
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
      deviceId?: string | null;
      error: { code: string; message: string; retryable: boolean };
    };

/** Desktop -> server: identify the machine behind this socket. */
export interface DesktopRegisterPayload {
  deviceId?: string | null;
  deviceName?: string | null;
}

export type DesktopRegisterAck =
  | { ok: true }
  | { ok: false; reason: 'MISSING_DEVICE_ID' };

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

/** Raised when the connector's owner device has no connected socket. */
export class DesktopOfflineError extends Error {}

/** Raised when the desktop did not ack within its budget. */
export class DesktopTimeoutError extends Error {}

/**
 * Raised when the desktop answered but reported a failure. Distinct from the
 * two above because the desktop *is* reachable — the controller maps this to
 * 502 and passes `retryable` through so the connector can decide whether to
 * back off or give up.
 *
 * `deviceId` names the machine that refused. The connector compares it with
 * the owner device on the connector before reading `code`: a non-owner
 * usually fails the pull (the owner's folder is not on its disk), and that
 * failure would otherwise hide the ownership mismatch.
 */
export class DesktopRemoteError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly deviceId: string | null = null,
  ) {
    super(message);
  }
}
