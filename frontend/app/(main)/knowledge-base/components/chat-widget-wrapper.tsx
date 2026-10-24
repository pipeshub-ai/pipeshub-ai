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
import type { MentionNode } from '@/chat/utils/node-mentions';
import { MAX_SELECTED_NODES } from '@/chat/utils/tree-selection';
import { toast } from '@/lib/store/toast-store';
import { filterBarChatScope } from '../utils/filter-bar-chat-scope';
import { allCollectionsChatScope } from '../utils/root-app-list';

// The widget is a single fixed-width line, so a long title pushes the placeholder under the send button.
const MAX_WIDGET_TITLE_LENGTH = 30;

interface ChatWidgetWrapperProps {
  /** Currently displayed title (collection name, folder name, etc.) */
  currentTitle: string;
  /** The KB id of the current collection being viewed */
  selectedKbId: string | null;
  /** Whether we're in all-records mode */
  isAllRecordsMode: boolean;
  /**
   * The folder (or collection, or connector) currently open. A question
   * with no `@` mention is filtered to this node. `@` searches under it.
   */
  scopeNode?: MentionNode | null;
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
  scopeNode,
}: ChatWidgetWrapperProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useTranslation();

  // Read selected items from KB store
  const selectedItems = useKnowledgeBaseStore((s) => s.selectedItems);
  const selectedRecords = useKnowledgeBaseStore((s) => s.selectedRecords);

  const selectedSet = isAllRecordsMode ? selectedRecords : selectedItems;
  const barFilter = useKnowledgeBaseStore((s) => (isAllRecordsMode ? s.allRecordsFilter : s.filter));
  const barSources = useKnowledgeBaseStore(
    (s) => (isAllRecordsMode ? s.allRecordsTableData : s.tableData)?.filters?.available?.connectors
  );

  // Dynamic placeholder — comes from the parent page
  const widgetPlaceholder = useMemo(() => {
    const fullTitle = currentTitle || (isAllRecordsMode ? t('nav.allRecords') : t('nav.collections'));
    const title =
      fullTitle.length > MAX_WIDGET_TITLE_LENGTH
        ? `${fullTitle.slice(0, MAX_WIDGET_TITLE_LENGTH).trimEnd()}…`
        : fullTitle;
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
    async (message: string, attachments?: AttachmentRef[], mentions?: MentionNode[]) => {
      if (!message.trim() && (!attachments || attachments.length === 0)) return false;

      // @ mentions, then the open node, then the open collection, then the filter bar,
      // then (at the Collections root) every collection.
      const mentioned = mentions ?? [];
      let selectedNodes: MentionNode[] | undefined =
        mentioned.length > 0 ? mentioned : scopeNode ? [scopeNode] : undefined;

      const collections: Array<{ id: string; name: string }> = [];
      if (!selectedNodes && selectedKbId && currentTitle) {
        collections.push({ id: selectedKbId, name: currentTitle });
      }
      if (!selectedNodes && collections.length === 0) {
        const fromBar = filterBarChatScope(barFilter, barSources ?? []);
        if (fromBar.length > MAX_SELECTED_NODES) {
          toast.error(t('kb.chatSelectionLimit', { max: MAX_SELECTED_NODES }));
          return false;
        }
        if (fromBar.length > 0) selectedNodes = fromBar;
      }
      // The API takes at most MAX_SELECTED_NODES (200) selected nodes, so a user with
      // more collections than that cannot ask the Collections root about all of them.
      if (!selectedNodes && collections.length === 0 && !isAllRecordsMode) {
        let all: MentionNode[] | null;
        try {
          all = await allCollectionsChatScope(MAX_SELECTED_NODES);
        } catch {
          toast.error(t('kb.chatCollectionsLoadFailed'));
          return false;
        }
        if (all === null) {
          toast.error(t('kb.chatTooManyCollections', { max: MAX_SELECTED_NODES }));
          return false;
        }
        // With no collection there is nothing to scope to, and an unscoped question would search everything.
        if (all.length === 0) {
          toast.error(t('kb.chatNoCollections'));
          return false;
        }
        selectedNodes = all;
      }

      const selectedRecordIds =
        selectedSet.size > 0 ? Array.from(selectedSet) : undefined;

      const context: PendingChatContext = {
        message,
        attachments,
        pageContext: {
          collections: collections.length > 0 ? collections : undefined,
          selectedNodes,
          selectedRecordIds,
          sourceLabel: currentTitle || undefined,
        },
        referrerPage: pathname,
      };

      usePendingChatStore.getState().setPending(context);
      router.push('/chat');
      return true;
    },
    [selectedKbId, currentTitle, selectedSet, scopeNode, barFilter, barSources, isAllRecordsMode, pathname, router, t],
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
      mentionParent={
        scopeNode ? { nodeType: scopeNode.nodeType, id: scopeNode.id } : null
      }
    />
  );
}
