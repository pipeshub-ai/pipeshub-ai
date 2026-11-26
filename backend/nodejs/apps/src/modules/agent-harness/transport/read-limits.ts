import http from 'http';

/**
 * Request-read bounds for an HTTP listener (SEC-DOS-04, C6.40). Node closes a
 * connection whose request head is not in within `headersTimeoutMs`, or whose
 * whole request is not in within `requestTimeoutMs`, of its first byte; it checks
 * every `checkIntervalMs`, so a slow client is closed within the bound plus one
 * interval. Only the request is timed: a response (an SSE stream, a download) may
 * last as long as it likes once the request is in. `connectionsCheckingInterval`
 * is fixed when the server is made, which is why servers are made here.
 */
export interface HttpReadLimits {
  headersTimeoutMs: number;
  requestTimeoutMs: number;
  keepAliveTimeoutMs: number;
  checkIntervalMs: number;
  maxConnections?: number;
}

/**
 * Python services reuse pooled connections to the public listener for up to 4s after
 * they *process* a response, and a busy event loop can get there seconds late.
 * Node's 5s keep-alive default closed sockets they were about to reuse ("Can not
 * write request body" on record uploads). The request bounds are Node's own
 * defaults (5 minutes to send a request, checked every 30 s), stated so a slow
 * client is closed by a choice rather than by luck (SEC-DOS-04): an upload may
 * take up to 5 minutes, and a response (SSE, a download) is never timed.
 */
const PUBLIC_KEEP_ALIVE_TIMEOUT_MS = 65_000;
export const PUBLIC_READ_LIMITS: HttpReadLimits = {
  keepAliveTimeoutMs: PUBLIC_KEEP_ALIVE_TIMEOUT_MS,
  headersTimeoutMs: PUBLIC_KEEP_ALIVE_TIMEOUT_MS + 1_000,
  requestTimeoutMs: 300_000,
  checkIntervalMs: 30_000,
};

export function boundedServer(
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => unknown,
  limits: HttpReadLimits,
): http.Server {
  // Node takes `headersTimeout` as an option (and checks it against `requestTimeout` there) though its
  // types do not list it; set later, the default would be checked against a shorter request bound and refused.
  const options: http.ServerOptions & { headersTimeout: number } = {
    headersTimeout: limits.headersTimeoutMs,
    requestTimeout: limits.requestTimeoutMs,
    connectionsCheckingInterval: limits.checkIntervalMs,
  };
  const server = http.createServer(options, (req, res) => {
    // Express 5 types an app as maybe returning a promise; the app answers its own errors, so nothing waits on it.
    void handler(req, res);
  });
  applyReadLimits(server, limits);
  return server;
}

/** For a server made elsewhere: every bound but the checking interval, which keeps Node's. */
export function applyReadLimits(
  server: http.Server,
  limits: HttpReadLimits,
): void {
  server.keepAliveTimeout = limits.keepAliveTimeoutMs;
  server.headersTimeout = limits.headersTimeoutMs;
  server.requestTimeout = limits.requestTimeoutMs;
  if (limits.maxConnections !== undefined)
    server.maxConnections = limits.maxConnections;
}

/** Strict, like every harness setting (F3): a value that is not a positive whole number stops the process and names it. */
export function positiveIntegerSetting(
  name: string,
  raw: string | undefined,
  fallback: number,
): number {
  const text = raw?.trim();
  if (text === undefined || text === '') return fallback;
  const value = Number(text);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(
      `${name} must be a positive whole number, got ${JSON.stringify(text)}`,
    );
  }
  return value;
}
