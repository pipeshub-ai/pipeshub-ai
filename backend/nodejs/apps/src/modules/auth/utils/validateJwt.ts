import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthSessionRequest } from '../middlewares/types';
const jwt = require('jsonwebtoken');

export const isJwtTokenValid = (
  req: AuthSessionRequest,
  privateKey: string,
) => {
  const bearerHeader = req.header('authorization');
  if (typeof bearerHeader === 'undefined') {
    throw new BadRequestError('Authorization header not found');
  }

  const bearer = bearerHeader.split(' ');
  const jwtAuthToken = bearer[1];

  if (typeof jwtAuthToken === 'undefined') {
    throw new BadRequestError('Token not found in Authorization header');
  }

  const decodedData = jwt.verify(jwtAuthToken, privateKey);
  if (!decodedData) {
    throw new UnauthorizedError('Invalid Token');
  }
  decodedData.jwtAuthToken = jwtAuthToken;
  return decodedData;
};
