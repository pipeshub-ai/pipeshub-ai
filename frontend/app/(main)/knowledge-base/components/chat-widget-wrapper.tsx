'use client';

import { useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter, usePathname } from 'next/navigation';
import { ChatInput } from '@/chat/components/chat-input';
import { useKnowledgeBaseStore } from '../store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import type { PendingChatContext } from '@/lib/store/pending-chat-store';
import { ChatApi } from '@/chat/api';
import type { AttachmentRef } from '@/chat/types';

interface ChatWidgetWrapperProps {
  /** Currently displayed title (collection name, folder name, etc.) */
  currentTitle: string;
  /** The KB id of the current collection being viewed */
  selectedKbId: string | null;
  /** Whether we're in all-records mode */
  isAllRecordsMode: boolean;
}

/**
 * Thin wrapper that connects `ChatInput` (widget variant) to the
 * knowledge-base page context. On send it stores a `PendingChatContext`
 * and navigates to `/chat` where the message is auto-sent.
 */
export function ChatWidgetWrapper({
  currentTitle,
  selectedKbId,
  isAllRecordsMode,
}: ChatWidgetWrapperProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useTranslation();

  // Read selected items from KB store
  const selectedItems = useKnowledgeBaseStore((s) => s.selectedItems);
  const selectedRecords = useKnowledgeBaseStore((s) => s.selectedRecords);

  const selectedSet = isAllRecordsMode ? selectedRecords : selectedItems;

  // Dynamic placeholder — comes from the parent page
  const widgetPlaceholder = useMemo(() => {
    const title = currentTitle || (isAllRecordsMode ? t('nav.allRecords') : t('nav.collections'));
    return t('chat.askInContext', { title });
  }, [currentTitle, isAllRecordsMode, t]);

  const expandedPlaceholder = useMemo(() => {
    const title = currentTitle || (isAllRecordsMode ? t('nav.allRecords') : t('nav.collections'));
    return t('chat.askAnythingInContext', { title });
  }, [currentTitle, isAllRecordsMode, t]);

  /**
   * Per-file upload, fired the moment the user adds an attachment to the
   * widget composer (not at send time). The widget posts to the same
   * unscoped endpoint as the main chat composer; conversationId is null
   * because the widget always starts a fresh chat on /chat.
   */
  const handleUploadFile = useCallback(
    async (file: File, signal: AbortSignal): Promise<AttachmentRef> => {
      const refs = await ChatApi.uploadAttachments([file], {
        conversationId: null,
        signal,
      });
      const ref = refs[0];
      if (!ref) throw new Error('Upload returned no attachment ref');
      return ref;
    },
    [],
  );

  const handleDeleteFile = useCallback((recordId: string) => {
    // Fire and forget — orphan is preferable to blocking the widget UI.
    ChatApi.deleteAttachment(recordId, {}).catch(() => {});
  }, []);

  const handleSend = useCallback(
    (message: string, attachments?: AttachmentRef[]) => {
      if (!message.trim() && (!attachments || attachments.length === 0)) return;

      // Build page context from KB state
      const collections: Array<{ id: string; name: string }> = [];
      if (selectedKbId && currentTitle) {
        collections.push({ id: selectedKbId, name: currentTitle });
      }

      const selectedRecordIds =
        selectedSet.size > 0 ? Array.from(selectedSet) : undefined;

      const context: PendingChatContext = {
        message,
        attachments,
        pageContext: {
          collections: collections.length > 0 ? collections : undefined,
          selectedRecordIds,
          sourceLabel: currentTitle || undefined,
        },
        referrerPage: pathname,
      };

      usePendingChatStore.getState().setPending(context);
      router.push('/chat');
    },
    [selectedKbId, currentTitle, selectedSet, pathname, router],
  );

  return (
    <ChatInput
      variant="widget"
      expandable
      placeholder={expandedPlaceholder}
      widgetPlaceholder={widgetPlaceholder}
      onSend={handleSend}
      onUploadFile={handleUploadFile}
      onDeleteFile={handleDeleteFile}
    />
  );
}
