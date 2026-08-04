import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
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
}

interface SendMailResponse {
  statusCode: number;
  data: any;
}

@injectable()
export class MailService {
  constructor(
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

      // 200 now means "accepted for delivery", not "sent" — MailConsumer
      // notifies admins if delivery ultimately fails.
      await this.mailProducer.publishEvent({
        eventType: MailEventType.SendMailEvent,
        timestamp: Date.now(),
        payload: { mail: data, orgId: initiator.orgId },
      });
      return { statusCode: 200, data: { message: 'Email queued for delivery' } };
    } catch (error: any) {
      // Always 500: callers only branch on `statusCode !== 200`.
      this.logger.error('Error queueing mail', { error: error?.message });
      return {
        statusCode: 500,
        data: error?.message || 'Error sending mail.',
      };
    }
  }
}
