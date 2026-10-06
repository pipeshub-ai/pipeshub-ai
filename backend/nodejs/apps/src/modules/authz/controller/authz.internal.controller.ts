import { NextFunction, Response } from 'express';
import { ForbiddenError } from '../../../libs/errors/http.errors';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { ChatContentCheckService } from '../chat-content-check.service';
import { ChatContentCheckBody } from '../validators/authz.internal.validators';

/** Always 200 `{allow, aclVersion}`; only a token for another org is refused. */
export const checkChatContent =
  (service: ChatContentCheckService) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const body = req.body as ChatContentCheckBody;
      if (String(req.tokenPayload?.orgId ?? '') !== body.orgId) {
        throw new ForbiddenError(
          'Token organization does not match the request',
        );
      }
      res.status(200).json(await service.check(body));
    } catch (error) {
      next(error);
    }
  };
