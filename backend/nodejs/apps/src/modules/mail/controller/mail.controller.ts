import { NextFunction, Request, Response } from 'express';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { getEmailContent } from '../utils/email-content';
import { MailSenderService } from '../services/mail.sender.service';

@injectable()
export class MailController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
    @inject(MailSenderService) private readonly sender: MailSenderService,
  ) {}

  async sendMail(
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    let result;
    try {
      const body = req.body;
      if (!this.config.smtp) {
        throw new NotFoundError('Smtp Configuration not set');
      }
      result = await this.emailSender(body, this.config.smtp);
      if (!result.status) {
        // `data` is the mail library's own complaint, packed in by emailSender.
        this.logger.error('Sending the email failed', { reason: result.data });
        throw markClientSafe(
          new InternalServerError(serverFailureMessage('send that email')),
        );
      }
      res.status(200).json({
        data: result,
      });
    } catch (error) {
      next(error);
    }
  }

  getEmailContent(
    emailTemplateType: string,
    templateData: Record<string, any>,
  ) {
    this.logger.debug('emailTemplateType', emailTemplateType);
    return getEmailContent(emailTemplateType, templateData);
  }

  /**
   * Retained so the direct HTTP route keeps its existing contract; delivery
   * itself lives in MailSenderService, shared with the broker consumer.
   */
  async emailSender(bodyData: MailBody, smtpConfig: SmtpConfig) {
    const result = await this.sender.send(bodyData, smtpConfig);
    return result.status === 'sent'
      ? { status: true, data: 'Email sent' }
      : { status: false, data: result.error };
  }
}
