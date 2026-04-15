'use client';

import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useRouter } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { Box, Flex, Text, TextField, Theme } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useChatStore } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import { groupConversationsByTime, getNonEmptyGroups } from '@/chat/sidebar/time-group';
import type { TimeGroupKey } from '@/lib/utils/group-by-time';
import { TimeGroupedSkeleton, SearchResultsSkeleton } from './skeleton';
import { ChatRow } from './chat-row';
import { SearchResultRow } from './search-result-row';
import { CommandPalette } from './command-palette';

// ── Constants ──

/** Translations for time-group labels */
const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  'Today': 'timeGroup.today',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

// ── Main component ──

interface ChatSearchProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Chat search overlay — triggered by ⌘+K.
 *
 * Shows a centered floating panel with:
 * - Search input
 * - Command Palette row (New Chat action, ⌘+Shift+K)
 * - Recent chats grouped by "Today" / "Previous 7 Days"
 * - Loading skeleton while conversations are loading
 * - "Coming Soon" toast when search is attempted
 *
 * Uses the same chat conversations from the sidebar store.
 * Rendered via React Portal to avoid z-index issues.
 */
export function ChatSearch({ open, onClose }: ChatSearchProps) {
  const { appearance } = useThemeAppearance();
  const { t } = useTranslation();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const addToast = useToastStore((s) => s.addToast);

  const [searchQuery, setSearchQuery] = useState('');
  const [contentLeft, setContentLeft] = useState(0);
  const [isSearching, setIsSearching] = useState(false);

  // Track whether we've already shown the local-search toast this session
  const localSearchToastShownRef = useRef(false);

  // ── Store selectors ──
  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const isConversationsLoading = useChatStore((s) => s.isConversationsLoading);
  const dispatch = useCommandStore((s) => s.dispatch);

  // Merge shared + own conversations for display
  const allConversations = useMemo(
    () => [...sharedConversations, ...conversations],
    [sharedConversations, conversations],
  );

  // Group by time only the two groups to show (Today, Previous 7 Days)
  const timeGroups = useMemo(() => {
    const grouped = groupConversationsByTime(allConversations);
    return getNonEmptyGroups(grouped);
  }, [allConversations]);

  // ── Measure main content area offset ──
  useEffect(() => {
    if (!open) return;

    function measure() {
      const contentArea = document.querySelector('[data-main-content]');
      if (contentArea) {
        setContentLeft(contentArea.getBoundingClientRect().left);
      }
    }

    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [open]);

  // ── Focus input on open ──
  useEffect(() => {
    if (open) {
      setSearchQuery('');
      setIsSearching(false);
      localSearchToastShownRef.current = false;
      // Small delay to ensure portal is mounted
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    }
  }, [open]);

  // ── Escape to close ──
  useEffect(() => {
    if (!open) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // ── Body scroll lock ──
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  // ── Handlers ──
  const handleNewChat = useCallback(() => {
    onClose();
    dispatch('newChat');
  }, [onClose, dispatch]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      onClose();
      router.push(buildChatHref({ conversationId: id }));
    },
    [onClose, router],
  );

  const handleSearchSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
    },
    [],
  );

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
    // Local filtering is instant — no loading state needed
    setIsSearching(false);
  }, []);

  // ── Filter conversations by search query (client-side only) ──
  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    return allConversations.filter((c) =>
      c.title.toLowerCase().includes(q),
    );
  }, [searchQuery, allConversations]);

  // ── Show "Coming Soon / Currently using local search" toast once per session ──
  useEffect(() => {
    if (filteredConversations !== null && !localSearchToastShownRef.current) {
      localSearchToastShownRef.current = true;
      addToast({
        variant: 'info',
        title: t('chat.comingSoon'),
        description: 'Currently using local search',
      });
    }
  }, [filteredConversations, addToast, t]);

  if (!open) return null;

  const overlay = (
    <Theme
      accentColor="jade"
      grayColor="olive"
      appearance={appearance}
      radius="medium"
    >
      <div
        onClick={(e: React.MouseEvent) => {
          if (e.target === e.currentTarget) onClose();
        }}
        style={{
          position: 'fixed',
          top: 0,
          left: contentLeft,
          right: 0,
          bottom: 0,
          zIndex: 50,
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          paddingTop: '15vh',
        }}
      >
        <Flex
          ref={panelRef}
          direction="column"
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          style={{
            width: '37.5rem',
            maxHeight: '512px',
            backdropFilter: 'blur(25px)',
            WebkitBackdropFilter: 'blur(25px)',
            backgroundColor: 'var(--effects-translucent)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow: '0px 20px 48px 0px var(--black-a6)',
            overflow: 'hidden',
          }}
        >
          {/* Search input */}
          <form onSubmit={handleSearchSubmit}>
            <Box style={{ padding: 'var(--space-3) var(--space-3) 0' }}>
              <TextField.Root
                ref={inputRef}
                size="3"
                placeholder={t('nav.searchChats') + '...'}
                value={searchQuery}
                onChange={handleSearchChange}
                style={{ width: '100%' }}
              >
                <TextField.Slot>
                  <MaterialIcon name="search" size={18} color="var(--slate-a11)" />
                </TextField.Slot>
              </TextField.Root>
            </Box>
          </form>

          {/* Command Palette row (New Chat action) */}
          <CommandPalette onClick={handleNewChat} />

          {/* Scrollable content area */}
          <Box
            className="no-scrollbar"
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '0 var(--space-3) var(--space-3)',
            }}
          >
            {/* Loading state */}
            {isConversationsLoading && !filteredConversations ? (
              <TimeGroupedSkeleton />
            ) : isSearching ? (
              <SearchResultsSkeleton />
            ) : filteredConversations ? (
              /* Search results */
              filteredConversations.length > 0 ? (
                <Flex direction="column" gap="1">
                  {filteredConversations.map((conv) => (
                    <SearchResultRow
                      key={conv.id}
                      conversation={conv}
                      onClick={() => handleSelectConversation(conv.id)}
                    />
                  ))}
                </Flex>
              ) : (
                <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                  <Text size="2" style={{ color: 'var(--slate-a9)' }}>
                    {t('message.noResults')}
                  </Text>
                </Flex>
              )
            ) : (
              /* Default: time-grouped recent chats */
              <Flex direction="column" gap="2">
                {timeGroups.map(([groupKey, groupConversations]) => (
                  <Flex key={groupKey} direction="column">
                    {/* Group header */}
                    <Flex
                      align="center"
                      style={{
                        height: 32,
                        padding: '0 var(--space-2)',
                      }}
                    >
                      <Text
                        size="1"
                        style={{
                          color: 'var(--slate-a9)',
                          fontWeight: 400,
                        }}
                      >
                        {t(TIME_GROUP_I18N[groupKey])}
                      </Text>
                    </Flex>

                    {/* Chat items */}
                    <Flex direction="column" gap="1">
                      {groupConversations.map((conv) => (
                        <ChatRow
                          key={conv.id}
                          conversation={conv}
                          onClick={() => handleSelectConversation(conv.id)}
                          showDate={false}
                        />
                      ))}
                    </Flex>
                  </Flex>
                ))}

                {timeGroups.length === 0 && !isConversationsLoading && (
                  <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                    <Text size="2" style={{ color: 'var(--slate-a9)' }}>
                      {t('chat.noChatsYet')}
                    </Text>
                  </Flex>
                )}
              </Flex>
            )}
          </Box>
        </Flex>
      </div>

      {/* Pulse animation for skeleton */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.8; }
        }
      `}</style>
    </Theme>
  );

  return createPortal(overlay, document.body);
}
