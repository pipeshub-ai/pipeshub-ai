import { Server as HttpServer } from 'http';
import { DefaultEventsMap, Namespace, Server, Socket } from 'socket.io';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';

type CliRpcSocketData = {
  userId: string;
  orgId: string;
};

type CliRpcSocket = Socket<
  DefaultEventsMap,
  DefaultEventsMap,
  DefaultEventsMap,
  CliRpcSocketData
>;

type RpcRequest = {
  type: 'request';
  id: string;
  op: 'restProxy';
  payload: {
    method: string;
    path: string;
    query?: Record<string, string | number | boolean | null | undefined>;
    body?: unknown;
  };
};

type RpcResponse =
  | {
      type: 'response';
      id: string;
      ok: true;
      result: { status: number; body: unknown };
    }
  | {
      type: 'response';
      id: string;
      ok: false;
      error: { code: string; message: string; status?: number };
    };

const ALLOWED_PREFIXES = ['/api/v1/connectors', '/api/v1/knowledgeBase'];
const ALLOWED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);
const NAMESPACE = '/cli-rpc';
const SOCKET_PATH = '/socket.io-cli-rpc';

export class CliRpcSocketGateway {
  private readonly logger = Logger.getInstance({ service: 'CliRpcSocketGateway' });
  private io: Server | null = null;
  private namespace: Namespace | null = null;

  constructor(
    private readonly authTokenService: AuthTokenService,
    private readonly getPort: () => number,
  ) {}

  initialize(server: HttpServer): void {
    this.io = new Server(server, {
      path: SOCKET_PATH,
      cors: {
        origin: process.env.ALLOWED_ORIGINS?.split(',') || '*',
        methods: ['GET', 'POST', 'PUT', 'PATCH', 'OPTIONS', 'DELETE'],
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
      },
    });
    this.namespace = this.io.of(NAMESPACE);
    this.namespace.use(async (socket: CliRpcSocket, next) => {
      const extractedToken = this.extractToken(socket.handshake.auth?.token as string);
      if (!extractedToken) {
        return next(new BadRequestError('Authentication token missing'));
      }
      const decoded = await this.authTokenService.verifyToken(extractedToken);
      if (!decoded) {
        return next(new BadRequestError('Authentication token expired'));
      }
      socket.data.userId = decoded.userId;
      socket.data.orgId = decoded.orgId;
      next();
    });

    this.namespace.on('connection', (socket: CliRpcSocket) => {
      socket.on('rpc:request', async (req: RpcRequest, ack?: (res: RpcResponse) => void) => {
        const response = await this.handleRequest(req, socket);
        if (ack) {
          ack(response);
        } else {
          socket.emit('rpc:response', response);
        }
      });
    });

    this.logger.info('CLI RPC Socket.IO namespace initialized');
  }

  shutdown(): void {
    this.namespace?.disconnectSockets(true);
    this.namespace = null;
    this.io?.close();
    this.io = null;
  }

  private async handleRequest(req: RpcRequest, socket: CliRpcSocket): Promise<RpcResponse> {
    if (!req || req.type !== 'request' || req.op !== 'restProxy' || !req.id) {
      return {
        type: 'response',
        id: req?.id ?? 'unknown',
        ok: false,
        error: { code: 'BAD_REQUEST', message: 'Invalid RPC envelope' },
      };
    }
    const { id, payload } = req;
    const method = String(payload.method || 'GET').toUpperCase();
    const path = String(payload.path || '');
    if (!ALLOWED_METHODS.has(method)) {
      return {
        type: 'response',
        id,
        ok: false,
        error: { code: 'METHOD_NOT_ALLOWED', message: `Method ${method} is not allowed` },
      };
    }
    if (!ALLOWED_PREFIXES.some((prefix) => path.startsWith(prefix))) {
      return {
        type: 'response',
        id,
        ok: false,
        error: { code: 'PATH_NOT_ALLOWED', message: 'Path is not allowed for CLI RPC' },
      };
    }

    const url = this.buildInternalUrl(path, payload.query);
    try {
      const token = this.extractToken(socket.handshake.auth?.token as string) || '';
      const response = await fetch(url, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: payload.body === undefined ? undefined : JSON.stringify(payload.body),
      });
      const text = await response.text();
      return {
        type: 'response',
        id,
        ok: true,
        result: {
          status: response.status,
          body: this.tryParseJson(text),
        },
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return {
        type: 'response',
        id,
        ok: false,
        error: { code: 'UPSTREAM_ERROR', message },
      };
    }
  }

  private buildInternalUrl(
    path: string,
    query: Record<string, string | number | boolean | null | undefined> | undefined,
  ): string {
    const port = this.getPort() || 3000;
    const url = new URL(`http://127.0.0.1:${port}${path}`);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value === null || value === undefined) continue;
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  private tryParseJson(text: string): unknown {
    if (!text.trim()) return null;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  private extractToken(token: string): string | null {
    const authHeader = token || '';
    const [bearer, tokenSanitized] = authHeader.split(' ');
    return bearer === 'Bearer' && tokenSanitized ? tokenSanitized : null;
  }
}
