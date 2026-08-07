import axios from 'axios';
import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { MailProducer } from '../../mail/services/mail.producer';
import { MailEventType } from '../../mail/types/mail-event.types';
import { MailBody } from '../../mail/middlewares/types';
interface SendMailParams {
  emailTemplateType: string;
  initiator: { orgId?: string; jwtAuthToken: string };
  usersMails: string[];
  subject: string;
  templateData?: Record<string, any>;
  fromEmailDomain?: string;
  attachedDocuments?: any[];
  ccEmails?: string[];
  deliverAsync?: boolean;
}

const SEND_MAIL_TIMEOUT_MS = 30_000;

interface SendMailResponse {
  statusCode: number;
  data: any;
}

@injectable()
export class MailService {
  constructor(
    @inject('AppConfig') private userConfig: AppConfig,
    @inject('Logger') private logger: Logger,
    @inject(MailProducer) private readonly mailProducer: MailProducer,
  ) {}

  async sendMail({
    emailTemplateType,
    initiator,
    usersMails,
    subject,
    templateData,
    fromEmailDomain,
    attachedDocuments,
    ccEmails,
    deliverAsync,
  }: SendMailParams): Promise<SendMailResponse> {
    try {
      this.logger.debug('sending mail ...');

      if (!usersMails?.length) throw new BadRequestError('usersMails is empty');
      if (!subject) throw new BadRequestError('subject is empty');
      if (!emailTemplateType)
        throw new BadRequestError('emailTemplateType is empty');

      const data: MailBody = {
        productName: 'PIP',
        emailTemplateType,
        isAutoEmail: false,
        fromEmailDomain: fromEmailDomain || 'noreply@contextualml.com',
        sendEmailTo: usersMails,
        subject,
        templateData,
      };

      if (attachedDocuments) {
        data.attachments = attachedDocuments;
      }

      if (ccEmails) {
        data.sendCcTo = ccEmails;
      }

      if (deliverAsync) {
        // 200 here means "accepted for delivery", not "sent" — MailConsumer
        // notifies admins if delivery ultimately fails.
        await this.mailProducer.publishEvent({
          eventType: MailEventType.SendMailEvent,
          timestamp: Date.now(),
          payload: { mail: data, orgId: initiator.orgId },
        });
        return {
          statusCode: 200,
          data: { message: 'Email queued for delivery', queued: true },
        };
      }

      const config = {
        method: 'post' as const,
        url: `${this.userConfig.communicationBackend}/api/v1/mail/emails/sendEmail`,
        headers: {
          Authorization: `Bearer ${initiator.jwtAuthToken}`,
          'Content-Type': 'application/json',
        },
        data,
        timeout: SEND_MAIL_TIMEOUT_MS,
      };
      const response = await axios(config);
      return { statusCode: 200, data: response.data };
    } catch (error: any) {
      this.logger.error('Error sending mail', {
        error: error?.response?.data ?? error?.message,
      });
      return {
        statusCode: error?.response?.status || 500,
        data:
          error?.response?.data?.error?.message ||
          error?.response?.data?.message ||
          error?.message ||
          'Error sending mail.',
      };
    }
  }
}
