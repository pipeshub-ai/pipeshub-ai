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

    // Add to everyone group
    await getGroupRepository().addToEveryone(orgId, newUser._id, {
      liveOnly: true,
    });

    // Publish user creation event
    try {
      await this.eventService.start();
      await this.eventService.publishEvent({
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: orgId.toString(),
          userId: newUser._id,
          fullName: newUser.fullName,
          email: newUser.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      });
    } catch (eventError) {
      this.logger.error('Failed to publish user creation event', {
        error: eventError,
        userId: newUser._id,
      });
    } finally {
      await this.eventService.stop();
    }

    this.logger.info(`User auto-provisioned successfully via ${provider}`, {
      userId: newUser._id,
      email,
    });

    return newUser;
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
