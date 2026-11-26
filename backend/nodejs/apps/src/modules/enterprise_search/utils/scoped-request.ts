import { UnauthorizedError } from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { resolveSoleOrgId as soleOrgId } from '../../slack_link/sole-org';
import {
  isSlackIdentity,
  type SlackIdentity,
  type SlackIdentityLinks,
} from '../../configuration_manager/slack-identity/slack-identity-links';
import {
  LinkedAccount,
  liveLinkedAccount,
  slackIdentityLinks,
} from '../../configuration_manager/slack-identity/slack-linked-account';

/**
 * Turns a scoped service token (`/internal/...` routes: the Slack bot) into the user request the chat handlers expect.
 */

export const STAND_IN_TOKEN_TTL_SECONDS = 60;

/** The install's only organisation (`slack_link/sole-org.ts`), the org this edition's Slack bots serve. */
export const resolveSoleOrgId = (): Promise<string> => soleOrgId();

export const hydrateScopedRequestAsUser = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  keyValueStoreService?: KeyValueStoreService,
): Promise<void> => {
  const existingUser = (req as AuthenticatedUserRequest).user;
  if (existingUser?.userId && existingUser?.orgId) {
    return;
  }

  const tokenPayload = (req as AuthenticatedServiceRequest).tokenPayload ?? {};
  if (tokenPayload.configId !== undefined) {
    await hydrateSlackCaller(req, appConfig, keyValueStoreService);
    return;
  }

  // Only the Slack bot calls these routes, and every token it mints names its configuration. A token without one is
  // not the bot's: it is refused rather than bound to a person by an email claim anyone holding the scope could write.
  throw new UnauthorizedError(
    'This Slack request does not name its Slack bot configuration',
  );
};

/** Said when a Slack person who has not linked their PipesHub account asks something only an account can answer. */
export const SLACK_LINK_REQUIRED_MESSAGE =
  'Link your Slack account to PipesHub first: I sent you a private link in Slack. ' +
  'After you sign in and approve it once, I answer as your PipesHub account.';

/**
 * A Slack bot's token (it names the bot's configuration) is answered only as the PipesHub account the Slack person
 * linked, by Slack team and user id (ADR-SLACKID-01). The token's email is never read here: it came from a Slack
 * profile, which another organisation's Slack admin can set to anyone's address.
 */
const hydrateSlackCaller = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  keyValueStoreService?: KeyValueStoreService,
): Promise<void> => {
  const orgId = await resolveSoleOrgId();
  // The bot names the org it serves; disagreement means the bot and this gateway are out of step, so the turn is refused.
  const claimedOrgId = (req as AuthenticatedServiceRequest).tokenPayload?.orgId;
  if (claimedOrgId !== undefined && String(claimedOrgId) !== orgId) {
    throw new UnauthorizedError(
      'Slack token org does not match the organisation this bot serves',
    );
  }
  await bindScopedRequestToSlackAccount(
    req,
    appConfig,
    orgId,
    keyValueStoreService ? slackIdentityLinks(keyValueStoreService) : undefined,
  );
};

/** The live PipesHub account a Slack account is linked to in `orgId` (ADR-SLACKID-01); never another org's link. */
export const linkedUser = (
  orgId: string,
  slack: SlackIdentity,
  links: SlackIdentityLinks = slackIdentityLinks(),
): Promise<LinkedAccount | null> => liveLinkedAccount(links, slack, orgId);

/**
 * Makes a Slack bot's request the request of the account the Slack person linked in `orgId`. An agent answers as the
 * person asking, so without a link there is no one to answer as, whatever kind of agent it is: refused with the link
 * message. The token's email is never read: it came from a Slack profile. The org is the caller's.
 */
export const bindScopedRequestToSlackAccount = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  orgId: string,
  links?: SlackIdentityLinks,
): Promise<void> => {
  const payload = (req as AuthenticatedServiceRequest).tokenPayload ?? {};
  const identity = {
    teamId: payload.slackTeamId,
    slackUserId: payload.slackUserId,
  };
  if (!isSlackIdentity(identity)) {
    throw new UnauthorizedError(
      'This Slack request does not name a Slack workspace and person',
    );
  }
  const account = await linkedUser(orgId, identity, links);
  if (account) {
    actAsUser(req, appConfig, account);
    return;
  }
  throw new UnauthorizedError(SLACK_LINK_REQUIRED_MESSAGE);
};

const actAsUser = (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  user: {
    orgId: string;
    userId: string;
    email: string;
    fullName?: string;
    mobile?: string;
    userSlug?: string;
  },
): void => {
  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );
  // The Python services ask Node whether every session-style token is still
  // live, and Node refuses one without a role claim. Member is what this token
  // has always been treated as there, so it stays member. With a role Node
  // honours it like a session, so it lives only as long as the request that
  // minted it needs to reach the AI service, not generateToken's default week.
  const jwtToken = authTokenService.generateToken(
    {
      userId: user.userId,
      orgId: user.orgId,
      email: user.email,
      fullName: user.fullName,
      mobile: user.mobile,
      userSlug: user.userSlug,
      role: 'member',
    },
    STAND_IN_TOKEN_TTL_SECONDS,
  );

  req.headers.authorization = `Bearer ${jwtToken}`;

  (req as AuthenticatedUserRequest).user = {
    userId: user.userId,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    mobile: user.mobile,
    userSlug: user.userSlug,
  };
};
