import * as crypto from 'crypto';
import { Types } from 'mongoose';
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
import { Org } from '../../user_management/schema/org.schema';
import { Users } from '../../user_management/schema/users.schema';

/**
 * Turns a scoped service token (`/internal/...` routes: Slack bot, service
 * accounts) into the user request the chat handlers expect.
 */

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** 24-char hex suitable for Mongo ObjectId; stable per email for Slack/service-account callers without a User row. */
export const stableObjectIdHexForExternalEmail = (email: string): string =>
  crypto
    .createHash('sha256')
    .update(`slack-service-account:${email.toLowerCase().trim()}`)
    .digest('hex')
    .slice(0, 24);

/**
 * The org a Slack scoped token acts in. The bot puts its own org on the token;
 * the email on it is then looked up only inside that org, since the same
 * address can belong to members of different orgs. A token without an org
 * (a bot configured before bots recorded one) is only unambiguous on a
 * single-org instance, so anything else is refused.
 */
const resolveScopedTokenOrgId = async (
  tokenOrgId: unknown,
): Promise<Types.ObjectId> => {
  if (tokenOrgId !== undefined && tokenOrgId !== null) {
    if (typeof tokenOrgId !== 'string' || !Types.ObjectId.isValid(tokenOrgId)) {
      throw new UnauthorizedError('Invalid organization in scoped token');
    }
    const org = await Org.findOne({ _id: tokenOrgId, isDeleted: false }, { _id: 1 });
    if (!org?._id) {
      throw new UnauthorizedError('Organization in scoped token not found');
    }
    return org._id as Types.ObjectId;
  }

  const orgs = await Org.find({ isDeleted: false }, { _id: 1 }, { limit: 2 });
  if (orgs.length !== 1 || !orgs[0]?._id) {
    throw new UnauthorizedError(
      'Scoped token does not name an organization; re-save the Slack bot configuration',
    );
  }
  return orgs[0]._id as Types.ObjectId;
};

export const hydrateScopedRequestAsUser = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  keyValueStoreService?: KeyValueStoreService,
): Promise<void> => {
  const existingUser = (req as AuthenticatedUserRequest).user;
  if (existingUser?.userId && existingUser?.orgId) {
    return;
  }

  const email = (req as AuthenticatedServiceRequest).tokenPayload?.email;
  if (!email) {
    throw new UnauthorizedError('Email not found in scoped token');
  }

  const orgId = await resolveScopedTokenOrgId(
    (req as AuthenticatedServiceRequest).tokenPayload?.orgId,
  );

  const user = await Users.findOne({
    email,
    orgId,
    isDeleted: false,
  });

  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );

  if (!user) {
    const { agentKey } = req.params;
    if (agentKey && keyValueStoreService) {
      const store = await getSlackBotStore(keyValueStoreService);
      const configs = store.configs;
      for (const config of configs) {
        if (config.agentId === agentKey) {
          const isServiceAccount = await checkServiceAccountAccess(
            req,
            appConfig,
          );
          if (isServiceAccount) {
            const stableUserIdHex = stableObjectIdHexForExternalEmail(email);
            const scopedJwtToken = authTokenService.generateScopedToken(
              {
                userId: stableUserIdHex,
                orgId,
                email: email,
                scopes: [TokenScopes.CONVERSATION_CREATE],
                isServiceAccount: true,
              },
              '1h',
            );
            (req as AuthenticatedServiceRequest).headers.authorization =
              `Bearer ${scopedJwtToken}`;
            (req as AuthenticatedServiceRequest).user = {
              userId: new Types.ObjectId(stableUserIdHex),
              orgId,
              email: email,
              scopes: [TokenScopes.CONVERSATION_CREATE],
              isServiceAccount: true,
            };
            return;
          }
        }
      }
    }
    throw new NotFoundError(
      'User not found, create an account on the Pipeshub platform first.',
    );
  }

  const jwtToken = authTokenService.generateToken({
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    mobile: user.mobile,
    userSlug: user.slug,
  });

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

export const checkServiceAccountAccess = async (
  req: AuthenticatedServiceRequest,
  appConfig: AppConfig,
): Promise<boolean> => {
  const requestId = req.context?.requestId;
  try {
    const agentKey = req.params.agentKey;

    const aiCommandOptions: AICommandOptions = {
      uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/internal/service-account`,
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
