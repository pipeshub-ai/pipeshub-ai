import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError, ForbiddenError } from '../../../libs/errors/http.errors';
import { forOrg } from '../../../libs/context/orgContext';
import { audit } from '../../../libs/services/audit';
import { authRefusal } from '../utils/sign-in-audit';
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
import {
  assertSlackOnlyAccountActive,
  SLACK_ACCOUNT_UNAVAILABLE_MESSAGE,
  upgradeSlackOnlyAccount,
} from '../../user_management/services/slack-only-accounts.service';

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
    @inject('EntitiesEventProducer')
    private eventService: EntitiesEventProducer,
  ) {}

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
    // Check if user was previously deleted
    if (await getUserRepository().hasRemovedAccount(orgId, email)) {
      // The sign-in line carries this refusal; provisioning audits only what it created.
      throw authRefusal(
        new BadRequestError(
          'User account deleted by admin. Please contact your admin to restore your account.',
        ),
        'account_removed',
      );
    }

    const newUser = await getUserRepository().create(orgId, {
      email,
      ...userDetails,
      hasLoggedIn: true,
      isDeleted: false,
    });

    await this.joinOrg(newUser);

    audit('auth.jit_provision', {
      actor: newUser._id,
      org: orgId,
      outcome: 'success',
      authMethod: provider,
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
    const orgId = String(user.orgId);
    const userId = String(user._id);
    // A sign-in carries no org context of its own at this point, and the
    // account's org is the one the server read it from. Every repository call
    // below runs as that org (PostgreSQL refuses a call with none).
    return forOrg(orgId, 'jit-upgrade-slack-only', async () => {
      const users = getUserRepository();
      // A removed, switched-off or blocked account is never brought back by a
      // sign-in, the same as an invite leaves it alone.
      const existing = await users.getLive(orgId, userId);
      if (!existing) throw new ForbiddenError(SLACK_ACCOUNT_UNAVAILABLE_MESSAGE);
      await assertSlackOnlyAccountActive(existing);
      if (!(await upgradeSlackOnlyAccount(userId, orgId))) return;
      const previous: Record<string, unknown> = {};
      try {
        const current = await users.getLive(orgId, userId);
        if (current && (current.fullName ?? '') === '') {
          previous.fullName = current.fullName;
          await users.updateProfile(orgId, userId, {
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
      } catch (error) {
        await this.undoSlackOnlyUpgrade(orgId, userId, previous);
        throw error;
      }
    });
  }

  /**
   * Puts a half-upgraded account back to Slack-only, so the next sign-in
   * retries the whole upgrade. It held no group before, so it leaves every
   * group. Each step is best effort on its own.
   */
  private async undoSlackOnlyUpgrade(
    orgId: string,
    userId: string,
    previous: Record<string, unknown>,
  ): Promise<void> {
    const logFailure = (step: string, error: unknown): void => {
      this.logger.error(`Undoing a failed Slack-only upgrade: ${step} failed`, {
        userId,
        error: error instanceof Error ? error.message : String(error),
      });
    };
    try {
      await getGroupRepository().removeFromAll(orgId, userId, {
        everyGroup: true,
      });
    } catch (error) {
      logFailure('leaving the groups', error);
    }
    try {
      await getUserRepository().updateProfile(orgId, userId, {
        ...previous,
        kind: 'slack',
      });
    } catch (error) {
      logFailure('restoring the record', error);
    }
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
  extractMicrosoftUserDetails(
    decodedToken: any,
    email: string,
  ): JitUserDetails {
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
