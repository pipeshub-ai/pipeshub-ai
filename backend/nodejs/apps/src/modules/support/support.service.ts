import { getOrgRepository } from '../../libs/repositories/org';
import { getUserRepository } from '../../libs/repositories/user';
import { Logger } from '../../libs/services/logger.service';
import {
  EmailTemplateType,
  MailBody,
  SmtpConfig,
} from '../mail/middlewares/types';
import { MailSenderService } from '../mail/services/mail.sender.service';
import { isUserOrgAdmin } from '../user_management/services/user-admin.service';
import { getSupportEmail, SUPPORT_LIMITS } from './support.config';
import {
  describeUserAgent,
  formatUtcDateTime,
  initialsOf,
  newSupportReference,
  toHeaderText,
} from './support.utils';
import {
  FeedbackKind,
  RecentErrorInput,
  SUPPORT_AREAS,
  SupportAreaKey,
  SupportAttachment,
} from './support.validators';

const HOUR_MS = 60 * 60 * 1000;

export type SupportOutcome = 'sent' | 'pending' | 'failed' | 'no_smtp';

export interface SupportRow {
  label: string;
  value: string;
}

interface SupportRowGroup {
  title: string;
  rows: SupportRow[];
}

export interface SupportContext {
  smtpConfigured: boolean;
  supportEmail: string;
  /** Empty when the workspace has no name. */
  workspaceName: string;
  /** What the form shows as sent with every request. */
  details: SupportRow[];
}

export interface SupportRequestResult {
  reference: string;
  outcome: SupportOutcome;
  supportEmail: string;
}

export interface SupportRequestInput {
  orgId: string;
  userId: string;
  area: SupportAreaKey;
  description: string;
  requestKey: string;
  recentErrors: RecentErrorInput[];
  files: SupportAttachment[];
  userAgent?: string;
}

export interface FeedbackInput {
  orgId: string;
  userId: string;
  kind: FeedbackKind;
  description: string;
  files: SupportAttachment[];
}

interface Requester {
  orgId: string;
  isAdmin: boolean;
  /** Empty when the workspace has no name. */
  orgName: string;
  userName: string;
  /** Empty when the account has no address. */
  userEmail: string;
}

interface Mail {
  smtp: SmtpConfig;
  orgId: string;
  template: EmailTemplateType;
  to: string;
  replyTo?: string;
  subject: string;
  templateData: Record<string, unknown>;
  attachments: NonNullable<MailBody['attachments']>;
  reference: string;
}

/**
 * Contact support and Send feedback. Both are emailed to the support address
 * and nothing is stored: without SMTP, or when the send fails, the form gives
 * the person a ready-made email to send from their own app instead.
 */
export class SupportService {
  // shortcut: kept per process, so a retry that reaches another replica can send twice; move to a shared store if that matters.
  private readonly recent = new Map<
    string,
    { at: number; result: Promise<SupportRequestResult> }
  >();

  constructor(
    private readonly mail: MailSenderService,
    private readonly logger: Logger,
  ) {}

  async getContext(orgId: string, userId: string): Promise<SupportContext> {
    const [smtp, requester] = await Promise.all([
      this.readSmtp(),
      loadRequester(orgId, userId),
    ]);
    return {
      smtpConfigured: smtp !== null,
      supportEmail: getSupportEmail(),
      workspaceName: requester.orgName,
      details: screenRows(requester),
    };
  }

  async isSmtpConfigured(): Promise<boolean> {
    return (await this.readSmtp()) !== null;
  }

  /** The same request key sent again within the hour answers with the first result instead of a second email. */
  createSupportRequest(
    input: SupportRequestInput,
  ): Promise<SupportRequestResult> {
    const now = Date.now();
    // Oldest first, so the sweep can stop at the first entry still inside the hour.
    for (const [key, entry] of this.recent) {
      if (entry.at > now - HOUR_MS) break;
      this.recent.delete(key);
    }
    const key = `${input.orgId}:${input.userId}:${input.requestKey}`;
    const earlier = this.recent.get(key);
    if (earlier) return earlier.result;

    const result = this.sendSupportRequest(input);
    this.recent.set(key, { at: now, result });
    result.catch(() => this.recent.delete(key));
    return result;
  }

  async createFeedback(
    input: FeedbackInput,
  ): Promise<{ outcome: SupportOutcome }> {
    const [smtp, requester] = await Promise.all([
      this.readSmtp(),
      loadRequester(input.orgId, input.userId),
    ]);
    if (!smtp) return { outcome: 'no_smtp' };

    const kindLabel = input.kind === 'issue' ? 'Feedback' : 'Feature request';
    const snippet = input.description.replace(/\s+/g, ' ').slice(0, 80);
    const attachments = toMailAttachments(input.files);
    const shared = {
      description: input.description,
      userName: requester.userName,
      attachmentCount: input.files.length,
      submittedAt: formatUtcDateTime(new Date()),
    };
    const reference = newSupportReference();

    const outcome = await this.send({
      smtp,
      orgId: input.orgId,
      template: EmailTemplateType.FeedbackReceived,
      to: getSupportEmail(),
      replyTo: optional(requester.userEmail),
      subject: toHeaderText(
        `[${kindLabel}] ${workspaceLabel(requester)} — ${snippet}`,
      ),
      templateData: {
        ...shared,
        kindLabel,
        orgName: workspaceLabel(requester),
        userEmail: requester.userEmail,
      },
      attachments,
      reference,
    });
    // Only once support has it, so nobody is told "received" for feedback that never arrived.
    if (outcome === 'sent' && requester.userEmail !== '') {
      void this.send({
        smtp,
        orgId: input.orgId,
        template: EmailTemplateType.FeedbackCopy,
        to: requester.userEmail,
        replyTo: getSupportEmail(),
        subject: `We received your ${kindLabel.toLowerCase()}`,
        templateData: { ...shared, kindLabel: kindLabel.toLowerCase() },
        attachments,
        reference,
      });
    }
    this.logger.info('Feedback handled', {
      reference,
      kind: input.kind,
      outcome,
    });
    return { outcome };
  }

  private async sendSupportRequest(
    input: SupportRequestInput,
  ): Promise<SupportRequestResult> {
    const [smtp, requester] = await Promise.all([
      this.readSmtp(),
      loadRequester(input.orgId, input.userId),
    ]);
    const reference = newSupportReference();
    if (!smtp) return this.result(reference, 'no_smtp');

    const issue = SUPPORT_AREAS[input.area];
    const browser = describeUserAgent(input.userAgent);
    const attachments = toMailAttachments(input.files);
    const issueRow = { label: 'Issue regarding', value: issue };
    const submitted = {
      reference,
      issue,
      description: input.description,
      orgName: workspaceLabel(requester),
      userName: requester.userName,
      userEmail: requester.userEmail,
      userRole: requester.isAdmin ? 'Admin' : 'Member',
      userInitials: initialsOf(requester.userName),
      submittedAt: formatUtcDateTime(new Date()),
    };

    const outcome = await this.send({
      smtp,
      orgId: input.orgId,
      template: EmailTemplateType.SupportRequest,
      to: getSupportEmail(),
      replyTo: optional(requester.userEmail),
      subject: toHeaderText(
        `[Support · ${issue}] ${workspaceLabel(requester)} · ${reference}`,
      ),
      templateData: {
        ...submitted,
        forRequester: false,
        groups: toGroups({
          Request: [
            issueRow,
            ...(input.files.length > 0
              ? [
                  {
                    label: 'Attachments',
                    value: `${String(input.files.length)} file(s) attached`,
                  },
                ]
              : []),
          ],
          // The workspace ID goes to support only, never on screen or in the person's copy.
          Account: [
            {
              label: 'Workspace',
              value:
                requester.orgName !== ''
                  ? `${requester.orgName} (${requester.orgId})`
                  : requester.orgId,
            },
          ],
          Environment: [
            { label: 'Browser', value: browser },
            ...input.recentErrors.map((recent) => ({
              label: 'Recent error',
              value: `${recent.method} ${recent.path} · ${String(recent.status)} · ${recent.requestId ?? 'no request ID'} · ${recent.at}`,
            })),
          ],
        }),
      },
      attachments,
      reference,
    });

    // A "pending" send may still fail, so the copy goes only after a confirmed send.
    if (outcome === 'sent' && requester.userEmail !== '') {
      void this.send({
        smtp,
        orgId: input.orgId,
        template: EmailTemplateType.SupportRequest,
        to: requester.userEmail,
        replyTo: getSupportEmail(),
        subject: toHeaderText(
          `We received your support request · ${reference}`,
        ),
        // Built only from what the person saw in the form.
        templateData: {
          ...submitted,
          forRequester: true,
          groups: toGroups({
            Request: [issueRow],
            Account: screenRows(requester),
            Environment: [{ label: 'Browser', value: browser }],
          }),
        },
        attachments: [],
        reference,
      });
    }
    this.logger.info('Support request handled', {
      reference,
      area: input.area,
      outcome,
    });
    return this.result(reference, outcome);
  }

  /** Never throws: a send that does not go through is a "failed" outcome, not a failed request. */
  private async send(mail: Mail): Promise<'sent' | 'pending' | 'failed'> {
    try {
      const result = await this.mail.send(
        {
          orgId: mail.orgId,
          emailTemplateType: mail.template,
          sendEmailTo: [mail.to],
          replyTo: mail.replyTo,
          subject: mail.subject,
          templateData: mail.templateData,
          attachments: mail.attachments,
        },
        mail.smtp,
        SUPPORT_LIMITS.mailDeadlineMs,
      );
      if (result.status === 'sent') return 'sent';
      // Past the deadline the mail server may still deliver it.
      if (result.status === 'indeterminate') return 'pending';
      this.logger.error('Support email was not sent', {
        reference: mail.reference,
        template: mail.template,
        error: result.error,
      });
    } catch (error) {
      this.logger.error('Support email was not sent', {
        reference: mail.reference,
        template: mail.template,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    return 'failed';
  }

  private async readSmtp(): Promise<SmtpConfig | null> {
    try {
      return await this.mail.getSmtpConfig();
    } catch (error) {
      this.logger.warn('SMTP settings could not be read', {
        error: error instanceof Error ? error.message : String(error),
      });
      return null;
    }
  }

  private result(
    reference: string,
    outcome: SupportOutcome,
  ): SupportRequestResult {
    return { reference, outcome, supportEmail: getSupportEmail() };
  }
}

async function loadRequester(
  orgId: string,
  userId: string,
): Promise<Requester> {
  const [isAdmin, org, user] = await Promise.all([
    isUserOrgAdmin(userId, orgId),
    getOrgRepository().getNames(orgId),
    getUserRepository().getLive(orgId, userId),
  ]);
  const email = user?.email ?? '';
  return {
    orgId,
    isAdmin,
    orgName: firstNonEmpty(org?.shortName, org?.registeredName) ?? '',
    userName: firstNonEmpty(user?.fullName, email) ?? userId,
    userEmail: email,
  };
}

/** What the form shows; the person's copy repeats only these. */
function screenRows(requester: Requester): SupportRow[] {
  return [
    ...(requester.orgName !== ''
      ? [{ label: 'Workspace', value: requester.orgName }]
      : []),
    {
      label: 'Signed in as',
      value:
        requester.userEmail !== ''
          ? `${requester.userName} (${requester.userEmail})`
          : requester.userName,
    },
  ];
}

function workspaceLabel(requester: Requester): string {
  return requester.orgName !== '' ? requester.orgName : requester.orgId;
}

/** An address to set as a header, or nothing when the account has none. */
function optional(value: string): string | undefined {
  return value !== '' ? value : undefined;
}

function toGroups(sections: Record<string, SupportRow[]>): SupportRowGroup[] {
  return Object.entries(sections)
    .map(([title, rows]) => ({ title, rows }))
    .filter((group) => group.rows.length > 0);
}

function toMailAttachments(
  files: SupportAttachment[],
): NonNullable<MailBody['attachments']> {
  return files.map((file) => ({
    filename: file.originalname,
    content: file.buffer,
  }));
}

// Null as well as undefined: a name can be null in a record written outside the app.
function firstNonEmpty(
  ...values: Array<string | null | undefined>
): string | undefined {
  return values.find(
    (value): value is string => typeof value === 'string' && value.length > 0,
  );
}
