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
      // Internal routes (/…/internal/…) are service-to-service calls protected
      // by scopedTokenValidator. That middleware runs AFTER the global rate
      // limiter (route middleware executes later than app.use middleware), so
      // req.tokenPayload is never set here for those requests. Checking the
      // path directly is safe: internal routes require a scoped JWT signed with
      // the server secret, so external callers cannot reach them.
      if (req.path.includes('/internal/') || req.path.endsWith('/internal')) {
        return true;
      }
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

export interface KeyedRateLimiterOptions {
  prefix: string;
  maxRequestsPerMinute: number;
  message: string;
}

/**
 * Per-user (fallback: per-IP) limiter used by the OAuth-client and skills-import
 * surfaces. The store is in-process, matching `createOAuthClientRateLimiter`'s
 * historical behaviour — N replicas therefore admit N×max/min until a shared
 * store is wired.
 */
export function createKeyedRateLimiter(
  logger: Logger,
  options: KeyedRateLimiterOptions,
): RequestHandler {
  const { prefix, maxRequestsPerMinute, message } = options;

  const keyFor = (req: Request): string => {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    if (authenticatedUserReq.user?.userId) {
      return `${prefix}:user:${authenticatedUserReq.user.userId}`;
    }
    return `${prefix}:ip:${getClientIp(req)}`;
  };

  const config: Partial<Options> = {
    windowMs: 60 * 1000,
    max: maxRequestsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: keyFor,
    handler: (req: Request, res: Response): void => {
      const retryAfter = res.getHeader('Retry-After');
      logger.warn('Rate limit exceeded', {
        key: keyFor(req),
        path: req.path,
        method: req.method,
        ip: getClientIp(req),
        retryAfter,
      });
      const error = new TooManyRequestsError(message);
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
 * Rate limiter for OAuth client management endpoints
 * Stricter limits: 10 requests per minute per user/IP
 * Used for creating, updating, and deleting OAuth applications
 */
export function createOAuthClientRateLimiter(
  logger: Logger,
  maxRequestsPerMinute: number,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'oauth-client',
    maxRequestsPerMinute,
    message: 'Too many OAuth client requests. Please try again later.',
  });
}

/**
 * Stricter limiter for skill package-import endpoints (npm/URL fetch + upload).
 * Default 10 req/min per user; the upload route must mount this BEFORE multer
 * so a throttled client never has a 25 MB archive buffered.
 */
export function createSkillsImportRateLimiter(
  logger: Logger,
  maxRequestsPerMinute = 10,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'skills-import',
    maxRequestsPerMinute,
    message: 'Too many skill import requests. Please try again later.',
  });
}