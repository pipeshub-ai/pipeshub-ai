import { Request, Response, NextFunction } from "express";
import {
  newAnonRoot,
  runWithRequestContext,
  sanitizeRootId,
} from "../context/request-context";
import { Logger } from "../services/logger.service";

/**
 * Interface for the request context object.
 */
interface RequestContext {
  requestId: string;
  correlationId?: string;
  timestamp: number;
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
 * Note: Request ID is just for tracking and debugging purposes. Does not expose anything to end users.
 */
export const requestContextMiddleware = (
  req: Request,
  _res: Response,
  next: NextFunction
): void => {
  req.context = createRequestContext(req);
  // Bind the id for the whole request so the Logger tags every line.
  runWithRequestContext(
    { rootId: req.context.requestId },
    () => next(),
  );
};
