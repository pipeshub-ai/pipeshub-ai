import { RequestHandler, Response } from 'express';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';

/** A JSON-RPC error with no id: what an MCP client reads when the HTTP layer refuses a request. */
export function sendMcpError(
  res: Response,
  status: number,
  message: string,
): void {
  res.status(status).json({
    jsonrpc: '2.0',
    error: { code: -32000, message },
    id: null,
  });
}

const originOf = (value: unknown): string | undefined => {
  if (typeof value !== 'string' || value.trim() === '') return undefined;
  try {
    return new URL(value.trim()).origin;
  } catch {
    return undefined;
  }
};

/**
 * MCP Streamable HTTP: a server MUST check `Origin`, so a page in someone's browser cannot
 * reach the endpoint by DNS rebinding. Desktop, CLI and hosted clients send no `Origin` and
 * pass; a browser page passes only from this instance's own origins, `ALLOWED_ORIGINS`, or
 * `MCP_ALLOWED_ORIGINS` (e.g. a browser-based MCP inspector).
 */
export function mcpOriginGuard(
  instanceUrls: Array<string | undefined>,
): RequestHandler {
  const allowed = new Set(
    [
      ...instanceUrls,
      ...(process.env.ALLOWED_ORIGINS ?? '').split(','),
      ...(process.env.MCP_ALLOWED_ORIGINS ?? '').split(','),
    ]
      .map(originOf)
      .filter((v): v is string => v !== undefined),
  );
  return (req, res, next) => {
    const origin = req.headers.origin;
    if (origin === undefined || allowed.has(origin)) {
      next();
      return;
    }
    sendMcpError(res, 403, 'Origin not allowed.');
  };
}

const respond = (res: Response, message: string): void => {
  sendMcpError(res, 429, message);
};

/**
 * The two /mcp limits. Before authentication, an address is counted only on rejected tokens,
 * so everyone behind one address (a hosted AI client, an office) is not throttled together,
 * while a flood of bad tokens still is. After it, each account gets its own budget.
 */
export function mcpRateLimiters(
  logger: Logger,
  maxRequestsPerMinute: number,
): { rejectedTokens: RequestHandler; perAccount: RequestHandler } {
  return {
    rejectedTokens: createKeyedRateLimiter(logger, {
      prefix: 'mcp-auth',
      maxRequestsPerMinute,
      message:
        'Too many requests with an invalid or expired token from this address. Try again in a minute.',
      countOnlyFailures: true,
      isFailure: (res) => res.statusCode === 401,
      respond,
    }),
    perAccount: createKeyedRateLimiter(logger, {
      prefix: 'mcp',
      maxRequestsPerMinute,
      message: `This account has made more than ${String(maxRequestsPerMinute)} PipesHub requests in the last minute. Wait a minute and try again.`,
      respond,
    }),
  };
}
