/**
 * Local FS protocol over the `/rest-proxy` socket: connector registration,
 * the metadata pull, and the chunked content transfer.
 *
 * Socket lifecycle (handshake, connect, disconnect) stays in the gateway; this
 * file only knows about the Local FS messages that ride on it.
 */
import { Socket } from 'socket.io';
import { Logger } from '../../../libs/services/logger.service';
import {
  DesktopOfflineError,
  DesktopRegisterAck,
  DesktopRemoteError,
  DesktopTimeoutError,
  LocalFsContentAbortPayload,
  LocalFsContentAck,
  LocalFsContentChunkPayload,
  LocalFsFetchContentPayload,
  LocalFsPullAck,
  LocalFsPullRequestPayload,
  LocalFsPullResult,
} from '../types/local-fs-pull.types';

/** Extra slack over the desktop's own budget, so a hang reads as a timeout here. */
const ACK_GRACE_MS = 5_000;
/**
 * Ceiling on a single file. Node buffers the whole transfer in memory before
 * answering the content route, so this bounds heap at
 * MAX_CONTENT_BYTES x MAX_CONCURRENT_CONTENT_PER_DEVICE per connected machine.
 */
export const MAX_CONTENT_BYTES = 100 * 1024 * 1024;
const MAX_CONCURRENT_CONTENT_PER_DEVICE = 3;
/** Under socket.io's 1MB default maxHttpBufferSize — do not raise that instead. */
export const CONTENT_CHUNK_BYTES = 256 * 1024;

export interface RelaySocketData {
  userId: string;
  orgId: string;
  deviceId?: string;
}

type RelaySocket = Socket<any, any, any, RelaySocketData>;

interface PendingContent {
  socket: RelaySocket;
  chunks: Buffer[];
  received: number;
  expectedSize: number | null;
  nextSeq: number;
  resolve: (value: Buffer) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
  settled: boolean;
}

function claimKey(orgId: string, connectorId: string): string {
  return `${orgId}:${connectorId}`;
}

function toBuffer(data: LocalFsContentChunkPayload['data']): Buffer | null {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (ArrayBuffer.isView(data)) {
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  }
  return null;
}

export class LocalFsRelay {
  private readonly logger = Logger.getInstance({ service: 'LocalFsRelay' });

  /**
   * Process-local: there is no socket.io Redis adapter on this namespace, so a
   * desktop connected to replica A is invisible to a pull that lands on
   * replica B and that pull answers DESKTOP_OFFLINE for a plainly-connected
   * machine. Deliberately out of scope — a multi-replica Node deployment needs
   * the adapter before Local FS works there.
   */
  private readonly claims = new Map<string, RelaySocket>();
  private readonly pendingContent = new Map<string, PendingContent>();
  private readonly contextByDevice = new Map<RelaySocket, Set<string>>();
  private nextRequestId = 1;

  /**
   * First claim wins for the life of the socket. A second machine is told
   * ALREADY_REGISTERED rather than silently taking over: whoever holds the
   * claim is also the device recorded on the sync point, and swapping owners
   * mid-life makes the next FULL run prune everything the first machine synced.
   */
  register(
    socket: RelaySocket,
    connectorIds: string[],
    deviceId?: string | null,
  ): DesktopRegisterAck {
    const ack: DesktopRegisterAck = { accepted: [], rejected: [] };
    const orgId = socket.data.orgId;
    if (deviceId) socket.data.deviceId = String(deviceId);

    for (const raw of connectorIds || []) {
      const connectorId = String(raw || '').trim();
      if (!connectorId) continue;
      const key = claimKey(orgId, connectorId);
      const holder = this.claims.get(key);
      if (holder && holder !== socket && holder.connected) {
        ack.rejected.push({ connectorId, reason: 'ALREADY_REGISTERED' });
        continue;
      }
      this.claims.set(key, socket);
      ack.accepted.push(connectorId);
    }
    this.logger.debug('Local FS desktop registered', {
      orgId,
      userId: socket.data.userId,
      accepted: ack.accepted.length,
      rejected: ack.rejected.length,
    });
    return ack;
  }

  unregister(socket: RelaySocket, connectorIds: string[]): void {
    const orgId = socket.data.orgId;
    for (const raw of connectorIds || []) {
      const key = claimKey(orgId, String(raw || '').trim());
      if (this.claims.get(key) === socket) this.claims.delete(key);
    }
  }

  handleDisconnect(socket: RelaySocket): void {
    for (const [key, holder] of this.claims) {
      if (holder === socket) this.claims.delete(key);
    }
    // Half-transferred buffers would otherwise sit in the heap until their
    // own timer fires, which for a large file is minutes away.
    const inFlight = this.contextByDevice.get(socket);
    if (inFlight) {
      for (const requestId of inFlight) {
        this.failContent(
          requestId,
          new DesktopOfflineError('Desktop disconnected mid-transfer'),
        );
      }
    }
    this.contextByDevice.delete(socket);
  }

  async requestFileEvents(
    orgId: string,
    userId: string,
    connectorId: string,
    payload: LocalFsPullRequestPayload,
  ): Promise<LocalFsPullResult> {
    const socket = this.resolveSocket(orgId, userId, connectorId);
    let ack: LocalFsPullAck;
    try {
      ack = (await socket
        .timeout(payload.timeoutMs + ACK_GRACE_MS)
        .emitWithAck('localfs:file-events:pull', payload)) as LocalFsPullAck;
    } catch (error) {
      throw new DesktopTimeoutError(
        `Desktop did not ack the pull within ${payload.timeoutMs + ACK_GRACE_MS}ms ` +
          `(connector=${connectorId} run=${payload.runId} batch=${payload.batchIndex})`,
      );
    }
    if (!ack || typeof ack !== 'object') {
      throw new DesktopRemoteError(
        'MALFORMED_ACK',
        'Desktop returned no pull ack body',
        false,
      );
    }
    if (ack.ok !== true) {
      const error = ack.error || {
        code: 'INTERNAL',
        message: 'Desktop reported a failure with no detail',
        retryable: true,
      };
      throw new DesktopRemoteError(
        String(error.code || 'INTERNAL'),
        String(error.message || 'Desktop could not serve the pull'),
        error.retryable !== false,
      );
    }
    const { ok: _ok, ...result } = ack;
    return {
      ...result,
      deviceId: result.deviceId ?? socket.data.deviceId ?? null,
    };
  }

  async requestContent(
    orgId: string,
    userId: string,
    connectorId: string,
    payload: LocalFsFetchContentPayload,
  ): Promise<Buffer> {
    const socket = this.resolveSocket(orgId, userId, connectorId);
    const inFlight = this.contextByDevice.get(socket);
    if (inFlight && inFlight.size >= MAX_CONCURRENT_CONTENT_PER_DEVICE) {
      throw new DesktopRemoteError(
        'CONTENT_BUSY',
        `Desktop already streaming ${inFlight.size} file(s) for this device`,
        true,
      );
    }

    const requestId = `lfc-${Date.now()}-${this.nextRequestId++}`;
    const transfer = new Promise<Buffer>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.failContent(
          requestId,
          new DesktopTimeoutError(
            `Desktop did not finish streaming ${payload.relPath} within ${payload.timeoutMs}ms`,
          ),
        );
      }, payload.timeoutMs + ACK_GRACE_MS);
      if (timer.unref) timer.unref();
      this.pendingContent.set(requestId, {
        socket,
        chunks: [],
        received: 0,
        expectedSize: null,
        nextSeq: 0,
        resolve,
        reject,
        timer,
        settled: false,
      });
    });
    // The transfer can be rejected (disconnect, abort) while we are still
    // awaiting the metadata ack below, i.e. before any caller has attached a
    // handler. Register one now so that window cannot surface as an
    // unhandled rejection and take the process down.
    transfer.catch(() => { /* the real handler is the returned promise */ });
    this.trackInFlight(socket, requestId);

    let ack: LocalFsContentAck;
    try {
      ack = (await socket
        .timeout(payload.timeoutMs + ACK_GRACE_MS)
        .emitWithAck('localfs:content:fetch', {
          ...payload,
          requestId,
          maxBytes: MAX_CONTENT_BYTES,
          chunkBytes: CONTENT_CHUNK_BYTES,
        })) as LocalFsContentAck;
    } catch {
      this.failContent(
        requestId,
        new DesktopTimeoutError(
          `Desktop did not ack the content fetch for ${payload.relPath}`,
        ),
      );
      return transfer;
    }

    if (!ack || ack.ok !== true) {
      const error = (ack && ack.error) || {
        code: 'INTERNAL',
        message: 'Desktop could not serve the file',
        retryable: true,
      };
      this.failContent(
        requestId,
        new DesktopRemoteError(
          String(error.code || 'INTERNAL'),
          String(error.message || 'Desktop could not serve the file'),
          error.retryable !== false,
        ),
      );
      return transfer;
    }

    if (Number(ack.size) > MAX_CONTENT_BYTES) {
      this.failContent(
        requestId,
        new DesktopRemoteError(
          'CONTENT_TOO_LARGE',
          `${payload.relPath} is ${ack.size} bytes, over the ${MAX_CONTENT_BYTES} limit`,
          false,
        ),
      );
      return transfer;
    }
    const pending = this.pendingContent.get(requestId);
    if (pending) pending.expectedSize = Number(ack.size);
    return transfer;
  }

  handleContentChunk(socket: RelaySocket, payload: LocalFsContentChunkPayload): void {
    const requestId = String(payload?.requestId || '');
    const pending = this.pendingContent.get(requestId);
    if (!pending) return;
    if (pending.socket !== socket) {
      // A different machine answering this requestId is either a bug or an
      // attempt to feed bytes into someone else's transfer.
      this.failContent(
        requestId,
        new DesktopRemoteError(
          'CHUNK_FROM_WRONG_DEVICE',
          `Chunk for ${requestId} arrived from a socket that did not own it`,
          false,
        ),
      );
      return;
    }
    // Sequence-checked rather than reordered: socket.io preserves order on one
    // connection, so a gap means frames were dropped and the file would be
    // silently corrupt.
    if (Number(payload.seq) !== pending.nextSeq) {
      this.failContent(
        requestId,
        new DesktopRemoteError(
          'CHUNK_OUT_OF_ORDER',
          `Expected chunk ${pending.nextSeq} for ${requestId}, got ${payload.seq}`,
          true,
        ),
      );
      return;
    }
    const buf = toBuffer(payload.data);
    if (!buf) {
      this.failContent(
        requestId,
        new DesktopRemoteError(
          'MALFORMED_CHUNK',
          `Chunk ${payload.seq} for ${requestId} was not binary`,
          false,
        ),
      );
      return;
    }
    pending.nextSeq += 1;
    pending.received += buf.length;
    if (pending.received > MAX_CONTENT_BYTES) {
      this.failContent(
        requestId,
        new DesktopRemoteError(
          'CONTENT_TOO_LARGE',
          `Transfer ${requestId} exceeded the ${MAX_CONTENT_BYTES} byte limit`,
          false,
        ),
      );
      return;
    }
    pending.chunks.push(buf);

    if (payload.final === true) {
      if (
        pending.expectedSize !== null &&
        pending.received !== pending.expectedSize
      ) {
        this.failContent(
          requestId,
          new DesktopRemoteError(
            'CONTENT_SIZE_MISMATCH',
            `Expected ${pending.expectedSize} bytes for ${requestId}, received ${pending.received}`,
            true,
          ),
        );
        return;
      }
      this.settleContent(requestId, Buffer.concat(pending.chunks));
    }
  }

  handleContentAbort(socket: RelaySocket, payload: LocalFsContentAbortPayload): void {
    const requestId = String(payload?.requestId || '');
    const pending = this.pendingContent.get(requestId);
    if (!pending || pending.socket !== socket) return;
    const error = payload?.error || {};
    this.failContent(
      requestId,
      new DesktopRemoteError(
        String(error.code || 'CONTENT_ABORTED'),
        String(error.message || 'Desktop aborted the transfer'),
        error.retryable === true,
      ),
    );
  }

  private resolveSocket(
    orgId: string,
    userId: string,
    connectorId: string,
  ): RelaySocket {
    const socket = this.claims.get(claimKey(orgId, connectorId));
    // No fallback to "some socket this user has open": the connector is bound
    // to one folder on one machine, and picking another would sync the wrong
    // disk into it.
    if (!socket || !socket.connected || socket.data.userId !== userId) {
      throw new DesktopOfflineError(
        `No desktop registered for connector ${connectorId}`,
      );
    }
    return socket;
  }

  private trackInFlight(socket: RelaySocket, requestId: string): void {
    let set = this.contextByDevice.get(socket);
    if (!set) {
      set = new Set();
      this.contextByDevice.set(socket, set);
    }
    set.add(requestId);
  }

  private releaseInFlight(socket: RelaySocket, requestId: string): void {
    const set = this.contextByDevice.get(socket);
    if (!set) return;
    set.delete(requestId);
    if (set.size === 0) this.contextByDevice.delete(socket);
  }

  private settleContent(requestId: string, body: Buffer): void {
    const pending = this.pendingContent.get(requestId);
    if (!pending || pending.settled) return;
    pending.settled = true;
    clearTimeout(pending.timer);
    this.pendingContent.delete(requestId);
    this.releaseInFlight(pending.socket, requestId);
    pending.chunks.length = 0;
    pending.resolve(body);
  }

  private failContent(requestId: string, error: Error): void {
    const pending = this.pendingContent.get(requestId);
    if (!pending || pending.settled) return;
    pending.settled = true;
    clearTimeout(pending.timer);
    this.pendingContent.delete(requestId);
    this.releaseInFlight(pending.socket, requestId);
    pending.chunks.length = 0;
    pending.reject(error);
  }
}
