import jwt from 'jsonwebtoken';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { ContainerRequest } from '../../auth/middlewares/types';
import { getJwtConfig } from '../../../libs/utils/jwtConfig';

export const isJwtTokenValid = (req: ContainerRequest, keyOrSecret: string) => {
  const brearerHeader = req.header('authorization');
  if (typeof brearerHeader === 'undefined') {
    throw new NotFoundError('Authorization header not found');
  }

  const bearer = brearerHeader.split(' ');
  const jwtAuthToken = bearer[1];

  if (typeof jwtAuthToken === 'undefined') {
    throw new NotFoundError('Token not found in Authorization header');
  }
  
  const config = getJwtConfig();
  const decodedData = jwt.verify(jwtAuthToken, keyOrSecret, {
    algorithms: [config.algorithm]
  });

  return decodedData;
};
