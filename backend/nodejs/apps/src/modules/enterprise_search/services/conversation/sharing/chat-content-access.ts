import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../../../libs/services/authtoken.service';
import { Logger } from '../../../../../libs/services/logger.service';
import { AppConfig } from '../../../../tokens_manager/config/config';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';

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
): Promise<string[]> => {
  const sessionMessages = await ChatSessionMessage.find(
    { sessionId: conversationId },
    { attachments: 1, citations: 1 },
  )
    .populate({
      path: 'citations.citationId',
      model: 'citation',
      select: 'metadata.recordId metadata.connector',
    })
    .lean();
  return collectChatAttachmentRecordIds(sessionMessages);
};

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

// Best-effort: a failure is logged and never fails the share/unshare/view.
const syncConversationRecordPermissions = async ({
  appConfig,
  records,
  method,
  grantorUserId,
  orgId,
  body,
  requestId,
  conversationId,
  timeoutMs = CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS,
  maxAttempts,
}: ConversationPermissionSyncOptions): Promise<void> => {
  const logContext = { requestId, conversationId, records, method };
  try {
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
    const response = await new AIServiceCommand({
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
    // execute() resolves on 4xx/5xx; only transport errors reject.
    if (response.statusCode < 200 || response.statusCode >= 300) {
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
 * How people a conversation is shared with get to read its attachments and artifacts. Every
 * call is best effort: a failure is logged and never fails the share, unshare or read.
 */
export interface IChatContentAccess {
  shared(change: ShareChange): Promise<void>;
  unshared(change: ShareChange): Promise<void>;
  /** Someone other than the owner opened a page of the conversation. */
  viewed(view: ConversationView): Promise<void>;
}

/**
 * Grants recipients READER on the conversation's attachments (message chips and ATTACHMENTS
 * citations) and artifacts in the graph when it is shared, and revokes them on unshare. Sharing
 * only grants on records that existed at share time, so a viewer is caught up as they read.
 */
export class RecordPermissionChatContentAccess implements IChatContentAccess {
  shared(change: ShareChange): Promise<void> {
    return this.sync(change, HttpMethod.POST);
  }

  unshared(change: ShareChange): Promise<void> {
    return this.sync(change, HttpMethod.DELETE);
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
