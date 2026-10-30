import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
// From the schema module, not the config barrel: the barrel re-exports the auth
// routers, whose controllers import this service, so importing it here made the
// two depend on each other. The schema module returns whatever `users` model is
// already registered, which is the same object the barrel would give.
import { getUserRepository } from '../../../libs/repositories/user';
import { getGroupRepository } from '../../../libs/repositories/group';
import {
  EntitiesEventProducer,
  EventType,
  UserAddedEvent,
  SyncAction,
} from '../../user_management/services/entity_events.service';
import { upgradeSlackOnlyAccount } from '../../user_management/services/slack-only-accounts.service';

export interface JitUserDetails {
  firstName?: string;
  lastName?: string;
  fullName: string;
}

export type JitProvider = 'google' | 'microsoft';

/**
 * Service responsible for Just-In-Time (JIT) user provisioning.
 * This service is shared across auth and user management modules to avoid circular dependencies.
 */
@injectable()
export class JitProvisioningService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EntitiesEventProducer') private eventService: EntitiesEventProducer,
  ) { }

  /**
   * Provision a new user via JIT from a Google or Microsoft sign-in.
   * Creates user, adds to everyone group, and publishes creation event.
   */
  async provisionUser(
    email: string,
    userDetails: JitUserDetails,
    orgId: string,
    provider: JitProvider,
  ) {
    this.logger.info(`Auto-provisioning user from ${provider}`, { email, orgId });


    // Check if user was previously deleted
    if (await getUserRepository().hasRemovedAccount(orgId, email)) {
      throw new BadRequestError(
        'User account deleted by admin. Please contact your admin to restore your account.',
      );
    }

    const newUser = await getUserRepository().create(orgId, {
      email,
      ...userDetails,
      hasLoggedIn: true,
      isDeleted: false,
    });

    await this.joinOrg(newUser);

    this.logger.info(`User auto-provisioned successfully via ${provider}`, {
      userId: newUser._id,
      email,
    });

    return newUser;
  }

  /**
   * Upgrades a Slack-only account the person just signed in to with SSO while
   * just-in-time provisioning is on. Same record, so its Slack threads stay
   * theirs; it then gets what a provisioned user gets.
   */
  async upgradeSlackOnlyUser(
    user: { _id: unknown; orgId: unknown; email: string },
    userDetails: JitUserDetails,
    provider: JitProvider,
  ): Promise<void> {
    if (!(await upgradeSlackOnlyAccount(user._id, user.orgId))) return;
    const users = getUserRepository();
    const current = await users.getLive(String(user.orgId), String(user._id));
    if (current && (current.fullName ?? '') === '') {
      await users.updateProfile(String(user.orgId), String(user._id), {
        fullName: userDetails.fullName,
      });
    }
    this.logger.info(`Slack-only account upgraded via ${provider}`, {
      userId: user._id,
    });
    await this.joinOrg({
      _id: user._id,
      orgId: user.orgId,
      email: user.email,
      fullName: userDetails.fullName,
    });
  }

  /** Adds a new member to the everyone group and tells the graph about them. */
  private async joinOrg(user: {
    _id: unknown;
    orgId: unknown;
    email: string;
    fullName?: string;
  }): Promise<void> {
    await getGroupRepository().addToEveryone(
      String(user.orgId),
      String(user._id),
      { liveOnly: true },
    );
    try {
      await this.eventService.start();
      await this.eventService.publishEvent({
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: String(user.orgId),
          userId: String(user._id),
          fullName: user.fullName,
          email: user.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      });
    } catch (eventError) {
      this.logger.error('Failed to publish user creation event', {
        error: eventError,
        userId: user._id,
      });
    } finally {
      await this.eventService.stop();
    }
  }

  /**
   * Extract user details from Google ID token payload
   */
  extractGoogleUserDetails(payload: any, email: string): JitUserDetails {
    const firstName = payload?.given_name;
    const lastName = payload?.family_name;
    const displayName = payload?.name;

    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }

  /**
   * Extract user details from a Microsoft decoded token
   */
  extractMicrosoftUserDetails(decodedToken: any, email: string): JitUserDetails {
    const firstName = decodedToken?.given_name;
    const lastName = decodedToken?.family_name;
    const displayName = decodedToken?.name;

    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }
}
