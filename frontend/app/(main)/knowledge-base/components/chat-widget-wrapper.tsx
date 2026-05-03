'use client';

import { useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter, usePathname } from 'next/navigation';
import { ChatInput } from '@/chat/components/chat-input';
import { useKnowledgeBaseStore } from '../store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import type { PendingChatContext } from '@/lib/store/pending-chat-store';
import type { UploadedFile } from '@/chat/types';

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

  const handleSend = useCallback(
    (message: string, files?: UploadedFile[]) => {
      if (!message.trim() && (!files || files.length === 0)) return;

      // Build page context from KB state
      const collections: Array<{ id: string; name: string }> = [];
      if (selectedKbId && currentTitle) {
        collections.push({ id: selectedKbId, name: currentTitle });
      }

      const selectedRecordIds =
        selectedSet.size > 0 ? Array.from(selectedSet) : undefined;

      const context: PendingChatContext = {
        message,
        uploadedFiles: files,
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
    />
  );
}
