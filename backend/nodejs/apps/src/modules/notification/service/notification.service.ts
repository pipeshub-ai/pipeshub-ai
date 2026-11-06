import { Server as HttpServer } from 'http';
import { DefaultEventsMap, Server, ServerOptions, Socket } from 'socket.io';
import { createAdapter, createShardedAdapter } from '@socket.io/redis-adapter';
import type { Cluster } from 'ioredis';
import { Logger } from '../../../libs/services/logger.service';
import { usesInProcessCache } from '../../../libs/services/redis.service';
import { inject, injectable } from 'inversify';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TYPES } from '../../../libs/types/container.types';
import { findSessionRefusal } from '../../../libs/utils/user-session.utils';
import type {
  IRedisConnectionProvider,
  RedisClient,
} from '../../../libs/services/redis/connectionProvider.interface';

interface CustomSocketData {
  userId: string;
  orgId: string;
  forceLogoutReason?: 'missing_role' | 'invalid_token' | 'session_ended';
}

type CustomSocket = Socket<
  DefaultEventsMap,
  DefaultEventsMap,
  DefaultEventsMap,
  CustomSocketData
>;

/** An authenticated socket: its user and org are the ones its token was verified for at the handshake. */
export type NotificationSocket = CustomSocket;

/** One of this replica's sockets, as `localSocketsIn` lists it. */
export type LocalSocket = Pick<NotificationSocket, 'id' | 'data' | 'emit'> & {
  join(room: string): unknown;
  leave(room: string): unknown;
};

/**
 * Socket events and long-lived work an edition adds to the notification socket (`use`). A failure in
 * one never stops the server or another extension.
 */
export interface SocketExtension {
  /** Once the Socket.IO server is up. */
  start(sockets: NotificationService): void;
  /** Each authenticated connection, before any of its events arrive. */
  connected(socket: NotificationSocket): void;
  /** When the server shuts down. */
  stop(): Promise<void>;
}

@injectable()
export class NotificationService {
  private io: Server | null = null;
  private logger: Logger;
  private connectedUsers: Set<string> = new Set();
  // The same session rules authMiddleware holds every HTTP request to.
  private sessionRefusal: typeof findSessionRefusal = findSessionRefusal;
  private subClient: RedisClient | null = null;
  private readonly extensions: SocketExtension[] = [];

  constructor(
    @inject(TYPES.AuthTokenService) private authTokenService: AuthTokenService,
    @inject('RedisConnectionProvider')
    private redisProvider: IRedisConnectionProvider,
  ) {
    this.logger = new Logger({ service: 'NotificationService' });
  }

  initialize(server: HttpServer): void {
    this.logger.info('Initializing Socket.IO server');

    // Create Socket.IO server with CORS configuration
    this.io = new Server(server, {
      cors: {
        origin: process.env.ALLOWED_ORIGINS?.split(',') || '*',
        methods: ['GET', 'POST', 'PUT', 'PATCH', 'OPTIONS', 'DELETE'],
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
      },
      // With no Redis server (REDIS_MODE=postgres or inprocess) there is one API process, so Socket.IO's
      // in-memory adapter reaches every socket; the Redis adapter would need a connection that does not exist.
      ...(usesInProcessCache() ? {} : { adapter: this.redisAdapter() }),
    });

    this.io.use(async (socket: CustomSocket, next) => {
      try {
        const extractedToken = this.extractToken(socket.handshake.auth.token);
        if (!extractedToken) {
          return next(new BadRequestError('Authentication token missing'));
        }
        const decodedData =
          await this.authTokenService.verifyToken(extractedToken);
        if (!decodedData) {
          return next(new BadRequestError('Authentication token expired'));
        }

        socket.data.userId = decodedData.userId;
        socket.data.orgId = decodedData.orgId;

        const role = (decodedData as { role?: unknown }).role;
        if (role !== 'admin' && role !== 'member') {
          // Allow the socket to connect briefly so we can emit force_logout.
          socket.data.forceLogoutReason = 'missing_role';
        } else {
          // A signature that still verifies says nothing about a logout,
          // password change or disabled account since the token was issued.
          let refusal: string | null;
          try {
            refusal = await this.sessionRefusal(decodedData, this.logger);
          } catch (error) {
            // Fail closed, and not as an auth failure, so the client retries rather than signing out.
            this.logger.error('Notification handshake: session check failed', {
              error,
              userId: socket.data.userId,
              orgId: socket.data.orgId,
            });
            return next(new Error('Unable to check the session right now'));
          }
          if (refusal) {
            socket.data.forceLogoutReason = 'session_ended';
          }
        }

        next();
      } catch (error) {
        next(
          error instanceof Error
            ? error
            : new BadRequestError('Authentication failed'),
        );
      }
    });

    // Connection event handler
    this.io.on('connection', (socket: CustomSocket) => {
      const userId = socket.data.userId;
      const orgId = socket.data.orgId;

      if (socket.data.forceLogoutReason) {
        socket.emit('force_logout', { reason: socket.data.forceLogoutReason });
        this.logger.info('force_logout on connect', {
          userId,
          reason: socket.data.forceLogoutReason,
        });
        socket.disconnect(true);
        return;
      }

      this.logger.debug('User connected', {
        userId,
        orgId,
      });

      if (userId) {
        // Join a room with the user's ID for direct messaging
        socket.join(userId);
        this.connectedUsers.add(userId);
        this.logger.debug(
          `User connected: userId: ${userId} & socketId: ${socket.id}`,
        );

        // You can also join organization room if needed
        if (orgId) {
          socket.join(`org:${orgId}`);
        }
      }

      for (const extension of this.extensions) {
        try {
          extension.connected(socket);
        } catch (error) {
          this.logger.error('Socket extension failed on connect', {
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }

      // Disconnect event handler
      // The reason tells a proxy or idle timeout ('transport close') from a client that stopped answering pings.
      socket.on('disconnect', (reason: string) => {
        if (userId) {
          // Check if user has any other active connections in the room
          const room = this.io?.sockets.adapter.rooms.get(userId);
          if (!room || room.size === 0) {
            this.connectedUsers.delete(userId);
            this.logger.info(
              `User disconnected: userId: ${userId} & socketId: ${socket.id} & reason: ${reason}`,
            );
          } else {
            // User still has other connections, keep them marked as connected
            this.logger.debug(
              `User socket disconnected but still has other connections: userId: ${userId} & socketId: ${socket.id}`,
            );
          }
        }
      });
    });

    this.logger.info('Socket.IO server initialized successfully');
    this.extensions.forEach((extension) => this.startExtension(extension));
  }

  /** Adds an edition's socket events; started at once when the server is already up. */
  use(extension: SocketExtension): void {
    this.extensions.push(extension);
    if (this.io) this.startExtension(extension);
  }

  private startExtension(extension: SocketExtension): void {
    try {
      extension.start(this);
    } catch (error) {
      this.logger.error('Socket extension failed to start', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  /** To the sockets in `room` on every replica. False only when the server is not initialized. */
  emitToRoom(room: string, event: string, data: unknown): boolean {
    if (!this.io) return false;
    this.io.to(room).emit(event, data);
    return true;
  }

  /** To the sockets in `room` on this replica only: each replica sends what it saw to its own. */
  emitToLocalRoom(room: string, event: string, data: unknown): void {
    this.io?.local.to(room).emit(event, data);
  }

  /** To every socket on this replica. */
  emitToAllLocal(event: string, data: unknown): void {
    this.io?.local.emit(event, data);
  }

  /** Takes this replica's sockets out of `room`. */
  leaveLocalRoom(room: string): void {
    this.io?.local.in(room).socketsLeave(room);
  }

  /** This replica's sockets in `room`; none before the server is up. */
  async localSocketsIn(room: string): Promise<LocalSocket[]> {
    if (!this.io) return [];
    return await this.io.local.in(room).fetchSockets();
  }

  /**
   * Pushes travel through Redis pub/sub, so they reach a user's sockets on
   * every replica. Redis Cluster uses sharded pub/sub, which keeps each
   * message on the shard that owns its channel.
   */
  private redisAdapter(): ServerOptions['adapter'] {
    const pubClient = this.redisProvider.getClient();
    if (this.redisProvider.isCluster) {
      this.subClient = (pubClient as Cluster).duplicate([], {
        shardedSubscribers: true,
      });
      return createShardedAdapter(pubClient, this.subClient);
    }
    this.subClient = this.redisProvider.createPubSubClient();
    return createAdapter(pubClient, this.subClient);
  }

  /**
   * Force-logout a user on every replica. Nothing is held for later: a held
   * sign-out would log them out again after their next sign-in.
   */
  emitForceLogout(
    userId: string,
    reason: 'role_changed' | 'missing_role' | 'invalid_token' = 'role_changed',
  ): boolean {
    if (!this.io || !userId) {
      return false;
    }
    this.io.to(userId).emit('force_logout', { reason });
    this.logger.info('force_logout emitted', { userId, reason });
    return true;
  }

  /**
   * Send a message to a user's sockets, on whichever replica they are.
   * Nothing is held for a user who is offline: the stored notification is the
   * durable copy, and the client fetches it when it connects.
   * Returns false only when the Socket.IO server is not initialized.
   */
  sendToUser(userId: string, event: string, data: any): boolean {
    if (!this.io) {
      this.logger.warn(
        'Socket.IO server not initialized, notification not sent',
        {
          userId,
          event,
        },
      );
      return false;
    }
    this.io.to(userId).emit(event, data);
    return true;
  }

  // Method to send a message to all users in an organization
  sendToOrg(orgId: string, event: string, data: any): boolean {
    if (this.io) {
      this.io.to(`org:${orgId}`).emit(event, data);
      return true;
    }
    return false;
  }

  // Method to broadcast to all connected clients
  broadcastToAll(event: string, data: any): void {
    if (this.io) {
      this.io.emit(event, data);
    }
  }

  // Method to shutdown the Socket.IO server
  shutdown(): void {
    for (const extension of this.extensions) {
      void Promise.resolve()
        .then(() => extension.stop())
        .catch((error: unknown) => {
          this.logger.warn('Socket extension did not stop cleanly', {
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }
    if (this.io) {
      this.logger.info('Shutting down Socket.IO server');
      // Local: through the adapter it would drop every replica's sockets.
      this.io.local.disconnectSockets(true);
      this.io.close();
      this.io = null;
    }
    if (this.subClient) {
      this.redisProvider.release(this.subClient);
      void this.subClient.quit().catch(() => undefined);
      this.subClient = null;
    }

    this.connectedUsers.clear();
  }

  private extractToken(token: string): string | null {
    const authHeader = token || 'hfgh';
    const [bearer, tokenSanitized] = authHeader.split(' ');
    return bearer === 'Bearer' && tokenSanitized ? tokenSanitized : null;
  }
}
