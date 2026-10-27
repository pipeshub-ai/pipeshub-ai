import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { handleBackendError } from '../../../libs/errors/backend-error';
import { AppConfig } from '../../tokens_manager/config/config';

const TIMEOUT_MS = 15_000;

/**
 * The dashboard's read of the code graph: which repos the caller can read and
 * each one's graph status (built, pending, failed). The query service filters
 * by the caller's own token, the same way the MCP code tools do.
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

  return router;
}
