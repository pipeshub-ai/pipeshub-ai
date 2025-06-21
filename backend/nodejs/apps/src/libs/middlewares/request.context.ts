import crypto from "crypto";
import { Request, Response, NextFunction } from "express";

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

/**
 * Generates a random request ID
 * @param userId - Optional user ID to include in the request ID (currently not used)
 * @returns Generated request ID
 */
const generateRequestId = (): string => {
  const timestamp = Date.now();
  const random = crypto.randomBytes(10).toString("hex");
  return `${timestamp}-${random}`;
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
    requestId: req.get("X-Request-ID") || generateRequestId(),
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
 */
export const requestContextMiddleware = (
  req: Request,
  res: Response,
  next: NextFunction
): void => {
  req.context = createRequestContext(req);
  // Add request ID to response headers
  res.setHeader("X-Request-ID", req.context.requestId);
  next();
};
