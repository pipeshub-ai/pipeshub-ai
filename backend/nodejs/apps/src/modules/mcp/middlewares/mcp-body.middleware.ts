/**
 * JSON body parsing for `/mcp`, with a smaller limit than the rest of the API.
 *
 * @module mcp/middlewares
 */

import express, { ErrorRequestHandler, RequestHandler } from 'express';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({
  service: 'MCPBodyParser',
});

/**
 * The largest JSON body `/mcp` accepts. The MCP SDK holds many times a tool
 * call's size in memory while it validates the arguments, so the shared 10 MB
 * API limit let one call grow the heap by gigabytes (GHSA-46jr-4wx8-v453).
 * The largest real input is a `pipeshub_chat` query, capped at 100,000
 * characters (at most about 600 KB of JSON), and no MCP tool uploads files.
 */
export const MCP_JSON_BODY_LIMIT_BYTES = 5 * 1024 * 1024;

export const MCP_BODY_TOO_LARGE_MESSAGE =
  'This request is too large for PipesHub. One MCP request can be at most 5 MB. ' +
  'Send less in one call, such as a shorter question or fewer IDs, and try again.';

// The MCP SDK answers a body over its own limit with this JSON-RPC code, and
// with id null because the request was never read far enough to find its id.
const JSON_RPC_SERVER_ERROR = -32000;

function isBodyTooLarge(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { type?: unknown }).type === 'entity.too.large'
  );
}

const answerBodyTooLarge: ErrorRequestHandler = (error, req, res, next) => {
  if (!isBodyTooLarge(error) || res.headersSent) {
    next(error);
    return;
  }
  const requestId = req.context?.requestId;
  logger.warn('MCP request body over the limit', {
    limitBytes: MCP_JSON_BODY_LIMIT_BYTES,
    contentLength: req.headers['content-length'],
    requestId,
  });
  res.status(413).json({
    jsonrpc: '2.0',
    error: {
      code: JSON_RPC_SERVER_ERROR,
      message: MCP_BODY_TOO_LARGE_MESSAGE,
      ...(requestId !== undefined && { data: { requestId } }),
    },
    id: null,
  });
};

/**
 * Parses an `/mcp` JSON body up to MCP_JSON_BODY_LIMIT_BYTES and answers a
 * larger one with a JSON-RPC error and HTTP 413. Any other parse failure goes
 * on to the app's error middleware as before.
 */
export function mcpJsonBodyParser(): Array<
  RequestHandler | ErrorRequestHandler
> {
  return [
    express.json({ limit: MCP_JSON_BODY_LIMIT_BYTES }),
    answerBodyTooLarge,
  ];
}
