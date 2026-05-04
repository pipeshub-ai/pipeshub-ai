import { Request, Response, RequestHandler } from 'express';
import rateLimit, { Options } from 'express-rate-limit';
import { Logger } from '../services/logger.service';
import { TooManyRequestsError } from '../errors/http.errors';
import { AuthenticatedUserRequest, AuthenticatedServiceRequest } from './types';

/**
 * Get client IP address from request
 */
function getClientIp(req: Request): string {
  const forwarded = req.headers['x-forwarded-for'];
  if (forwarded) {
    const forwardedValue = typeof forwarded === 'string' ? forwarded : forwarded[0];
    if (forwardedValue) {
      const ips = forwardedValue.split(',');
      const firstIp = ips[0];
      if (firstIp) {
        return firstIp.trim();
      }
    }
  }
  const realIp = req.headers['x-real-ip'];
  if (realIp) {
    const realIpValue = typeof realIp === 'string' ? realIp : realIp[0];
    if (realIpValue) {
      return realIpValue;
    }
  }
  return req.ip || req.socket.remoteAddress || 'unknown';
}

// Single global rate limiter
export function createGlobalRateLimiter(logger: Logger, maxRequestsPerMinute: number): RequestHandler {
  const config: Partial<Options> = {
    windowMs: 60 * 1000,
    max: maxRequestsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,

    keyGenerator: (req: Request): string => {
      const authenticatedUserReq = req as AuthenticatedUserRequest;
      const authenticatedServiceReq = req as AuthenticatedServiceRequest;

      if (authenticatedUserReq.user?.userId) {
        return `user:${authenticatedUserReq.user.userId}`;
      }
      if (authenticatedServiceReq.tokenPayload?.orgId) {
        return `org:${authenticatedServiceReq.tokenPayload.orgId}`;
      }
      const ip = getClientIp(req);
      return `ip:${ip}`;
    },

    skip: (req: Request): boolean => {
      const authenticatedServiceReq = req as AuthenticatedServiceRequest;
      if (authenticatedServiceReq.tokenPayload) {
        logger.debug('Skipping rate limit for service request', {
          orgId: authenticatedServiceReq.tokenPayload.orgId,
          userId: authenticatedServiceReq.tokenPayload.userId,
        });
        return true;
      }
      return false;
    },

    handler: (req: Request, res: Response): void => {
      const retryAfter = res.getHeader('Retry-After');
      const rateLimitKey = getRateLimitKey(req);

      logger.warn('Rate limit exceeded', {
        key: rateLimitKey,
        path: req.path,
        method: req.method,
        ip: getClientIp(req),
        retryAfter,
      });

      const error = new TooManyRequestsError('Too many requests. Please try again later.');
      res.status(429).json({
        error: {
          code: error.code,
          message: error.message,
          retryAfter: retryAfter ? parseInt(retryAfter as string, 10) : null,
        },
      });
    },
  };

  function getRateLimitKey(req: Request): string {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    const authenticatedServiceReq = req as AuthenticatedServiceRequest;
    if (authenticatedUserReq.user?.userId) {
      return `user:${authenticatedUserReq.user.userId}`;
    }
    if (authenticatedServiceReq.tokenPayload?.orgId) {
      return `org:${authenticatedServiceReq.tokenPayload.orgId}`;
    }
    return `ip:${getClientIp(req)}`;
  }

  return rateLimit(config);
}

/**
 * Rate limiter for OAuth client management endpoints
 * Stricter limits: 10 requests per minute per user/IP
 * Used for creating, updating, and deleting OAuth applications
 */
export function createOAuthClientRateLimiter(logger: Logger, maxRequestsPerMinute: number): RequestHandler {
  const config: Partial<Options> = {
    windowMs: 60 * 1000, // 1 minute
    max: maxRequestsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,

    keyGenerator: (req: Request): string => {
      const authenticatedUserReq = req as AuthenticatedUserRequest;

      if (authenticatedUserReq.user?.userId) {
        return `oauth-client:user:${authenticatedUserReq.user.userId}`;
      }
      const ip = getClientIp(req);
      return `oauth-client:ip:${ip}`;
    },

    handler: (req: Request, res: Response): void => {
      const retryAfter = res.getHeader('Retry-After');
      const authenticatedUserReq = req as AuthenticatedUserRequest;
      const key = authenticatedUserReq.user?.userId
        ? `oauth-client:user:${authenticatedUserReq.user.userId}`
        : `oauth-client:ip:${getClientIp(req)}`;

      logger.warn('OAuth client rate limit exceeded', {
        key,
        path: req.path,
        method: req.method,
        ip: getClientIp(req),
        retryAfter,
      });

      const error = new TooManyRequestsError(
        'Too many OAuth client requests. Please try again later.',
      );
      res.status(429).json({
        error: {
          code: error.code,
          message: error.message,
          retryAfter: retryAfter ? parseInt(retryAfter as string, 10) : null,
        },
      });
    },
  };

  return rateLimit(config);
}

/**
 * Strict per-IP rate limiter for the anonymous OAuth Dynamic Client Registration
 * endpoint (POST /api/v1/oauth2/register). Each successful call writes a new
 * row to `oauthApps` and mints a `registration_access_token`, so abuse equals
 * DB pollution + CPU work. Default is intentionally tight: 5 registrations
 * per minute per IP.
 *
 * RFC 7591 §5: "the authorization server SHOULD perform some form of rate
 * limiting on the registration endpoint."
 *
 * Note: response shape is RFC 7591 §3.2.2 (OAuth-style error), not the generic
 * envelope used by `createOAuthClientRateLimiter`. MCP clients expect this.
 */
export function createDcrRegisterRateLimiter(
  logger: Logger,
  maxRegistrationsPerMinute: number,
): RequestHandler {
  const config: Partial<Options> = {
    windowMs: 60 * 1000,
    max: maxRegistrationsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,
    // Always per-IP — this endpoint is anonymous.
    keyGenerator: (req: Request): string => `dcr-register:ip:${getClientIp(req)}`,
    handler: (req: Request, res: Response): void => {
      const retryAfter = res.getHeader('Retry-After');
      const ip = getClientIp(req);
      logger.warn('DCR register rate limit exceeded', {
        ip,
        path: req.path,
        retryAfter,
      });
      if (retryAfter) {
        res.setHeader('Retry-After', String(retryAfter));
      }
      res.status(429).json({
        error: 'too_many_requests',
        error_description:
          'Too many client registration attempts from this address. Please try again later.',
      });
    },
  };
  return rateLimit(config);
}