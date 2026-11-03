import { createHash } from 'node:crypto';
import type { NextFunction, Request, RequestHandler, Response } from 'express';
import { TooManyRequestsError } from '../errors/http.errors';
import type { Logger } from '../services/logger.service';
import type { RedisService } from '../services/redis.service';

export interface EmailRateLimiterOptions {
  /** Names the bucket, e.g. `address-lookup`. */
  prefix: string;
  /** Requests about one address allowed in a minute, across every replica. */
  maxPerMinute: number;
  /** The address the request asks about, if it names one. */
  emailOf: (req: Request) => string | undefined;
}

/**
 * Limits the requests that ask about ONE address before any organisation is known (a password-reset or sign-in-code request, an address change). It is never put on the internal address lookup that a correct sign-in also uses, so it cannot lock out the owner. The
 * IP-keyed limiters stop one client from asking about many addresses; this one stops many clients (or one service that
 * relays them all) from probing a single address. The counter lives in Redis, so it is shared by every replica, and only a
 * hash of the address is stored. One counter per clock minute: a burst across a minute boundary can reach twice the limit.
 * If Redis cannot be reached the request goes ahead (the IP limiter still applies) and the failure is logged.
 */
export function createEmailRateLimiter(
  redis: Pick<RedisService, 'increment'>,
  logger: Logger,
  options: EmailRateLimiterOptions,
): RequestHandler {
  const { prefix, maxPerMinute, emailOf } = options;
  return async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // a body that is not a string address (a number, an array, an object) is not limited here; the route's own validation answers it
      const raw: unknown = emailOf(req);
      const email = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
      if (!email) return next();
      const bucket = Math.floor(Date.now() / 60_000);
      const digest = createHash('sha256')
        .update(email)
        .digest('hex')
        .slice(0, 32);
      const count = await redis.increment(
        `rl:email:${prefix}:${digest}:${String(bucket)}`,
        { ttl: 120 },
      );
      if (count > maxPerMinute) {
        logger.warn('Rate limit exceeded for one address', { prefix });
        res.setHeader(
          'Retry-After',
          String(60 - (Math.floor(Date.now() / 1000) % 60)),
        );
        return next(
          new TooManyRequestsError(
            'Too many requests for this address. Please try again later.',
          ),
        );
      }
    } catch (error) {
      logger.error('The per-address rate limit could not be checked', {
        prefix,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    next();
  };
}

/** A limiter whose Redis client is made on first use, so building a router needs no connection (and a missing one only logs). */
export function lazyRedisCounter(
  get: () => Pick<RedisService, 'increment'>,
): Pick<RedisService, 'increment'> {
  return { increment: (key, options) => get().increment(key, options) };
}

/** Requests per minute about one address: `ADDRESS_RATE_LIMIT_PER_MINUTE` or 60. */
export function addressRateLimitPerMinute(): number {
  const parsed = Number(process.env.ADDRESS_RATE_LIMIT_PER_MINUTE);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
}
