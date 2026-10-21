import { Server as HttpServer } from 'http';
import { DefaultEventsMap, Server, ServerOptions, Socket } from 'socket.io';
import { createAdapter, createShardedAdapter } from '@socket.io/redis-adapter';
import type { Cluster } from 'ioredis';
import { Logger } from '../../../libs/services/logger.service';
import { inject, injectable } from 'inversify';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TYPES } from '../../../libs/types/container.types';
import type {
  IRedisConnectionProvider,
  RedisClient,
} from '../../../libs/services/redis/connectionProvider.interface';

interface CustomSocketData {
  userId: string;
  orgId: string;
  forceLogoutReason?: 'missing_role' | 'invalid_token';
}

type CustomSocket = Socket<
  DefaultEventsMap,
  DefaultEventsMap,
  DefaultEventsMap,
  CustomSocketData
>;

@injectable()
export class NotificationService {
  private io: Server | null = null;
  private logger: Logger;
  private connectedUsers: Set<string> = new Set();
  private subClient: RedisClient | null = null;

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
      adapter: this.redisAdapter(),
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
        this.logger.info('force_logout on connect (missing JWT role)', {
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

      // Disconnect event handler
      socket.on('disconnect', () => {
        if (userId) {
          // Check if user has any other active connections in the room
          const room = this.io?.sockets.adapter.rooms.get(userId);
          if (!room || room.size === 0) {
            this.connectedUsers.delete(userId);
            this.logger.info(
              `User disconnected: userId: ${userId} & socketId: ${socket.id}`,
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
      this.logger.warn('Socket.IO server not initialized, notification not sent', {
        userId,
        event,
      });
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
