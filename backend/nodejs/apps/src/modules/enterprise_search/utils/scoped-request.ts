import * as crypto from 'crypto';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceResponse } from '../types/conversation.interfaces';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  ForbiddenError,
  InternalServerError,
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
import { getUserRepository } from '../../../libs/repositories/user';
import type { UserDto } from '../../../libs/repositories/user';
import { isSlackOnlyAccount } from '../../user_management/constants/service-account.constants';
import {
  assertSlackOnlyAccountActive,
  findOrCreateSlackOnlyAccount,
  SLACK_ONLY_INVITE_MESSAGE,
  slackMemberClaims,
} from '../../user_management/services/slack-only-accounts.service';

/**
 * Turns a scoped service token (`/internal/...` routes: Slack bot, service
 * accounts) into the user request the chat handlers expect.
 */

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

export const STAND_IN_TOKEN_TTL_SECONDS = 60;

/**
 * The OSS Slack bot configuration carries no orgId, so this edition only has a
 * safe answer when there is exactly one org. On a multi-org instance the old
 * `Org.findOne` picked whichever document came back first, which could answer a
 * Slack workspace with another tenant's data — refuse instead. Multi-org Slack
 * is an EE feature and resolves the org from the bot config's own orgId.
 */
export const resolveSoleOrgId = async (): Promise<string> => {
  const { count, oldestId } = await getOrgRepository().liveSummary();
  const soleOrg = count === 1 && oldestId !== null ? oldestId : undefined;
  if (soleOrg === undefined) {
    if (count > 1) {
      throw new UnauthorizedError(
        'Slack access is limited to single-organization deployments on this edition',
      );
    }
    throw new NotFoundError('Organization not found');
  }
  return soleOrg;
};

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

  const email: unknown = (req as AuthenticatedServiceRequest).tokenPayload
    ?.email;
  if (typeof email !== 'string' || email === '') {
    throw new UnauthorizedError('Email not found in scoped token');
  }

  const orgId = await resolveSoleOrgId();

  const user = await getUserRepository().getLiveByEmail(orgId, email);

  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );

  if (!user || isSlackOnlyAccount(user)) {
    const { agentKey } = req.params;
    const config =
      agentKey && keyValueStoreService
        ? (await getSlackBotStore(keyValueStoreService)).configs.find(
            (entry) => entry.agentId === agentKey,
          )
        : undefined;
    await hydrateAsServiceAgentCaller(req, appConfig, {
      email,
      orgId,
      user,
      config,
    });
    return;
  }

  // The Python services ask Node whether every session-style token is still
  // live, and Node refuses one without a role claim. Member is what this token
  // has always been treated as there, so it stays member. With a role Node
  // honours it like a session, so it lives only as long as the request that
  // minted it needs to reach the AI service, not generateToken's default week.
  const jwtToken = authTokenService.generateToken(
    {
      userId: user._id,
      orgId: user.orgId,
      email: user.email,
      fullName: user.fullName,
      mobile: user.mobile,
      userSlug: user.slug,
      role: 'member',
    },
    STAND_IN_TOKEN_TTL_SECONDS,
  );

  req.headers.authorization = `Bearer ${jwtToken}`;

  (req as AuthenticatedUserRequest).user = {
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    mobile: user.mobile,
    userSlug: user.slug,
  };
};

/** The parts of a Slack bot configuration the service-agent path reads. */
export interface ServiceAgentSlackConfig {
  id: string;
  agentId?: string;
  createAccountsForSlackUsers?: boolean;
}

/**
 * Hydrates a Slack caller who is not a full member: someone with no account,
 * or with a Slack-only one. They may only chat with the bot's agent, and only
 * if it is a service agent; the token they get is a service-agent caller's,
 * never a session. When the bot's `createAccountsForSlackUsers` is on and the
 * bot vouched that the person is a full member of its workspace, an account
 * is found or created for them so their chats have a real owner.
 */
export const hydrateAsServiceAgentCaller = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  params: {
    email: string;
    orgId: string;
    user: UserDto | null;
    config: ServiceAgentSlackConfig | undefined;
  },
): Promise<void> => {
  const { email, orgId, user, config } = params;
  const refusal = (): Error =>
    user
      ? new ForbiddenError(SLACK_ONLY_INVITE_MESSAGE)
      : new NotFoundError(
          'User not found, create an account on the Pipeshub platform first.',
        );
  if (!config) throw refusal();
  if (user) await assertSlackOnlyAccountActive(user);

  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );
  const mint = (
    userId: string,
    ttl: number | '1h',
    isServiceAccount: boolean,
  ): string =>
    authTokenService.generateScopedToken(
      {
        userId,
        orgId,
        email,
        scopes: [TokenScopes.CONVERSATION_CREATE],
        ...(isServiceAccount ? { isServiceAccount: true } : {}),
      },
      ttl,
    );

  let callerId = user ? user._id : stableObjectIdHexForExternalEmail(email);
  // The incoming Slack token carries no userId, and the AI service needs one
  // (and the org) to look the agent up in this org.
  const isServiceAgent = await checkServiceAccountAccess(
    req as AuthenticatedServiceRequest,
    appConfig,
    mint(callerId, STAND_IN_TOKEN_TTL_SECONDS, false),
  );
  if (!isServiceAgent) throw refusal();

  const org = await getOrgRepository().getLive(orgId);
  if (!org?._id) {
    throw new NotFoundError('Organization not found');
  }

  if (!user && config.createAccountsForSlackUsers === true) {
    const tokenPayload: Record<string, unknown> | undefined = (
      req as AuthenticatedServiceRequest
    ).tokenPayload;
    const claims = slackMemberClaims(tokenPayload);
    // Only the bot the setting belongs to may create accounts with it.
    if (claims && tokenPayload?.configId === config.id) {
      const account = await findOrCreateSlackOnlyAccount({
        orgId,
        email,
        claims,
        configId: config.id,
      });
      callerId = account._id;
    }
  }

  (req as AuthenticatedServiceRequest).headers.authorization =
    `Bearer ${mint(callerId, '1h', true)}`;
  (req as AuthenticatedUserRequest).user = {
    userId: callerId,
    orgId: org._id,
    email,
    scopes: [TokenScopes.CONVERSATION_CREATE],
    isServiceAccount: true,
  };
};

/**
 * Asks the AI service whether `agentKey` is a service agent in the org named
 * by `token`. Only that token and the request id travel, never the caller's
 * own headers. 403 (not a service agent) and 404 (no such agent here) are a
 * plain "no"; any other failure is a server error, so a broken check is not
 * reported to the user as a missing account.
 */
export const checkServiceAccountAccess = async (
  req: AuthenticatedServiceRequest,
  appConfig: AppConfig,
  token: string,
): Promise<boolean> => {
  const requestId = req.context?.requestId;
  const agentKey = req.params.agentKey as string;
  const failure = new InternalServerError(
    'Could not check whether this agent is a service agent',
  );
  let aiResponse: AIServiceResponse<{ isServiceAccount?: boolean }>;
  try {
    aiResponse = await new AIServiceCommand<{ isServiceAccount?: boolean }>({
      uri: `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}/internal/service-account`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
    }).execute();
  } catch (error) {
    logger.error('Service agent check failed', {
      requestId,
      error: error instanceof Error ? error.message : String(error),
    });
    throw failure;
  }
  if (aiResponse.statusCode === 200) {
    return aiResponse.data?.isServiceAccount === true;
  }
  if (aiResponse.statusCode === 403 || aiResponse.statusCode === 404) {
    return false;
  }
  logger.error('Service agent check failed', {
    requestId,
    statusCode: aiResponse.statusCode,
  });
  throw failure;
};
