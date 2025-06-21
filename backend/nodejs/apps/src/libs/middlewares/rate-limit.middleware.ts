// import { Request, Response, NextFunction } from 'express';
// import { Redis } from 'ioredis';
// import { Logger } from '../services/logger.service';
// import { TooManyRequestsError } from '../errors/http.errors';

// export interface RateLimitConfig {
//   windowMs: number;
//   max: number;
//   keyPrefix?: string;
//   handler?: (req: Request, res: Response) => void;
//   skipFailure?: boolean;
// }

// export class RateLimitMiddleware {
//   private redis: Redis;
//   private logger: Logger;

//   constructor() {
//     this.redis = new Redis(process.env.REDIS_URL!);
//     this.logger = Logger.getInstance();
//   }

//   createLimiter(config: RateLimitConfig) {
//     const keyPrefix = config.keyPrefix || 'rate-limit:';

//     return async (req: Request, res: Response, next: NextFunction) => {
//       try {
//         const key = `${keyPrefix}${req.ip}:${req.path}`;

//         const result = await this.redis
//           .multi()
//           .incr(key)
//           .expire(key, Math.ceil(config.windowMs / 1000))
//           .exec();

//         if (!result || result[0][0]) {
//           throw new Error('Redis operation failed');
//         }

//         const current = result[0][1] as number;

//         // Set rate limit headers
//         res.setHeader('X-RateLimit-Limit', config.max);
//         res.setHeader(
//           'X-RateLimit-Remaining',
//           Math.max(0, config.max - current),
//         );
//         res.setHeader(
//           'X-RateLimit-Reset',
//           new Date(Date.now() + config.windowMs).toISOString(),
//         );

//         if (current > config.max) {
//           this.logger.warn('Rate limit exceeded', {
//             ip: req.ip,
//             path: req.path,
//             current,
//             max: config.max,
//           });

//           if (config.skipFailure) {
//             return next();
//           }

//           if (config.handler) {
//             return config.handler(req, res);
//           }

//           throw new TooManyRequestsError('Too many requests', {
//             limit: config.max,
//             windowMs: config.windowMs,
//           });
//         }

//         next();
//       } catch (error) {
//         next(error);
//       }
//     };
//   }
// }
