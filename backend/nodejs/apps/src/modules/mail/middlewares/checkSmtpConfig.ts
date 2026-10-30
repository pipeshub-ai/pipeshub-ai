import { NextFunction, Response } from 'express';
import {
  BadRequestError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';

import { ContainerRequest } from '../../auth/middlewares/types';
import { SmtpConfig } from './types';
import {
  MailSenderService,
  SmtpSettingsUnreadableError,
} from '../services/mail.sender.service';

export const smtpConfigChecker = async (
  req: ContainerRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Mail container not found');
    }
    // Not AppConfig: this replica may have missed the reload after email was turned on.
    let smtp: SmtpConfig | null;
    try {
      smtp = await container
        .get<MailSenderService>(MailSenderService)
        .getSmtpConfig();
    } catch (error) {
      // The settings may well exist; the caller should retry, not be told to set them up.
      throw error instanceof SmtpSettingsUnreadableError
        ? new ServiceUnavailableError(error.message)
        : error;
    }

    if (!smtp || !smtp.port || !smtp.host || !smtp.fromEmail) {
      throw new BadRequestError('Smtp not configured properly');
    }
    next();
  } catch (error) {
    next(error);
  }
};
