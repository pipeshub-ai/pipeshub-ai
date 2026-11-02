/**
 * API Toolsets Controller
 *
 * One-to-one forwards to the Python connectors service's `/api/v1/api-toolsets`
 * router (`backend/python/app/api/routes/api_toolsets.py`). Python checks the
 * caller's admin role itself, so this layer only forwards the caller's
 * identity, the body and the query, and translates the upstream response.
 */

import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { redactSensitiveQueryParams } from '../../../libs/utils/log-redaction.utils';
import {
  executeConnectorCommand,
  handleBackendError,
  handleConnectorResponse,
} from '../../tokens_manager/utils/connector.utils';

const logger = Logger.getInstance({ service: 'ApiToolsetsController' });

const API_TOOLSETS_BASE = '/api/v1/api-toolsets';
// Above the longest Python-side work: a test call (up to 120 s timeout) after a token refresh (15 s).
export const API_TOOLSETS_PROXY_TIMEOUT_MS = 150_000;

type HttpMethodValue = (typeof HttpMethod)[keyof typeof HttpMethod];
type PathBuilder = (req: AuthenticatedUserRequest) => string;

const BODY_METHODS = new Set<HttpMethodValue>([
  HttpMethod.POST,
  HttpMethod.PUT,
  HttpMethod.PATCH,
]);

// The caller's identity and request correlation; every other client header is dropped.
const PROXY_FORWARD_HEADERS: readonly string[] = [
  'authorization',
  'x-request-id',
  'x-correlation-id',
  'accept-language',
];

function buildProxyHeaders(
  req: AuthenticatedUserRequest,
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const name of PROXY_FORWARD_HEADERS) {
    const value = req.headers[name];
    if (typeof value === 'string') {
      headers[name] = value;
    } else if (Array.isArray(value)) {
      headers[name] = value.join(',');
    }
  }
  return headers;
}

/** `forwarded`: the query parameters passed on to the backend; any others are dropped. */
const instancePath =
  (suffix = '', forwarded: readonly string[] = []) =>
  (req: AuthenticatedUserRequest) => {
    const params = new URLSearchParams();
    for (const key of forwarded) {
      const value = req.query[key];
      if (typeof value === 'string') params.append(key, value);
    }
    const query = params.toString();
    return `/instances/${encodeURIComponent(String(req.params.instanceId))}${suffix}${query ? `?${query}` : ''}`;
  };

/** `sendsOnce`: never resent, because a repeat could create a second toolset or reuse a sign-in. */
function proxy(
  method: HttpMethodValue,
  pathBuilder: PathBuilder,
  action: string,
  { sendsOnce = method !== HttpMethod.GET }: { sendsOnce?: boolean } = {},
) {
  return (appConfig: AppConfig) =>
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        const userId = req.user?.userId as unknown;
        if (typeof userId !== 'string' || userId === '') {
          throw new UnauthorizedError('User authentication required');
        }
        // The OAuth callback's query carries the authorization code and state.
        logger.debug(`API toolsets proxy: ${action}`, {
          userId,
          path: redactSensitiveQueryParams(pathBuilder(req)),
        });
        const connectorResponse = await executeConnectorCommand(
          `${appConfig.connectorBackend}${API_TOOLSETS_BASE}${pathBuilder(req)}`,
          method,
          buildProxyHeaders(req),
          BODY_METHODS.has(method) ? req.body : undefined,
          {
            timeoutMs: API_TOOLSETS_PROXY_TIMEOUT_MS,
            retries: sendsOnce ? 1 : 3,
          },
        );
        handleConnectorResponse(
          connectorResponse,
          res,
          action,
          `${action} failed`,
        );
      } catch (error: unknown) {
        logger.error(`Error in API toolsets proxy: ${action}`, {
          error: error instanceof Error ? error.message : String(error),
          userId: req.user?.userId as unknown,
        });
        next(handleBackendError(error, action));
      }
    };
}

export const validateApiToolsetSpec = proxy(
  HttpMethod.POST,
  () => '/validate',
  'Validate API toolset spec',
);

export const listApiToolsets = proxy(
  HttpMethod.GET,
  () => '/instances',
  'List API toolsets',
);

export const createApiToolset = proxy(
  HttpMethod.POST,
  () => '/instances',
  'Create API toolset',
);

export const getApiToolset = proxy(
  HttpMethod.GET,
  instancePath(),
  'Get API toolset',
);

export const getApiToolsetConfig = proxy(
  HttpMethod.GET,
  instancePath('/config'),
  'Get API toolset settings',
);

export const updateApiToolset = proxy(
  HttpMethod.PUT,
  instancePath(),
  'Update API toolset',
);

export const deleteApiToolset = proxy(
  HttpMethod.DELETE,
  instancePath(),
  'Delete API toolset',
);

export const saveApiToolsetCredentials = proxy(
  HttpMethod.PUT,
  instancePath('/credentials'),
  'Save API toolset credentials',
);

export const deleteApiToolsetCredentials = proxy(
  HttpMethod.DELETE,
  instancePath('/credentials'),
  'Delete API toolset credentials',
);

export const saveApiToolsetSharedCredentials = proxy(
  HttpMethod.PUT,
  instancePath('/shared-credentials'),
  'Save API toolset shared credential',
);

export const deleteApiToolsetSharedCredentials = proxy(
  HttpMethod.DELETE,
  instancePath('/shared-credentials'),
  'Delete API toolset shared credential',
);

export const testApiToolset = proxy(
  HttpMethod.POST,
  instancePath('/test'),
  'Test API toolset',
);

// Starts a sign-in (stores its state), so it is sent once although it is a GET.
export const getApiToolsetOAuthUrl = proxy(
  HttpMethod.GET,
  instancePath('/oauth/authorize', ['desktop']),
  'Start API toolset sign-in',
  { sendsOnce: true },
);

export const getApiToolsetSharedOAuthUrl = proxy(
  HttpMethod.GET,
  instancePath('/shared-credentials/oauth/authorize', ['desktop']),
  'Start API toolset shared sign-in',
  { sendsOnce: true },
);

export const handleApiToolsetOAuthCallback = proxy(
  HttpMethod.GET,
  (req) => {
    const params = new URLSearchParams();
    for (const key of ['code', 'state']) {
      const value = req.query[key];
      if (typeof value === 'string') {
        params.append(key, value);
      }
    }
    return `/oauth/callback?${params.toString()}`;
  },
  'Finish API toolset sign-in',
  { sendsOnce: true },
);
