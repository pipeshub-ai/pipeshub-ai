import { NextFunction, Response } from 'express';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';

import { ContainerRequest } from '../../auth/middlewares/types';
import { AppConfig } from '../../tokens_manager/config/config';

export const smtpConfigChecker = (
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

    if (
      !config.smtp ||
      !config.smtp.port ||
      !config.smtp.host ||
      !config.smtp.fromEmail
    ) {
      throw new BadRequestError('Smtp not configured properly');
    }
    next();
  } catch (error) {
    next(error);
  }
};
