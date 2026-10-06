import { createSecretKey, KeyObject } from 'node:crypto';
import { Request, Response, RequestHandler } from 'express';
import rateLimit, { Options, ipKeyGenerator } from 'express-rate-limit';
import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';
import { Logger } from '../services/logger.service';
import { TooManyRequestsError } from '../errors/http.errors';
import { AuthenticatedUserRequest, AuthenticatedServiceRequest } from './types';
import { SharedRateLimitStore } from './shared-rate-limit-store';

/**
 * Never read X-Forwarded-For / X-Real-IP directly: the client controls them.
 * req.ip honours the app's `trust proxy` setting (TRUST_PROXY).
 */
function getClientIp(req: Request): string {
  return req.ip ?? req.socket.remoteAddress ?? 'unknown';
}

function getClientIpKey(req: Request): string {
  // Anonymous requests are counted by IP. An IPv4 address is one per client
  // and is used as-is. An IPv6 client gets a whole block of addresses, so
  // those are folded into one key; switching address would reset the limit.
  return ipKeyGenerator(getClientIp(req));
}

export const CALLER_ROLE_LOOKUP_PATH = '/api/v1/users/me/role';
export const SERVICE_AUTHORIZATION_HEADER = 'x-service-authorization';

/**
 * A Python service asking about one of its callers, proven by a service token
 * signed with the scoped secret and scoped to exactly this lookup. Every
 * user's lookup leaves from the same few service addresses, so counting them
 * per address would throttle every user at once. The route still checks the
 * user's own token; only the counting is skipped.
 */
function isVerifiedCallerRoleLookup(
  req: Request,
  key: KeyObject | null,
): boolean {
  if (!key || req.method !== 'GET' || req.path !== CALLER_ROLE_LOOKUP_PATH) {
    return false;
  }
  const header = req.headers[SERVICE_AUTHORIZATION_HEADER];
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) {
    return false;
  }
  try {
    const claims = jwt.verify(header.slice('Bearer '.length), key, {
      algorithms: ['HS256'],
    });
    const scopes: unknown =
      typeof claims === 'object'
        ? (claims as { scopes?: unknown }).scopes
        : undefined;
    return Array.isArray(scopes) && scopes.includes(TokenScopes.CALLER_ROLE);
  } catch {
    return false;
  }
}

// Single global rate limiter
export function createGlobalRateLimiter(
  logger: Logger,
  maxRequestsPerMinute: number,
  scopedJwtSecret?: string,
): RequestHandler {
  // Built once: given a raw string, jsonwebtoken re-parses the key on every call.
  const serviceKey =
    scopedJwtSecret !== undefined && scopedJwtSecret !== ''
      ? createSecretKey(Buffer.from(scopedJwtSecret))
      : null;
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
      return `ip:${getClientIpKey(req)}`;
    },

    skip: (req: Request): boolean => {
      if (isVerifiedCallerRoleLookup(req, serviceKey)) {
        return true;
      }
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

      const error = new TooManyRequestsError(
        'Too many requests. Please try again later.',
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

  function getRateLimitKey(req: Request): string {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    const authenticatedServiceReq = req as AuthenticatedServiceRequest;
    if (authenticatedUserReq.user?.userId) {
      return `user:${authenticatedUserReq.user.userId}`;
    }
    if (authenticatedServiceReq.tokenPayload?.orgId) {
      return `org:${authenticatedServiceReq.tokenPayload.orgId}`;
    }
    return `ip:${getClientIpKey(req)}`;
  }

  return rateLimit(config);
}

export interface KeyedRateLimiterOptions {
  prefix: string;
  maxRequestsPerMinute: number;
  message: string;
  /** Error code sent on a 429, with `retryAfter` under `details`. Without it the historical body is kept. */
  code?: string;
  /** Count in the shared cache so the limit holds across replicas; falls back to in-process on error. */
  shared?: boolean;
}

/**
 * Per-user (fallback: per-IP) limiter. The store is in-process unless `shared`
 * is set — N replicas then admit N×max/min. `prefix` is the bucket identity, so
 * two limiters that must not share a count need different prefixes.
 */
export function createKeyedRateLimiter(
  logger: Logger,
  options: KeyedRateLimiterOptions,
): RequestHandler {
  const { prefix, maxRequestsPerMinute, message, code, shared } = options;

  const keyFor = (req: Request): string => {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    if (authenticatedUserReq.user?.userId) {
      return `${prefix}:user:${authenticatedUserReq.user.userId}`;
    }
    return `${prefix}:ip:${getClientIpKey(req)}`;
  };

  const config: Partial<Options> = {
    windowMs: 60 * 1000,
    max: maxRequestsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: keyFor,
    ...(shared ? { store: new SharedRateLimitStore(prefix, logger) } : {}),
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
      const retryAfterSeconds = retryAfter
        ? parseInt(retryAfter as string, 10)
        : null;
      res.status(429).json({
        error:
          code === undefined
            ? {
                code: error.code,
                message: error.message,
                retryAfter: retryAfterSeconds,
              }
            : {
                code,
                message: error.message,
                details: { retryAfter: retryAfterSeconds },
              },
      });
    },
  };

  return rateLimit(config);
}

export type OAuthClientRateLimitSurface =
  | 'token'
  | 'pat'
  | 'service-token'
  | 'clients'
  | 'service-accounts';

/**
 * Rate limiter for OAuth client, PAT, service-token and service-account endpoints.
 * Stricter limits: 10 requests per minute per user/IP. Each router passes its own
 * `surface`, so the shared Redis key (`oauth-client:<surface>:user:<id>`) keeps the
 * per-router buckets that the in-process stores gave before.
 */
export function createOAuthClientRateLimiter(
  logger: Logger,
  maxRequestsPerMinute: number,
  surface: OAuthClientRateLimitSurface,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: `oauth-client:${surface}`,
    maxRequestsPerMinute,
    message: 'Too many OAuth client requests. Please try again later.',
    shared: true,
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
    shared: true,
  });
}

/**
 * Login/OTP/password endpoints. The global limiter is sized for general API
 * traffic and is too loose to stop password spraying or OTP/email bombing.
 * Unauthenticated, so counted per IP in the shared store: the limit holds across
 * replicas (it falls back to per-replica counting if the cache is unavailable).
 */
export function createAuthRateLimiter(
  logger: Logger,
  maxRequestsPerMinute = 10,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'auth',
    maxRequestsPerMinute,
    message: 'Too many authentication requests. Please try again later.',
    shared: true,
  });
}
