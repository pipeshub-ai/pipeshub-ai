import { Router } from 'express';
import { Container } from 'inversify';

import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { readStoredAiModelsConfig } from '../../configuration_manager/utils/util';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  createChatCompletion,
  createResponse,
  listModels,
  OpenAICompatDeps,
  retrieveModel,
} from '../controller/openai_compat.controller';
import {
  chatCompletionSchema,
  responseSchema,
} from '../validators/openai_compat.validators';

/**
 * OpenAI-compatible surface, mounted at `/api/v1/openai`: a client whose base
 * URL is `{origin}/api/v1/openai` lists the organization's configured chat
 * models and chats through Chat Completions or Responses. Each chat runs
 * PipesHub's assistant (knowledge search, tools, citations) with that model.
 */
export function createOpenAICompatRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const deps: OpenAICompatDeps = {
    appConfig: container.get<AppConfig>('AppConfig'),
    readModels: () => readStoredAiModelsConfig(keyValueStoreService),
  };
  const guards = [
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
  ];

  router.get('/models', ...guards, listModels(deps));
  router.get('/models/*', ...guards, retrieveModel(deps));
  router.post(
    '/chat/completions',
    ...guards,
    ValidationMiddleware.validate(chatCompletionSchema),
    createChatCompletion(deps),
  );
  router.post(
    '/responses',
    ...guards,
    ValidationMiddleware.validate(responseSchema),
    createResponse(deps),
  );
  return router;
}
