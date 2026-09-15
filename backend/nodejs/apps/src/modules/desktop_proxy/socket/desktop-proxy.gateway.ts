/**
 * Socket.IO gateway for desktop clients: authenticates the handshake on
 * `/rest-proxy` and routes Local FS messages to `LocalFsRelay`.
 */
import { Server as HttpServer } from 'http';
import { Namespace, Server, Socket } from 'socket.io';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { LocalFsRelay } from './local-fs-relay';
import {
  DesktopRegisterAck,
  DesktopRegisterPayload,
  DesktopUnregisterPayload,
  LocalFsContentAbortPayload,
  LocalFsContentChunkPayload,
  LocalFsFetchContentPayload,
  LocalFsPullRequestPayload,
  LocalFsPullResult,
} from '../types/local-fs.types';

type RestProxySocketData = {
  userId: string;
  orgId: string;
  deviceId?: string;
};

type ClientToServerEvents = {
  'desktop:register': (
    payload: DesktopRegisterPayload,
    ack?: (res: DesktopRegisterAck) => void,
  ) => void;
  'desktop:unregister': (payload: DesktopUnregisterPayload) => void;
  'localfs:content:chunk': (payload: LocalFsContentChunkPayload) => void;
  'localfs:content:abort': (payload: LocalFsContentAbortPayload) => void;
};

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

export class DesktopProxySocketGateway {
  private readonly logger = Logger.getInstance({
    service: 'DesktopProxySocketGateway',
  });
  private io: Server | null = null;
  private namespace: Namespace | null = null;
  private readonly localFsRelay = new LocalFsRelay();

  constructor(private readonly authTokenService: AuthTokenService) {}

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
      const extractedToken = this.extractToken(this.getHandshakeToken(socket));
      if (!extractedToken) {
        next(new BadRequestError('Authentication token missing'));
        return;
      }
      this.authTokenService
        .verifyToken(extractedToken)
        .then((decoded) => {
          const userId = String(decoded.userId ?? '');
          const orgId = String(decoded.orgId ?? '');
          if (!userId || !orgId) {
            // Otherwise the socket connects but can never be addressed,
            // which reads as "desktop offline" forever.
            next(new BadRequestError('Token is missing userId or orgId'));
            return;
          }
          socket.data.userId = userId;
          socket.data.orgId = orgId;
          next();
        })
        .catch(() => {
          next(new BadRequestError('Authentication token expired'));
        });
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
            payload?.connectorIds || [],
            payload?.deviceId,
          );
          if (ack) ack(result);
        },
      );

      socket.on('desktop:unregister', (payload: DesktopUnregisterPayload) => {
        this.localFsRelay.unregister(socket, payload?.connectorIds || []);
      });

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

    this.logger.info('Desktop relay Socket.IO namespace initialized');
  }

  shutdown(): void {
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
   * claimed a given connector. Every socket joins the org:user room on
   * connect. `null` before the namespace is attached.
   */
  isDesktopConnected(orgId: string, userId: string): boolean | null {
    if (!this.namespace) return null;
    const room = this.namespace.adapter.rooms.get(`${orgId}:${userId}`);
    return (room?.size ?? 0) > 0;
  }

  /**
   * `null` before the namespace is attached: at that point no desktop could
   * have registered yet, so "offline" would be wrong for every connector.
   */
  isLocalFsDesktopOnline(
    orgId: string,
    userId: string,
    connectorId: string,
  ): boolean | null {
    if (!this.isReady()) return null;
    return this.localFsRelay.hasDesktop(orgId, userId, connectorId);
  }

  /** Ask the desktop that registered this connector for one page of file events. */
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
