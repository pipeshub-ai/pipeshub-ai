import { Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { AuthenticatedUserRequest } from './types';
import { Logger } from '../services/logger.service';
import { PrometheusService } from '../services/prometheus/prometheus.service';

const logger = Logger.getInstance({
  service: 'Prometheus Middleware',
});
/**
 * Create metrics middleware for Express
 */
export function metricsMiddleware(container: Container) {
  const prometheusService = container.get(PrometheusService);
  return (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    // Extract user information from request
    const userId = req.user?.userId || 'anonymous';
    const orgId = req.user?.orgId || 'unknown';
    const email = req.user?.email || 'unknown';
    const requestId = req.context?.requestId;
    const reqContext = req.context;

    res.on('finish', () => {
      if (res.statusCode >= 400) {
        logger.error(
          `Request error: RequestId: ${requestId} StatusCode: ${res.statusCode} Method: ${req.method} Path: ${req.path} (User: ${userId}, Org: ${orgId})`,
        );
        prometheusService.recordActivity(
          'error',
          userId,
          orgId,
          email,
          requestId,
          req.method,
          req.path,
          JSON.stringify(reqContext),
          res.statusCode,
        );
      } else if (res.statusCode >= 200 && res.statusCode < 400) {
        logger.info(
          `Request success: RequestId: ${requestId} StatusCode: ${res.statusCode} Method: ${req.method} Path: ${req.path} (User: ${userId}, Org: ${orgId})`,
        );
        prometheusService.recordActivity(
          'success',
          userId,
          orgId,
          email,
          requestId,
          req.method,
          req.path,
          JSON.stringify(reqContext),
          res.statusCode,
        );
      }
    });

    next();
  };
}
