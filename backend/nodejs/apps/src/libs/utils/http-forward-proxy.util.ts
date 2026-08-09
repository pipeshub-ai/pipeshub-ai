/**
 * Shared thin-proxy forwarding helpers -- extracted from the (previously
 * byte-for-byte duplicated) `tasks.controller.ts` and
 * `workflows.controller.ts`. Any new gateway module that forwards
 * dashboard requests verbatim to the Python query service should use
 * this instead of re-copying `buildForwardHeaders`/`mapAxiosError`/
 * `forwardJson`.
 */

import { Response, NextFunction } from 'express';
import axios, { AxiosRequestConfig, AxiosResponse } from 'axios';

import { AuthenticatedUserRequest } from '../middlewares/types';
import { Logger } from '../services/logger.service';
import { BadGatewayError, ServiceUnavailableError } from '../errors/http.errors';
import { HttpMethod } from '../enums/http-methods.enum';
import { AppConfig } from '../../modules/tokens_manager/config/config';

// Avoids stale content-length/connection headers breaking the forwarded
// request.
const HOP_BY_HOP_HEADERS = new Set([
  'host',
  'content-length',
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'accept-encoding',
]);

export function buildForwardHeaders(
  req: AuthenticatedUserRequest,
  extra: Record<string, string> = {},
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    if (HOP_BY_HOP_HEADERS.has(key.toLowerCase())) continue;
    headers[key] = Array.isArray(value) ? value.join(', ') : String(value);
  }
  if (req.headers.authorization && !headers.authorization) {
    headers.authorization = String(req.headers.authorization);
  }
  return { ...headers, ...extra };
}

export function mapAxiosError(logger: Logger, error: unknown, action: string): Error {
  const err = error as {
    response?: { status: number; data: unknown };
    code?: string;
    message?: string;
  };
  if (err?.response) {
    logger.error(`${action} upstream returned ${err.response.status}`, {
      status: err.response.status,
      data: err.response.data,
    });
    return new BadGatewayError(
      typeof err.response.data === 'object' && err.response.data !== null
        ? ((err.response.data as Record<string, unknown>).detail as string) ||
          ((err.response.data as Record<string, unknown>).message as string) ||
          `${action} failed (upstream ${err.response.status})`
        : `${action} failed (upstream ${err.response.status})`,
    );
  }
  logger.error(`${action} upstream unreachable`, {
    code: err?.code,
    message: err?.message,
  });
  return new ServiceUnavailableError(`${action} failed: upstream service is unavailable`);
}

export type PathBuilder = (req: AuthenticatedUserRequest) => string;
type HttpMethodValue = (typeof HttpMethod)[keyof typeof HttpMethod];

/**
 * Factory for the common case: JSON in, JSON out, one upstream call.
 * `req.query` is forwarded via axios's `params` for every method
 * (harmless for POST/DELETE, and is how list-filter query params reach
 * Python without each handler re-deriving a query string).
 */
export function createForwardJsonHandler(logger: Logger) {
  return function forwardJson(method: HttpMethodValue, pathBuilder: PathBuilder, action: string) {
    return (appConfig: AppConfig) =>
      async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
        const path = pathBuilder(req);
        const url = `${appConfig.aiBackend}${path}`;
        logger.info(`[proxy] ${method.toUpperCase()} ${path} → ${url}`, {
          action,
          query: req.query,
          hasBody: !!(req.body && Object.keys(req.body).length),
        });
        try {
          const requestConfig: AxiosRequestConfig = {
            url,
            method,
            params: req.query,
            data: method === HttpMethod.GET || method === HttpMethod.DELETE ? undefined : req.body,
            headers: buildForwardHeaders(req, { 'Content-Type': 'application/json' }),
            timeout: 30_000,
            validateStatus: () => true,
          };
          const response: AxiosResponse = await axios.request(requestConfig);
          logger.info(`[proxy] ${action} → ${response.status}`, {
            action,
            upstream: url,
            status: response.status,
          });
          if (response.status >= 400) {
            logger.warn(`[proxy] ${action} upstream error`, {
              status: response.status,
              data: response.data,
            });
          }
          res.status(response.status).json(response.data);
        } catch (error) {
          logger.error(`[proxy] ${action} request failed`, { upstream: url });
          next(mapAxiosError(logger, error, action));
        }
      };
  };
}

export const encId = (value: string | undefined): string => encodeURIComponent(String(value ?? ''));
