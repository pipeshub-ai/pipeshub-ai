import { Router } from 'express';
import { Container } from 'inversify';

import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  createChatCompletion,
  createResponse,
  listModels,
  retrieveModel,
} from '../controller/openai_agents.controller';
import {
  chatCompletionSchema,
  responseSchema,
} from '../validators/openai_agents.validators';

/**
 * OpenAI-compatible surface for agents, mounted at `/api/v1/openai`: a client
 * whose base URL is `{origin}/api/v1/openai` lists the caller's agents as
 * models and chats with them through Chat Completions or Responses.
 */
export function createOpenAIAgentsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');
  const authenticate = authMiddleware.authenticate.bind(authMiddleware);
  const read = [authenticate, requireScopes(OAuthScopeNames.AGENT_READ)];
  const execute = [authenticate, requireScopes(OAuthScopeNames.AGENT_EXECUTE)];

  router.get('/models', ...read, listModels(appConfig));
  router.get('/models/:modelId', ...read, retrieveModel(appConfig));
  router.post(
    '/chat/completions',
    ...execute,
    ValidationMiddleware.validate(chatCompletionSchema),
    createChatCompletion(appConfig),
  );
  router.post(
    '/responses',
    ...execute,
    ValidationMiddleware.validate(responseSchema),
    createResponse(appConfig),
  );
  return router;
}
