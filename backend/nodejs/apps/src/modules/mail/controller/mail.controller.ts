import { NextFunction, Request, Response } from 'express';
import {
  BadGatewayError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import { withCause } from '../../../libs/errors/base.error';
import type { ErrorClass } from '../../../libs/utils/error-class';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { getEmailContent } from '../utils/email-content';
import {
  MailSenderService,
  SMTP_SYNC_SEND_DEADLINE_MS,
  SmtpSettingsUnreadableError,
} from '../services/mail.sender.service';
import { SMTP_PROBLEM_MESSAGES } from '../types/mail-event.types';

/**
 * A text-free cause for a send the server failed, so the request's one line takes the upstream class while the client still
 * gets the same 500. Our own failures (an unknown template, our sender or login refused, email turned off) get none: they stay
 * `internal`, with the stack.
 */
const UPSTREAM_CAUSE_STATUS: Partial<Record<ErrorClass, number>> = {
  bad_request: 502,
  upstream_5xx: 502,
  unavailable: 503,
  timeout: 504,
};

@injectable()
export class MailController {
  constructor(
    // Kept for the constructor's shape; SMTP comes from the sender, which reads the stored copy.
    @inject('AppConfig') _config: AppConfig,
    // Kept for the constructor's shape; the error middleware logs a failed send once.
    @inject('Logger') _logger: Logger,
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
      let smtp: SmtpConfig | null;
      try {
        smtp = await this.sender.getSmtpConfig();
      } catch (error) {
        // The settings may well exist; the caller should retry, not be told to set them up.
        throw error instanceof SmtpSettingsUnreadableError
          ? new ServiceUnavailableError(error.message)
          : error;
      }
      if (!smtp) {
        throw new NotFoundError('Smtp Configuration not set');
      }
      result = await this.emailSender(body, smtp);
      if (!result.status) {
        // Not logged here: the error middleware writes the request's one line. `data` (the server's reply) quotes the
        // recipient, so it never reaches a log.
        // A bad SMTP setup is the admin's to fix; saying so beats "something went wrong".
        const mapped = markClientSafe(
          result.problem
            ? new BadGatewayError(SMTP_PROBLEM_MESSAGES[result.problem])
            : new InternalServerError(serverFailureMessage('send that email')),
        );
        const causeStatus = result.errorClass
          ? UPSTREAM_CAUSE_STATUS[result.errorClass]
          : undefined;
        throw causeStatus === undefined
          ? mapped
          : withCause(mapped, { status: causeStatus });
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
    return getEmailContent(emailTemplateType, templateData);
  }

  /** Kept so the direct HTTP route keeps its existing contract. */
  async emailSender(bodyData: MailBody, smtpConfig: SmtpConfig) {
    // Must fail before the HTTP caller waiting on this route times out.
    const result = await this.sender.send(
      bodyData,
      smtpConfig,
      SMTP_SYNC_SEND_DEADLINE_MS,
    );
    return result.status === 'sent'
      ? { status: true, data: 'Email sent' }
      : {
          status: false,
          data: result.error,
          errorClass: result.errorClass,
          problem: 'problem' in result ? result.problem : undefined,
        };
  }
}
