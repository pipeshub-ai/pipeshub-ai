import { AppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { JwtServiceTokenIssuer } from '../../../libs/services/service-token.issuer';
import { Logger } from '../../../libs/services/logger.service';
import { IChatAttachmentRef } from '../types/conversation.interfaces';

const logger = Logger.getInstance({ service: 'Attachment Validation' });

export const ATTACHMENT_VALIDATE_TIMEOUT_MS = 2_000;

export interface ConversationPermissionsCaller {
  userId: unknown;
  orgId: unknown;
  isServiceAccount?: boolean;
}

export const mintConversationPermissionsToken = (
  appConfig: AppConfig,
  { userId, orgId, isServiceAccount }: ConversationPermissionsCaller,
): string =>
  new JwtServiceTokenIssuer(
    new AuthTokenService(appConfig.jwtSecret, appConfig.scopedJwtSecret),
  ).issue(
    {
      userId: String(userId),
      orgId: String(orgId),
      scopes: [TokenScopes.CONVERSATION_PERMISSIONS],
      isServiceAccount,
    },
    '5m',
  );

/**
 * Keeps only the attachment refs the AI backend confirms are chat attachments
 * owned by this user in this org (or, for a
 * service account, uploaded into this org). Fails closed: any error or non-2xx drops all.
 */
export const filterOwnedAttachments = async (
  appConfig: AppConfig,
  caller: ConversationPermissionsCaller,
  attachments: IChatAttachmentRef[] | undefined,
): Promise<IChatAttachmentRef[] | undefined> => {
  if (!attachments || attachments.length === 0) {
    return attachments;
  }
  const requested = attachments.length;
  try {
    const serviceToken = mintConversationPermissionsToken(appConfig, caller);
    const response = await new AIServiceCommand<{ recordIds?: unknown }>({
      uri: `${appConfig.aiBackend}/api/v1/chat/attachments/validate`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${serviceToken}`,
        'Content-Type': 'application/json',
      },
      body: { recordIds: attachments.map((a) => a.recordId) },
      timeoutMs: ATTACHMENT_VALIDATE_TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    const returned = response.data?.recordIds;
    if (
      response.statusCode < 200 ||
      response.statusCode >= 300 ||
      !Array.isArray(returned)
    ) {
      logger.warn('Attachment validation rejected; dropping attachments', {
        statusCode: response.statusCode,
        requested,
      });
      return [];
    }
    const allowed = new Set(returned.filter((id) => typeof id === 'string'));
    const kept = attachments.filter((a) => allowed.has(a.recordId));
    if (kept.length < requested) {
      logger.warn('Dropped unvalidated attachments', {
        requested,
        dropped: requested - kept.length,
      });
    }
    return kept;
  } catch (error: unknown) {
    logger.warn('Attachment validation failed; dropping attachments', {
      requested,
      error: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
};
