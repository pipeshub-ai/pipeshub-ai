import { inject, injectable } from 'inversify';
import nodemailer from 'nodemailer';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { MailModel } from '../schema/mailInfo.schema';
import { getEmailContent } from '../utils/email-content';
import { classifyMailError, MailSendResult } from '../types/mail-event.types';

const SMTP_DNS_TIMEOUT_MS = 30_000;
const SMTP_CONNECTION_TIMEOUT_MS = 30_000;
const SMTP_GREETING_TIMEOUT_MS = 30_000;
const SMTP_SOCKET_TIMEOUT_MS = 60_000;
export const SMTP_SEND_DEADLINE_MS = 120_000;
export const SMTP_SYNC_SEND_DEADLINE_MS = 25_000;
const SMTP_POOL_MAX_CONNECTIONS = 5;
const SMTP_POOL_MAX_MESSAGES = 100;

/**
 * Owns the actual SMTP delivery. Shared by the HTTP route and the broker
 * consumer so both paths send mail through exactly one implementation.
 */
@injectable()
export class MailSenderService {
  private pooled?: { key: string; transporter: nodemailer.Transporter };

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

  /** Releases pooled SMTP connections so shutdown is not held open. */
  close(): void {
    this.discardTransporter();
  }

  private getTransporter(smtpConfig: SmtpConfig): nodemailer.Transporter {
    const key = JSON.stringify([
      smtpConfig.host,
      smtpConfig.port,
      smtpConfig.username,
      smtpConfig.password,
    ]);
    if (this.pooled?.key === key) {
      return this.pooled.transporter;
    }

    this.discardTransporter();
    const transporter = nodemailer.createTransport({
      host: smtpConfig.host,
      port: smtpConfig.port || 587,
      secure: false,
      pool: true,
      maxConnections: SMTP_POOL_MAX_CONNECTIONS,
      maxMessages: SMTP_POOL_MAX_MESSAGES,
      dnsTimeout: SMTP_DNS_TIMEOUT_MS,
      connectionTimeout: SMTP_CONNECTION_TIMEOUT_MS,
      greetingTimeout: SMTP_GREETING_TIMEOUT_MS,
      socketTimeout: SMTP_SOCKET_TIMEOUT_MS,
      ...(!smtpConfig.username
        ? {}
        : smtpConfig.password
          ? { auth: { user: smtpConfig.username, pass: smtpConfig.password } }
          : { auth: { user: smtpConfig.username } }),
    });
    this.pooled = { key, transporter };
    return transporter;
  }

  private discardTransporter(): void {
    try {
      this.pooled?.transporter.close();
    } catch {
    }
    this.pooled = undefined;
  }

  private async sendWithDeadline(
    transporter: nodemailer.Transporter,
    message: Parameters<nodemailer.Transporter['sendMail']>[0],
    deadlineMs: number,
  ): Promise<void> {
    let timer: NodeJS.Timeout | undefined;
    let timedOut = false;
    const deadline = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        timedOut = true;
        reject(new Error(`SMTP send exceeded ${deadlineMs}ms deadline`));
      }, deadlineMs);
    });

    try {
      await Promise.race([transporter.sendMail(message), deadline]);
    } catch (error) {
      if (timedOut) {
        this.discardTransporter();
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Sends one message. Never throws for delivery problems — the outcome is
   * returned so the caller can decide between retrying and giving up.
   */
  async send(
    bodyData: MailBody,
    smtpConfig: SmtpConfig,
    deadlineMs: number = SMTP_SEND_DEADLINE_MS,
  ): Promise<MailSendResult> {
    try {
      const emailContent = getEmailContent(
        bodyData.emailTemplateType!,
        bodyData.templateData!,
      );
      await this.sendWithDeadline(
        this.getTransporter(smtpConfig),
        {
          from: smtpConfig.fromEmail,
          to: bodyData.sendEmailTo,
          cc: bodyData.sendCcTo,
          subject: bodyData.subject,
          html: emailContent,
          attachments: bodyData.attachments || [],
        },
        deadlineMs,
      );

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
