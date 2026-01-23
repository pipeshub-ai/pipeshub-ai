import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { Users } from '../../user_management/schema/users.schema';
import { UserGroups } from '../../user_management/schema/userGroup.schema';
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

export type JitProvider = 'google' | 'microsoft' | 'azureAd' | 'oauth' | 'saml';

/**
 * Service responsible for Just-In-Time (JIT) user provisioning.
 * This service is shared across auth and user management modules to avoid circular dependencies.
 */
@injectable()
export class JitProvisioningService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EntitiesEventProducer') private eventService: EntitiesEventProducer,
  ) {}

  /**
   * Provision a new user via JIT from an OAuth/SAML provider.
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
    const deletedUser = await Users.findOne({
      email,
      orgId,
      isDeleted: true,
    });
    if (deletedUser) {
      throw new BadRequestError(
        'User account deleted by admin. Please contact your admin to restore your account.',
      );
    }

    const newUser = new Users({
      email,
      ...userDetails,
      orgId,
      hasLoggedIn: false,
      isDeleted: false,
    });

    await newUser.save();

    // Add to everyone group
    await UserGroups.updateOne(
      { orgId, type: 'everyone', isDeleted: false },
      { $addToSet: { users: newUser._id } },
    );

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

    return newUser.toObject();
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
   * Extract user details from Microsoft/Azure AD decoded token
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

  /**
   * Extract user details from OAuth userInfo response
   */
  extractOAuthUserDetails(userInfo: any, email: string): JitUserDetails {
    // Common OAuth/OIDC claims
    const firstName =
      userInfo?.given_name ||
      userInfo?.first_name ||
      userInfo?.firstName;
    const lastName =
      userInfo?.family_name ||
      userInfo?.last_name ||
      userInfo?.lastName;
    const displayName =
      userInfo?.name ||
      userInfo?.displayName ||
      userInfo?.preferred_username;

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
   * Extract user details from SAML assertion with fallbacks for different IdP formats
   */
  extractSamlUserDetails(samlUser: any, email: string): JitUserDetails {
    const SAML_CLAIM_GIVENNAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname';
    const SAML_OID_GIVENNAME = 'urn:oid:2.5.4.42';
    const SAML_CLAIM_SURNAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname';
    const SAML_OID_SURNAME = 'urn:oid:2.5.4.4';
    const SAML_CLAIM_NAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name';
    const SAML_OID_DISPLAYNAME = 'urn:oid:2.16.840.1.113730.3.1.241';

    // Try multiple SAML attribute names for first name
    const firstName =
      samlUser.firstName ||
      samlUser.givenName ||
      samlUser[SAML_CLAIM_GIVENNAME] ||
      samlUser[SAML_OID_GIVENNAME];

    // Try multiple SAML attribute names for last name
    const lastName =
      samlUser.lastName ||
      samlUser.surname ||
      samlUser.sn ||
      samlUser[SAML_CLAIM_SURNAME] ||
      samlUser[SAML_OID_SURNAME];

    // Try multiple SAML attribute names for display name
    const displayName =
      samlUser.displayName ||
      samlUser.name ||
      samlUser.fullName ||
      samlUser[SAML_CLAIM_NAME] ||
      samlUser[SAML_OID_DISPLAYNAME];

    // Construct full name with fallbacks
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
