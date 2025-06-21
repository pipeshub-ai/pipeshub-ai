import axios, { AxiosError } from 'axios';
import { injectable, inject } from 'inversify';
import { SendMailParams } from '../middlewares/types';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';

interface SendMailResponse {
  statusCode: number;
  data: any;
}

@injectable()
export class MailService {
  constructor(
    @inject('AppConfig') private authConfig: AppConfig,
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
      this.logger.info('Sending mail');

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
      let mailUrl = `${this.authConfig.communicationBackend}/api/v1/mail/emails/sendEmail`;
      const config = {
        method: 'post' as const,
        url: mailUrl,
        headers: {
          Authorization: `Bearer ${initiator.jwtAuthToken}`,
          'Content-Type': 'application/json',
        },
        data,
      };

      const response = await axios(config);
      return { statusCode: 200, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error sending mail',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      throw new InternalServerError(
        error instanceof Error ? error.message : 'Unexpected error occurred',
      );
    }
  }
}
