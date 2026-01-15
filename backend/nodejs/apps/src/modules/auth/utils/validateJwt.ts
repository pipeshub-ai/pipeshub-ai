import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthSessionRequest } from '../middlewares/types';
import { getJwtConfig } from '../../../libs/utils/jwtConfig';
const jwt = require('jsonwebtoken');

export const isJwtTokenValid = (
  req: AuthSessionRequest,
  keyOrSecret: string,
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

  const config = getJwtConfig();
  const decodedData = jwt.verify(jwtAuthToken, keyOrSecret, {
    algorithms: [config.algorithm]
  });
  if (!decodedData) {
    throw new UnauthorizedError('Invalid Token');
  }
  decodedData.jwtAuthToken = jwtAuthToken;
  return decodedData;
};
