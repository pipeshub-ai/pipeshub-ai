import { NextFunction, Request, Response } from 'express';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { EmailTemplateType, MailBody, SmtpConfig } from '../middlewares/types';
import { MailModel } from '../schema/mailInfo.schema';
import {
  accountCreation,
  appUserInvite,
  loginWithOTPRequest,
  resetPassword,
  suspiciousLoginAttempt,
} from '../utils/emailTemplates';
import nodemailer from 'nodemailer';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
@injectable()
export class MailController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
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
    let emailContent;
    this.logger.info('emailTemplateType', emailTemplateType);
    switch (emailTemplateType) {
      case EmailTemplateType.LoginWithOtp:
        emailContent = loginWithOTPRequest(templateData);
        return emailContent;

      case EmailTemplateType.AccountCreation:
        emailContent = accountCreation(templateData);
        return emailContent;

      case EmailTemplateType.SuspiciousLoginAttempt:
        emailContent = suspiciousLoginAttempt(templateData);
        return emailContent;

      case EmailTemplateType.ResetPassword:
        emailContent = resetPassword(templateData);
        return emailContent;

      case EmailTemplateType.AppuserInvite:
        emailContent = appUserInvite(templateData);
        return emailContent;

      default:
        throw 'Unknown Template';
    }
  }

  async emailSender(bodyData: MailBody, smtpConfig: SmtpConfig) {
    try {
      const fromEmailDomain = smtpConfig.fromEmail;
      const attachments = bodyData.attachments || [];
      const emailContent = this.getEmailContent(
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
        from: fromEmailDomain,
        to: bodyData.sendEmailTo,
        cc: bodyData.sendCcTo,
        subject: bodyData.subject,
        html: emailContent,
        attachments: attachments,
      });

      const mailEntry = new MailModel({
        subject: bodyData.subject,
        from: bodyData.fromEmailDomain,
        to: bodyData.sendEmailTo,
        cc: bodyData.sendCcTo ? bodyData.sendCcTo : [],
        emailTemplateType: bodyData.emailTemplateType,
      });

      await mailEntry.save();

      return { status: true, data: 'Email sent' };
    } catch (error) {
      this.logger.error('Mail send error', { error });
      return {
        status: false,
        error: 'Failed to send email',
      }; // Return a response instead of throwing
    }
  }
}
