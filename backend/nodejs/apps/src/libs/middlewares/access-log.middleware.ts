import { NextFunction, Request, RequestHandler, Response } from 'express';
import {
  getRequestContext,
  runWithRequestContext,
} from '../context/request-context';
import { Logger } from '../services/logger.service';
import { accessLogLevel } from '../utils/api-path.utils';
import { classifyRequestError, ErrorClass } from '../utils/error-class';

const UNMATCHED = 'unmatched';

interface MatchedRoute {
  baseUrl: string;
  params: Record<string, string>;
}

const matched = new WeakMap<Request, MatchedRoute>();
const deepestBase = new WeakMap<Request, string>();
const failures = new WeakMap<Response, ErrorClass>();

/**
 * Express restores `req.baseUrl` and `req.params` once a router hands an error
 * on with `next(err)`, so by the time the error middleware or `close` runs, the
 * mount and the ids are gone. Record them when Express assigns them.
 */
function trackRouting(req: Request): void {
  let route: unknown = req.route;
  Object.defineProperty(req, 'route', {
    configurable: true,
    enumerable: true,
    get: () => route,
    set: (value: unknown) => {
      route = value;
      matched.set(req, { baseUrl: req.baseUrl, params: { ...req.params } });
    },
  });
  let baseUrl = req.baseUrl;
  Object.defineProperty(req, 'baseUrl', {
    configurable: true,
    enumerable: true,
    get: () => baseUrl,
    set: (value: string) => {
      baseUrl = value;
      if (
        typeof value === 'string' &&
        value.length > (deepestBase.get(req)?.length ?? 0)
      ) {
        deepestBase.set(req, value);
      }
    },
  });
}

/**
 * The route the request matched, as a template (`/api/v1/kb/:kbId`). A router
 * that answered before any route matched (router-level auth) gives its mount;
 * nothing at all gives `unmatched`.
 * shortcut: a parameterised mount renders its raw value; template it if one is ever added (none today).
 */
export function routeTemplate(req: Request): string {
  const route = req.route as { path?: unknown } | undefined;
  if (route !== undefined) {
    if (typeof route.path !== 'string') return UNMATCHED;
    return `${matched.get(req)?.baseUrl ?? req.baseUrl}${route.path}`;
  }
  const mount = deepestBase.get(req) ?? req.baseUrl;
  return typeof mount === 'string' && mount !== '' ? mount : UNMATCHED;
}

/** The route params as the matched route saw them. */
export function routeParams(req: Request): Record<string, string> {
  return matched.get(req)?.params ?? req.params;
}

/** The error middleware records why the request failed, so the access line says the same. */
export function markRequestError(res: Response, errorClass: ErrorClass): void {
  failures.set(res, errorClass);
}

function requestErrorClass(
  res: Response,
  status: number | undefined,
): ErrorClass | undefined {
  // The error middleware's class wins: a stream that failed midway never finishes either.
  const marked = failures.get(res);
  if (marked !== undefined) return marked;
  if (!res.writableFinished) return 'cancelled';
  return status !== undefined && status >= 400
    ? classifyRequestError(undefined, status)
    : undefined;
}

function logAccess(req: Request, res: Response, started: bigint): void {
  const [path = ''] = req.originalUrl.split('?');
  const status = res.headersSent ? res.statusCode : undefined;
  const level = accessLogLevel(path, status ?? 0);
  if (level === null) return;
  const errorClass = requestErrorClass(res, status);
  // No URL, query, referrer or user agent: the route template says what, and nothing is left to redact.
  Logger.getInstance({ service: 'HttpAccess' })[level]('http.request.end', {
    method: req.method,
    route: routeTemplate(req),
    status,
    duration_ms: Math.round(Number(process.hrtime.bigint() - started) / 1e6),
    client_ip: req.ip,
    error_class: errorClass,
    outcome: errorClass === undefined ? 'success' : 'failure',
    event_name: 'http.request.end',
  });
}

/**
 * One `http.request.end` line per request, written when the response closes
 * (finished, failed or aborted; for a stream, when it ends), with the request's
 * context as it stood then: the auth layer adds org and user to it in place.
 */
export function accessLog(): RequestHandler {
  return (req: Request, res: Response, next: NextFunction) => {
    const started = process.hrtime.bigint();
    const store = getRequestContext();
    trackRouting(req);
    res.once('close', () => {
      const log = (): void => {
        // In a 'close' listener a throw would be an uncaught exception.
        try {
          logAccess(req, res, started);
        } catch (logFailure) {
          console.error('Logging the access line failed:', logFailure);
        }
      };
      if (store !== undefined) runWithRequestContext(store, log);
      else log();
    });
    next();
  };
}
