import { inject, injectable } from 'inversify';
import nodemailer from 'nodemailer';
import type { Transporter } from 'nodemailer';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { MailBody, SmtpConfig } from '../middlewares/types';
import { MailModel } from '../schema/mailInfo.schema';
import { getEmailContent } from '../utils/email-content';
import {
  classifyMailError,
  MailSendResult,
  SMTP_CONNECTION_ERROR_CODES,
  SMTP_DEADLINE_ERROR_CODE,
} from '../types/mail-event.types';

const SMTP_DNS_TIMEOUT_MS = 30_000;
const SMTP_CONNECTION_TIMEOUT_MS = 30_000;
const SMTP_GREETING_TIMEOUT_MS = 30_000;
const SMTP_SOCKET_TIMEOUT_MS = 60_000;
export const SMTP_SEND_DEADLINE_MS = 120_000;
export const SMTP_SYNC_SEND_DEADLINE_MS = 25_000;
const SMTP_POOL_MAX_CONNECTIONS = 5;
const SMTP_POOL_MAX_MESSAGES = 100;
export const SMTP_CIRCUIT_FAILURE_THRESHOLD = 5;
export const SMTP_CIRCUIT_COOLDOWN_MS = 60_000;

interface PooledTransport {
  key: string;
  transporter: Transporter;
  inFlight: number;
}

function smtpConfigKey(smtpConfig: SmtpConfig): string {
  return JSON.stringify([
    smtpConfig.host,
    smtpConfig.port,
    smtpConfig.username,
    smtpConfig.password,
  ]);
}

function errorCode(error: unknown): string | undefined {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === 'string' ? code : undefined;
}

/** SMTP delivery, shared by the HTTP route and the broker consumer. */
@injectable()
export class MailSenderService {
  private pooled?: PooledTransport;
  // Replaced pools keep running until their in-flight sends settle: closing
  // one mid-DATA looks like a transient failure and the retry duplicates it.
  private readonly draining = new Set<PooledTransport>();
  private circuit = { key: '', failures: 0, openUntil: 0 };

  constructor(
    // Resolved per call: an SMTP update rebinds AppConfig, and this outlives it.
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
    for (const pool of [this.pooled, ...this.draining]) {
      if (pool) this.closeQuietly(pool.transporter);
    }
    this.draining.clear();
    this.pooled = undefined;
  }

  private getTransporter(smtpConfig: SmtpConfig): PooledTransport {
    const key = smtpConfigKey(smtpConfig);
    if (this.pooled?.key === key) {
      return this.pooled;
    }

    if (this.pooled) {
      this.retire(this.pooled);
    }
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
      // Attachments come from the request body; never let one read a local file or fetch a URL.
      disableFileAccess: true,
      disableUrlAccess: true,
      ...(!smtpConfig.username
        ? {}
        : smtpConfig.password
          ? { auth: { user: smtpConfig.username, pass: smtpConfig.password } }
          : { auth: { user: smtpConfig.username } }),
    });
    this.pooled = { key, transporter, inFlight: 0 };
    return this.pooled;
  }

  private retire(pool: PooledTransport): void {
    if (pool.inFlight === 0) {
      this.closeQuietly(pool.transporter);
    } else {
      this.draining.add(pool);
    }
  }

  private closeQuietly(transporter: Transporter): void {
    try {
      transporter.close();
    } catch {
    }
  }

  private onSendSettled(pool: PooledTransport): void {
    pool.inFlight -= 1;
    if (pool.inFlight === 0 && this.draining.delete(pool)) {
      this.closeQuietly(pool.transporter);
    }
  }

  private circuitOpenUntil(key: string): number | null {
    if (this.circuit.key !== key) {
      this.circuit = { key, failures: 0, openUntil: 0 };
    }
    return Date.now() < this.circuit.openUntil ? this.circuit.openUntil : null;
  }

  private recordConnectionFailure(key: string): void {
    if (this.circuit.key !== key) return;
    this.circuit.failures += 1;
    // Past the threshold every failure re-opens, so a failed probe after the
    // cooldown trips straight back instead of needing N more failures.
    if (this.circuit.failures >= SMTP_CIRCUIT_FAILURE_THRESHOLD) {
      this.circuit.openUntil = Date.now() + SMTP_CIRCUIT_COOLDOWN_MS;
      this.logger.error('SMTP server unreachable; pausing sends', {
        consecutiveFailures: this.circuit.failures,
        cooldownMs: SMTP_CIRCUIT_COOLDOWN_MS,
      });
    }
  }

  private recordSuccess(key: string): void {
    if (this.circuit.key === key) {
      this.circuit.failures = 0;
      this.circuit.openUntil = 0;
    }
  }

  private async sendWithDeadline(
    pool: PooledTransport,
    message: Parameters<Transporter['sendMail']>[0],
    deadlineMs: number,
  ): Promise<void> {
    let timer: NodeJS.Timeout | undefined;
    // Keep a handle: Promise.race does not cancel sendMail, and a consumer
    // retry while it is still running can deliver the same email twice.
    pool.inFlight += 1;
    const sendPromise = pool.transporter
      .sendMail(message)
      .finally(() => this.onSendSettled(pool));
    const deadline = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        reject(
          Object.assign(
            new Error(`SMTP send exceeded ${deadlineMs}ms deadline`),
            { code: SMTP_DEADLINE_ERROR_CODE },
          ),
        );
      }, deadlineMs);
    });

    try {
      await Promise.race([sendPromise, deadline]);
    } catch (error) {
      if (errorCode(error) === SMTP_DEADLINE_ERROR_CODE) {
        void sendPromise.then(
          () =>
            this.logger.warn(
              'SMTP send completed after deadline; skipped retry to avoid duplicate',
            ),
          (err) =>
            this.logger.warn('SMTP send failed after deadline', {
              error: err instanceof Error ? err.message : String(err),
            }),
        );
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Not awaited: the email is already delivered, so the audit write must not
   * spend the send budget or turn a delivery into a failed, retried send.
   */
  private recordAudit(bodyData: MailBody, smtpConfig: SmtpConfig): void {
    const logFailure = (persistError: unknown) =>
      this.logger.error('Mail sent but audit record failed to save', {
        error:
          persistError instanceof Error
            ? persistError.message
            : String(persistError),
      });
    try {
      new MailModel({
        orgId: bodyData.orgId,
        subject: bodyData.subject,
        from: smtpConfig.fromEmail,
        to: bodyData.sendEmailTo,
        cc: bodyData.sendCcTo ? bodyData.sendCcTo : [],
        emailTemplateType: bodyData.emailTemplateType,
      })
        .save()
        .catch(logFailure);
    } catch (persistError) {
      logFailure(persistError);
    }
  }

  /** Returns the outcome instead of throwing, so the caller decides on retry. */
  async send(
    bodyData: MailBody,
    smtpConfig: SmtpConfig,
    deadlineMs: number = SMTP_SEND_DEADLINE_MS,
  ): Promise<MailSendResult> {
    let emailContent: string;
    try {
      emailContent = getEmailContent(
        bodyData.emailTemplateType!,
        bodyData.templateData!,
      );
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : typeof error === 'string'
            ? error
            : 'Failed to render email template';
      this.logger.error('Mail template error', { error: message });
      return { status: 'permanent', error: message };
    }

    const key = smtpConfigKey(smtpConfig);
    const openUntil = this.circuitOpenUntil(key);
    if (openUntil !== null) {
      return {
        status: 'unavailable',
        error: `SMTP server unreachable; sending paused until ${new Date(openUntil).toISOString()}`,
      };
    }

    try {
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
      this.recordSuccess(key);
      this.recordAudit(bodyData, smtpConfig);
      return { status: 'sent' };
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : typeof error === 'string'
            ? error
            : 'Failed to send email';
      const code = errorCode(error);
      if (code && SMTP_CONNECTION_ERROR_CODES.has(code)) {
        this.recordConnectionFailure(key);
      } else if (
        code === 'EAUTH' ||
        typeof (error as { responseCode?: unknown } | null)?.responseCode ===
          'number'
      ) {
        // The server answered, so it is reachable: the streak is not consecutive.
        this.recordSuccess(key);
      }
      if (code === SMTP_DEADLINE_ERROR_CODE) {
        this.logger.error('Mail send deadline exceeded; not retrying', {
          error: message,
        });
        return { status: 'indeterminate', error: message };
      }
      // An unknown template is a caller bug; replaying it fails identically.
      const kind =
        typeof error === 'string' ? 'permanent' : classifyMailError(error);
      this.logger.error('Mail send error', { error: message, kind });
      return { status: kind, error: message };
    }
  }
}
