/**
 * Socket.IO gateway for desktop clients: authenticated REST proxy over `/rest-proxy`.
 */
import { Server as HttpServer } from 'http';
import { Namespace, Server, Socket } from 'socket.io';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import {
  DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
  normalizeAndAssertRestProxyPath,
} from './desktop-proxy-allowlist';

type JsonPrimitive = string | number | boolean | null;
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

type RestProxyQuery = Record<string, JsonPrimitive | undefined>;

type RestProxySocketData = {
  userId: string;
  orgId: string;
};

type ClientToServerEvents = {
  'rpc:request': (
    req: RpcRequest,
    ack?: (res: RpcResponse) => void,
  ) => void;
};

type ServerToClientEvents = {
  'rpc:response': (res: RpcResponse) => void;
};

type InterServerEvents = Record<string, never>;

type RestProxySocket = Socket<
  ClientToServerEvents,
  ServerToClientEvents,
  InterServerEvents,
  RestProxySocketData
>;

type RpcRequest = {
  type: 'request';
  id: string;
  op: 'restProxy';
  payload: {
    method: string;
    path: string;
    query?: RestProxyQuery;
    body?: JsonValue;
  };
};

type RpcResponse =
  | {
      type: 'response';
      id: string;
      ok: true;
      result: { status: number; body: JsonValue };
    }
  | {
      type: 'response';
      id: string;
      ok: false;
      error: { code: string; message: string; status?: number };
    };

const ALLOWED_PREFIXES = DEFAULT_REST_PROXY_ALLOWED_PREFIXES;
const ALLOWED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);
const NAMESPACE = '/rest-proxy';
const SOCKET_PATH = '/socket.io-rest-proxy';
const INTERNAL_REST_PROXY_FETCH_TIMEOUT_MS = 30_000;

export class DesktopProxySocketGateway {
  private readonly logger = Logger.getInstance({
    service: 'DesktopProxySocketGateway',
  });
  private io: Server | null = null;
  private namespace: Namespace | null = null;

  constructor(
    private readonly authTokenService: AuthTokenService,
    private readonly getPort: () => number,
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
      const extractedToken = this.extractToken(this.getHandshakeToken(socket));
      if (!extractedToken) {
        next(new BadRequestError('Authentication token missing'));
        return;
      }
      this.authTokenService
        .verifyToken(extractedToken)
        .then((decoded) => {
          socket.data.userId = String(decoded.userId ?? '');
          socket.data.orgId = String(decoded.orgId ?? '');
          next();
        })
        .catch(() => {
          next(new BadRequestError('Authentication token expired'));
        });
    });

    this.namespace.on('connection', (socket: RestProxySocket) => {
      socket.on(
        'rpc:request',
        async (req: RpcRequest, ack?: (res: RpcResponse) => void) => {
          const response = await this.handleRequest(req, socket);
          if (ack) {
            ack(response);
          } else {
            socket.emit('rpc:response', response);
          }
        },
      );
    });

    this.logger.info('REST proxy Socket.IO namespace initialized');
  }

  shutdown(): void {
    this.namespace?.disconnectSockets(true);
    this.namespace = null;
    void this.io?.close();
    this.io = null;
  }

  private async handleRequest(
    req: RpcRequest,
    socket: RestProxySocket,
  ): Promise<RpcResponse> {
    if (req.id.trim().length === 0) {
      return {
        type: 'response',
        id: 'unknown',
        ok: false,
        error: { code: 'BAD_REQUEST', message: 'Invalid RPC envelope' },
      };
    }
    const { id, payload } = req;
    const methodRaw = payload.method.trim();
    const rawPath = payload.path.trim();
    const method = methodRaw.length > 0 ? methodRaw.toUpperCase() : 'GET';
    if (!ALLOWED_METHODS.has(method)) {
      return {
        type: 'response',
        id,
        ok: false,
        error: {
          code: 'METHOD_NOT_ALLOWED',
          message: `Method ${method} is not allowed`,
        },
      };
    }

    const handshakeToken = this.getHandshakeToken(socket);
    const extractedForVerify = this.extractToken(handshakeToken);
    if (extractedForVerify === null) {
      return {
        type: 'response',
        id,
        ok: false,
        error: {
          code: 'UNAUTHORIZED',
          message: 'Authentication token missing',
          status: 401,
        },
      };
    }
    try {
      await this.authTokenService.verifyToken(extractedForVerify);
    } catch {
      return {
        type: 'response',
        id,
        ok: false,
        error: {
          code: 'TOKEN_EXPIRED',
          message: 'Authentication token expired or invalid',
          status: 401,
        },
      };
    }

    const pathCheck = normalizeAndAssertRestProxyPath(
      rawPath,
      ALLOWED_PREFIXES,
    );
    if (!pathCheck.ok) {
      return {
        type: 'response',
        id,
        ok: false,
        error: {
          code: 'PATH_NOT_ALLOWED',
          message: pathCheck.reason,
        },
      };
    }

    const url = this.buildInternalUrl(pathCheck.normalizedPath, payload.query);
    try {
      const token = extractedForVerify;
      const controller = new AbortController();
      const timer = setTimeout(
        () => controller.abort(),
        INTERNAL_REST_PROXY_FETCH_TIMEOUT_MS,
      );
      let response: Response;
      try {
        response = await fetch(url, {
          method,
          signal: controller.signal,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'application/json',
            'Content-Type': 'application/json',
          },
          body:
            payload.body === undefined ? undefined : JSON.stringify(payload.body),
        });
      } finally {
        clearTimeout(timer);
      }
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
    query: RestProxyQuery | undefined,
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

  private tryParseJson(text: string): JsonValue {
    if (!text.trim()) return null;
    try {
      return JSON.parse(text) as JsonValue;
    } catch {
      return text;
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
