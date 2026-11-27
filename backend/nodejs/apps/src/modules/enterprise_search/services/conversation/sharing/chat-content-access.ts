import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../../libs/enums/token-scopes.enum';
import { handleBackendError } from '../../../../../libs/errors/backend-error';
import { withCause } from '../../../../../libs/errors/base.error';
import { InternalServerError } from '../../../../../libs/errors/http.errors';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../../../libs/errors/reader-friendly';
import { getConversationRepository } from '../../../../../libs/repositories/conversation';
import { AuthTokenService } from '../../../../../libs/services/authtoken.service';
import { Logger } from '../../../../../libs/services/logger.service';
import { AppConfig } from '../../../../tokens_manager/config/config';
import { AIServiceResponse } from '../../../types/conversation.interfaces';

const logger = Logger.getInstance({ service: 'ChatContentAccess' });

const CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS = 15_000;
// Awaited on a shared viewer's conversation load, so a degraded AI service must
// not stall the read.
const CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS = 3_000;

const CHAT_ATTACHMENT_CONNECTOR = 'ATTACHMENTS';

const field = (value: unknown, key: string): unknown =>
  typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)[key]
    : undefined;

const listField = (value: unknown, key: string): readonly unknown[] => {
  const list = field(value, key);
  return Array.isArray(list) ? list : [];
};

const nonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value !== '';

export const collectChatAttachmentRecordIds = (
  messages: readonly unknown[],
): string[] => {
  const ids = new Set<string>();
  for (const msg of messages) {
    for (const att of listField(msg, 'attachments')) {
      const recordId = field(att, 'recordId');
      if (nonEmptyString(recordId)) {
        ids.add(recordId);
      }
    }
    for (const cit of listField(msg, 'citations')) {
      const citationId = field(cit, 'citationId');
      const populated =
        field(cit, 'citationData') ??
        (field(citationId, 'metadata') ? citationId : null);
      const metadata = field(populated, 'metadata');
      const recordId = field(metadata, 'recordId');
      if (!nonEmptyString(recordId)) {
        continue;
      }
      const connector = field(metadata, 'connector');
      if (
        typeof connector === 'string' &&
        connector.toUpperCase() === CHAT_ATTACHMENT_CONNECTOR
      ) {
        ids.add(recordId);
      }
    }
  }
  return [...ids];
};

const loadConversationChatAttachmentRecordIds = async (
  conversationId: string,
): Promise<string[]> =>
  collectChatAttachmentRecordIds(
    await getConversationRepository().listAttachmentsAndCitedRecords(
      conversationId,
    ),
  );

interface ConversationPermissionSyncOptions {
  appConfig: AppConfig;
  records: 'attachments' | 'artifacts';
  method: typeof HttpMethod.POST | typeof HttpMethod.DELETE;
  // Conversation owner. Python only grants/revokes on records this user OWNS,
  // so callers must have verified ownership or sharing in Mongo first.
  grantorUserId: string;
  orgId: string;
  body: Record<string, unknown>;
  requestId?: string;
  conversationId?: string;
  timeoutMs?: number;
  maxAttempts?: number;
}

/** One call to the query service's record-permission route: resolves on any status, rejects on a transport failure. */
const requestConversationRecordPermissions = async ({
  appConfig,
  records,
  method,
  grantorUserId,
  orgId,
  body,
  timeoutMs = CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS,
  maxAttempts,
}: ConversationPermissionSyncOptions): Promise<AIServiceResponse<unknown>> => {
  const serviceToken = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  ).generateScopedToken(
    {
      userId: grantorUserId,
      orgId,
      scopes: [TokenScopes.CONVERSATION_PERMISSIONS],
    },
    '5m',
  );
  return new AIServiceCommand<unknown>({
    uri: `${appConfig.aiBackend}/api/v1/chat/${records}/permissions`,
    method,
    headers: {
      Authorization: `Bearer ${serviceToken}`,
      'Content-Type': 'application/json',
    },
    body,
    timeoutMs,
    maxAttempts,
  }).execute();
};

const isSuccess = (statusCode: number): boolean =>
  statusCode >= 200 && statusCode < 300;

// Best-effort: a failure is logged and never fails the share, the view, or an unshare already written.
const syncConversationRecordPermissions = async (
  options: ConversationPermissionSyncOptions,
): Promise<void> => {
  const { requestId, conversationId, records, method } = options;
  const logContext = { requestId, conversationId, records, method };
  try {
    const response = await requestConversationRecordPermissions(options);
    if (!isSuccess(response.statusCode)) {
      logger.warn('Conversation record permission sync rejected', {
        ...logContext,
        statusCode: response.statusCode,
        response: response.data,
      });
    }
  } catch (error: unknown) {
    logger.warn('Conversation record permission sync failed', {
      ...logContext,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

const UNSHARE_OPERATION = 'Unshare conversation';

/** An unshare's revoke: anything but a 2xx fails the unshare (an outage keeps its Retry-After). */
const requireRecordPermissionsRevoked = async (
  options: ConversationPermissionSyncOptions,
): Promise<void> => {
  let response: AIServiceResponse<unknown>;
  try {
    response = await requestConversationRecordPermissions(options);
  } catch (error) {
    throw handleBackendError(error, UNSHARE_OPERATION);
  }
  if (isSuccess(response.statusCode)) return;
  // Node writes this request's body and token, so a 4xx is our fault, not the owner's:
  // relaying a 401 would send the owner's browser to re-login.
  throw response.statusCode < 500
    ? withCause(
        markClientSafe(
          new InternalServerError(serverFailureMessage(UNSHARE_OPERATION)),
        ),
        { status: response.statusCode },
      )
    : handleBackendError(response, UNSHARE_OPERATION);
};

/** The DELETEs that take an unshare's people off the conversation's attachments (when it has any) and artifacts. */
const revokesFor = (
  change: ShareChange,
  attachmentRecordIds: readonly string[],
): ConversationPermissionSyncOptions[] => {
  const grantor = {
    appConfig: change.appConfig,
    method: HttpMethod.DELETE,
    grantorUserId: change.ownerId,
    orgId: change.orgId,
    requestId: change.requestId,
    conversationId: change.conversationId,
  } as const;
  const userIds = [...change.userIds];
  return [
    ...(attachmentRecordIds.length > 0
      ? [
          {
            ...grantor,
            records: 'attachments' as const,
            body: { userIds, recordIds: [...attachmentRecordIds] },
          },
        ]
      : []),
    {
      ...grantor,
      records: 'artifacts' as const,
      body: { conversationId: change.conversationId, userIds },
    },
  ];
};

export interface ShareChange {
  appConfig: AppConfig;
  conversationId: string;
  /** The conversation's owner, who grants on the records they own. */
  ownerId: string;
  orgId: string;
  userIds: readonly string[];
  requestId?: string;
}

export interface ConversationView {
  appConfig: AppConfig;
  conversationId: string;
  ownerId: string;
  viewerId: string;
  orgId: string;
  /** 1 for the newest page. */
  page: number;
  /** The messages of the page being returned. */
  messages: unknown[];
  requestId?: string;
}

/**
 * How people a conversation is shared with get to read its attachments and artifacts. Every call but `revoke` is
 * best effort: a failure is logged and never fails the share, the unshare or the read.
 */
export interface IChatContentAccess {
  shared(change: ShareChange): Promise<void>;
  /**
   * Before an unshare is written: takes back what the people losing access were given. Anything but a yes from the
   * query service throws (an outage keeps its Retry-After), and the unshare is not written: the people stay listed,
   * so the owner can try again.
   */
  revoke(change: ShareChange): Promise<void>;
  /**
   * After the unshare is written: the same again, best effort, so a view that read the old list and granted while
   * the revoke ran is undone.
   */
  unshared(change: ShareChange): Promise<void>;
  /** Someone other than the owner opened a page of the conversation. */
  viewed(view: ConversationView): Promise<void>;
}

/**
 * Grants recipients READER on the conversation's attachments (message chips and ATTACHMENTS
 * citations) and artifacts in the graph when it is shared, and revokes them on unshare: first
 * required, then once more after the unshare is written. Sharing only grants on records that
 * existed at share time, so a viewer is caught up as they read.
 */
export class RecordPermissionChatContentAccess implements IChatContentAccess {
  /** The attachments each unshare's revoke read, for its repeat when they can't be read again. */
  private readonly revoked = new WeakMap<ShareChange, string[]>();

  shared(change: ShareChange): Promise<void> {
    return this.sync(change, HttpMethod.POST);
  }

  async revoke(change: ShareChange): Promise<void> {
    const attachmentRecordIds = await loadConversationChatAttachmentRecordIds(
      change.conversationId,
    );
    this.revoked.set(change, attachmentRecordIds);
    // Attachments first, then artifacts; the first refusal ends it.
    for (const revoke of revokesFor(change, attachmentRecordIds)) {
      await requireRecordPermissionsRevoked(revoke);
    }
  }

  async unshared(change: ShareChange): Promise<void> {
    const earlier = this.revoked.get(change) ?? [];
    this.revoked.delete(change);
    // Read again: an attachment may have been added while the revokes ran. The unshare is written, so a failed
    // read is only logged, and the repeat takes the list the revoke read.
    const attachmentRecordIds = await loadConversationChatAttachmentRecordIds(
      change.conversationId,
    ).catch((error: unknown) => {
      logger.warn(
        'Could not re-read chat attachments after unshare; repeating the revoke on the earlier list',
        {
          requestId: change.requestId,
          conversationId: change.conversationId,
          errorType: error instanceof Error ? error.name : typeof error,
        },
      );
      return earlier;
    });
    // It only has to beat a view's grant, so it gets the view's budget.
    for (const revoke of revokesFor(change, attachmentRecordIds)) {
      await syncConversationRecordPermissions({
        ...revoke,
        timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
        maxAttempts: 1,
      });
    }
  }

  async viewed(view: ConversationView): Promise<void> {
    const { appConfig, conversationId, ownerId, viewerId, orgId, requestId } =
      view;
    if (!ownerId || ownerId === viewerId) {
      return;
    }
    // Awaited so the edges exist before the client requests artifact content; older pages carry
    // no new artifacts and skip it.
    if (view.page === 1) {
      await syncConversationRecordPermissions({
        appConfig,
        records: 'artifacts',
        method: HttpMethod.POST,
        grantorUserId: ownerId,
        orgId,
        body: { conversationId, userIds: [viewerId] },
        requestId,
        conversationId,
        timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
        maxAttempts: 1,
      });
    }
    // On every page the viewer loads: an attachment added after the share can already sit on an older page.
    const attachmentRecordIds = collectChatAttachmentRecordIds(view.messages);
    if (attachmentRecordIds.length > 0) {
      await syncConversationRecordPermissions({
        appConfig,
        records: 'attachments',
        method: HttpMethod.POST,
        grantorUserId: ownerId,
        orgId,
        body: { userIds: [viewerId], recordIds: attachmentRecordIds },
        requestId,
        conversationId,
        timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
        maxAttempts: 1,
      });
    }
  }

  private async sync(
    change: ShareChange,
    method: typeof HttpMethod.POST | typeof HttpMethod.DELETE,
  ): Promise<void> {
    const { appConfig, conversationId, ownerId, orgId, requestId } = change;
    const userIds = [...change.userIds];
    const attachmentRecordIds =
      await loadConversationChatAttachmentRecordIds(conversationId);
    const grantor = {
      appConfig,
      method,
      grantorUserId: ownerId,
      orgId,
      requestId,
      conversationId,
    } as const;
    if (attachmentRecordIds.length > 0) {
      await syncConversationRecordPermissions({
        ...grantor,
        records: 'attachments',
        body: { userIds, recordIds: attachmentRecordIds },
      });
    }
    await syncConversationRecordPermissions({
      ...grantor,
      records: 'artifacts',
      body: { conversationId, userIds },
    });
  }
}
