import { Request, Response, NextFunction } from "express";
import {
  HEADER_MCP_RELAY,
  newAnonRoot,
  runWithRequestContext,
  sanitizeRootId,
} from "../context/request-context";
import type { CallerClient } from "../platform-events/events";
import { Logger } from "../services/logger.service";
import { classifyCaller } from "../utils/request-client.utils";

/**
 * Interface for the request context object.
 */
interface RequestContext {
  requestId: string;
  correlationId?: string;
  timestamp: number;
  /** What kind of client sent the request, from its headers. */
  client: CallerClient;
  headers: {
    userAgent: string;
    clientIp?: string;
    correlationId?: string;
    requestId: string;
    origin?: string;
    referer?: string;
    language?: string;
  };
  meta: {
    path: string;
    method: string;
    protocol: string;
    originalUrl: string;
  };
}

// Extend Express's Request interface to include our custom context
declare global {
  namespace Express {
    interface Request {
      context?: RequestContext;
    }
  }
}


const resolveRequestId = (raw: string | undefined): string => {
  const sanitized = sanitizeRootId(raw);
  if (raw && sanitized !== raw) {
    Logger.getInstance().debug("Sanitized client-supplied x-request-id", {
      rawLength: raw.length,
      sanitized: sanitized ?? "(empty, regenerated)",
    });
  }
  return sanitized || newAnonRoot();
};

/**
 * Parses request headers and creates a context object
 * @param req - Express request object
 * @returns Request context object
 */
const createRequestContext = (req: Request): RequestContext => {
  const headers = {
    userAgent: req.get("user-agent") || "unknown",
    clientIp: req.ip || undefined,
    correlationId: req.get("X-Correlation-ID") || undefined,
    requestId: resolveRequestId(req.get("X-Request-ID")),
    origin: req.get("origin") || undefined,
    referer: req.get("referer") || undefined,
    language: req.get("accept-language") || undefined,
  };

  return {
    requestId: headers.requestId,
    correlationId: headers.correlationId,
    timestamp: Date.now(),
    client: classifyCaller(req.headers),
    headers,
    meta: {
      path: req.path,
      method: req.method,
      protocol: req.protocol,
      originalUrl: req.originalUrl,
    },
  };
};

/**
 * Express middleware to attach request context to the req object.
 * The request id is for tracking and debugging. It is sent back in the
 * `X-Request-Id` response header, so a caller can quote it when reporting a
 * problem: the caller's own id when it sent a usable one, else a new `anon-` id.
 */
export const requestContextMiddleware = (
  req: Request,
  res: Response,
  next: NextFunction
): void => {
  req.context = createRequestContext(req);
  res.setHeader('X-Request-Id', req.context.requestId);
  // Bind the id for the whole request so the Logger tags every line.
  const relay = req.headers[HEADER_MCP_RELAY];
  runWithRequestContext(
    {
      rootId: req.context.requestId,
      client: req.context.client,
      ...(typeof relay === 'string' ? { mcpRelay: relay } : {}),
    },
    () => next(),
  );
};
