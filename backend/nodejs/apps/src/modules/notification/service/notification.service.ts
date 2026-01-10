import { Server as HttpServer } from 'http';
import { DefaultEventsMap, Server, Socket } from 'socket.io';
import { Logger } from '../../../libs/services/logger.service';
import { inject, injectable } from 'inversify';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TYPES } from '../../../libs/types/container.types';

interface CustomSocketData {
  userId: string;
  orgId: string;
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

  constructor(
    @inject(TYPES.AuthTokenService) private authTokenService: AuthTokenService,
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
    });

    this.io.use(async (socket: CustomSocket, next) => {
      const extractedToken = this.extractToken(socket.handshake.auth.token);
      if (!extractedToken) {
        return next(new BadRequestError('Authentication token missing'));
      }
      const decodedData =
        await this.authTokenService.verifyToken(extractedToken);
      if (!decodedData) {
        return next(new BadRequestError('Authentication token expired'));
      }

      socket.data.userId = decodedData.userId; // Store userId in socket object
      socket.data.orgId = decodedData.orgId; // Store userId in socket object
      next();
    });

    // Connection event handler
    this.io.on('connection', (socket: CustomSocket) => {
      const userId = socket.data.userId;
      const orgId = socket.data.orgId;
      this.logger.info('User connected', {
        userId,
        orgId,
      });

      if (userId) {
        // Join a room with the user's ID for direct messaging
        socket.join(userId);
        this.connectedUsers.add(userId);
        this.logger.info(
          `User connected: userId: ${userId} & socketId: ${socket.id}`,
        );

        // Process any queued notifications for this user immediately upon connection
        this.processQueuedNotificationsForUser(userId);

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

    // Start queue processing interval
    this.startQueueProcessing();

    this.logger.info('Socket.IO server initialized successfully');
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
      this.logger.warn('Notification queue full, dropping oldest notification', {
        userId,
        event: removed?.event,
        queueSize: queue.length,
        maxSize: this.MAX_QUEUE_SIZE_PER_USER,
      });
    }

    queue.push({
      userId,
      event,
      data,
      timestamp: Date.now(),
      retryCount: 0,
    });

    this.logger.debug('Notification queued for user', {
      userId,
      event,
      queueSize: queue.length,
    });
  }

  /**
   * Process queued notifications for a specific user
   */
  private processQueuedNotificationsForUser(userId: string): void {
    const queue = this.notificationQueue.get(userId);
    if (!queue || queue.length === 0) {
      return;
    }

    if (!this.isUserConnected(userId)) {
      return;
    }

    const processed: QueuedNotification[] = [];
    const remaining: QueuedNotification[] = [];

    for (const notification of queue) {
      try {
        const sent = this.sendToUserDirect(userId, notification.event, notification.data);
        if (sent) {
          processed.push(notification);
          this.logger.debug('Queued notification delivered', {
            userId,
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
            this.logger.warn('Notification exceeded max retry count, dropping', {
              userId,
              event: notification.event,
              retryCount: notification.retryCount,
            });
          }
        }
      } catch (error) {
        const err = error instanceof Error ? error : new Error(String(error));
        this.logger.error('Error processing queued notification', {
          userId,
          event: notification.event,
          error: err.message,
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
   * Direct send to user (internal method, no queuing)
   */
  private sendToUserDirect(userId: string, event: string, data: any): boolean {
    if (!this.io) {
      return false;
    }
    this.io.to(userId).emit(event, data);
    return true;
  }

  /**
   * Send a message to a specific user.
   * If user is not connected, the notification is queued for delivery when they connect.
   * Returns true if sent immediately, false if queued or failed.
   */
  sendToUser(userId: string, event: string, data: any): boolean {
    if (!this.io) {
      this.logger.warn('Socket.IO server not initialized, notification not sent', {
        userId,
        event,
      });
      return false;
    }

    // Check if user is connected
    if (this.isUserConnected(userId)) {
      // User is connected, send immediately
      this.sendToUserDirect(userId, event, data);
      this.logger.debug('Notification sent immediately to connected user', {
        userId,
        event,
      });
      return true;
    } else {
      // User is not connected, queue for later delivery
      this.queueNotification(userId, event, data);
      this.logger.debug('User not connected, notification queued', {
        userId,
        event,
      });
      return false; // Indicates notification was queued, not sent immediately
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

    if (this.io) {
      this.logger.info('Shutting down Socket.IO server', {
        queuedNotifications: Array.from(this.notificationQueue.values()).reduce(
          (sum, queue) => sum + queue.length,
          0,
        ),
      });
      this.io.disconnectSockets(true);
      this.io.close();
      this.io = null;
    }

    this.connectedUsers.clear();
    this.notificationQueue.clear();
  }

  private extractToken(token: string): string | null {
    const authHeader = token || 'hfgh';
    const [bearer, tokenSanitized] = authHeader.split(' ');
    return bearer === 'Bearer' && tokenSanitized ? tokenSanitized : null;
  }
}
