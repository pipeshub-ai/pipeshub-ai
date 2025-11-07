import { Request, Response, NextFunction, RequestHandler } from 'express';
import rateLimit, { Options } from 'express-rate-limit';
import { inject, injectable } from 'inversify';
import { Logger } from '../services/logger.service';
import { TooManyRequestsError } from '../errors/http.errors';
import { AuthenticatedUserRequest, AuthenticatedServiceRequest } from './types';

/**
 * Rate limiter configuration options
 */
export interface RateLimiterOptions {
  windowMs?: number;
  max?: number;
  message?: string;
  skipSuccessfulRequests?: boolean;
  skipFailedRequests?: boolean;
}

/**
 * Rate limiter tier types
 */
export enum RateLimiterTier {
  READ = 'READ',
  WRITE = 'WRITE',
  STRICT = 'STRICT',
  CUSTOM = 'CUSTOM',
}

/**
 * Default rate limiter configurations for different tiers
 */
const DEFAULT_RATE_LIMITS = {
  [RateLimiterTier.READ]: {
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 100, // 100 requests per window
    message: 'Too many read requests. Please try again later.',
  },
  [RateLimiterTier.WRITE]: {
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 20, // 20 requests per window
    message: 'Too many write requests. Please try again later.',
  },
  [RateLimiterTier.STRICT]: {
    windowMs: 60 * 60 * 1000, // 1 hour
    max: 10, // 10 requests per window
    message: 'Too many requests to this sensitive endpoint. Please try again later.',
  },
};

/**
 * Rate Limiter Middleware
 * Provides rate limiting functionality with user-based quotas and service exemption
 */
@injectable()
export class RateLimiterMiddleware {
  constructor(@inject('Logger') private logger: Logger) {}

  /**
   * Creates a rate limiter for read operations (GET)
   * Default: 100 requests per 15 minutes
   */
  createReadLimiter(options?: RateLimiterOptions): RequestHandler {
    return this.createRateLimiter(RateLimiterTier.READ, options);
  }

  /**
   * Creates a rate limiter for write operations (POST, PUT, PATCH, DELETE)
   * Default: 20 requests per 15 minutes
   */
  createWriteLimiter(options?: RateLimiterOptions): RequestHandler {
    return this.createRateLimiter(RateLimiterTier.WRITE, options);
  }

  /**
   * Creates a strict rate limiter for highly sensitive operations
   * Default: 10 requests per 1 hour
   */
  createStrictLimiter(options?: RateLimiterOptions): RequestHandler {
    return this.createRateLimiter(RateLimiterTier.STRICT, options);
  }

  /**
   * Creates a custom rate limiter with specified options
   */
  createCustomLimiter(options: Required<RateLimiterOptions>): RequestHandler {
    return this.createRateLimiter(RateLimiterTier.CUSTOM, options);
  }

  /**
   * Internal method to create rate limiters
   */
  private createRateLimiter(
    tier: RateLimiterTier,
    customOptions?: RateLimiterOptions,
  ): RequestHandler {
    const defaultConfig =
      tier === RateLimiterTier.CUSTOM
        ? customOptions
        : DEFAULT_RATE_LIMITS[tier];

    // Capture the message to pass to the handler
    const message = customOptions?.message ?? defaultConfig?.message ?? 'Too many requests. Please try again later.';

    const config: Partial<Options> = {
      windowMs: customOptions?.windowMs ?? defaultConfig?.windowMs,
      max: customOptions?.max ?? defaultConfig?.max,
      message,
      standardHeaders: true, // Return rate limit info in `RateLimit-*` headers
      legacyHeaders: false, // Disable `X-RateLimit-*` headers
      skipSuccessfulRequests: customOptions?.skipSuccessfulRequests ?? false,
      skipFailedRequests: customOptions?.skipFailedRequests ?? false,

      // Use a key generator that considers both IP and user ID for authenticated routes
      keyGenerator: (req: Request): string => {
        return this.generateRateLimitKey(req);
      },

      // Skip rate limiting for internal service requests
      skip: (req: Request): boolean => {
        return this.shouldSkipRateLimit(req);
      },

      // Custom handler for when limit is exceeded
      handler: (req: Request, res: Response): void => {
        this.handleRateLimitExceeded(req, res, tier, message);
      },
    };

    return rateLimit(config);
  }

  /**
   * Generates a unique key for rate limiting based on user ID or IP
   */
  private generateRateLimitKey(req: Request): string {
    const authenticatedUserReq = req as AuthenticatedUserRequest;
    const authenticatedServiceReq = req as AuthenticatedServiceRequest;

    // Priority 1: Use user ID if available (authenticated user)
    if (authenticatedUserReq.user?.userId) {
      return `user:${authenticatedUserReq.user.userId}`;
    }

    // Priority 2: Use org ID for service requests (backup)
    if (authenticatedServiceReq.tokenPayload?.orgId) {
      return `org:${authenticatedServiceReq.tokenPayload.orgId}`;
    }

    // Priority 3: Fall back to IP address
    const ip = this.getClientIp(req);
    return `ip:${ip}`;
  }

  /**
   * Determines whether to skip rate limiting for a request
   */
  private shouldSkipRateLimit(req: Request): boolean {
    const authenticatedServiceReq = req as AuthenticatedServiceRequest;

    // Skip rate limiting for internal service-to-service requests
    if (authenticatedServiceReq.tokenPayload) {
      this.logger.debug('Skipping rate limit for service request', {
        orgId: authenticatedServiceReq.tokenPayload.orgId,
        userId: authenticatedServiceReq.tokenPayload.userId,
      });
      return true;
    }

    return false;
  }

  /**
   * Handles rate limit exceeded scenarios
   */
  private handleRateLimitExceeded(
    req: Request,
    res: Response,
    tier: RateLimiterTier,
    message: string,
  ): void {
    const retryAfter = res.getHeader('Retry-After');
    const rateLimitKey = this.generateRateLimitKey(req);

    // Log rate limit hit
    this.logger.warn('Rate limit exceeded', {
      tier,
      key: rateLimitKey,
      path: req.path,
      method: req.method,
      ip: this.getClientIp(req),
      retryAfter,
    });

    // Send structured error response with the configured message
    const error = new TooManyRequestsError(
      message || 'Too many requests. Please try again later.',
    );

    res.status(429).json({
      error: {
        code: error.code,
        message: error.message,
        retryAfter: retryAfter ? parseInt(retryAfter as string, 10) : null,
        tier,
      },
    });
  }

  /**
   * Gets the client IP address from the request
   */
  private getClientIp(req: Request): string {
    // Check for proxied requests
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

    // Check for real IP header (common in cloud environments)
    const realIp = req.headers['x-real-ip'];
    if (realIp) {
      const realIpValue = typeof realIp === 'string' ? realIp : realIp[0];
      if (realIpValue) {
        return realIpValue;
      }
    }

    // Fall back to connection remote address
    return req.ip || req.socket.remoteAddress || 'unknown';
  }

  /**
   * Middleware to check rate limit status without consuming quota
   * Useful for preflight checks
   */
  checkRateLimitStatus(): RequestHandler {
    return (req: Request, res: Response, next: NextFunction) => {
      try {
        const key = this.generateRateLimitKey(req);

        // Add rate limit info to response headers (if available from previous middleware)
        const limit = res.getHeader('RateLimit-Limit');
        const remaining = res.getHeader('RateLimit-Remaining');
        const reset = res.getHeader('RateLimit-Reset');

        this.logger.debug('Rate limit status check', {
          key,
          limit,
          remaining,
          reset,
        });

        next();
      } catch (error) {
        next(error);
      }
    };
  }
}

/**
 * Rate limiter middleware factory
 * Provides a convenient way to create rate limiters without dependency injection
 */
export class RateLimiterFactory {
  /**
   * Creates a read limiter (100 requests / 15 minutes)
   */
  static createReadLimiter(
    logger: Logger,
    options?: RateLimiterOptions,
  ): RequestHandler {
    const middleware = new RateLimiterMiddleware(logger);
    return middleware.createReadLimiter(options);
  }

  /**
   * Creates a write limiter (20 requests / 15 minutes)
   */
  static createWriteLimiter(
    logger: Logger,
    options?: RateLimiterOptions,
  ): RequestHandler {
    const middleware = new RateLimiterMiddleware(logger);
    return middleware.createWriteLimiter(options);
  }

  /**
   * Creates a strict limiter (10 requests / 1 hour)
   */
  static createStrictLimiter(
    logger: Logger,
    options?: RateLimiterOptions,
  ): RequestHandler {
    const middleware = new RateLimiterMiddleware(logger);
    return middleware.createStrictLimiter(options);
  }

  /**
   * Creates a custom limiter with specified options
   */
  static createCustomLimiter(
    logger: Logger,
    options: Required<RateLimiterOptions>,
  ): RequestHandler {
    const middleware = new RateLimiterMiddleware(logger);
    return middleware.createCustomLimiter(options);
  }
}