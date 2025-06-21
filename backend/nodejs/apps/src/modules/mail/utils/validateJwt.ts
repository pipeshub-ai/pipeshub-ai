import jwt from 'jsonwebtoken';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { ContainerRequest } from '../../auth/middlewares/types';

export const isJwtTokenValid = (req: ContainerRequest, privateKey: string) => {
  const brearerHeader = req.header('authorization');
  if (typeof brearerHeader === 'undefined') {
    throw new NotFoundError('Authorization header not found');
  }

  const bearer = brearerHeader.split(' ');
  const jwtAuthToken = bearer[1];

  if (typeof jwtAuthToken === 'undefined') {
    throw new NotFoundError('Token not found in Authorization header');
  }
  const decodedData = jwt.verify(jwtAuthToken, privateKey);

  return decodedData;
};
