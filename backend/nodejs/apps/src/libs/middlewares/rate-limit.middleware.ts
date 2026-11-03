import { createHash, createSecretKey, KeyObject } from 'node:crypto';
import { Request, Response, RequestHandler } from 'express';
import rateLimit, { Options, ipKeyGenerator } from 'express-rate-limit';
import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';
import { Logger } from '../services/logger.service';
import { TooManyRequestsError } from '../errors/http.errors';
import { AuthenticatedUserRequest, AuthenticatedServiceRequest } from './types';

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

export function slackEventsRateLimitPerMinute(): number {
  const parsed = Number(process.env.SLACK_EVENTS_RATE_LIMIT_PER_MINUTE);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 300;
}

export interface KeyedRateLimiterOptions {
  prefix: string;
  maxRequestsPerMinute: number;
  message: string;
  countOnlyFailures?: boolean;
  // Narrows an anonymous caller's per-IP bucket, e.g. to the account it names.
  anonymousSubKey?: (req: Request) => string | undefined;
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
  const {
    prefix,
    maxRequestsPerMinute,
    message,
    countOnlyFailures = false,
    anonymousSubKey,
  } = options;

  const keyFor = (req: Request): string => {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    if (authenticatedUserReq.user?.userId) {
      return `${prefix}:user:${authenticatedUserReq.user.userId}`;
    }
    const ipKey = `${prefix}:ip:${getClientIpKey(req)}`;
    const subKey = anonymousSubKey?.(req);
    return subKey === undefined ? ipKey : `${ipKey}:${subKey}`;
  };

  const config: Partial<Options> = {
    windowMs: 60 * 1000,
    max: maxRequestsPerMinute,
    standardHeaders: true,
    legacyHeaders: false,
    skipSuccessfulRequests: countOnlyFailures,
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

// Per-IP auth buckets admit this many times the per-account auth limit, so
// several people signing in from one office or VPN address are not throttled.
const AUTH_PER_IP_MULTIPLIER = 6;

/**
 * The account a sign-in request targets. After /initAuth the session's email
 * is authoritative (the controller ignores body.email then), so it wins; a
 * client could otherwise rotate body.email to get a fresh bucket. Hashed so
 * addresses stay out of the 429 log line and an oversized value cannot bloat
 * the store.
 */
function signInAccountKey(req: Request): string | undefined {
  const sessionEmail = (req as { sessionInfo?: { email?: unknown } })
    .sessionInfo?.email;
  const body = req.body as { email?: unknown; username?: unknown } | undefined;
  const account = [sessionEmail, body?.email, body?.username].find(
    (value): value is string =>
      typeof value === 'string' && value.trim() !== '',
  );
  if (account === undefined) {
    return undefined;
  }
  const digest = createHash('sha256')
    .update(account.trim().toLowerCase())
    .digest('base64url');
  return `acct:${digest.slice(0, 22)}`;
}

/**
 * Login/OTP/password endpoints. The global limiter is sized for general API
 * traffic and is too loose to stop password guessing or OTP/email bombing.
 * Counted per signed-in user, else per client IP and the account the request
 * names, so people sharing one address do not use up each other's attempts.
 * Pair it with createAuthIpRateLimiter on anonymous routes: on its own it
 * would let one address try every account. Mount it after any middleware
 * that sets req.user or req.sessionInfo, or it falls back to the shared
 * per-IP key. The limit is per replica (in-process store), so N pods admit
 * up to N × maxRequestsPerMinute until a shared store is wired.
 */
export function createAuthRateLimiter(
  logger: Logger,
  maxRequestsPerMinute = 10,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'auth',
    maxRequestsPerMinute,
    message: 'Too many authentication requests. Please try again later.',
    anonymousSubKey: signInAccountKey,
  });
}

/**
 * Per-IP backstop for the anonymous auth routes, at a multiple of the
 * per-account limit: it caps one address spraying passwords or OTP/reset
 * emails across many accounts.
 */
export function createAuthIpRateLimiter(
  logger: Logger,
  maxAuthRequestsPerMinute = 10,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'auth-ip',
    maxRequestsPerMinute: maxAuthRequestsPerMinute * AUTH_PER_IP_MULTIPLIER,
    message: 'Too many authentication requests. Please try again later.',
  });
}

/**
 * /initAuth only. The sign-in page calls it on every load, before anyone types
 * a password, and it checks no credential, so it gets its own per-IP bucket:
 * page loads must not use up the attempts createAuthRateLimiter allows. Still
 * limited because every call stores a sign-in session.
 */
export function createInitAuthRateLimiter(
  logger: Logger,
  maxAuthRequestsPerMinute = 10,
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'auth-init',
    maxRequestsPerMinute: maxAuthRequestsPerMinute * AUTH_PER_IP_MULTIPLIER,
    message: 'Too many sign-in requests. Please try again later.',
  });
}

/**
 * Public /slack/events entry. Slack deliveries are anonymous, so this keys on
 * the client IP. Mount it on the Express gateway only: the bot process behind
 * it sees every org's traffic from the gateway's address, so a limiter there
 * would be one bucket shared by all orgs.
 *
 * Only responses of 400 or above count. Every org's real deliveries arrive from
 * Slack's few egress addresses, so counting them would let one busy workspace
 * throttle all the others, and a 429 makes Slack redeliver and add more load.
 * Bad signatures (the bot's 401) still fill the bucket.
 */
export function createSlackEventsRateLimiter(
  logger: Logger,
  maxRequestsPerMinute = slackEventsRateLimitPerMinute(),
): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix: 'slack-events',
    maxRequestsPerMinute,
    message: 'Too many Slack event deliveries.',
    countOnlyFailures: true,
  });
}
