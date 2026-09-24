import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from './types';
import { recordHttpRequest } from '../services/telemetry/modules/http-metrics';
import {
  domainFromEmail,
  normalizeOrgId,
} from '../services/telemetry/identity';

type MetricsRequest = AuthenticatedUserRequest & {
  __metricsAttached?: boolean;
};

function getRouteTemplate(req: AuthenticatedUserRequest): string {
  const route: unknown = req.route;
  if (route != null && typeof route === 'object' && 'path' in route) {
    const routePath = (route as { path: unknown }).path;
    if (typeof routePath === 'string' && routePath !== '') {
      return `${req.baseUrl}${routePath}`;
    }
  }
  // Raw paths (assets, 404s, bot scans) are unbounded label values.
  return 'unmatched';
}

function getOrgId(req: AuthenticatedUserRequest): string {
  const user = req.user;
  if (user != null && typeof user === 'object' && 'orgId' in user) {
    return normalizeOrgId((user as { orgId: unknown }).orgId);
  }
  return 'unknown';
}

function getDomain(req: AuthenticatedUserRequest): string {
  const user = req.user;
  if (user != null && typeof user === 'object' && 'email' in user) {
    const email = (user as { email: unknown }).email;
    if (typeof email === 'string') {
      return domainFromEmail(email);
    }
  }
  return 'unknown';
}

export function metricsMiddleware(): (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => void {
  return (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const metricsReq = req as MetricsRequest;

    // Attach once per request even if the middleware is added multiple times.
    if (metricsReq.__metricsAttached === true) {
      next();
      return;
    }
    metricsReq.__metricsAttached = true;

    const start = process.hrtime.bigint();

    res.on('finish', () => {
      const durationSeconds = Number(process.hrtime.bigint() - start) / 1e9;
      const template = getRouteTemplate(req);
      const orgId = getOrgId(req);
      const domain = getDomain(req);

      recordHttpRequest(
        template,
        req.method,
        res.statusCode,
        orgId,
        durationSeconds,
        domain,
      );
    });

    next();
  };
}
