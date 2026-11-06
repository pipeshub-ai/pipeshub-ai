import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { Logger } from '../../../libs/services/logger.service';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import { deleteConversationArtifactsOwnedBy } from '../../artifacts/services/conversation-artifacts.service';
import { AppConfig } from '../../tokens_manager/config/config';

const logger = Logger.getInstance({ service: 'Temporary Chat Cleanup' });
const CHAT_ATTACHMENT_CONNECTOR = 'ATTACHMENTS';
const STAND_IN_TTL_SECONDS = 60;

/** Chat-upload record ids on this conversation only — never KB/connector citations. */
export function collectTemporaryChatAttachmentRecordIds(
  messages: Array<{
    attachments?: Array<{ recordId?: string } | null> | null;
    citations?: Array<{
      citationData?: { metadata?: { recordId?: string; connector?: string } };
      citationId?:
        | string
        | { metadata?: { recordId?: string; connector?: string } };
    }> | null;
  }>,
): string[] {
  const ids = new Set<string>();
  for (const msg of messages ?? []) {
    for (const att of msg.attachments ?? []) {
      if (typeof att?.recordId === 'string' && att.recordId) {
        ids.add(att.recordId);
      }
    }
    for (const cit of msg.citations ?? []) {
      const populated =
        cit?.citationData ??
        (cit?.citationId &&
        typeof cit.citationId === 'object' &&
        cit.citationId.metadata
          ? cit.citationId
          : null);
      const recordId = populated?.metadata?.recordId;
      if (typeof recordId !== 'string' || !recordId) continue;
      if (
        String(populated?.metadata?.connector ?? '').toUpperCase() ===
        CHAT_ATTACHMENT_CONNECTOR
      ) {
        ids.add(recordId);
      }
    }
  }
  return [...ids];
}

/**
 * Deletes chat-attachment records this temporary conversation uploaded.
 * Each upload has its own recordId, so a normal chat that uploaded the same
 * bytes keeps its own copy. Best-effort: a failed delete is logged and does
 * not block the chat hard-delete (leave uses keepalive).
 */
export async function deleteTemporaryChatAttachments(
  appConfig: AppConfig,
  conversationId: string,
  authorization: string,
): Promise<void> {
  const rows =
    await getConversationRepository().listAttachmentsAndCitedRecords(
      conversationId,
    );
  const recordIds = collectTemporaryChatAttachmentRecordIds(rows);
  await Promise.all(
    recordIds.map(async (recordId) => {
      try {
        const aiUrl = `${appConfig.aiBackend}/api/v1/chat/attachments/${encodeURIComponent(recordId)}`;
        const aiRes = await fetch(aiUrl, {
          method: 'DELETE',
          headers: { Authorization: authorization },
        });
        if (aiRes.status >= 400 && aiRes.status !== 404) {
          logger.warn('Temporary chat attachment delete rejected', {
            conversationId,
            recordId,
            status: aiRes.status,
          });
        }
      } catch (error: unknown) {
        logger.warn('Temporary chat attachment delete failed', {
          conversationId,
          recordId,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }),
  );
}

export function mintTemporaryChatCleanupAuthorization(
  appConfig: AppConfig,
  owner: { orgId: string; userId: string },
): string {
  const token = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  ).generateToken(
    {
      userId: owner.userId,
      orgId: owner.orgId,
      role: 'member',
    },
    STAND_IN_TTL_SECONDS,
  );
  return `Bearer ${token}`;
}

/** Artifacts (strict) then chat attachments (best-effort) for a temporary chat. */
export async function purgeTemporaryChatSideEffects(
  appConfig: AppConfig,
  conversationId: string,
  owner: { authorization: string; orgId: string; userId: string },
): Promise<void> {
  await deleteConversationArtifactsOwnedBy(
    appConfig,
    conversationId,
    owner,
  );
  await deleteTemporaryChatAttachments(
    appConfig,
    conversationId,
    owner.authorization,
  );
}
