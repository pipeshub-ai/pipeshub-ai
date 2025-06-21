import { NextFunction, Response } from 'express';

import { isJwtTokenValid } from '../utils/validateJwt';
import { JwtPayload } from 'jsonwebtoken';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { ContainerRequest } from '../../auth/middlewares/types';
import { AppConfig } from '../../tokens_manager/config/config';

export const jwtValidator = (
  req: ContainerRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Mail container not found');
    }
    const config = container.get<AppConfig>('AppConfig');
    const decodedData = isJwtTokenValid(req, config.jwtSecret) as JwtPayload;
    if (!decodedData) {
      throw new BadRequestError('Invalid Token');
    }
    next();
  } catch (error) {
    next(error);
  }
};
