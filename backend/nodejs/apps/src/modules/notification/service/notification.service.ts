import { IncomingMessage, Server as HttpServer } from 'http';
import { DefaultEventsMap, Server, ServerOptions, Socket } from 'socket.io';
import { createAdapter, createShardedAdapter } from '@socket.io/redis-adapter';
import type { Cluster } from 'ioredis';
import { Logger } from '../../../libs/services/logger.service';
import { usesInProcessCache } from '../../../libs/services/redis.service';
import { inject, injectable } from 'inversify';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { classifyError } from '../../../libs/utils/error-class';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TYPES } from '../../../libs/types/container.types';
import { findSessionRefusal } from '../../../libs/utils/user-session.utils';
import { isNotYetVisible } from '../../../libs/repositories/notification/dates';
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

interface QueuedNotification {
  userId: string;
  event: string;
  data: any;
  timestamp: number;
  retryCount: number;
}

/**
 * Between replicas, with Redis: a user has just connected on the replica that sends it, so each other replica sends
 * them now what it holds for them.
 */
const HELD_NOTIFICATIONS_WANTED = 'notifications:held-wanted';

/**
 * Between replicas, with Redis: whether the replica that answers has a socket for a user. Only the yes or no crosses
 * Redis, never a socket's handshake.
 */
const HAS_SOCKET_FOR = 'notifications:has-socket-for';

/**
 * How long a replica waits for the others to answer `HAS_SOCKET_FOR`: longer than the Redis adapter's own wait for
 * every replica (5 s), which a Redis error skips without answering at all.
 */
const PRESENCE_ANSWER_TIMEOUT_MS = 6_000;

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
  private notificationQueue: Map<string, QueuedNotification[]> = new Map();
  private queueProcessingInterval: NodeJS.Timeout | null = null;
  private readonly MAX_RETRY_COUNT = 3;
  private readonly QUEUE_PROCESSING_INTERVAL_MS = 5000; // Process queue every 5 seconds
  private readonly MAX_QUEUE_SIZE_PER_USER = 100; // Maximum notifications per user to prevent memory issues
  /** Pushes go through Redis to every replica: a user with no socket here may have one on another. */
  private distributed = false;
  /**
   * With Redis, per user, the notifications waiting for the replicas to say whether the user has a socket on any of
   * them. Those sent while one answer is on its way share it, so they are held in the order they were sent.
   */
  private readonly awaitingPresence = new Map<
    string,
    Array<{ event: string; data: unknown }>
  >();
  private presenceAnswerTimeoutMs = PRESENCE_ANSWER_TIMEOUT_MS;
  private subClient: RedisClient | null = null;
  // The same session rules authMiddleware holds every HTTP request to.
  private sessionRefusal: typeof findSessionRefusal = findSessionRefusal;
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
    this.distributed = !usesInProcessCache();
    if (this.distributed) {
      // A user connected on another replica: what this replica holds for them goes out now, through Redis.
      this.io.on(HELD_NOTIFICATIONS_WANTED, (userId: unknown) => {
        if (typeof userId === 'string' && userId !== '') {
          this.processQueuedNotificationsForUser(userId, true);
        }
      });
      this.io.on(HAS_SOCKET_FOR, (userId: unknown, reply: unknown) => {
        if (typeof reply !== 'function') return;
        const answer = reply as (hasSocket: boolean) => void;
        answer(typeof userId === 'string' && this.isUserConnected(userId));
      });
    }

    // engine.io turns these away before Socket.IO sees a connection (a session id another process holds, a bad
    // transport or handshake), so they are logged nowhere else. The request's headers, cookies and session id stay out.
    this.io.engine.on(
      'connection_error',
      (err: { req: IncomingMessage; code: number; message: string }) => {
        const url = new URL(err.req.url ?? '', 'http://socket');
        this.logger.warn('socket.engine.connection_error', {
          code: err.code,
          message: err.message,
          transport: url.searchParams.get('transport'),
          upgrade: err.req.headers.upgrade?.toLowerCase() === 'websocket',
          userAgent: err.req.headers['user-agent']?.slice(0, 200),
        });
      },
    );

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
              target_user_id: socket.data.userId,
              org_id: socket.data.orgId,
            });
            return next(new Error('Unable to check the session right now'));
          }
          if (refusal) {
            socket.data.forceLogoutReason = 'session_ended';
          }
        }

        next();
      } catch (error) {
        // The socket is refused here, so this is the one place it is logged.
        this.logger.warn('socket.auth.rejected', {
          error_class: classifyError(error),
          error,
        });
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
          target_user_id: userId,
          reason: socket.data.forceLogoutReason,
        });
        socket.disconnect(true);
        return;
      }

      this.logger.debug('User connected', {
        target_user_id: userId,
        org_id: orgId,
      });

      // Whichever replica sends force_logout, the socket that gets it is closed here, so nothing more reaches it.
      // Listeners run before the packet is written, so the close waits a microtask.
      socket.onAnyOutgoing((event: string) => {
        if (event === 'force_logout') queueMicrotask(() => socket.disconnect(true));
      });

      if (userId) {
        // Join a room with the user's ID for direct messaging
        socket.join(userId);
        this.connectedUsers.add(userId);
        this.logger.debug('Socket joined its user room', {
          target_user_id: userId,
          socket_id: socket.id,
        });

        // Process any queued notifications for this user immediately upon connection
        this.processQueuedNotificationsForUser(userId);
        // With Redis, another replica may hold some from while they had no socket on any replica.
        if (this.distributed) {
          this.io?.serverSideEmit(HELD_NOTIFICATIONS_WANTED, userId);
        }

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
            this.logger.debug('Socket disconnected', {
              target_user_id: userId,
              socket_id: socket.id,
              reason,
            });
          } else {
            // User still has other connections, keep them marked as connected
            this.logger.debug(
              'Socket disconnected; user has other connections',
              {
                target_user_id: userId,
                socket_id: socket.id,
              },
            );
          }
        }
      });
    });

    // Start queue processing interval
    this.startQueueProcessing();

    this.logger.info('Socket.IO server initialized successfully');
    this.extensions.forEach((extension) => this.startExtension(extension));
  }

  /**
   * Check if a user is currently connected via Socket.IO
   */
  private isUserConnected(userId: string): boolean {
    if (!this.io) {
      return false;
    }
    const room = this.io.sockets.adapter.rooms.get(userId);
    return room !== undefined && room.size > 0;
  }

  /**
   * Queue a notification for later delivery when user connects
   */
  private queueNotification(userId: string, event: string, data: any): void {
    if (!this.notificationQueue.has(userId)) {
      this.notificationQueue.set(userId, []);
    }

    const queue = this.notificationQueue.get(userId)!;

    // Prevent queue from growing unbounded
    if (queue.length >= this.MAX_QUEUE_SIZE_PER_USER) {
      // Remove oldest notification (FIFO)
      const removed = queue.shift();
      this.logger.warn(
        'Notification queue full, dropping oldest notification',
        {
          target_user_id: userId,
          event: removed?.event,
          queueSize: queue.length,
          maxSize: this.MAX_QUEUE_SIZE_PER_USER,
        },
      );
    }

    queue.push({
      userId,
      event,
      data,
      timestamp: Date.now(),
      retryCount: 0,
    });

    this.logger.debug('Notification queued for user', {
      target_user_id: userId,
      event,
      queueSize: queue.length,
    });
  }

  /**
   * Process queued notifications for a specific user: one connected here, or, when another replica says so, there.
   */
  private processQueuedNotificationsForUser(
    userId: string,
    connectedElsewhere = false,
  ): void {
    const queue = this.notificationQueue.get(userId);
    if (!queue || queue.length === 0) {
      return;
    }

    if (!connectedElsewhere && !this.isUserConnected(userId)) {
      return;
    }

    const processed: QueuedNotification[] = [];
    const remaining: QueuedNotification[] = [];

    for (const notification of queue) {
      try {
        const sent = this.sendToUserDirect(
          userId,
          notification.event,
          notification.data,
        );
        if (sent) {
          processed.push(notification);
          this.logger.debug('Queued notification delivered', {
            target_user_id: userId,
            event: notification.event,
            queuedAt: notification.timestamp,
            deliveryDelay: Date.now() - notification.timestamp,
          });
        } else {
          // If send failed, increment retry count
          notification.retryCount += 1;
          if (notification.retryCount < this.MAX_RETRY_COUNT) {
            remaining.push(notification);
          } else {
            this.logger.warn(
              'Notification exceeded max retry count, dropping',
              {
                target_user_id: userId,
                event: notification.event,
                retryCount: notification.retryCount,
              },
            );
          }
        }
      } catch (error) {
        const err = error instanceof Error ? error : new Error(String(error));
        this.logger.error('Error processing queued notification', {
          target_user_id: userId,
          event: notification.event,
          error: err,
        });
        notification.retryCount += 1;
        if (notification.retryCount < this.MAX_RETRY_COUNT) {
          remaining.push(notification);
        }
      }
    }

    if (remaining.length > 0) {
      this.notificationQueue.set(userId, remaining);
    } else {
      this.notificationQueue.delete(userId);
    }
  }

  /**
   * Start periodic processing of notification queue
   */
  private startQueueProcessing(): void {
    if (this.queueProcessingInterval) {
      return;
    }

    this.queueProcessingInterval = setInterval(() => {
      const userIds = Array.from(this.notificationQueue.keys());
      for (const userId of userIds) {
        if (this.isUserConnected(userId)) {
          this.processQueuedNotificationsForUser(userId);
        }
      }
    }, this.QUEUE_PROCESSING_INTERVAL_MS);

    this.logger.info('Notification queue processing started', {
      intervalMs: this.QUEUE_PROCESSING_INTERVAL_MS,
    });
  }

  /**
   * Direct send to user (internal method, no queuing): to the user's room, on every replica.
   */
  private sendToUserDirect(userId: string, event: string, data: any): boolean {
    if (!this.io) {
      return false;
    }
    this.io.to(userId).emit(event, data);
    return true;
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
    reason:
      | 'role_changed'
      | 'missing_role'
      | 'invalid_token'
      | 'signed_out' = 'role_changed',
  ): boolean {
    if (!this.io || !userId) {
      return false;
    }
    this.io.to(userId).emit('force_logout', { reason });
    this.logger.info('force_logout emitted', {
      target_user_id: userId,
      reason,
    });
    return true;
  }

  /**
   * Send a message to a specific user.
   * If user is not connected, the notification is queued for delivery when they connect.
   * Returns true if sent immediately, false if queued or failed.
   * With Redis, a user with no socket on this replica may have one on another: it is sent there at once too (true).
   * It is held here only if no replica has a socket for them, and sent when they connect on any replica.
   */
  sendToUser(userId: string, event: string, data: any): boolean {
    // A scheduled notification is never pushed and never queued for an offline user: a client sees it through its own
    // list or stats, at or after `visibleAt`. Nothing delivers it later.
    if (
      event === 'newNotification' &&
      isNotYetVisible((data as { visibleAt?: unknown } | undefined)?.visibleAt)
    ) {
      this.logger.debug('Scheduled notification not pushed', {
        target_user_id: userId,
        event,
      });
      return false;
    }
    if (!this.io) {
      this.logger.warn(
        'Socket.IO server not initialized, notification not sent',
        {
          target_user_id: userId,
          event,
        },
      );
      return false;
    }

    // Check if user is connected
    if (this.isUserConnected(userId)) {
      // User is connected, send immediately
      this.sendToUserDirect(userId, event, data);
      this.logger.debug('Notification sent immediately to connected user', {
        target_user_id: userId,
        event,
      });
      return true;
    } else if (this.distributed) {
      // To their sockets on the other replicas, through Redis; held here only if none of them has one.
      this.sendToUserDirect(userId, event, data);
      this.holdUnlessConnectedElsewhere(userId, event, data);
      this.logger.debug(
        'No socket here; notification sent to the other replicas',
        {
          target_user_id: userId,
          event,
        },
      );
      return true;
    } else {
      // User is not connected, queue for later delivery
      this.queueNotification(userId, event, data);
      this.logger.debug('User not connected, notification queued', {
        target_user_id: userId,
        event,
      });
      return false; // Indicates notification was queued, not sent immediately
    }
  }

  /**
   * Holds a notification only for a user with no socket on any replica, as a replica without Redis holds one for a
   * user with no socket on it. A user with a socket on another replica got it there through Redis: a copy held here
   * would reach them a second time when they next connect here, an old unread copy over the row they may have read.
   */
  private holdUnlessConnectedElsewhere(
    userId: string,
    event: string,
    data: unknown,
  ): void {
    const waiting = this.awaitingPresence.get(userId);
    if (waiting) {
      waiting.push({ event, data });
      return;
    }
    const batch = [{ event, data }];
    this.awaitingPresence.set(userId, batch);
    void this.connectedOnAnyReplica(userId).then((connected) => {
      this.awaitingPresence.delete(userId);
      if (connected || !this.io) return;
      for (const held of batch) {
        this.queueNotification(userId, held.event, held.data);
      }
      // They may have connected here while the replicas answered.
      this.processQueuedNotificationsForUser(userId);
    });
  }

  /**
   * Whether another replica has a socket for the user (the caller found none here). When the replicas cannot all be
   * asked, the answer is no, so the notification is held, as it is for a user with no socket here on a replica
   * without Redis.
   */
  private async connectedOnAnyReplica(userId: string): Promise<boolean> {
    if (!this.io) return false;
    let timer: NodeJS.Timeout | undefined;
    try {
      const answers: unknown[] = await Promise.race([
        this.io.serverSideEmitWithAck(HAS_SOCKET_FOR, userId),
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(
            () =>
              reject(new Error('the other replicas did not answer in time')),
            this.presenceAnswerTimeoutMs,
          );
          timer.unref();
        }),
      ]);
      return answers.some((answer) => answer === true);
    } catch (error) {
      this.logger.warn(
        'Could not ask the other replicas for a user; the notification is held here',
        {
          target_user_id: userId,
          error: error instanceof Error ? error.message : String(error),
        },
      );
      return false;
    } finally {
      clearTimeout(timer);
    }
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
    if (this.queueProcessingInterval) {
      clearInterval(this.queueProcessingInterval);
      this.queueProcessingInterval = null;
    }

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
      this.logger.info('Shutting down Socket.IO server', {
        queuedNotifications: Array.from(this.notificationQueue.values()).reduce(
          (sum, queue) => sum + queue.length,
          0,
        ),
      });
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
    this.notificationQueue.clear();
    this.awaitingPresence.clear();
  }

  private extractToken(token: string): string | null {
    const authHeader = token || 'hfgh';
    const [bearer, tokenSanitized] = authHeader.split(' ');
    return bearer === 'Bearer' && tokenSanitized ? tokenSanitized : null;
  }
}
