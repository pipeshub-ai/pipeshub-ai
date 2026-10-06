import { Logger } from '../../../../../libs/services/logger.service';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { EmailTemplateType } from '../../../../mail/middlewares/types';
import {
  ChatEmailTemplate,
  EmailIntent,
} from '../../../../notification/service/notification-email.dispatcher';

const logger = Logger.getInstance({ service: 'CollaborationEmailIntent' });

/** Deployment-level gates; evaluate once per mutation, not per recipient. */
export interface EmailIntentGates {
  smtpConfigured: boolean;
  shareEmailsEnabled: boolean;
}

/** `null` means the recipient has no preferences row, so defaults apply (all on). */
export interface RecipientEmailPreferences {
  email: {
    chatShared: boolean;
    ownershipTransferred: boolean;
    /** Absent (a row written before the preference existed) means off. */
    chatMentioned?: boolean;
  };
}

export interface EmailIntentInput {
  template: ChatEmailTemplate;
  /** True for a direct user principal or the transfer target; false for team and org-wide recipients. */
  isDirectRecipient: boolean;
  gates: EmailIntentGates;
  preferences: RecipientEmailPreferences | null;
  actorName: string;
  orgName: string;
  accessLevel?: 'read' | 'write';
}

/** Returns the broker-only `emailIntent` for a notification, or `undefined` when no email may be sent. */
export function decideEmailIntent(
  input: EmailIntentInput,
): EmailIntent | undefined {
  if (
    !input.isDirectRecipient ||
    !input.gates.smtpConfigured ||
    !input.gates.shareEmailsEnabled
  ) {
    return undefined;
  }
  const email = input.preferences?.email;
  const preferenceOn =
    input.template === EmailTemplateType.ChatShared
      ? (email?.chatShared ?? true)
      : input.template === EmailTemplateType.ChatMentioned
        ? email?.chatMentioned === true
        : (email?.ownershipTransferred ?? true);
  if (!preferenceOn) {
    return undefined;
  }
  return {
    template: input.template,
    actorName: input.actorName,
    orgName: input.orgName,
    ...(input.accessLevel !== undefined && { accessLevel: input.accessLevel }),
  };
}

/** A failed lookup closes the gate: a missed email is better than failing the share. */
export async function resolveEmailIntentGates(deps: {
  isSmtpConfigured: () => Promise<boolean>;
  flags: IFeatureFlags;
}): Promise<EmailIntentGates> {
  const [smtpConfigured, shareEmailsEnabled] = await Promise.all([
    deps.isSmtpConfigured().catch((error: unknown) => {
      logger.warn(
        'SMTP status lookup failed; chat emails off for this change',
        {
          error: error instanceof Error ? error.message : String(error),
        },
      );
      return false;
    }),
    deps.flags.isEnabled(COLLAB_FLAG_KEYS.chatShareEmails),
  ]);
  return { smtpConfigured, shareEmailsEnabled };
}
