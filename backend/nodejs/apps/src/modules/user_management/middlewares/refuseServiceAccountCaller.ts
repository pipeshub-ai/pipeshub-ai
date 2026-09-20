import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Users } from '../schema/users.schema';

export const SERVICE_ACCOUNT_CANNOT_MINT_MESSAGE =
  'A service account cannot create credentials. Ask an administrator.';

/**
 * Refuses a request whose caller is a service account.
 *
 * Service tokens are read-only and always expire. Neither of those is a
 * property of the token itself — they are decisions made when it was minted —
 * so they only hold if a service account cannot go on to mint something else.
 *
 * It could. A service token authenticates as an ordinary member, and minting a
 * personal access token is open to any member: omitting the scope list grants
 * every scope the instance allows, `agent:execute` among them, and the expiry
 * may be set to never. A read-only, 90-day service token could therefore mint
 * itself a write-capable credential that never expires. Registering an OAuth
 * app is the same shape of hole, since `agent:execute` is not admin-only and
 * members may ask for `client_credentials`.
 *
 * So the rule is that credentials are minted by people. A service account is
 * given one; it does not make more.
 *
 * The lookup is by id against the database rather than a claim on the token,
 * so it cannot be avoided by a token that omits the field, and a caller whose
 * record has gone is refused rather than waved through.
 */
export const refuseServiceAccountCaller = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId: unknown = req.user?.userId;
    if (typeof userId !== 'string' || userId === '') {
      throw new NotFoundError('Account not found');
    }

    const caller = await Users.findOne({ _id: userId, isDeleted: false })
      .select('kind')
      .lean()
      .exec();

    if (!caller) {
      throw new NotFoundError('Account not found');
    }
    if (caller.kind === 'service') {
      throw new ForbiddenError(SERVICE_ACCOUNT_CANNOT_MINT_MESSAGE);
    }
    next();
  } catch (error) {
    next(error as Error);
  }
};
