import axios from 'axios';
import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
interface SendMailParams {
  emailTemplateType: string;
  initiator: { orgId?: string; jwtAuthToken: string };
  usersMails: string[];
  subject: string;
  templateData?: Record<string, any>;
  fromEmailDomain?: string;
  attachedDocuments?: any[];
  ccEmails?: string[];
}

interface SendMailResponse {
  statusCode: number;
  data: any;
}

@injectable()
export class MailService {
  constructor(
    @inject('AppConfig') private userConfig: AppConfig,
    @inject('Logger') private logger: Logger,
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
  }: SendMailParams): Promise<SendMailResponse> {
    try {
      this.logger.debug('sending mail ...');

      if (!usersMails?.length) throw new BadRequestError('usersMails is empty');
      if (!subject) throw new BadRequestError('subject is empty');
      if (!emailTemplateType)
        throw new BadRequestError('emailTemplateType is empty');

      const data: Record<string, any> = {
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

      const config = {
        method: 'post' as const,
        url: `${this.userConfig.communicationBackend}/api/v1/mail/emails/sendEmail`,
        headers: {
          Authorization: `Bearer ${initiator.jwtAuthToken}`,
          'Content-Type': 'application/json',
        },
        data,
      };
      const response = await axios(config);
      return { statusCode: 200, data: response.data };
    } catch (error: any) {
      this.logger.error('Error sending mail', { error: error?.response?.data });
      return {statusCode: 500, data: "Error sending mail. Check your SMTP configuration."}
    }
  }
}
