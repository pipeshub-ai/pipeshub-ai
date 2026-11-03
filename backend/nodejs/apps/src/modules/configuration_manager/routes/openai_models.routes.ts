import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import {
  listOpenAIModels,
  retrieveOpenAIModel,
} from '../controller/openai_models.controller';
import { AiModelsConfigRepository } from '../services/aiModelsConfig.repository';

/**
 * OpenAI-compatible catalog. Mounted at ``/v1`` and ``/api/v1`` so a client
 * whose base URL is either ``{origin}/v1`` or ``{origin}/api/v1`` can list
 * the models this organization has configured.
 */
export function createOpenAIModelsRouter(container: Container): Router {
  const router = Router();
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const read = () => new AiModelsConfigRepository(keyValueStoreService).read();
  const guards = [
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
  ];

  router.get('/models', ...guards, listOpenAIModels(read));
  router.get('/models/*', ...guards, retrieveOpenAIModel(read));
  return router;
}
