/**
 * Local repositories controller
 *
 * One-to-one forwards to the Python connectors service's `/api/v1/local-repos`
 * router (`backend/python/app/api/routes/local_repos.py`). The caller's token is
 * forwarded as is: the organisation and user are read from it there, never from
 * the request, and the repository is addressed only by an opaque key.
 */

import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
  handleConnectorResponse,
} from '../../tokens_manager/utils/connector.utils';

const logger = Logger.getInstance({ service: 'LocalReposController' });

const LOCAL_REPOS_BASE = '/api/v1/local-repos';
// A batch of files is up to a few MiB of source; the mirror write is local disk.
export const LOCAL_REPOS_PROXY_TIMEOUT_MS = 120_000;

type HttpMethodValue = (typeof HttpMethod)[keyof typeof HttpMethod];

const BODY_METHODS = new Set<HttpMethodValue>([
  HttpMethod.POST,
  HttpMethod.PUT,
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

/** `repoKey` is checked by `guardPathParams` before this builds a URL. */
const repoPath =
  (suffix = '') =>
  (req: AuthenticatedUserRequest) =>
    `/${encodeURIComponent(String(req.params.repoKey))}${suffix}`;

function proxy(
  method: HttpMethodValue,
  pathBuilder: (req: AuthenticatedUserRequest) => string,
  action: string,
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
        const connectorResponse = await executeConnectorCommand(
          `${appConfig.connectorBackend}${LOCAL_REPOS_BASE}${pathBuilder(req)}`,
          method,
          buildProxyHeaders(req),
          BODY_METHODS.has(method) ? req.body : undefined,
          {
            timeoutMs: LOCAL_REPOS_PROXY_TIMEOUT_MS,
            retries: method === HttpMethod.GET ? 3 : 1,
          },
        );
        // The connectors service answers a refusal with a body the client is meant to read ({ detail }; a quota refusal of an upload also says
        // which files were stored). Passed on as it is, with its status: the generic error mapping would turn a 413 into a 500 and drop it.
        const status = connectorResponse.statusCode;
        const refusal: unknown = connectorResponse.data;
        if (
          typeof status === 'number' &&
          status >= 400 &&
          status < 500 &&
          typeof refusal === 'object' &&
          refusal !== null
        ) {
          res.status(status).json(refusal);
          return;
        }
        handleConnectorResponse(
          connectorResponse,
          res,
          action,
          `${action} failed`,
        );
      } catch (error: unknown) {
        logger.error(`Error in local repositories proxy: ${action}`, {
          error: error instanceof Error ? error.message : String(error),
          userId: req.user?.userId as unknown,
        });
        next(handleBackendError(error, action));
      }
    };
}

export const listLocalRepos = proxy(
  HttpMethod.GET,
  () => '',
  'List local repositories',
);
export const registerLocalRepo = proxy(
  HttpMethod.PUT,
  repoPath(),
  'Register local repository',
);
export const getLocalRepo = proxy(
  HttpMethod.GET,
  repoPath(),
  'Get local repository',
);
export const diffLocalRepoManifest = proxy(
  HttpMethod.POST,
  repoPath('/manifest'),
  'Compare local repository manifest',
);
export const uploadLocalRepoFiles = proxy(
  HttpMethod.PUT,
  repoPath('/files'),
  'Upload local repository files',
);
export const commitLocalRepo = proxy(
  HttpMethod.POST,
  repoPath('/commit'),
  'Index local repository',
);
export const deleteLocalRepo = proxy(
  HttpMethod.DELETE,
  repoPath(),
  'Delete local repository',
);
