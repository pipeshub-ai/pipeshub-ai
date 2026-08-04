import { NextFunction, Request, Response } from 'express';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { getEmailContent } from '../utils/email-content';
import {
  MailSenderService,
  SMTP_SYNC_SEND_DEADLINE_MS,
} from '../services/mail.sender.service';

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
        throw new InternalServerError(result.data || 'Error sending mail');
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
    // Shorter deadline than the consumer's: this path has an HTTP caller
    // waiting, and it must fail here before that caller times out.
    const result = await this.sender.send(
      bodyData,
      smtpConfig,
      SMTP_SYNC_SEND_DEADLINE_MS,
    );
    return result.status === 'sent'
      ? { status: true, data: 'Email sent' }
      : { status: false, data: result.error };
  }
}
