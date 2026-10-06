import { NextFunction, RequestHandler, Response } from 'express';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../../../libs/middlewares/types';
import { KeyValueStoreService } from '../../../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../../../tokens_manager/config/config';
import { hydrateScopedRequestAsUser } from '../../../utils/scoped-request';

/** Puts the scoped-token caller on `req.user` before the guard runs; the handler-side hydration then returns early. */
export function hydrateScopedUser(
  appConfig: AppConfig,
  keyValueStoreService?: KeyValueStoreService,
): RequestHandler {
  return async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
      next();
    } catch (error) {
      next(error);
    }
  };
}
