import { NextFunction, Response } from 'express';
import {
  ForbiddenError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';

// Only the owner of an account may change its email address: connector
// permissions attach to the address, so moving a colleague's account to an
// address someone else controls is a path to signing in as them.
//
// This runs before `userExists`, deliberately. If the existence check went
// first, a non-owner would get 404 for an unknown id and 403 for a known one —
// which lets them probe which accounts exist. Refusing here, before the
// lookup, gives the same 403 either way.
//
// The guard only fires when the request actually changes the email: PUT /:id
// also handles role and name edits, which an admin may make for other users.
export const emailChangeSelfOnly = (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void => {
  try {
    const email: unknown = (req.body as { email?: unknown } | undefined)?.email;
    if (email === undefined) {
      next();
      return;
    }
    const actor = req.user?.userId;
    if (!actor) {
      throw new UnauthorizedError('Unauthorized to change the email address');
    }
    // Ids are hex, where case doesn't change the id; the next check
    // (userAdminOrSelfCheck) treats them the same way.
    if (String(actor).toLowerCase() !== String(req.params.id).toLowerCase()) {
      throw new ForbiddenError(
        'Only the account owner can change its email address',
      );
    }
    next();
  } catch (error) {
    next(error);
  }
};
