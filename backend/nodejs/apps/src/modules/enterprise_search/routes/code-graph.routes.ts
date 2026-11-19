import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { handleBackendError } from '../../../libs/errors/backend-error';
import { AppConfig } from '../../tokens_manager/config/config';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';

const TIMEOUT_MS = 15_000;
// The code tools read source and walk edges: the MCP tools allow them a minute.
const CODE_TOOL_TIMEOUT_MS = 60_000;

/**
 * POST `body` to the query service with the caller's own token and return what
 * it answers. A 4xx keeps the service's words; a 5xx or an unreachable service
 * becomes the standard unavailable error, never an empty result.
 */
export async function relayPost(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  path: string,
  operation: string,
  timeoutMs: number,
): Promise<unknown> {
  try {
    const response = await new AIServiceCommand<Record<string, unknown>>({
      uri: `${appConfig.aiBackend}${path}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: req.headers.authorization ?? '',
        'Content-Type': 'application/json',
      },
      body: req.body as unknown,
      timeoutMs,
      maxAttempts: 1,
    }).execute();
    if (response.statusCode >= 400) {
      throw handleBackendError(
        {
          response: {
            status: response.statusCode,
            // The code tools answer `{ error }`; the mapper reads `detail`.
            data: { detail: response.data?.error, ...response.data },
          },
        },
        operation,
      );
    }
    return response.data;
  } catch (error) {
    throw handleBackendError(error, operation);
  }
}

// The repo is named the way the MCP tools name it; the query service resolves it.
const repoFields = {
  repo: z.string().min(1).max(300).optional(),
  include_tests: z.boolean().optional(),
};
const codeToolSchemas = {
  '/search': z.object({
    body: z.object({
      ...repoFields,
      pattern: z.string().min(1).max(200),
      mode: z.enum(['identifier', 'text', 'regex']).optional(),
      path: z.string().max(1000).optional(),
      limit: z.number().int().min(1).max(200).optional(),
    }),
  }),
  '/semantic-search': z.object({
    body: z.object({
      ...repoFields,
      query: z.string().min(1).max(500),
      limit: z.number().int().min(1).max(50).optional(),
    }),
  }),
  '/read': z.object({
    body: z.object({
      ...repoFields,
      file_path: z.string().max(1000).optional(),
      record_id: z.string().max(200).optional(),
      qualified_name: z.string().max(1000).optional(),
      lines: z.string().max(40).optional(),
      max_lines: z.number().int().min(1).max(2000).optional(),
    }),
  }),
  '/outline': z.object({
    body: z.object({
      ...repoFields,
      file_path: z.string().max(1000).optional(),
      record_id: z.string().max(200).optional(),
    }),
  }),
};

/**
 * The dashboard's read of the code graph: which repos the caller can read,
 * each one's graph status (built, pending, failed), and the search and read
 * tools. The query service filters by the caller's own token, the same way the
 * MCP code tools do.
 */
export function createCodeGraphRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  router.get(
    '/repos',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CODE_READ),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const response = await new AIServiceCommand<Record<string, unknown>>({
          uri: `${appConfig.aiBackend}/api/v1/code-graph/repos`,
          method: HttpMethod.GET,
          headers: { Authorization: req.headers.authorization ?? '' },
          timeoutMs: TIMEOUT_MS,
          maxAttempts: 1,
        }).execute();
        if (response.statusCode >= 400) {
          throw handleBackendError(
            { response: { status: response.statusCode, data: response.data } },
            'Listing code repositories',
          );
        }
        res.status(200).json(response.data ?? { repos: [] });
      } catch (error) {
        next(handleBackendError(error, 'Listing code repositories'));
      }
    },
  );

  // Search and read, relayed as the MCP tools reach them: the query service
  // checks the scope's owner, the repo and every file's access.
  for (const [path, schema] of Object.entries(codeToolSchemas)) {
    router.post(
      path,
      authMiddleware.authenticate.bind(authMiddleware),
      requireScopes(OAuthScopeNames.CODE_READ),
      ValidationMiddleware.validate(schema),
      async (
        req: AuthenticatedUserRequest,
        res: Response,
        next: NextFunction,
      ) => {
        try {
          res
            .status(200)
            .json(
              await relayPost(
                appConfig,
                req,
                `/api/v1/code-graph${path}`,
                'Reading the code graph',
                CODE_TOOL_TIMEOUT_MS,
              ),
            );
        } catch (error) {
          next(error);
        }
      },
    );
  }

  return router;
}
