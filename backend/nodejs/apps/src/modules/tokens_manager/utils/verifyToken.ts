import jwt, { JwtPayload } from 'jsonwebtoken';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { BadRequestError } from '../../../libs/errors/http.errors';

export const verifyGoogleWorkspaceToken = (
  req: AuthenticatedUserRequest,
  id_token: string,
) => {
  const decoded = jwt.decode(id_token) as JwtPayload | null;

  if (!decoded || !decoded.email || !req.user) {
    throw new BadRequestError('Invalid ID token or missing email.');
  }

  if (decoded.email !== req.user.email) {
    throw new BadRequestError(
      'Account email is different from the consent-giving mail.',
    );
  }
};
