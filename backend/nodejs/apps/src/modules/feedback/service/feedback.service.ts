import mongoose from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { MailController } from '../../mail/controller/mail.controller';
import { EmailTemplateType } from '../../mail/middlewares/types';
import { AppConfig } from '../../tokens_manager/config/config';
import { ConfigService } from '../../tokens_manager/services/cm.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { Users } from '../../user_management/schema/users.schema';
import { Org } from '../../user_management/schema/org.schema';
import {
  Feedbacks,
  FeedbackKind,
  FeedbackMimeType,
  feedbackMimeTypes,
  IFeedbackAttachment,
} from '../schema/feedback.schema';
import { createZipBuffer } from '../utils/create-zip';

const SUPPORT_EMAIL_ENV = 'FEEDBACK_SUPPORT_EMAIL';

function normalizeMimeType(mimeType: string): FeedbackMimeType | null {
  if (mimeType === 'image/jpg') {
    return 'image/jpeg';
  }
  if (feedbackMimeTypes.includes(mimeType as FeedbackMimeType)) {
    return mimeType as FeedbackMimeType;
  }
  return null;
}

function isSmtpReady(smtp: AppConfig['smtp']): smtp is NonNullable<AppConfig['smtp']> {
  return Boolean(smtp?.host && smtp.port && smtp.fromEmail);
}

function supportEmails(): string[] {
  return (process.env[SUPPORT_EMAIL_ENV] ?? '')
    .split(',')
    .map((email) => email.trim())
    .filter((email) => email.length > 0);
}

export class FeedbackService {
  constructor(
    private readonly appConfig: AppConfig,
    private readonly logger: Logger,
  ) {}

  async createFeedback(input: {
    orgId: string;
    userId: string;
    kind: FeedbackKind;
    description: string;
    files: FileBufferInfo[];
  }): Promise<{ id: string }> {
    const attachments: IFeedbackAttachment[] = [];
    for (const file of input.files) {
      const mimeType = normalizeMimeType(file.mimetype);
      if (!mimeType) {
        continue;
      }
      attachments.push({
        fileName: file.originalname,
        mimeType,
        sizeInBytes: file.size,
        data: file.buffer,
      });
    }

    const doc = await Feedbacks.create({
      kind: input.kind,
      description: input.description,
      orgId: new mongoose.Types.ObjectId(input.orgId),
      createdBy: new mongoose.Types.ObjectId(input.userId),
      attachments,
    });
    const id = String(doc._id);

    void this.sendSupportEmail({
      feedbackId: id,
      orgId: input.orgId,
      userId: input.userId,
      kind: input.kind,
      description: input.description,
      attachments,
    }).catch((error) => {
      this.logger.error('Feedback email failed after save', {
        feedbackId: id,
        error: error instanceof Error ? error.message : error,
      });
    });

    return { id };
  }

  private async sendSupportEmail(input: {
    feedbackId: string;
    orgId: string;
    userId: string;
    kind: FeedbackKind;
    description: string;
    attachments: IFeedbackAttachment[];
  }): Promise<void> {
    const to = supportEmails();
    const smtp = await this.resolveSmtp();
    if (to.length === 0 || !isSmtpReady(smtp)) {
      this.logger.info('Feedback stored; skipping email', {
        feedbackId: input.feedbackId,
        hasSupportEmail: to.length > 0,
        hasSmtp: isSmtpReady(smtp),
      });
      return;
    }

    const [user, org] = await Promise.all([
      Users.findById(input.userId).select('fullName email').lean(),
      Org.findById(input.orgId).select('registeredName shortName').lean(),
    ]);

    const orgName = org?.shortName || org?.registeredName || input.orgId;
    const userName = user?.fullName || user?.email || input.userId;
    const kindLabel = input.kind === 'issue' ? 'Issue' : 'Feedback';
    const snippet = input.description.replace(/\s+/g, ' ').slice(0, 80);

    const mailAttachments =
      input.attachments.length > 0
        ? [
            {
              filename: `feedback-${input.feedbackId}.zip`,
              content: createZipBuffer(
                input.attachments.map((file) => ({
                  name: file.fileName,
                  data: file.data,
                })),
              ),
              contentType: 'application/zip',
            },
          ]
        : [];

    const mail = new MailController(this.appConfig, this.logger);
    const result = await mail.emailSender(
      {
        orgId: input.orgId,
        emailTemplateType: EmailTemplateType.FeedbackReceived,
        sendEmailTo: to,
        subject: `[${kindLabel}] ${orgName} — ${snippet}`,
        fromEmailDomain: smtp.fromEmail,
        templateData: {
          kindLabel,
          description: input.description,
          orgName,
          userName,
          userEmail: user?.email || '',
          attachmentCount: input.attachments.length,
          submittedAt: new Date().toISOString(),
        },
        attachments: mailAttachments,
      },
      smtp,
    );

    if (!result.status) {
      this.logger.error('Feedback email was not sent', {
        feedbackId: input.feedbackId,
        detail: result.data,
      });
    }
  }

  private async resolveSmtp(): Promise<AppConfig['smtp']> {
    try {
      const latest = await ConfigService.getInstance().getSmtpConfig();
      if (isSmtpReady(latest)) {
        return latest;
      }
    } catch (error) {
      this.logger.warn('Failed to reload SMTP for feedback email', {
        error: error instanceof Error ? error.message : error,
      });
    }
    return this.appConfig.smtp;
  }
}
