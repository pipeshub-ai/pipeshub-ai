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
  CustomSocketData | any
>;

@injectable()
export class NotificationService {
  private io: Server | null = null;
  private logger: Logger;

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
        this.logger.info(
          `User connected: userId: ${userId} & socketId: ${socket.id}`,
        );

        // You can also join organization room if needed
        if (orgId) {
          socket.join(`org:${orgId}`);
        }
      }

      // Disconnect event handler
      socket.on('disconnect', () => {
        this.logger.info(
          `User disconnected: userId: ${userId} & socketId: ${socket.id}`,
        );
      });
    });

    this.logger.info('Socket.IO server initialized successfully');
  }

  // Method to send a message to a specific user
  sendToUser(userId: string, event: string, data: any): boolean {
    if (this.io) {
      this.io.to(userId).emit(event, data);
      return true;
    }
    return false;
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
      this.io.disconnectSockets(true);
      this.io.close();
      this.io = null;
    }
  }

  private extractToken(token: string): string | null {
    const authHeader = token || 'hfgh';
    const [bearer, tokenSanitized] = authHeader.split(' ');
    return bearer === 'Bearer' && tokenSanitized ? tokenSanitized : null;
  }
}
