/**
 * Socket.IO gateway for desktop clients: authenticates the handshake on
 * `/rest-proxy`, keeps re-checking the session behind each connected socket,
 * and routes Local FS messages to `LocalFsRelay`.
 */
import { Server as HttpServer } from 'http';
import { Namespace, Server, Socket } from 'socket.io';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { findSessionRefusal, findSessionRefusals } from '../../../config';
import { LocalFsRelay } from './local-fs-relay';
import {
  DesktopRegisterAck,
  DesktopRegisterPayload,
  LocalFsContentAbortPayload,
  LocalFsContentChunkPayload,
  LocalFsFetchContentPayload,
  LocalFsPullRequestPayload,
  LocalFsPullResult,
} from '../types/local-fs.types';

type RestProxySocketData = {
  userId: string;
  orgId: string;
  /** `iat` and `email` of the handshake token, for re-checking the session later. */
  issuedAt?: number;
  email?: string;
  deviceId?: string;
  deviceName?: string;
};

type ClientToServerEvents = {
  'desktop:register': (
    payload: DesktopRegisterPayload,
    ack?: (res: DesktopRegisterAck) => void,
  ) => void;
  'localfs:content:chunk': (payload: LocalFsContentChunkPayload) => void;
  'localfs:content:abort': (payload: LocalFsContentAbortPayload) => void;
};

/** The edition's session rules; see findSessionRefusal in config.ts. */
export interface DesktopSessionChecker {
  findSessionRefusal: typeof findSessionRefusal;
  findSessionRefusals: typeof findSessionRefusals;
}

type ServerToClientEvents = Record<string, never>;

type InterServerEvents = Record<string, never>;

type RestProxySocket = Socket<
  ClientToServerEvents,
  ServerToClientEvents,
  InterServerEvents,
  RestProxySocketData
>;

const NAMESPACE = '/rest-proxy';
const SOCKET_PATH = '/socket.io-rest-proxy';
// A socket outlives the token it connected with, so removing, disabling or
// logging out a user has to reach the sockets already open too. This bounds
// how long one survives that on any replica.
const SESSION_RECHECK_INTERVAL_MS = 30_000;

export class DesktopProxySocketGateway {
  private readonly logger = Logger.getInstance({
    service: 'DesktopProxySocketGateway',
  });
  private io: Server | null = null;
  private namespace: Namespace | null = null;
  private readonly localFsRelay = new LocalFsRelay();
  private sessionRecheckTimer: NodeJS.Timeout | null = null;
  private sessionRecheckRunning = false;

  constructor(
    private readonly authTokenService: AuthTokenService,
    private readonly sessions: DesktopSessionChecker = {
      findSessionRefusal,
      findSessionRefusals,
    },
  ) {}

  initialize(server: HttpServer): void {
    const rawOrigins = process.env.ALLOWED_ORIGINS;
    const parsedOrigins =
      rawOrigins !== undefined && rawOrigins.length > 0
        ? rawOrigins
            .split(',')
            .map((o) => o.trim())
            .filter((o) => o.length > 0)
        : [];
    const allowedOrigins: string[] | false =
      parsedOrigins.length > 0 ? parsedOrigins : false;
    this.io = new Server(server, {
      path: SOCKET_PATH,
      cors: {
        origin: allowedOrigins,
        methods: ['GET', 'POST', 'PUT', 'PATCH', 'OPTIONS', 'DELETE'],
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
      },
    });
    this.namespace = this.io.of(NAMESPACE);
    this.namespace.use((socket: RestProxySocket, next) => {
      void this.authenticateHandshake(socket).then(next);
    });

    this.namespace.on('connection', (socket: RestProxySocket) => {
      socket.join(`${socket.data.orgId}:${socket.data.userId}`);

      socket.on(
        'desktop:register',
        (
          payload: DesktopRegisterPayload,
          ack?: (res: DesktopRegisterAck) => void,
        ) => {
          const result = this.localFsRelay.register(
            socket,
            payload?.deviceId,
            payload?.deviceName,
          );
          if (ack) ack(result);
        },
      );

      socket.on('localfs:content:chunk', (payload: LocalFsContentChunkPayload) => {
        this.localFsRelay.handleContentChunk(socket, payload);
      });

      socket.on('localfs:content:abort', (payload: LocalFsContentAbortPayload) => {
        this.localFsRelay.handleContentAbort(socket, payload);
      });

      socket.on('disconnect', () => {
        this.localFsRelay.handleDisconnect(socket);
      });
    });

    this.sessionRecheckTimer = setInterval(() => {
      void this.disconnectRevokedSessions();
    }, SESSION_RECHECK_INTERVAL_MS);
    this.sessionRecheckTimer.unref();

    this.logger.info('Desktop relay Socket.IO namespace initialized');
  }

  shutdown(): void {
    if (this.sessionRecheckTimer) {
      clearInterval(this.sessionRecheckTimer);
      this.sessionRecheckTimer = null;
    }
    this.namespace?.disconnectSockets(true);
    this.namespace = null;
    void this.io?.close();
    this.io = null;
  }

  /** False until initialize() runs, which happens after routes are mounted. */
  isReady(): boolean {
    return this.namespace !== null;
  }

  /**
   * Any desktop socket of this user on this replica, whether or not it has
   * registered a device. Every socket joins the org:user room on connect.
   * `null` before the namespace is attached.
   */
  isDesktopConnected(orgId: string, userId: string): boolean | null {
    if (!this.namespace) return null;
    const room = this.namespace.adapter.rooms.get(`${orgId}:${userId}`);
    return (room?.size ?? 0) > 0;
  }

  /**
   * `null` before the namespace is attached: at that point no desktop could
   * have registered yet, so "offline" would be wrong for every device.
   */
  isLocalFsDeviceOnline(
    orgId: string,
    userId: string,
    deviceId: string,
  ): boolean | null {
    if (!this.isReady()) return null;
    return this.localFsRelay.isDeviceOnline(orgId, userId, deviceId);
  }

  /** Ask the connector's owner device for one page of file events. */
  async requestLocalFsFileEvents(
    orgId: string,
    userId: string,
    connectorId: string,
    payload: LocalFsPullRequestPayload,
  ): Promise<LocalFsPullResult> {
    return this.localFsRelay.requestFileEvents(
      orgId,
      userId,
      connectorId,
      payload,
    );
  }

  /** Fetch one file's bytes from the desktop. Same seam as the pull above. */
  async requestLocalFsContent(
    orgId: string,
    userId: string,
    connectorId: string,
    payload: LocalFsFetchContentPayload,
  ): Promise<Buffer> {
    return this.localFsRelay.requestContent(
      orgId,
      userId,
      connectorId,
      payload,
    );
  }

  /**
   * Resolves to the error refusing the handshake, or undefined to accept it.
   * Refusals that mean "sign in again" carry an auth-looking message: the
   * desktop client stops on those and retries the rest after a delay.
   */
  private async authenticateHandshake(
    socket: RestProxySocket,
  ): Promise<Error | undefined> {
    const extractedToken = this.extractToken(this.getHandshakeToken(socket));
    if (!extractedToken) {
      return new BadRequestError('Authentication token missing');
    }

    let decoded: Awaited<ReturnType<AuthTokenService['verifyToken']>>;
    try {
      decoded = await this.authTokenService.verifyToken(extractedToken);
    } catch {
      return new BadRequestError('Authentication token expired');
    }

    const userId = String(decoded.userId ?? '');
    const orgId = String(decoded.orgId ?? '');
    if (!userId || !orgId) {
      // Otherwise the socket connects but can never be addressed,
      // which reads as "desktop offline" forever.
      return new BadRequestError('Token is missing userId or orgId');
    }

    // The same rules as every HTTP request: a signature that still verifies
    // says nothing about whether the user was removed or disabled since.
    let refusal: string | null;
    try {
      refusal = await this.sessions.findSessionRefusal(decoded, this.logger);
    } catch (error) {
      // Fail closed, as HTTP does. The message must not look like an auth
      // failure, so the desktop client keeps retrying until the check works.
      this.logger.error('Desktop handshake: session check failed', {
        error,
        orgId,
        userId,
      });
      return new Error('Unable to check the session right now');
    }
    if (refusal) {
      return new UnauthorizedError(`Authentication refused: ${refusal}`);
    }

    socket.data.userId = userId;
    socket.data.orgId = orgId;
    socket.data.issuedAt =
      typeof decoded.iat === 'number' ? decoded.iat : undefined;
    socket.data.email =
      typeof decoded.email === 'string' ? decoded.email : undefined;
    return undefined;
  }

  /**
   * Disconnects this replica's sockets whose session no longer holds. Each
   * replica checks only the sockets it holds, so this needs no cross-replica
   * fan-out. The client reconnects, and the handshake refuses it.
   */
  private async disconnectRevokedSessions(): Promise<void> {
    if (!this.namespace || this.sessionRecheckRunning) return;
    const sockets = (
      [...this.namespace.sockets.values()] as RestProxySocket[]
    ).filter((socket) => socket.data.userId !== '' && socket.data.orgId !== '');
    if (sockets.length === 0) return;

    this.sessionRecheckRunning = true;
    try {
      const refusals = await this.sessions.findSessionRefusals(
        sockets.map((socket) => ({
          userId: socket.data.userId,
          orgId: socket.data.orgId,
          iat: socket.data.issuedAt,
          email: socket.data.email,
        })),
      );
      sockets.forEach((socket, i) => {
        if (!refusals[i]) return;
        this.logger.info('Disconnecting desktop socket: session revoked', {
          orgId: socket.data.orgId,
          userId: socket.data.userId,
          reason: refusals[i],
        });
        socket.disconnect(true);
      });
    } catch (error) {
      // Leave the sockets up: a database blip must not drop every desktop.
      this.logger.error('Desktop session re-check failed', { error });
    } finally {
      this.sessionRecheckRunning = false;
    }
  }

  private extractToken(token: string): string | null {
    if (!token) return null;
    const [bearer, extracted] = token.split(' ');
    return bearer === 'Bearer' && extracted ? extracted : null;
  }

  private getHandshakeToken(socket: RestProxySocket): string {
    const auth = socket.handshake.auth as { token?: string } | undefined;
    return typeof auth?.token === 'string' ? auth.token : '';
  }
}
