import { io, type Socket } from 'socket.io-client';
import type { DesktopCredentialsStore } from '../persistence/credentials';
import type { ServePullRequest, ServePullResponse } from '../pull-responder-types';
import type { ContentFetchRequest, ContentStreamAck } from './content-streamer';

/**
 * The gateway's namespace and its socket.io path are different strings and
 * both are required — conflating them yields a connection that never
 * establishes and no useful error.
 */
const NAMESPACE = '/rest-proxy';
const SOCKET_PATH = '/socket.io-rest-proxy';
const RECONNECT_DELAY_MS = 2_000;
const RECONNECT_DELAY_MAX_MS = 60_000;
/** Give up re-minting after this many consecutive auth failures until a new login. */
const MAX_AUTH_RETRIES = 5;
/** `local-sync/start` must not return until the claim is ack'd, or the enable pull races it. */
const REGISTER_TIMEOUT_MS = 5_000;

export interface DesktopSocketDeps {
  credentials: DesktopCredentialsStore;
  /** Connector ids this machine actually has a configured folder for. */
  listConnectorIds: () => string[];
  servePull: (request: ServePullRequest) => Promise<ServePullResponse>;
  serveContent: (
    request: ContentFetchRequest,
    emitChunk: (seq: number, data: Buffer, final: boolean) => void,
    abort: (error: { code: string; message: string; retryable: boolean }) => void,
  ) => Promise<ContentStreamAck>;
  /**
   * Called with the register ack. A connector in `rejected` is owned by
   * another machine and must not be served from here.
   */
  onRegistration?: (result: DesktopRegisterAck) => void;
  log?: (message: string, ...args: unknown[]) => void;
}

export interface DesktopRegisterAck {
  accepted: string[];
  rejected: Array<{ connectorId: string; reason: string }>;
}

type PullAckFn = (response: ServePullResponse & { deviceId?: string }) => void;
type ContentAckFn = (response: ContentStreamAck) => void;

export class DesktopSocketClient {
  private socket: Socket | null = null;
  private authFailures = 0;
  private stopped = false;
  private readonly log: (message: string, ...args: unknown[]) => void;

  constructor(private readonly deps: DesktopSocketDeps) {
    this.log = deps.log || ((msg, ...args) => console.log('[desktop-socket]', msg, ...args));
  }

  /**
   * Bring the socket up if a credential exists. Safe to call repeatedly — the
   * renderer hands credentials over on every login, and a boot with no
   * credential must wait for that IPC rather than retrying against a 401.
   */
  async connect(): Promise<void> {
    this.stopped = false;
    if (this.socket) return;
    const { credentials } = this.deps;
    if (!credentials.hasCredential()) {
      this.log('no stored credential yet; waiting for sign-in');
      return;
    }
    const baseUrl = credentials.apiBaseUrl;
    if (!baseUrl) return;

    this.authFailures = 0;
    this.socket = io(`${baseUrl}${NAMESPACE}`, {
      path: SOCKET_PATH,
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: RECONNECT_DELAY_MS,
      reconnectionDelayMax: RECONNECT_DELAY_MAX_MS,
      // Function form so every attempt — including reconnects after the token
      // expired — mints a fresh token. A value captured at construction makes
      // the first reconnect past expiry fail the handshake forever.
      auth: (cb: (data: Record<string, unknown>) => void) => {
        void credentials
          .getAccessToken()
          // extractToken splits on a space and requires the literal
          // "Bearer <token>" form; a bare token is rejected at the handshake.
          .then((token) => cb({ token: token ? `Bearer ${token}` : '' }))
          .catch(() => cb({ token: '' }));
      },
    });

    this.attachListeners(this.socket);
  }

  /** Sign-in / server change: drop the old socket and come back up with the new credential. */
  async reconnectWithNewCredential(): Promise<void> {
    this.disconnect();
    await this.connect();
  }

  disconnect(): void {
    this.stopped = true;
    if (!this.socket) return;
    this.socket.removeAllListeners();
    this.socket.disconnect();
    this.socket = null;
  }

  get connected(): boolean {
    return this.socket?.connected === true;
  }

  /** Release a claim so another machine can take the folder without waiting for quit. */
  unregister(connectorIds: string[]): void {
    if (!this.socket?.connected || connectorIds.length === 0) return;
    this.socket.emit('desktop:unregister', { connectorIds });
  }

  /**
   * Re-announce the connector set. Resolves when the gateway acks (or the
   * wait times out / the socket is down). Callers that then publish an
   * immediate pull — `local-sync/start` before toggle-on — must await this
   * so Node's claim map is populated first.
   */
  async register(timeoutMs: number = REGISTER_TIMEOUT_MS): Promise<DesktopRegisterAck | null> {
    const socket = this.socket;
    if (!socket || this.stopped) return null;
    if (!socket.connected) {
      const connected = await this.waitForConnect(socket, timeoutMs);
      if (!connected || this.stopped) return null;
    }
    return this.emitRegister(socket, timeoutMs);
  }

  private waitForConnect(socket: Socket, timeoutMs: number): Promise<boolean> {
    if (socket.connected) return Promise.resolve(true);
    return new Promise((resolve) => {
      const onConnect = (): void => {
        cleanup();
        resolve(true);
      };
      const timer = setTimeout(() => {
        cleanup();
        resolve(false);
      }, timeoutMs);
      const cleanup = (): void => {
        clearTimeout(timer);
        socket.off('connect', onConnect);
      };
      socket.once('connect', onConnect);
    });
  }

  private emitRegister(
    socket: Socket,
    timeoutMs: number,
  ): Promise<DesktopRegisterAck | null> {
    const connectorIds = this.deps.listConnectorIds();
    return new Promise((resolve) => {
      let settled = false;
      const finish = (ack: DesktopRegisterAck | null): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(ack);
      };
      const timer = setTimeout(() => {
        this.log('register ack timed out');
        finish(null);
      }, timeoutMs);
      socket.emit(
        'desktop:register',
        { connectorIds, deviceId: this.deps.credentials.deviceId },
        (ack: DesktopRegisterAck | undefined) => {
          if (ack?.rejected?.length) {
            this.log(
              `registration rejected for ${ack.rejected.map((r) => r.connectorId).join(', ')} ` +
              '— another machine already syncs those folders',
            );
          }
          if (ack) this.deps.onRegistration?.(ack);
          finish(ack ?? null);
        },
      );
    });
  }

  private attachListeners(socket: Socket): void {
    socket.on('connect', () => {
      this.authFailures = 0;
      this.log(`connected as device ${this.deps.credentials.deviceId}`);
      void this.register();
    });

    socket.on('connect_error', (error: Error) => {
      const message = String(error?.message || '');
      const looksLikeAuth = /token|auth|unauthor|expired/i.test(message);
      if (!looksLikeAuth) {
        this.log(`connect error: ${message}`);
        return;
      }
      this.authFailures += 1;
      if (this.authFailures > MAX_AUTH_RETRIES) {
        this.log(`giving up after ${MAX_AUTH_RETRIES} auth failures; sign in again to resume`);
        socket.io.opts.reconnection = false;
        socket.disconnect();
        return;
      }
      // Force a fresh mint before socket.io's own retry re-invokes `auth`.
      this.deps.credentials.invalidateAccessToken();
    });

    socket.on('disconnect', (reason: string) => {
      this.log(`disconnected (${reason})`);
      if (this.stopped) return;
      // socket.io does not auto-reconnect after a server-side disconnect.
      if (reason === 'io server disconnect') socket.connect();
    });

    socket.on(
      'localfs:file-events:pull',
      (request: ServePullRequest, ack?: PullAckFn) => {
        void this.handlePull(request, ack);
      },
    );

    socket.on(
      'localfs:content:fetch',
      (request: ContentFetchRequest, ack?: ContentAckFn) => {
        void this.handleContentFetch(socket, request, ack);
      },
    );
  }

  private async handlePull(request: ServePullRequest, ack?: PullAckFn): Promise<void> {
    if (!ack) return;
    try {
      const response = await this.deps.servePull(request);
      ack({ ...response, deviceId: this.deps.credentials.deviceId });
    } catch (error) {
      ack({
        ok: false,
        runId: request?.runId,
        batchIndex: request?.batchIndex,
        error: {
          code: 'INTERNAL',
          message: error instanceof Error ? error.message : String(error),
          retryable: true,
        },
      });
    }
  }

  private async handleContentFetch(
    socket: Socket,
    request: ContentFetchRequest,
    ack?: ContentAckFn,
  ): Promise<void> {
    if (!ack) return;
    const requestId = request?.requestId;
    const emitChunk = (seq: number, data: Buffer, final: boolean): void => {
      socket.emit('localfs:content:chunk', { requestId, seq, data, final });
    };
    const abort = (error: { code: string; message: string; retryable: boolean }): void => {
      // Bail out loudly rather than letting the server wait out its budget —
      // every content timeout burns one of the indexing consumer's retries.
      socket.emit('localfs:content:abort', { requestId, error });
    };
    try {
      ack(await this.deps.serveContent(request, emitChunk, abort));
    } catch (error) {
      ack({
        ok: false,
        requestId,
        error: {
          code: 'INTERNAL',
          message: error instanceof Error ? error.message : String(error),
          retryable: true,
        },
      });
    }
  }
}
