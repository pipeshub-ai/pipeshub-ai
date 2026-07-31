import { inject, injectable } from 'inversify';
import nodemailer from 'nodemailer';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { MailModel } from '../schema/mailInfo.schema';
import { getEmailContent } from '../utils/email-content';
import { classifyMailError, MailSendResult } from '../types/mail-event.types';

/**
 * Owns the actual SMTP delivery. Shared by the HTTP route and the broker
 * consumer so both paths send mail through exactly one implementation.
 */
@injectable()
export class MailSenderService {
  constructor(
    // Resolved per call, not captured: updating SMTP settings rebinds
    // AppConfig, and this service outlives that rebind as a singleton.
    @inject('AppConfigProvider')
    private readonly getAppConfig: () => AppConfig,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  /** Resolves the live SMTP config, or null when the org has not set one up. */
  getSmtpConfig(): SmtpConfig | null {
    return (this.getAppConfig().smtp as SmtpConfig | undefined) ?? null;
  }

  /**
   * Sends one message. Never throws for delivery problems — the outcome is
   * returned so the caller can decide between retrying and giving up.
   */
  async send(bodyData: MailBody, smtpConfig: SmtpConfig): Promise<MailSendResult> {
    try {
      const emailContent = getEmailContent(
        bodyData.emailTemplateType!,
        bodyData.templateData!,
      );
      const transporter = nodemailer.createTransport({
        host: smtpConfig.host,
        port: smtpConfig.port || 587,
        secure: false,
        ...(smtpConfig.password
          ? {
            auth: {
              user: smtpConfig.username,
              pass: smtpConfig.password, // Included only if password exists
            },
          }
          : {
            auth: {
              user: smtpConfig.username, // Include only the username
            },
          }),
      });

      await transporter.sendMail({
        from: smtpConfig.fromEmail,
        to: bodyData.sendEmailTo,
        cc: bodyData.sendCcTo,
        subject: bodyData.subject,
        html: emailContent,
        attachments: bodyData.attachments || [],
      });

      // The message is already delivered at this point, so a failure to write
      // the audit row must not be reported as a send failure — the caller
      // would retry and the recipient would get a duplicate email.
      try {
        const mailEntry = new MailModel({
          subject: bodyData.subject,
          from: bodyData.fromEmailDomain,
          to: bodyData.sendEmailTo,
          cc: bodyData.sendCcTo ? bodyData.sendCcTo : [],
          emailTemplateType: bodyData.emailTemplateType,
        });
        await mailEntry.save();
      } catch (persistError) {
        this.logger.error('Mail sent but audit record failed to save', {
          error:
            persistError instanceof Error
              ? persistError.message
              : String(persistError),
        });
      }

      return { status: 'sent' };
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : typeof error === 'string'
            ? error
            : 'Failed to send email';
      // An unknown template is a caller bug, not an SMTP problem — replaying it
      // would fail identically, so never classify it as transient.
      const kind =
        typeof error === 'string' ? 'permanent' : classifyMailError(error);
      this.logger.error('Mail send error', { error: message, kind });
      return { status: kind, error: message };
    }
  }
}
