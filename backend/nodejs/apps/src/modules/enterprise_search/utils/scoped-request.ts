import * as crypto from 'crypto';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { handleBackendError } from '../../../libs/errors/backend-error';
import {
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { getSlackBotStore } from '../../configuration_manager/controller/cm_controller';
import { AppConfig } from '../../tokens_manager/config/config';
import { getOrgRepository } from '../../../libs/repositories/org';
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
 * Turns a scoped service token (`/internal/...` routes: Slack bot, service
 * accounts) into the user request the chat handlers expect.
 */

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

export const STAND_IN_TOKEN_TTL_SECONDS = 60;

/** The install's only organisation (`slack_link/sole-org.ts`), the org this edition's Slack bots serve. */
export const resolveSoleOrgId = (): Promise<string> => soleOrgId();

/** 24-char hex suitable for Mongo ObjectId; stable per email for Slack/service-account callers without a User row. */
export const stableObjectIdHexForExternalEmail = (email: string): string =>
  crypto
    .createHash('sha256')
    .update(`slack-service-account:${email.toLowerCase().trim()}`)
    .digest('hex')
    .slice(0, 24);

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

  // Only the Slack bot calls these routes, and every token it mints names its configuration. The only other minter
  // of conversation:create is this file, for the services downstream, so a token without a configuration is not the
  // bot's: it is refused rather than bound to a person by an email claim anyone holding the scope could write.
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
    await slackAgentConfigs(req, keyValueStoreService),
    keyValueStoreService ? slackIdentityLinks(keyValueStoreService) : undefined,
  );
};

/** The Slack bots of the organisation, as far as binding needs them: which agent each one answers for. */
export interface SlackAgentConfig {
  agentId?: string | null;
}

const slackAgentConfigs = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  keyValueStoreService?: KeyValueStoreService,
): Promise<readonly SlackAgentConfig[]> =>
  req.params.agentKey && keyValueStoreService
    ? (await getSlackBotStore(keyValueStoreService)).configs
    : [];

/** The live PipesHub account a Slack account is linked to in `orgId` (ADR-SLACKID-01); never another org's link. */
export const linkedUser = (
  orgId: string,
  slack: SlackIdentity,
  links: SlackIdentityLinks = slackIdentityLinks(),
): Promise<LinkedAccount | null> => liveLinkedAccount(links, slack, orgId);

/**
 * Makes a Slack bot's request the request of the account the Slack person linked in `orgId`, or of the agent's own
 * service account when there is no link and the agent is one of `agentConfigs` and runs as one. Otherwise it is refused
 * with the link message. The token's email is never read: it came from a Slack profile. The org is the caller's.
 */
export const bindScopedRequestToSlackAccount = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  orgId: string,
  agentConfigs: readonly SlackAgentConfig[],
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
  // An agent that runs as its own service account answers a workspace member who has no PipesHub account; who
  // they are is their Slack identity, never an address they typed into their profile.
  const subject =
    `slack-${identity.teamId}-${identity.slackUserId}@slack.invalid`.toLowerCase();
  if (
    await actAsServiceAccountAgent(req, appConfig, agentConfigs, orgId, subject)
  ) {
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

/** A caller with no PipesHub account, let in only for an agent set up for Slack that runs as a service account. */
const actAsServiceAccountAgent = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  agentConfigs: readonly SlackAgentConfig[],
  orgId: string,
  email: string,
): Promise<boolean> => {
  const { agentKey } = req.params;
  if (!agentKey) return false;
  if (!agentConfigs.some((config) => config.agentId === agentKey)) return false;
  if (
    !(await checkServiceAccountAccess(
      req as AuthenticatedServiceRequest,
      appConfig,
    ))
  )
    return false;
  const org = await getOrgRepository().getLive(orgId);
  if (!org?._id) {
    throw new NotFoundError('Organization not found');
  }
  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );
  const stableUserIdHex = stableObjectIdHexForExternalEmail(email);
  const scopedJwtToken = authTokenService.generateScopedToken(
    {
      userId: stableUserIdHex,
      orgId: org._id,
      email: email,
      scopes: [TokenScopes.CONVERSATION_CREATE],
      isServiceAccount: true,
      // The query service records it as the service identity an upload came through (QA-15G).
      agentKey,
    },
    '1h',
  );
  (req as AuthenticatedServiceRequest).headers.authorization =
    `Bearer ${scopedJwtToken}`;
  (req as AuthenticatedUserRequest).user = {
    userId: stableUserIdHex,
    orgId: org._id,
    email: email,
    scopes: [TokenScopes.CONVERSATION_CREATE],
    isServiceAccount: true,
  };
  return true;
};

export const checkServiceAccountAccess = async (
  req: AuthenticatedServiceRequest,
  appConfig: AppConfig,
): Promise<boolean> => {
  const requestId = req.context?.requestId;
  try {
    const agentKey = req.params.agentKey as string;

    const aiCommandOptions: AICommandOptions = {
      uri: `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}/internal/service-account`,
      method: HttpMethod.GET,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
    };
    const aiCommand = new AIServiceCommand(aiCommandOptions);
    const aiResponse = await aiCommand.execute();
    if (!aiResponse) {
      return false;
    }
    if (aiResponse.statusCode !== 200) {
      throw handleBackendError(aiResponse, 'Check Service Account Access');
    }
    const response = aiResponse.data as { isServiceAccount: boolean };
    const serviceAccountResponse = response.isServiceAccount;
    return serviceAccountResponse;
  } catch (error: any) {
    logger.error('Error checking service account access', {
      requestId,
      message: 'Error checking service account access',
      error: error.message,
    });
    return false;
  }
};
