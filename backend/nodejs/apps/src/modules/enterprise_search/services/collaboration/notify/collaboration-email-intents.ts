import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { INotificationPreferencesRepository } from '../../../../notification/repository/notification-preferences.repository';
import {
  ChatEmailTemplate,
  EmailIntent,
} from '../../../../notification/service/notification-email.dispatcher';
import { IOrgDirectory } from '../../../../user_management/services/org-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { AccessLevel } from '../domain/types';
import { decideEmailIntent, resolveEmailIntentGates } from './email-intent';

export interface EmailIntentRecipient {
  userId: string;
  /** Omitted for a mention, which carries no access. */
  accessLevel?: AccessLevel;
}

export interface ICollaborationEmailIntents {
  /** Recipients with no intent (gate closed, preference off) are absent from the map. */
  forDirectUsers(args: {
    orgId: string;
    actorUserId: string;
    template: ChatEmailTemplate;
    recipients: readonly EmailIntentRecipient[];
  }): Promise<ReadonlyMap<string, EmailIntent>>;
}

export interface CollaborationEmailIntentsDeps {
  isSmtpConfigured: () => Promise<boolean>;
  flags: IFeatureFlags;
  preferences: INotificationPreferencesRepository;
  users: IUserDirectory;
  orgs: IOrgDirectory;
}

const UNKNOWN_ACTOR = 'Someone';

export class CollaborationEmailIntents implements ICollaborationEmailIntents {
  constructor(private readonly deps: CollaborationEmailIntentsDeps) {}

  async forDirectUsers(args: {
    orgId: string;
    actorUserId: string;
    template: ChatEmailTemplate;
    recipients: readonly EmailIntentRecipient[];
  }): Promise<ReadonlyMap<string, EmailIntent>> {
    const intents = new Map<string, EmailIntent>();
    if (args.recipients.length === 0) {
      return intents;
    }
    const gates = await resolveEmailIntentGates({
      isSmtpConfigured: this.deps.isSmtpConfigured,
      flags: this.deps.flags,
    });
    if (!gates.smtpConfigured || !gates.shareEmailsEnabled) {
      return intents;
    }
    const [names, orgName, preferences] = await Promise.all([
      this.deps.users.displayNames(args.orgId, [args.actorUserId], {
        emailFallback: false,
      }),
      this.deps.orgs.displayName(args.orgId),
      Promise.all(
        args.recipients.map((r) =>
          this.deps.preferences.get(args.orgId, r.userId),
        ),
      ),
    ]);
    const actorName = names.get(args.actorUserId);
    args.recipients.forEach((recipient, index) => {
      const intent = decideEmailIntent({
        template: args.template,
        isDirectRecipient: true,
        gates,
        preferences: preferences[index] ?? null,
        actorName:
          actorName === undefined || actorName === ''
            ? UNKNOWN_ACTOR
            : actorName,
        orgName,
        ...(recipient.accessLevel !== undefined && {
          accessLevel: recipient.accessLevel,
        }),
      });
      if (intent) {
        intents.set(recipient.userId, intent);
      }
    });
    return intents;
  }
}
