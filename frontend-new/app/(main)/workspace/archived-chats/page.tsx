'use client';

import React, { useEffect, useCallback, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Box } from '@radix-ui/themes';
import {
  ArchivedChatsSidebar,
  ArchivedChatView,
  ArchivedChatsEmptyState,
  ArchivedChatSearch,
} from './components';
import { useArchivedChatsStore } from './store';
import type { Conversation } from './types';

/**
 * /workspace/archived-chats
 *
 * Layout: fixed-width left sidebar (archived chat list) + flex content area.
 *
 * URL pattern: /workspace/archived-chats?conversationId=xxx
 * - No conversationId → empty state ("Select an archived chat to preview")
 * - conversationId     → load and show that conversation read-only
 *
 * After Restore: navigate to /chat?conversationId=xxx
 * After Permanent Delete: select the next conversation in the list,
 *   or show empty state if none remain.
 */
function ArchivedChatsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get('conversationId');
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  // Store slices
  const conversations = useArchivedChatsStore((s) => s.conversations);
  const isLoadingList = useArchivedChatsStore((s) => s.isLoadingList);
  const messagesMap = useArchivedChatsStore((s) => s.messagesMap);
  const selected = useArchivedChatsStore((s) => s.selected);
  const isLoadingConversation = useArchivedChatsStore((s) => s.isLoadingConversation);
  const conversationError = useArchivedChatsStore((s) => s.conversationError);

  const loadArchivedConversations = useArchivedChatsStore((s) => s.loadArchivedConversations);
  const loadConversation = useArchivedChatsStore((s) => s.loadConversation);
  const removeConversation = useArchivedChatsStore((s) => s.removeConversation);
  const clearSelection = useArchivedChatsStore((s) => s.clearSelection);

  // ── On mount: load the archived conversations list ──────────────────
  useEffect(() => {
    loadArchivedConversations();
    // Cleanup: clear selection when leaving the page
    return () => clearSelection();
  }, []);

  // ── URL → store sync: load conversation when URL conversationId changes ──
  useEffect(() => {
    if (!conversationId) {
      clearSelection();
      return;
    }
    // Title is resolved from the API response inside loadConversation
    loadConversation(conversationId, '');
  }, [conversationId]);

  // ── Race-condition guard: re-try once the messages map is populated ──
  // On direct navigation the conversationId effect fires before
  // loadArchivedConversations finishes, so the messagesMap is empty and
  // loadConversation can't resolve the messages. Once the map arrives,
  // we check if the selection is still missing and retry.
  useEffect(() => {
    if (!conversationId) return;
    if (selected?.id === conversationId) return; // already resolved
    if (isLoadingConversation) return;
    if (Object.keys(messagesMap).length === 0) return; // list not ready yet
    loadConversation(conversationId, '');
    // intentionally only reacts to messagesMap transitions
  }, [messagesMap]);

  // ── ⌘+K to open search modal ───────────────────────────────────────
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsSearchOpen((prev) => !prev);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // ── Handlers ────────────────────────────────────────────────────────

  const handleSelect = useCallback(
    (conversation: Conversation) => {
      router.push(
        `/workspace/archived-chats?conversationId=${conversation.id}`
      );
    },
    [router]
  );

  const handleRestored = useCallback(
    (restoredId: string) => {
      // Remove from archived list and navigate to the live chat
      removeConversation(restoredId);
      router.push(`/chat?conversationId=${restoredId}`);
    },
    [router, removeConversation]
  );

  const handleDeleted = useCallback(
    (deletedId: string) => {
      removeConversation(deletedId);

      // Find the next conversation to show
      const remaining = conversations.filter((c) => c.id !== deletedId);
      if (remaining.length > 0) {
        // Navigate to the conversation that was above or below the deleted one
        const deletedIndex = conversations.findIndex((c) => c.id === deletedId);
        const nextIndex = Math.min(deletedIndex, remaining.length - 1);
        router.push(
          `/workspace/archived-chats?conversationId=${remaining[nextIndex].id}`
        );
      } else {
        // No more archived conversations — show empty state
        router.push('/workspace/archived-chats');
      }
    },
    [router, conversations, removeConversation]
  );

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <Flex style={{ height: '100%', width: '100%', overflow: 'hidden' }}>
      {/* Left: archived chats list */}
      <ArchivedChatsSidebar
        conversations={conversations}
        isLoading={isLoadingList}
        selectedConversationId={conversationId}
        onSelect={handleSelect}
        onSearchClick={() => setIsSearchOpen(true)}
      />

      {/* Right: conversation preview or empty state */}
      <Box style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {!conversationId ? (
          <ArchivedChatsEmptyState />
        ) : (
          <ArchivedChatView
            conversationId={conversationId}
            conversationTitle={selected?.title ?? ''}
            messages={selected?.messages ?? []}
            // Show as loading whenever the selected conv doesn't match the URL
            // (covers the gap between URL change and loadConversation starting)
            isLoading={isLoadingConversation || selected?.id !== conversationId}
            error={conversationError}
            onRestored={handleRestored}
            onDeleted={handleDeleted}
          />
        )}
      </Box>
      {/* Search modal */}
      <ArchivedChatSearch
        open={isSearchOpen}
        onClose={() => setIsSearchOpen(false)}
        conversations={conversations}
        isLoading={isLoadingList}
        onSelect={(id) =>
          router.push(`/workspace/archived-chats?conversationId=${id}`)
        }
      />
    </Flex>
  );
}

export default function ArchivedChatsPage() {
  return (
    <Suspense>
      <ArchivedChatsPageContent />
    </Suspense>
  );
}
